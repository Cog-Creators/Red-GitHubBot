import asyncio
import dataclasses
import datetime
import enum
import logging
import os
from collections.abc import Callable, MutableMapping
from typing import Any, Optional

import aiohttp
import cachetools
from apscheduler.job import Job
from gidgethub import aiohttp as gh_aiohttp, apps
from typing_extensions import ParamSpec

from . import tasks
from .constants import REQUESTER, UPSTREAM_REPO

log = logging.getLogger(__name__)

git_lock = asyncio.Lock()
session = aiohttp.ClientSession()

_gh_cache: MutableMapping[Any, Any] = cachetools.LRUCache(maxsize=500)
machine_gh = gh_aiohttp.GitHubAPI(
    session, REQUESTER, cache=_gh_cache, oauth_token=os.environ.get("GH_AUTH")
)

_gh_installation_tokens_cache: MutableMapping[int, str] = cachetools.TTLCache(
    maxsize=100, ttl=55 * 60
)
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


def _noneless_dict_factory(result: list[tuple[str, Any]]) -> dict[str, Any]:
    return dict((key, value) for key, value in result if value is not None)


@dataclasses.dataclass
class CheckRunOutput:
    title: str
    summary: str
    text: Optional[str] = None
    # Output can also contain `annotations` and `images` but they can always be added in the future

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self, dict_factory=_noneless_dict_factory)


async def get_gh_client(installation_id: int) -> gh_aiohttp.GitHubAPI:
    return gh_aiohttp.GitHubAPI(
        session,
        requester=REQUESTER,
        oauth_token=await get_installation_access_token(installation_id),
        cache=_gh_cache,
    )


async def get_installation_access_token(
    installation_id: int, *, force_refresh: bool = False
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
    data: dict[str, Any] = {"name": name, "head_sha": head_sha}
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

    await gh.post(check_run_url, data=data)


async def get_open_pr_for_commit(
    gh: gh_aiohttp.GitHubAPI, sha: str, *, get_pr_data: bool = False
) -> Optional[dict[str, Any]]:
    """
    Get the most recently updated open PR associated with the given commit.

    This is needed for `check_run` hooks because GitHub does not provide associated PR
    when the PR is made from a fork.

    Note: This is like getting a PR from the issues endpoint
    so some PR attributes might be missing.
    To get full PR data, you need to set the `get_pr_data` kwarg to `True`.
    """
    search_results = await gh.getitem(
        "/search/issues{?q,sort}",
        {"q": f"type:pr repo:{UPSTREAM_REPO} sha:{sha} is:open", "sort": "updated"},
    )
    if search_results["total_count"] > 0:
        if search_results["total_count"] > 1:
            log.warning(
                "Found more than one possible candidate when searching for an open PR"
                " associated with the commit `%s`. Choosing the most recently updated one...",
                sha,
            )
        issue_data = search_results["items"][0]
        if get_pr_data:
            return await gh.getitem(issue_data["pull_request"]["url"])
        return issue_data

    return None


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
