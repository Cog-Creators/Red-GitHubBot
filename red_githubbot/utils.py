import asyncio
import dataclasses
import datetime
import enum
import os
from collections.abc import Callable, MutableMapping
from typing import Any, Optional, Union

import aiohttp
import cachetools
from apscheduler.job import Job
from gidgethub import aiohttp as gh_aiohttp, apps
from typing_extensions import ParamSpec

from . import tasks
from .constants import REQUESTER, UPSTREAM_REPO

git_lock = asyncio.Lock()
session = aiohttp.ClientSession()

_gh_cache: MutableMapping[Any, Any] = cachetools.LRUCache(maxsize=500)
machine_gh = gh_aiohttp.GitHubAPI(
    session, REQUESTER, cache=_gh_cache, oauth_token=os.environ.get("GH_AUTH")
)

_gh_installation_tokens_cache = cachetools.TTLCache(maxsize=100, ttl=55 * 60)
_P = ParamSpec("P")


class CheckRunStatus(enum.Enum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class CheckRunConclusion(enum.Enum):
    ACTION_REQUIRED = "action_required"
    CANCELLED = "cancelled"
    FAILURE = "failure"
    NEUTRAL = "neutral"
    SUCCESS = "success"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"


@dataclasses.dataclass
class CheckRunOutput:
    title: str
    summary: str
    text: Optional[str] = None
    # Output can also contain `annotations` and `images` but they can always be added in the future

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


async def get_gh_client(installation_id: Union[int, str]) -> gh_aiohttp.GitHubAPI:
    return gh_aiohttp.GitHubAPI(
        session,
        requester=REQUESTER,
        oauth_token=await get_installation_access_token(installation_id),
        cache=_gh_cache,
    )


async def get_installation_access_token(
    installation_id: Union[int, str], *, force_refresh: bool = False
) -> str:
    if force_refresh:
        token_data = await apps.get_installation_access_token(
            machine_gh,
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
    issue_comment_url = f"/repos/{UPSTREAM_REPO}/issues/{issue_number}/comments"
    data = {"body": body}
    await gh.post(issue_comment_url, data=data)


async def post_check_run(
    gh: gh_aiohttp.GitHubAPI,
    *,
    name: str,
    head_sha: str,
    status: Optional[CheckRunStatus] = None,
    conclusion: Optional[CheckRunConclusion] = None,
    details_url: Optional[str] = None,
    output: Optional[CheckRunOutput] = None,
) -> None:
    check_run_url = f"/repos/{UPSTREAM_REPO}/check-runs"
    data = {"name": name, "head_sha": head_sha}
    if status is not None:
        if conclusion is not None and status is not CheckRunStatus.COMPLETED:
            raise RuntimeError("`status` needs to be `COMPLETED` when `conclusion` is provided.")
        data["status"] = status.value
    if conclusion is not None:
        data["conclusion"] = conclusion.value
    if details_url is not None:
        data["details_url"] = details_url
    if output is not None:
        data["output"] = output.to_dict()

    await gh.post(check_run_url)


def normalize_title(title: str, body: str) -> str:
    """Normalize the title if it spills over into the PR's body."""
    if not (title.endswith("…") and body.startswith("…")):
        return title
    else:
        return title[:-1] + body[1:].partition("\n")[0].rstrip("\r")


def add_job(func: Callable[_P, Any], *args: _P.args, **kwargs: _P.kwargs) -> Job:
    return tasks.scheduler.add_job(func, args=args, kwargs=kwargs)


def run_job_in(seconds: int, func: Callable[_P, Any], *args: _P.args, **kwargs: _P.kwargs) -> Job:
    td = datetime.timedelta(seconds=seconds)
    return tasks.scheduler.add_job(
        func, "date", run_date=datetime.datetime.now() + td, args=args, kwargs=kwargs
    )
