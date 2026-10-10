import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.catalog.covers import CoverService
from app.config import settings
from app.db.session import init_db
from app.scheduler.jobs import build_scheduler

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(">>> lifespan: вход")

    try:
        settings.ensure_dirs()
        log.info(">>> lifespan: каталоги готовы")
    except Exception:
        log.exception(">>> lifespan: ошибка ensure_dirs")
        raise

    try:
        init_db()
        log.info(">>> lifespan: БД инициализирована")
    except Exception:
        log.exception(">>> lifespan: ошибка init_db")
        raise

    try:
        covers = CoverService()
        app.state.covers = covers
        log.info(">>> lifespan: CoverService создан")
    except Exception:
        log.exception(">>> lifespan: ошибка CoverService")
        raise

    try:
        scheduler = build_scheduler(covers)
        log.info(">>> lifespan: scheduler собран, state=%s, running=%s",
                 scheduler.state, scheduler.running)
        log.info(">>> lifespan: задачи: %s",
                 [(j.id, str(j.next_run_time)) for j in scheduler.get_jobs()])
    except Exception:
        log.exception(">>> lifespan: ошибка build_scheduler")
        raise

    try:
        scheduler.start()
        app.state.scheduler = scheduler
        log.info(">>> lifespan: scheduler.start() вызван, "
                 "state=%s, running=%s",
                 scheduler.state, scheduler.running)
    except Exception:
        log.exception(">>> lifespan: ошибка scheduler.start")
        raise

    log.info(">>> lifespan: startup завершён, отдаём управление")

    try:
        yield
    except Exception:
        log.exception(">>> lifespan: ошибка во время работы приложения")
        raise
    finally:
        log.info(">>> lifespan: shutdown начался")

        try:
            scheduler.shutdown(wait=False)
            log.info(">>> lifespan: scheduler остановлен")
        except Exception:
            log.exception(">>> lifespan: ошибка scheduler.shutdown")

        try:
            await covers.aclose()
            log.info(">>> lifespan: CoverService закрыт")
        except Exception:
            log.exception(">>> lifespan: ошибка covers.aclose")

        log.info(">>> lifespan: выход")


def create_app() -> FastAPI:
    log.info(">>> create_app: старт")

    try:
        from app.api.routes import books, health
        log.info(">>> create_app: роуты импортированы")
    except Exception:
        log.exception(">>> create_app: ошибка импорта роутов")
        raise

    app = FastAPI(title="audiobook-server", lifespan=lifespan)
    log.info(">>> create_app: FastAPI создан, lifespan=%s",
             app.router.lifespan_context)

    app.include_router(health.router, tags=["system"])
    app.include_router(books.router, tags=["books"])
    log.info(">>> create_app: роуты подключены, всего маршрутов=%d",
             len(app.routes))

    return app