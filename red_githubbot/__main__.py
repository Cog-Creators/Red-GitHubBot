import asyncio
import logging
import os

from aiohttp import web

from .web import app as web_app

log = logging.getLogger("red_githubbot")


def main() -> None:
    if _sentry_dsn := os.environ.get("SENTRY_DSN"):
        # pylint: disable=import-outside-toplevel
        import sentry_sdk
        from sentry_sdk.integrations.pure_eval import PureEvalIntegration

        sentry_sdk.init(
            _sentry_dsn,
            release=os.environ["HEROKU_SLUG_COMMIT"],
            traces_sample_rate=0.1,
            integrations=[PureEvalIntegration()],
        )

    logging.basicConfig(
        format="[{levelname}] {name}: {message}",
        style="{",
        level=logging.INFO,
    )
    port = int(os.environ.get("PORT", 8080))
    loop = asyncio.get_event_loop()
    web.run_app(web_app, port=port, loop=loop)


if __name__ == "__main__":
    main()
