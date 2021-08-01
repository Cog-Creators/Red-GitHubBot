import logging
import re
from typing import Any

from gidgethub import aiohttp as gh_aiohttp, sansio

from .. import utils
from ..constants import MAINTENANCE_BRANCHES, UPSTREAM_REPO
from . import gh_router

log = logging.getLogger(__name__)

COPYABLE_LABELS_PREFIXES = ("Type: ", "Release Blocker", "High Priority", "Breaking Change")
TITLE_RE = re.compile(r"\s*\[(?P<branch>\d+\.\d+)\].+\(#(?P<pr_number>\d+)\)")
MAINTENANCE_BRANCH_TITLE_RE = re.compile(
    r"^\s*\[(?P<branch>\d+\.\d+)\].+?(\(#(?P<pr_number>\d+)\))?\s*$"
)
CHECK_RUN_NAME = "Verify title of PR to maintenance branch"


@gh_router.register("pull_request", action="opened")
@gh_router.register("pull_request", action="edited")
async def handle_backport_prs(event: sansio.Event) -> None:
    """
    Handle backport and its original PR.

    This will remove a "Needs Backport To" label from the original PR
    and comment on it about this backport.
    It will also update the backport with labels from the original PR.
    """
    if event.data["action"] == "edited" and "title" not in event.data["changes"]:
        return
    pr_data = event.data["pull_request"]
    installation_id = event.data["installation"]["id"]

    title = utils.normalize_title(pr_data["title"], pr_data["body"])
    if (match := TITLE_RE.match(title)) is None:
        return

    gh = await utils.get_gh_client(installation_id)

    branch = match.group("branch")
    original_pr_number = match.group("pr_number")

    original_pr_data = await gh.getitem(
        event.data["repository"]["issues_url"], {"number": original_pr_number}
    )

    await _remove_backport_label(
        gh,
        original_pr_data=original_pr_data,
        branch=branch,
        backport_pr_number=event.data["number"],
    )
    await _copy_over_labels(
        gh, original_pr_data=original_pr_data, backport_pr_number=event.data["number"]
    )


async def _remove_backport_label(
    gh: gh_aiohttp.GitHubAPI,
    *,
    original_pr_data: dict[str, Any],
    branch: str,
    backport_pr_number: int,
) -> None:
    """
    Remove the appropriate "Needs Backport To" label on the original PR.

    Also leave a comment on the original PR referencing the backport PR.
    """
    backport_label = f"Needs Backport To {branch}"
    if not any(label_data["name"] == backport_label for label_data in original_pr_data["labels"]):
        return

    await gh.delete(original_pr_data["labels_url"], {"name": backport_label})
    message = (
        f"#{backport_pr_number} is a backport of this pull request to"
        f" [Red {branch}](https://github.com/{UPSTREAM_REPO}/tree/{branch}."
    )
    await gh.post(original_pr_data["comments_url"], data={"body": message})


async def _copy_over_labels(
    gh: gh_aiohttp.GitHubAPI, *, original_pr_data: dict[str, Any], backport_pr_number: int
):
    """Copy over relevant labels from the original PR to the backport PR."""
    labels = [
        label_data["name"]
        for label_data in original_pr_data["labels"]
        if label_data["name"].startswith(COPYABLE_LABELS_PREFIXES)
    ]
    if labels:
        labels_url = f"/repos/{UPSTREAM_REPO}/issues/{original_pr_data['number']}/labels"
        await gh.post(labels_url, data=labels)


@gh_router.register("pull_request", action="opened")
@gh_router.register("pull_request", action="reopened")
@gh_router.register("pull_request", action="edited")
@gh_router.register("pull_request", action="synchronize")
@gh_router.register("check_run", action="rerequested")
async def validate_maintenance_branch_pr(event: sansio.Event) -> None:
    """
    Check the PR title for maintenance branch pull requests.

    If the PR was made against maintenance branch, and the title does not
    match the maintenance branch PR pattern, then post a failure status.

    The maintenance branch PR has to start with `[X.Y]`
    """
    if event.event == "pull_request":
        if event.data["action"] == "edited" and "title" not in event.data["changes"]:
            return

        pr_data = event.data["pull_request"]
    else:
        check_run_data = event.data["check_run"]
        if check_run_data["name"] != CHECK_RUN_NAME:
            return

        pull_requests = check_run_data["pull_requests"]
        if len(pull_requests) > 1:
            # if this happens, I want this on Sentry
            log.error(
                "Check run with ID %s was rerequested but multiple PRs were found:\n%r",
                check_run_data["id"],
                pull_requests,
            )
            return
        elif pull_requests:
            pr_data = pull_requests[0]
        else:
            return

    base_branch = pr_data["base"]["ref"]

    if base_branch not in MAINTENANCE_BRANCHES:
        return

    installation_id = event.data["installation"]["id"]
    gh = await utils.get_gh_client(installation_id)

    head_sha = pr_data["head"]["sha"]
    title = utils.normalize_title(pr_data["title"], pr_data["body"])
    match = MAINTENANCE_BRANCH_TITLE_RE.match(title)
    original_pr_number = match and match.group("pr_number")

    if match is None:
        conclusion = utils.CheckRunConclusion.FAILURE
        title = f"[{base_branch}] {title}"
        output = utils.CheckRunOutput(
            title="PR title is not prefixed with the branch's name.",
            summary=(
                "Title of a PR made to a maintenance branch must be prefixed"
                f" with the branch's name, for example:\n```\n{title}\n```"
            ),
        )
    elif match.group("branch") != base_branch:
        conclusion = utils.CheckRunConclusion.FAILURE
        title = f"[{base_branch}] " + title.replace(f"[{match.group('branch')}] ", "", 1)
        output = utils.CheckRunOutput(
            title="PR title is prefixed with incorrect branch's name.",
            summary=(
                "Title of a PR made to a maintenance branch must be prefixed"
                f" with the branch's name, for example:\n```\n{title}\n```"
            ),
        )
    else:
        conclusion = utils.CheckRunConclusion.SUCCESS
        output = utils.CheckRunOutput(
            title="PR title is prefixed with maintenance branch's name.",
            summary="Title of a PR has a proper prefix.",
        )

    if original_pr_number is None:
        output.summary += (
            "\n\n"
            "Note: If this is a backport of a different PR,"
            " you should also include the original PR number, for example:\n"
            f"```\n{title} (#123)\n```"
        )
        if conclusion is utils.CheckRunConclusion.SUCCESS:
            conclusion = utils.CheckRunConclusion.NEUTRAL
            output.title = f"{output.title[:-1]}, but it does not include original PR number."

    await utils.post_check_run(
        gh, name=CHECK_RUN_NAME, head_sha=head_sha, conclusion=conclusion, output=output
    )
