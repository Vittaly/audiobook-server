import asyncio
import logging
import os
import sys
from datetime import datetime

import uvicorn

from app.api.server import create_app
from app.config import settings
from app.logging_setup import setup_logging

log = logging.getLogger(__name__)


async def run_api() -> None:
    log.info(">>> main: создаём приложение")
    app = create_app()
    log.info(">>> main: приложение создано, тип=%s", type(app).__name__)

    config = uvicorn.Config(
        app,
        host=settings.API_HOST,
        port=settings.API_PORT,
        log_config=None,
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)

    log.info(">>> main: запускаем API на http://%s:%d",
             settings.API_HOST, settings.API_PORT)
    await server.serve()
    log.info(">>> main: API остановлен")


async def main() -> None:
    setup_logging()

    log.info("=== audiobook-server запускается ===")
    log.info(">>> main: __file__=%s", __file__)
    log.info(">>> main: python=%s", sys.version.split()[0])
    log.info(">>> main: cwd=%s", os.getcwd())
    log.info(">>> main: data_dir=%s", settings.data_dir)
    log.info(">>> main: db_url=%s", settings.db_url)
    log.info(">>> main: SCRAPE_RUN_ON_START=%s", settings.SCRAPE_RUN_ON_START)
    log.info(">>> main: SCRAPE_INTERVAL_HOURS=%s",
             settings.SCRAPE_INTERVAL_HOURS)

    import fastapi, starlette, uvicorn as _u, apscheduler
    log.info(">>> main: fastapi=%s", fastapi.__version__)
    log.info(">>> main: starlette=%s", starlette.__version__)
    log.info(">>> main: uvicorn=%s", _u.__version__)
    log.info(">>> main: apscheduler=%s", apscheduler.__version__)

    t0 = datetime.utcnow()
    try:
        await run_api()
    finally:
        dt = (datetime.utcnow() - t0).total_seconds()
        log.info("=== audiobook-server остановлен (%.1f сек) ===", dt)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info(">>> main: Ctrl+C")