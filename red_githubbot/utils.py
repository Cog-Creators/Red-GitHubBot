import asyncio
import os
from collections.abc import Callable, MutableMapping
from typing import Any, Union

import aiohttp
import cachetools
from apscheduler.job import Job
from gidgethub import aiohttp as gh_aiohttp, apps
from typing_extensions import ParamSpec

from .tasks import scheduler

git_lock = asyncio.Lock()
session = aiohttp.ClientSession()

_gh_cache: MutableMapping[Any, Any] = cachetools.LRUCache(maxsize=500)
public_gh = gh_aiohttp.GitHubAPI(session, "jack1142/Red-GitHubBot", cache=_gh_cache)

_gh_installation_tokens_cache = cachetools.TTLCache(maxsize=100, ttl=55 * 60)
_P = ParamSpec("P")


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


async def leave_comment(gh: gh_aiohttp.GitHubAPI, issue_number: int, body: str) -> None:
    issue_comment_url = f"/repos/Cog-Creators/Red-DiscordBot/issues/{issue_number}/comments"
    data = {"body": body}
    await gh.post(issue_comment_url, data=data)


def add_job(func: Callable[_P, Any], *args: _P.args, **kwargs: _P.kwargs) -> Job:
    return scheduler.add_job(func, args=args, kwargs=kwargs)
