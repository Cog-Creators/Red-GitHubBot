import asyncio
import logging
import os

import aiohttp
import cachetools
import gidgethub
from aiohttp import web
from gidgethub import aiohttp as gh_aiohttp, routing, sansio

log = logging.getLogger("red_githubbot")

routes = web.RouteTableDef()

gh_cache = cachetools.LRUCache(maxsize=500)
gh_router = routing.Router()


@routes.get("/")
async def hello(_request: web.Request) -> web.Response:
    # maybe one day there will be some front-facing UI, you can never know...
    return web.Response(text="Hello, world")


@routes.post("/webhook")
async def webhook(request: web.Request) -> web.Response:
    try:
        body = await request.read()
        secret = os.environ.get("GH_WEBHOOK_SECRET")
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

        async with aiohttp.ClientSession() as session:
            gh = gh_aiohttp.GitHubAPI(session, "jack1142/Red-GitHubBot", cache=gh_cache)
            # Give GitHub some time to reach internal consistency.
            await asyncio.sleep(1)
            await gh_router.dispatch(event, gh, session=session)

        try:
            log.info("GH requests remaining: %s", gh.rate_limit.remaining)
        except AttributeError:
            pass

        return web.Response(status=200)
    except Exception as exc:
        log.error("The app did not handle an exception", exc_info=exc)
        return web.Response(status=500)


def main() -> None:
    if _sentry_dsn := os.environ.get("SENTRY_DSN"):
        import sentry_sdk  # pylint: disable=import-outside-toplevel

        sentry_sdk.init(_sentry_dsn)

    logging.basicConfig(
        format="[{levelname}] {name}: {message}",
        style="{",
        level=logging.INFO,
    )
    app = web.Application()
    app.add_routes(routes)
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, port=port)


if __name__ == "__main__":
    main()
