import contextlib

import gidgethub
from gidgethub import sansio

from .. import utils
from ..constants import FORK_REPO, MACHINE_USERNAME
from . import gh_router


@gh_router.register("pull_request", action="closed")
async def auto_delete_pr_branch(event: sansio.Event) -> None:
    pr_data = event.data["pull_request"]
    if event.data["pull_request"]["user"]["login"] == MACHINE_USERNAME:
        branch_name = pr_data["head"]["ref"]
        branch_url = f"/repos/{FORK_REPO}/git/refs/heads/{branch_name}"
        if pr_data["merged"]:
            with contextlib.suppress(gidgethub.InvalidField):
                await utils.machine_gh.delete(branch_url)
        else:
            # this is delayed to ensure that the bot doesn't remove the branch
            # if PR was closed and reopened to rerun checks (or similar)
            utils.run_job_in(
                60, maybe_delete_pr_branch, pr_url=pr_data["url"], branch_url=branch_url
            )


async def maybe_delete_pr_branch(*, pr_url: str, branch_url: str) -> None:
    pr_data = await utils.machine_gh.getitem(pr_url)
    if pr_data["state"] == "closed":
        with contextlib.suppress(gidgethub.InvalidField):
            await utils.machine_gh.delete(branch_url)
