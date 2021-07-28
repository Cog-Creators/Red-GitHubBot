import os
from collections.abc import MutableMapping
from typing import Any, Union

import aiohttp
import cachetools
from gidgethub import aiohttp as gh_aiohttp, apps

session = aiohttp.ClientSession()

_gh_cache: MutableMapping[Any, Any] = cachetools.LRUCache(maxsize=500)
public_gh = gh_aiohttp.GitHubAPI(session, "jack1142/Red-GitHubBot", cache=_gh_cache)

_gh_installation_tokens_cache = cachetools.TTLCache(maxsize=100, ttl=55 * 60)


async def get_gh_client(installation_id: Union[int, str]) -> gh_aiohttp.GitHubAPI:
    return gh_aiohttp.GitHubAPI(
        session,
        requester="jack1142/Red-GitHubBot",
        oauth_token=await get_installation_access_token(installation_id),
        cache=_gh_cache,
    )


async def get_forker_gh_client() -> gh_aiohttp.GitHubAPI:
    return await get_gh_client(os.environ["GH_FORKER_INSTALLATION_ID"])


async def get_installation_access_token(
    installation_id: Union[int, str], *, force_refresh: bool = False
) -> str:
    if force_refresh:
        token_data = await apps.get_installation_access_token(
            public_gh,
            installation_id=str(installation_id),
            app_id=os.environ["GH_APP_ID"],
            private_key=os.environ["GH_PRIVATE_KEY"],
        )
        token = token_data["token"]
        _gh_installation_tokens_cache[installation_id] = token
        return token

    try:
        return _gh_installation_tokens_cache[installation_id]
    except KeyError:
        return await get_installation_access_token(installation_id, force_refresh=True)
