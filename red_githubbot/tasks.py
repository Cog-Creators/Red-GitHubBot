import logging
import os
import subprocess

from apscheduler.schedulers.asyncio import AsyncIOScheduler

_log = logging.getLogger(__name__)

# consider adding kwarg:
#  job_defaults={"misfire_grace_time": None}
scheduler = AsyncIOScheduler()


async def on_startup() -> None:
    _prepare_red_git_repo()
    scheduler.add_jobstore("sqlalchemy", url=os.environ["DATABASE_URL"])
    scheduler.start()


def _prepare_git() -> None:
    subprocess.check_output(("git", "config", "--global", "user.name", "Red-GitHubBot"))
    subprocess.check_output(
        (
            "git",
            "config",
            "--global",
            "user.email",
            "87398303+red-githubbot[bot]@users.noreply.github.com",
        )
    )


def _prepare_red_git_repo() -> None:
    _log.info("Setting up Red-DiscordBot repository...")
    if "Red-DiscordBot" in os.listdir("."):
        os.chdir("./Red-DiscordBot")
        _log.info("Red-DiscordBot directory already exists.")
        return

    _prepare_git()
    subprocess.check_output(("git", "clone", "https://github.com/Red-GitHubBot/Red-DiscordBot"))
    subprocess.check_output(
        ("git", "remote", "add", "upstream", "https://github.com/Cog-Creators/Red-DiscordBot")
    )
    os.chdir("./Red-DiscordBot")
    _log.info("Finished setting up Red-DiscordBot repository.")
