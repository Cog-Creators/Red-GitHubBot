import logging

from gidgethub import sansio

from .. import utils
from . import gh_router

log = logging.getLogger(__name__)


@gh_router.register("installation", action="created")
@gh_router.register("installation", action="deleted")
async def update_installation_id_cache(event: sansio.Event) -> None:
    installation_data = event.data["installation"]
    login = installation_data["account"]["login"].lower()
    if event.data["action"] == "created":
        utils.gh_installation_id_cache[login] = installation_data["id"]
    else:
        utils.gh_installation_id_cache.pop(login, None)
