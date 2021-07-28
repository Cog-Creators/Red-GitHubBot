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

    # https://help.heroku.com/ZKNTJQSK
    database_url = os.environ["DATABASE_URL"]
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    scheduler.add_jobstore("sqlalchemy", url=database_url)
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
    os.chdir(f"./{REPO_NAME}")
    subprocess.check_output(
        ("git", "remote", "add", "upstream", f"https://github.com/{UPSTREAM_REPO}")
    )
    _log.info("Finished setting up %s repository.", REPO_NAME)
