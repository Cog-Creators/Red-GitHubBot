import logging
import os
import subprocess

from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .constants import FORK_REPO, GIT_EMAIL, GIT_NAME, REPO_NAME, UPSTREAM_REPO

_log = logging.getLogger(__name__)

# consider adding kwarg:
#  job_defaults={"misfire_grace_time": None}
scheduler = AsyncIOScheduler()


async def on_startup(app: web.Application) -> None:
    _prepare_red_git_repo()
    scheduler.add_jobstore("sqlalchemy", url=os.environ["DATABASE_URL"])
    scheduler.start()


def _prepare_git() -> None:
    subprocess.check_output(("git", "config", "--global", "user.name", GIT_NAME))
    subprocess.check_output(("git", "config", "--global", "user.email", GIT_EMAIL))


def _prepare_red_git_repo() -> None:
    _log.info(f"Setting up {REPO_NAME} repository...")
    if REPO_NAME in os.listdir("."):
        os.chdir(f"./{REPO_NAME}")
        _log.info("%s directory already exists.", REPO_NAME)
        return

    _prepare_git()
    subprocess.check_output(("git", "clone", f"https://github.com/{FORK_REPO}"))
    subprocess.check_output(
        ("git", "remote", "add", "upstream", f"https://github.com/{UPSTREAM_REPO}")
    )
    os.chdir(f"./{REPO_NAME}")
    _log.info("Finished setting up %s repository.", REPO_NAME)
