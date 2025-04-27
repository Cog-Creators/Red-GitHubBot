from gidgethub import sansio

from .. import utils
from . import gh_router

CHECK_RUN_NAME = "Blocked status"


@gh_router.register("pull_request", action="opened")
@gh_router.register("pull_request", action="reopened")
@gh_router.register("pull_request", action="synchronize")
@gh_router.register("pull_request", action="labeled")
@gh_router.register("pull_request", action="unlabeled")
@gh_router.register("check_run", action="rerequested")
@gh_router.register("merge_group", action="check_requested")
async def check_for_blocked_labels(event: sansio.Event) -> None:
    installation_id = event.data["installation"]["id"]
    gh = await utils.get_gh_client(installation_id)
    pr_data, head_sha = await utils.get_pr_data_for_check_run(
        gh, event=event, check_run_name=CHECK_RUN_NAME
    )
    if pr_data is None or pr_data["merged"]:
        return

    blocked_labels = [
        label_data
        for label_data in pr_data["labels"]
        if label_data["name"] == "Blocked" or label_data["name"].startswith("Blocked By: ")
    ]
    if blocked_labels:
        conclusion = utils.CheckRunConclusion.FAILURE
        summary = "The PR is labeled with these Blocked labels:\n" + "\n".join(
            f"- {label_data['name']} - {label_data['description'] or 'No description'}"
            for label_data in blocked_labels
        )
        output = utils.CheckRunOutput(
            title=(
                "PR is blocked by something, see labels and PR description for more information."
            ),
            summary=summary,
        )
    else:
        conclusion = utils.CheckRunConclusion.SUCCESS
        output = utils.CheckRunOutput(
            title="PR is not blocked by anything.",
            summary="The PR is not labeled with any Blocked labels.",
        )

    await utils.post_check_run(
        gh, name=CHECK_RUN_NAME, head_sha=head_sha, conclusion=conclusion, output=output
    )
