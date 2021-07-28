import asyncio
import logging
import os
import subprocess

from cherry_picker import cherry_picker
from gidgethub import aiohttp as gh_aiohttp, sansio

from .. import utils
from ..constants import UPSTREAM_REPO
from . import gh_router

log = logging.getLogger(__name__)

CHERRY_PICKER_CONFIG = {
    "team": "jack1142",
    "repo": "Red-DiscordBot",
    "check_sha": "6251c585e4ec0a53813a9993ede3ab5309024579",
    "fix_commit_msg": False,
    "default_branch": "V3/develop",
}
SUPPORTED_BRANCHES = {"3.4"}


@gh_router.register("pull_request", action="closed")
@gh_router.register("pull_request", action="labeled")
async def backport_pr(event: sansio.Event) -> None:
    if not event.data["pull_request"]["merged"]:
        return

    installation_id = event.data["installation"]["id"]
    gh = await utils.get_gh_client(installation_id)

    pr_number = event.data["pull_request"]["number"]
    sender = event.data["sender"]
    created_by = event.data["pull_request"]["user"]["login"]

    commit_hash = event.data["pull_request"]["merge_commit_sha"]

    pr_labels = []
    if event.data["action"] == "labeled":
        pr_labels = [event.data["label"]]
    else:
        gh_issue = await gh.getitem(
            event.data["repository"]["issues_url"],
            {"number": f"{event.data['pull_request']['number']}"},
        )
        pr_labels = await gh.getitem(gh_issue["labels_url"])

    unsupported_branches = []
    branches = []
    for label in pr_labels:
        if label["name"].startswith("Needs Backport To"):
            branch = label["name"].rsplit(maxsplit=1)[1]
            if branch not in SUPPORTED_BRANCHES:
                unsupported_branches.append(branch)
                continue
            branches.append(branch)

    if unsupported_branches:
        log.warning(
            "Seen a Needs Backport label with unsupported branches (%s)",
            ", ".join(unsupported_branches),
        )
        await utils.leave_comment(
            gh,
            pr_number,
            f"Sorry @{sender}, {'some of' if branches else ''} the branches you want to"
            f" backport to ({', '.join(unsupported_branches)}) seem to be unsupported."
            " Please consider reporting this to Red-GitHubBot's issue tracker"
            " and backport using [cherry_picker](https://pypi.org/project/cherry-picker/)"
            " on command line."
            "```\n"
            f"cherry_picker {commit_hash} {branch}\n"
            "```",
        )

    if branches:
        message = (
            f"Thanks @{created_by} for the PR \N{PARTY POPPER}."
            f" I'm working now to backport this PR to: {', '.join(branches)}."
        )

        await utils.leave_comment(gh, pr_number, message)

        sorted_branches = sorted(
            branches, reverse=True, key=lambda v: tuple(map(int, v.split(".")))
        )

        for branch in sorted_branches:
            utils.add_job(
                backport_task,
                installation_id=installation_id,
                commit_hash=commit_hash,
                branch=branch,
                pr_number=pr_number,
                sender=sender,
            )


async def backport_task(
    *, installation_id: int, commit_hash: str, branch: str, pr_number: int, sender: str
):
    async with utils.git_lock:
        gh = await utils.get_gh_client(installation_id)
        forker_gh = await utils.get_forker_gh_client()

        try:
            await asyncio.to_thread(
                backport, gh=gh, forker_gh=forker_gh, commit_hash=commit_hash, branch=branch
            )
        except cherry_picker.BranchCheckoutException:
            await utils.leave_comment(
                gh,
                pr_number,
                f"Sorry @{sender}, I had trouble checking out the `{branch}` backport branch."
                " Please backport using [cherry_picker](https://pypi.org/project/cherry-picker/)"
                " on command line."
                "```\n"
                f"cherry_picker {commit_hash} {branch}\n"
                "```",
            )
        except cherry_picker.CherryPickException:
            await utils.leave_comment(
                gh,
                pr_number,
                f"Sorry, @{sender}, I could not cleanly backport this to `{branch}`"
                " due to a conflict."
                " Please backport using [cherry_picker](https://pypi.org/project/cherry-picker/)"
                " on command line.\n"
                "```"
                f"cherry_picker {commit_hash} {branch}\n"
                "```",
            )


def backport(
    *, gh: gh_aiohttp.GitHubAPI, forker_gh: gh_aiohttp.GitHubAPI, commit_hash: str, branch: str
) -> None:
    subprocess.check_output(
        (
            "git",
            "remote",
            "set-url",
            "origin",
            f"https://x-access-token:{forker_gh.oauth_token}@github.com/{UPSTREAM_REPO}.git",
        )
    )
    os.environ["GH_AUTH"] = gh.oauth_token

    try:
        cp = cherry_picker.CherryPicker(
            pr_remote="origin",
            commit_sha1=commit_hash,
            branches=[branch],
            config=CHERRY_PICKER_CONFIG,
        )
        try:
            cp.backport()
        except Exception:
            cp.abort_cherry_pick()
            raise
    finally:
        del os.environ["GH_AUTH"]
