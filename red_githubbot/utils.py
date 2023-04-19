from __future__ import annotations

import asyncio
import dataclasses
import datetime
import enum
import functools
import logging
import os
import subprocess
from collections.abc import Callable, Coroutine, Generator, Mapping, MutableMapping
from contextlib import AbstractAsyncContextManager, AbstractContextManager, asynccontextmanager
from typing import Any, ParamSpec, TypeVar

import aiohttp
import cachetools
import gidgethub
import mistune
import sentry_sdk
import sqlalchemy.exc
from aiohttp import web
from apscheduler.job import Job
from apscheduler.triggers.interval import IntervalTrigger
from gidgethub import aiohttp as gh_aiohttp, apps, sansio

from . import tasks
from .constants import MACHINE_USERNAME, REQUESTER, UPSTREAM_REPO

log = logging.getLogger(__name__)

SENTRY_DSN = os.environ.get("SENTRY_DSN")
DB_ERRORS = (sqlalchemy.exc.OperationalError,)
git_lock = asyncio.Lock()
session: aiohttp.ClientSession
machine_gh: GitHubAPI

_gh_cache: MutableMapping[Any, Any] = cachetools.LRUCache(maxsize=500)
_gh_installation_tokens_cache: MutableMapping[int, str] = cachetools.TTLCache(
    maxsize=100, ttl=55 * 60
)
gh_installation_id_cache: MutableMapping[str, int] = cachetools.LRUCache(100)

parse_markdown = mistune.create_markdown(
    renderer="ast", plugins=("strikethrough", "table", "task_lists")
)


async def on_startup(app: web.Application) -> None:
    global machine_gh, session
    session = aiohttp.ClientSession()
    machine_gh = GitHubAPI(
        f"{MACHINE_USERNAME} (Machine account)", oauth_token=os.environ.get("GH_AUTH")
    )


class GitHubAPI(gh_aiohttp.GitHubAPI):
    def __init__(self, client_name: str, *, oauth_token: str | None = None) -> None:
        """
        GitHub API client that logs current rate limit status after each request.

        This class requires the developer to pass a client name for inclusion of it in logs.
        """
        self.client_name = client_name
        super().__init__(session, REQUESTER, cache=_gh_cache, oauth_token=oauth_token)

    async def _make_request(
        self, method: str, *args: Any, **kwargs: Any
    ) -> tuple[bytes, str | None]:
        async with github_rate_limiter(should_sleep=method != "GET"):
            try:
                return await super()._make_request(method, *args, **kwargs)
            except gidgethub.RateLimitExceeded as exc:
                sleep_delta = exc.reset_datetime - datetime.datetime.now(datetime.timezone.utc)
                await self.sleep(sleep_delta.total_seconds())
                return await super()._make_request(method, *args, **kwargs)

    async def _request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes = b""
    ) -> tuple[int, Mapping[str, str], bytes]:
        ret = await super()._request(method, url, headers, body)
        rate_limit = sansio.RateLimit.from_http(headers)
        if rate_limit is None:
            log.info(
                "Processing GitHub API response...\n"
                "    - Client name: %s\n"
                "    - Request Method: %s\n"
                "    - Request URL: %s",
                self.client_name,
                method,
                url,
            )
        else:
            log.info(
                "Processing GitHub API response...\n"
                "    - Client Name: %s\n"
                "    - Request Method: %s\n"
                "    - Request URL: %s\n"
                "    - Rate Limit Points Remaining: %d/%d\n"
                "    - Rate Limit Resets At: %s",
                self.client_name,
                method,
                url,
                rate_limit.remaining,
                rate_limit.limit,
                rate_limit.reset_datetime,
            )
        return ret


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
    text: str | None = None
    # Output can also contain `annotations` and `images` but they can always be added in the future

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self, dict_factory=_noneless_dict_factory)


async def get_gh_client(
    installation_id: int | None = None, *, slug: str = UPSTREAM_REPO
) -> GitHubAPI:
    if installation_id is None:
        installation_id = await get_installation_id_by_repo(slug)
    return GitHubAPI(
        f"Installation {installation_id}",
        oauth_token=await get_installation_access_token(installation_id),
    )


