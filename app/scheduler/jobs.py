import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.catalog.covers import CoverService
from app.catalog.ingest import sync_catalog
from app.config import settings

log = logging.getLogger(__name__)


# ============================================================
# ОБЁРТКА ЗАДАЧ
# ============================================================

def _logged(name):
    """
    Декоратор: логирует старт/финиш/ошибку задачи.
    Не даёт исключению убить планировщик.
    """
    def wrap(fn):
        async def inner(*args, **kwargs):
            t0 = datetime.now(timezone.utc)
            log.info(">>> task %s: СТАРТ", name)
            try:
                result = await fn(*args, **kwargs)
                dt = (datetime.now(timezone.utc) - t0).total_seconds()
                log.info(">>> task %s: ГОТОВО (%s) за %.1f сек",
                         name, result, dt)
                return result
            except Exception:
                dt = (datetime.now(timezone.utc) - t0).total_seconds()
                log.exception(">>> task %s: ОШИБКА за %.1f сек", name, dt)
        return inner
    return wrap


# ============================================================
# СБОРКА ПЛАНИРОВЩИКА
# ============================================================

def build_scheduler(covers: CoverService) -> AsyncIOScheduler:
    log.info(">>> build_scheduler: вход")

    scheduler = AsyncIOScheduler(timezone="UTC")
    log.info(">>> build_scheduler: scheduler создан")

    # первый запуск — через 5 секунд после старта
    if settings.SCRAPE_RUN_ON_START:
        first_run = datetime.now(timezone.utc) + timedelta(seconds=5)
        log.info(">>> build_scheduler: первый запуск sync_catalog в %s",
                 first_run)
    else:
        first_run = None
        log.info(">>> build_scheduler: SCRAPE_RUN_ON_START=False, "
                 "первый запуск по триггеру")

    scheduler.add_job(
        _logged("sync_catalog")(sync_catalog),
        trigger=IntervalTrigger(hours=settings.SCRAPE_INTERVAL_HOURS),
        args=[covers],
        id="sync_catalog",
        max_instances=1,
        coalesce=True,
        next_run_time=first_run,
    )
    log.info(">>> build_scheduler: job sync_catalog добавлен "
             "(interval=%d ч)", settings.SCRAPE_INTERVAL_HOURS)

    # TODO: cleanup_unused — подключить после реализации app/torrents/

    jobs = scheduler.get_jobs()
    log.info(">>> build_scheduler: всего задач %d", len(jobs))
    for j in jobs:
        log.info(">>>   id=%s trigger=%s next_run=%s",
                 j.id, j.trigger,
                 getattr(j, "next_run_time", None))

    log.info(">>> build_scheduler: выход")
    return scheduler