import logging
import os

from aiohttp import web

from .utils import SENTRY_DSN
from .web import app as web_app

log = logging.getLogger("red_githubbot")


def main() -> None:
    if SENTRY_DSN:
        # pylint: disable=import-outside-toplevel
        import sentry_sdk
        from sentry_sdk.integrations.pure_eval import PureEvalIntegration

        sentry_sdk.init(
            SENTRY_DSN,
            release=os.getenv("HEROKU_SLUG_COMMIT"),
            traces_sample_rate=0.1,
            integrations=[PureEvalIntegration()],
        )

    logging.basicConfig(
        format="[{levelname}] {name}: {message}",
        style="{",
        level=logging.INFO,
    )
    logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.DEBUG)
    port = int(os.environ.get("PORT", 8080))
    web.run_app(web_app, port=port)


if __name__ == "__main__":
    main()