async def get_installation_id_by_repo(slug: str, *, force_refresh: bool = False) -> int:
    owner = slug.split("/", maxsplit=1)[0].lower()

    if force_refresh:
        jwt = apps.get_jwt(
            app_id=os.environ["GH_APP_ID"], private_key=os.environ["GH_PRIVATE_KEY"]
        )
        installation_data = await machine_gh.getitem(f"/repos/{slug}/installation", jwt=jwt)
        installation_id = installation_data["id"]
        gh_installation_id_cache[owner] = installation_id
        return installation_id

    try:
        return gh_installation_id_cache[owner]
    except KeyError:
        return await get_installation_id_by_repo(slug, force_refresh=True)


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


async def leave_comment(gh: GitHubAPI, issue_number: int, body: str) -> None:
    issue_comment_url = f"/repos/{UPSTREAM_REPO}/issues/{issue_number}/comments"
    data = {"body": body}
    await gh.post(issue_comment_url, data=data)


async def post_check_run(
    gh: GitHubAPI,
    *,
    name: str,
    head_sha: str,
    status: CheckRunStatus | None = None,
    conclusion: CheckRunConclusion | None = None,
    details_url: str | None = None,
    output: CheckRunOutput | None = None,
) -> int:
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

    return (await gh.post(check_run_url, data=data))["id"]


async def patch_check_run(
    gh: GitHubAPI,
    *,
    check_run_id: int,
    status: CheckRunStatus | None = None,
    conclusion: CheckRunConclusion | None = None,
    details_url: str | None = None,
    output: CheckRunOutput | None = None,
) -> None:
    check_run_updates_url = f"/repos/{UPSTREAM_REPO}/check-runs/{check_run_id}"
    data = {}
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

    await gh.patch(check_run_updates_url, data=data)


async def get_open_pr_for_commit(
    gh: GitHubAPI, sha: str, *, get_pr_data: bool = False
) -> dict[str, Any] | None:
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


async def get_pr_data_for_check_run(
    gh: GitHubAPI,
    *,
    event: sansio.Event,
    check_run_name: str,
    get_pr_data: bool = False,
) -> tuple[dict[str, Any] | None, str]:
    if event.event == "pull_request":
        pr_data = event.data["pull_request"]
        head_sha = pr_data["head"]["sha"]
    else:
        check_run_data = event.data["check_run"]
        head_sha = check_run_data["head_sha"]
        if check_run_data["name"] != check_run_name:
            return None, head_sha

        pull_requests = check_run_data["pull_requests"]
        if len(pull_requests) > 1:
            # if this happens, I want this on Sentry
            log.error(
                "Check run with ID %s was rerequested but multiple PRs were found:\n%r",
                check_run_data["id"],
                pull_requests,
            )
            return None, head_sha
        elif pull_requests:
            pr_data = pull_requests[0]
        else:
            pr_data = await get_open_pr_for_commit(gh, head_sha, get_pr_data=get_pr_data)
            if pr_data is None:
                log.error(
                    "Could not find an open PR for the rerequested check run with ID %s",
                    check_run_data["id"],
                )
                return None, head_sha

    return pr_data, head_sha


async def copy_over_labels(
    gh: GitHubAPI,
    *,
    source_issue_data: dict[str, Any],
    target_issue_number: int,
    copyable_labels_prefixes: tuple[str] = (
        "Type: ",
        "Release Blocker",
        "High Priority",
        "Breaking Change",
    ),
) -> None:
    """Copy over relevant labels from one issue/PR to another."""
    labels = [
        label_data["name"]
        for label_data in source_issue_data["labels"]
        if label_data["name"].startswith(copyable_labels_prefixes)
    ]
    if labels:
        labels_url = f"/repos/{UPSTREAM_REPO}/issues/{target_issue_number}/labels"
        await gh.post(labels_url, data=labels)


def minify_graphql_call(call: str) -> str:
    """
    Minify GraphQL call.

    Right now this just strips leading whitespace from all lines
    which is enough to reduce size by ~50%.
    """
    return "\n".join(line.lstrip() for line in call.strip().splitlines())


def normalize_title(title: str, body: str) -> str:
    """Normalize the title if it spills over into the PR's body."""
    if not (title.endswith("…") and body.startswith("…")):
        return title
    else:
        return title[:-1] + body[1:].partition("\n")[0].rstrip("\r")


