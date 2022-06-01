import asyncio
import logging

from cherry_picker import cherry_picker
from gidgethub import sansio

from .. import utils
from ..constants import MAINTENANCE_BRANCHES, UPSTREAM_REPO, UPSTREAM_USERNAME
from . import gh_router

log = logging.getLogger(__name__)

CHERRY_PICKER_CONFIG = {
    "team": UPSTREAM_USERNAME,
    "repo": "Red-DiscordBot",
    "check_sha": "6251c585e4ec0a53813a9993ede3ab5309024579",
    "fix_commit_msg": False,
    "default_branch": "V3/develop",
}


@gh_router.register("pull_request", action="closed")
@gh_router.register("pull_request", action="labeled")
async def backport_pr(event: sansio.Event) -> None:
    if not event.data["pull_request"]["merged"]:
        return

    installation_id = event.data["installation"]["id"]
    gh = await utils.get_gh_client(installation_id)

    pr_number = event.data["pull_request"]["number"]
    sender = event.data["sender"]["login"]

    head_sha = event.data["pull_request"]["head"]["sha"]
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
            if branch not in MAINTENANCE_BRANCHES:
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
            f"Sorry @{sender}, {'some of' if branches else ''} the branches you want to backport"
            f" to ({', '.join(unsupported_branches)}) seem to not be maintenance branches."
            " Please consider reporting this to Red-GitHubBot's issue tracker"
            " and backport using [cherry_picker](https://pypi.org/project/cherry-picker/)"
            " on command line.\n"
            "```\n"
            f"cherry_picker {commit_hash} <branches...>\n"
            "```",
        )

    if branches:
        check_run_id = await utils.post_check_run(
            gh,
            name=f"Backport to {branch}",
            head_sha=head_sha,
            status=utils.CheckRunStatus.IN_PROGRESS,
        )

        sorted_branches = sorted(
            branches, reverse=True, key=lambda v: tuple(map(int, v.split(".")))
        )

        for branch in sorted_branches:
            try:
                utils.add_job(
                    backport_task,
                    installation_id=installation_id,
                    commit_hash=commit_hash,
                    branch=branch,
                    pr_number=pr_number,
                    sender=sender,
                    check_run_id=check_run_id,
                )
            except utils.DB_ERRORS as exc:
                await utils.leave_comment(
                    gh,
                    pr_number,
                    f"I'm having trouble backporting to `{branch}`.\n"
                    f"Reason '`{exc}`'.\n"
                    f"Please retry by removing and re-adding the"
                    f" `Needs Backport To {branch}` label.",
                )


async def backport_task(
    *,
    installation_id: int,
    commit_hash: str,
    branch: str,
    pr_number: int,
    sender: str,
    check_run_id: int,
) -> None:
    async with utils.git_lock:
        gh = await utils.get_gh_client(installation_id)
        details_url = None
        try:
            cp = await asyncio.to_thread(backport, commit_hash=commit_hash, branch=branch)
        except cherry_picker.BranchCheckoutException:
            summary = (
                f"Sorry @{sender}, I had trouble checking out the `{branch}` backport branch."
                " Please backport using [cherry_picker](https://pypi.org/project/cherry-picker/)"
                " on command line.\n"
                "```\n"
                f"cherry_picker {commit_hash} {branch}\n"
                "```"
            )
            conclusion = utils.CheckRunConclusion.FAILURE
            output = utils.CheckRunOutput(
                title="Failed to backport due to checkout failure.", summary=summary
            )
            await utils.leave_comment(gh, pr_number, summary)
        except cherry_picker.CherryPickException:
            summary = (
                f"Sorry, @{sender}, I could not cleanly backport this to `{branch}`"
                " due to a conflict."
                " Please backport using [cherry_picker](https://pypi.org/project/cherry-picker/)"
                " on command line.\n"
                "```\n"
                f"cherry_picker {commit_hash} {branch}\n"
                "```"
            )
            conclusion = utils.CheckRunConclusion.FAILURE
            output = utils.CheckRunOutput(
                title="Failed to backport due to a conflict.", summary=summary
            )
            await utils.leave_comment(gh, pr_number, summary)
        except Exception:
            summary = (
                f"Sorry, @{sender}, I'm having trouble backporting this to `{branch}`.\n"
                f"Please retry by removing and re-adding the **Needs Backport To {branch}** label."
                "If this issue persist, please report this to Red-GitHubBot's issue tracker"
                " and backport using [cherry_picker](https://pypi.org/project/cherry-picker/)"
                " on command line.\n"
                "```\n"
                f"cherry_picker {commit_hash} {branch}\n"
                "```",
            )
            conclusion = utils.CheckRunConclusion.FAILURE
            output = utils.CheckRunOutput(
                title="Failed to backport due to an unexpected error.", summary=summary
            )
            await utils.leave_comment(gh, pr_number, summary)
            await utils.patch_check_run(
                gh,
                check_run_id=check_run_id,
                conclusion=utils.CheckRunConclusion.FAILURE,
                output=output,
            )
            raise
        else:
            conclusion = utils.CheckRunConclusion.SUCCESS
            output = utils.CheckRunOutput(
                title=f"Backport PR (#{cp.pr_number}) created.",
                summary=(
                    f"#{cp.pr_number} is a backport of this pull request to"
                    f" [Red {branch}](https://github.com/{UPSTREAM_REPO}/tree/{branch})."
                ),
            )
            details_url = f"https://github.com/{UPSTREAM_REPO}/pull/{cp.pr_number}"

    await utils.patch_check_run(
        gh,
        check_run_id=check_run_id,
        conclusion=conclusion,
        output=output,
        details_url=details_url,
    )


def backport(*, commit_hash: str, branch: str) -> cherry_picker.CherryPicker:
    cp = _get_cherry_picker(commit_hash=commit_hash, branch=branch)
    try:
        cp.backport()
    except cherry_picker.BranchCheckoutException:
        # We need to set the state to BACKPORT_PAUSED so that CherryPicker allows us to
        # abort it, switch back to the default branch, and clean up the backport branch.
        #
        # Ideally, we would be able to do it in a less-hacky way but that will require some changes
        # in the upstream, so for now this is probably the best we can do here.
        cp.initial_state = cherry_picker.WORKFLOW_STATES.BACKPORT_PAUSED
        cp.abort_cherry_pick()
        raise
    except cherry_picker.CherryPickException:
        # We need to get a new CherryPicker here to get an up-to-date (PAUSED) state.
        cp = _get_cherry_picker(commit_hash=commit_hash, branch=branch)
        cp.abort_cherry_pick()
        raise
    return cp


def _get_cherry_picker(*, commit_hash: str, branch: str) -> cherry_picker.CherryPicker:
    return cherry_picker.CherryPicker(
        pr_remote="origin",
        commit_sha1=commit_hash,
        branches=[branch],
        config=CHERRY_PICKER_CONFIG,
    )
