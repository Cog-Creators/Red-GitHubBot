from gidgethub import sansio

from .. import utils
from . import gh_router


@gh_router.register("pull_request", action="closed")
async def auto_apply_changelog_entry_pending(event: sansio.Event) -> None:
    pr_data = event.data["pull_request"]
    if not pr_data["merged"] or pr_data["base"]["ref"] != "V3/develop":
        return

    for label in pr_data["labels"]:
        if label["name"].startswith("Changelog Entry: "):
            return

    installation_id = event.data["installation"]["id"]
    gh = await utils.get_gh_client(installation_id)

    await gh.post(f"{pr_data['issue_url']}/labels", data=["Changelog Entry: Pending"])
