import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# consider adding kwarg:
#  job_defaults={"misfire_grace_time": None}
scheduler = AsyncIOScheduler()


async def on_startup() -> None:
    scheduler.add_jobstore("sqlalchemy", url=os.environ["DATABASE_URL"])
    scheduler.start()