_P = ParamSpec("_P")


def add_job(func: Callable[_P, Any], *args: _P.args, **kwargs: _P.kwargs) -> Job:
    return tasks.scheduler.add_job(func, args=args, kwargs=kwargs)


def run_job_in(seconds: int, func: Callable[_P, Any], *args: _P.args, **kwargs: _P.kwargs) -> Job:
    td = datetime.timedelta(seconds=seconds)
    return tasks.scheduler.add_job(
        func, "date", run_date=datetime.datetime.now() + td, args=args, kwargs=kwargs
    )


_NoArgsCallableT = TypeVar("_NoArgsCallableT", bound=Callable[[], Any])


def interval_job(
    job_id: str | None = None,
    *,
    weeks: int = 0,
    days: int = 0,
    hours: int = 0,
    minutes: int = 0,
    seconds: int = 0,
) -> Callable[[_NoArgsCallableT], Any]:
    def decorator(func: _NoArgsCallableT) -> _NoArgsCallableT:
        nonlocal job_id
        if job_id is None:
            module_name = getattr(func, "__module__", None)
            job_id = func.__name__
            if module_name is not None:
                job_id = f"{module_name}.{job_id}"

        tasks.scheduler.add_job(
            func,
            IntervalTrigger(
                weeks=weeks,
                days=days,
                hours=hours,
                minutes=minutes,
                seconds=seconds,
            ),
            id=job_id,
            jobstore="memory",
            replace_existing=True,
        )
        return func

    return decorator


async def call(program: str, *args: str) -> int:
    process = await asyncio.create_subprocess_exec(program, *args)
    return await process.wait()


async def check_call(program: str, *args: str) -> None:
    process = await asyncio.create_subprocess_exec(program, *args)
    await process.wait()
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, (program, *args))


async def check_output(program: str, *args: str) -> str:
    process = await asyncio.create_subprocess_exec(program, *args, stdout=asyncio.subprocess.PIPE)
    stdout_data, stderr_data = await process.communicate()
    stdout = stdout_data.decode().strip()
    stderr = stderr_data.decode().strip() if stderr_data is not None else None
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, (program, *args), stdout, stderr)
    return stdout


_T = TypeVar("_T")


def async_with_context(
    context_manager: AbstractAsyncContextManager,
) -> Callable[[Callable[_P, Coroutine[Any, Any, _T]]], Callable[_P, Coroutine[Any, Any, _T]]]:
    def deco(func: Callable[_P, Coroutine[Any, Any, _T]]) -> Callable[_P, Coroutine[Any, Any, _T]]:
        @functools.wraps(func)
        async def inner(*args: _P.args, **kwargs: _P.kwargs) -> _T:
            async with context_manager:
                return await func(*args, **kwargs)

        return inner

    return deco


def with_context(
    context_manager: AbstractContextManager,
) -> Callable[[Callable[_P, _T]], Callable[_P, _T]]:
    def deco(func: Callable[_P, _T]) -> Callable[_P, _T]:
        @functools.wraps(func)
        def inner(*args: _P.args, **kwargs: _P.kwargs) -> _T:
            with context_manager:
                return func(*args, **kwargs)

        return inner

    return deco


_gh_lock = asyncio.Lock()
# allows re-entrance without sleep
_gh_lock_owner = None
# whether a sleep was requested by current owner
_gh_lock_should_sleep = False


@asynccontextmanager
async def github_rate_limiter(*, should_sleep: bool = True) -> Generator[None, None, None]:
    global _gh_lock_owner, _gh_lock_should_sleep
    current_task = asyncio.current_task()
    if _gh_lock_owner is current_task:
        _gh_lock_should_sleep |= should_sleep
        yield
        return

    await _gh_lock.acquire()
    try:
        _gh_lock_should_sleep |= should_sleep
        _gh_lock_owner = current_task
        yield
    finally:
        _gh_lock_owner = None
        should_sleep = _gh_lock_should_sleep
        _gh_lock_should_sleep = False
        if should_sleep:
            asyncio.get_running_loop().call_later(1, _gh_lock.release)
        else:
            _gh_lock.release()


def capture_exception(exc: BaseException) -> None:
    if SENTRY_DSN:
        sentry_sdk.capture_exception(exc)
