import asyncio
import logging
import os
import re

import gidgethub
from aiohttp import web
from gidgethub import sansio

from . import discord_webhook, tasks, utils
from .constants import UPSTREAM_REPO
from .routers import gh_router

log = logging.getLogger(__name__)

routes = web.RouteTableDef()

_DISCORD_ID_RE = re.compile(r"^[0-9]{17,20}$")
_DISCORD_WEBHOOK_TOKEN_RE = re.compile(r"^[A-Za-z0-9\.\-\_]{60,}$")


@routes.get("/")
async def hello(_request: web.Request) -> web.Response:
    # maybe one day there will be some front-facing UI, you can never know...
    return web.Response(text="Hello, world")


@routes.post("/webhook")
async def webhook(request: web.Request) -> web.Response:
    try:
        body = await request.read()
        secret = os.environ["GH_WEBHOOK_SECRET"]
        try:
            event = sansio.Event.from_http(request.headers, body, secret=secret)
        except gidgethub.ValidationFailure as exc:
            log.info("GH webhook failed secret validation: %s", exc)
            return web.Response(status=401, text=str(exc))
        except gidgethub.BadRequest as exc:
            log.info("GH webhook received a bad request (%d): %s", exc.status_code, exc)
            return web.Response(status=exc.status_code.value, text=str(exc))

        log.info("GH delivery ID: %s", event.delivery_id)

        if event.event == "ping":
            return web.Response(status=200)

        # We don't want to handle events received from the bot's fork
        repo_full_name = event.data.get("repository", {}).get("full_name")
        if repo_full_name is not None and repo_full_name != UPSTREAM_REPO:
            return web.Response(status=200)

        # Give GitHub some time to reach internal consistency.
        await asyncio.sleep(1)
        await gh_router.dispatch(event)

        return web.Response(status=200)
    except Exception as exc:
        log.error("The app did not handle an exception", exc_info=exc)
        return web.Response(status=500)


@routes.post("/discord-webhook/{webhook_id}/{webhook_token}")
@routes.post("/discord-webhook/{webhook_id}/{webhook_token}/github")
async def discord_webhook_route(request: web.Request) -> web.Response:
    try:
        body = await request.read()
        secret = os.environ["GH_DISCORD_WEBHOOK_SECRET"]
        try:
            event = sansio.Event.from_http(request.headers, body, secret=secret)
        except gidgethub.ValidationFailure as exc:
            log.info("GH Discord webhook failed secret validation: %s", exc)
            return web.Response(status=401, text=str(exc))
        except gidgethub.BadRequest as exc:
            log.info("GH Discord webhook received a bad request (%d): %s", exc.status_code, exc)
            return web.Response(status=exc.status_code.value, text=str(exc))

        webhook_id = request.match_info["webhook_id"]
        if _DISCORD_ID_RE.match(webhook_id) is None:
            raise web.HTTPBadRequest(reason="invalid webhook_id value") from None
        webhook_token = request.match_info["webhook_token"]
        if _DISCORD_WEBHOOK_TOKEN_RE.match(webhook_token) is None:
            raise web.HTTPBadRequest(reason="invalid webhook_token value") from None
        log.info("Discord webhook ID: %s, GH delivery ID: %s", webhook_id, event.delivery_id)

        if event.event == "ping":
            return web.Response(status=200)

        thread_id: int | None = None
        raw_thread_id = request.query.get("thread_id")
        if raw_thread_id:
            if _DISCORD_ID_RE.match(raw_thread_id) is None:
                raise web.HTTPBadRequest(reason="invalid thread_id value") from None
            thread_id = int(raw_thread_id)

        return await discord_webhook.handle_event(
            event, webhook_id=webhook_id, webhook_token=webhook_token, thread_id=thread_id
        )
    except Exception as exc:
        log.error("The app did not handle an exception", exc_info=exc)
        return web.Response(status=500)


async def on_startup(app: web.Application) -> None:
    await utils.on_startup(app)
    await tasks.on_startup(app)


async def on_cleanup(app: web.Application) -> None:
    await utils.session.close()


# this is a pretty hacky way to ensure we don't log discord webhook tokens
# but I frankly see no better way to do this with aiohttp APIs
class SafeAccessLogger(web.AccessLogger):
    @staticmethod
    def _format_r(request: web.BaseRequest, _response: web.StreamResponse, _time: float) -> str:
        path = request.path_qs
        if path.startswith("/discord-webhook/"):
            parts = path.split("/")
            parts[3] = "<webhook_token>"
            path = "/".join(parts)
        return f"{request.method} {path} HTTP/{request.version.major}.{request.version.minor}"


app = web.Application()
app.add_routes(routes)
app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)
