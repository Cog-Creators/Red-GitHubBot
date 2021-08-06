import logging
import os

from aiohttp import web

from .web import app as web_app

log = logging.getLogger("red_githubbot")


def main() -> None:
    if _sentry_dsn := os.environ.get("SENTRY_DSN"):
        import sentry_sdk  # pylint: disable=import-outside-toplevel

        sentry_sdk.init(_sentry_dsn, release=os.environ["HEROKU_SLUG_COMMIT"])

    logging.basicConfig(
        format="[{levelname}] {name}: {message}",
        style="{",
        level=logging.INFO,
    )
    port = int(os.environ.get("PORT", 8080))
    # in aiohttp 4.0, we will need to pass `loop` kwarg here
    web.run_app(web_app, port=port)


if __name__ == "__main__":
    main()
