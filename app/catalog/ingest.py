"""
Наполнение каталога.

Принимает уже нормализованные данные:
  - из crawler.walk — items с готовыми полями
  - из rutor.fetch_torrent_details — готовый update

Никакой нормализации данных не делает. Только пишет в БД.

Правило: title и author книги устанавливаются один раз в _upsert_book
из title_raw (заголовок раздачи). Поля формы на детальной странице
ненадёжны (заливщик может путать «Название» и «Автор»), поэтому
_apply_book_update их не перезаписывает.
"""

import asyncio
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.covers import CoverService
from app.catalog.layout import sanitize_name
from app.config import settings
from app.db.models import (
    Book, BookPerson, BookRelease, BookRubric, BookStatus,
    Chapter, ChapterStatus, Person, Release, Rubric,
)
from app.db.session import SessionLocal
from app.scraper import crawler, rutor
from app.scraper.state import CrawlStateStore
from app.torrents import structure

log = logging.getLogger(__name__)


@contextmanager
def _sync_session():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ============================================================
# СИНХРОНИЗАЦИЯ КАТАЛОГА
# ============================================================

async def sync_catalog(covers: CoverService) -> int:
    log.info(">>> sync_catalog: старт")
    timeout = settings.SCRAPE_TOTAL_TIMEOUT_SEC
    try:
        result = await asyncio.wait_for(
            _sync_catalog_inner(covers),
            timeout=timeout,
        )
        log.info(">>> sync_catalog: завершено, обработано %d", result)
        return result
    except asyncio.TimeoutError:
        log.warning(">>> sync_catalog: прерван по таймауту %d сек", timeout)
        return 0


async def _sync_catalog_inner(covers: CoverService) -> int:
    sem = asyncio.Semaphore(max(1, settings.SCRAPE_DETAILS_CONCURRENCY))
    total = 0

    with _sync_session() as db:
        store = CrawlStateStore(db)
        state = store.load("rutor.info", rutor.BASE, "audiobooks")
        log.info(">>> sync_catalog: state = {oldest_seen=%s, resume_page=%s}",
                 state.oldest_seen_at, state.resume_page)

    def on_progress(page: int, st):
        with _sync_session() as db:
            s = db.merge(st)
            CrawlStateStore(db).save_progress(s)

    page_num = 0
    async for page in crawler.walk(
        query="mp3",
        state=state,
        on_progress=on_progress,
    ):
        log.info(">>> sync_catalog: страница %d (%d записей)",
                 page_num, len(page))

        for i, item in enumerate(page):
            try:
                total += await _process_item(item, covers, sem)
                if (i + 1) % 10 == 0:
                    log.info(">>> sync_catalog: %d/%d, всего %d",
                             i + 1, len(page), total)
            except Exception:
                log.exception(">>> sync_catalog: ошибка %s",
                              item.get("torrent_hash"))

        page_num += 1

    with _sync_session() as db:
        CrawlStateStore(db).finish(db.merge(state))

    log.info(">>> sync_catalog: обход завершён, всего %d", total)
    return total


async def _process_item(item: dict, covers: CoverService,
                        sem: asyncio.Semaphore) -> int:
    with _sync_session() as db:
        book = _upsert_book(db, item)
        if book is None:
            return 0

        if book.description is None and item.get("source_url"):
            raw = await _fetch_details(sem, item["source_url"])
            if raw:
                _apply_book_update(db, book, raw)

        if book.cover_cache_path is None and book.cover_source_url:
            await _fetch_cover(sem, book, covers)

        return 1


# ============================================================
# UPSERT КНИГИ
# ============================================================

def _upsert_book(db: Session, item: dict) -> Book | None:
    """item нормализован rutor.fetch_search_page: title, author, year готовы."""
    torrent_hash = item["torrent_hash"]

    release = db.execute(
        select(Release).where(Release.torrent_hash == torrent_hash)
    ).scalar_one_or_none()

    if release is not None:
        release.last_seen_at = datetime.now(timezone.utc)
        br = db.execute(
            select(BookRelease).where(BookRelease.release_id == release.id)
        ).scalar_one_or_none()
        return br.book if br else None

    title = item["title"]
    title_norm = _norm(title)
    year = item.get("year")

    book = db.execute(
        select(Book).where(
            Book.title_norm == title_norm,
            Book.year == year,
        )
    ).scalar_one_or_none()

    if book is None:
        book = Book(
            title=title,
            title_norm=title_norm,
            title_raw=item.get("title_raw"),
            year=year,
            status=BookStatus.ANNOUNCED.value,
        )
        db.add(book)
        db.flush()

        if item.get("author"):
            _link_person(db, book, "author", item["author"])
        if item.get("narrator"):
            _link_person(db, book, "narrator", item["narrator"])

    release = Release(
        source_id=_ensure_source(db).id,
        torrent_hash=torrent_hash,
        magnet=item["magnet"],
        source_url=item.get("source_url"),
        save_path=str(settings.downloads_dir / sanitize_name(title)),
        title_raw=item.get("title_raw"),
        size_bytes=item.get("size_bytes"),
        content_type=item["content_type"],
        books_hint=item["books_hint"],
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(release)
    db.flush()

    db.add(BookRelease(
        book_id=book.id,
        release_id=release.id,
        is_primary=1,
        local_title=book.title,
    ))

    return book


# ============================================================
# ПРИМЕНЕНИЕ UPDATE ИЗ ДЕТАЛЬНОЙ
# ============================================================

def _apply_book_update(db: Session, book: Book, update: dict) -> None:
    """
    Применяет поля с детальной страницы.

    НЕ трогает title и author: заголовок раздачи надёжнее полей формы.
    Поля формы используем только для того, чего нет в заголовке.
    """
    if update.get("description"):
        book.description = update["description"]

    # year уточняем только если в поисковой выдаче его не было
    if update.get("year") and book.year is None:
        book.year = update["year"]

    # чтец — отдельное поле, ему доверяем
    if update.get("narrator"):
        _replace_person(db, book, "narrator", update["narrator"])

    # рубрики
    for tag in update.get("rubrics") or []:
        _link_rubric(db, book, tag)

    # обложка
    if not book.cover_source_url and update.get("cover_url"):
        book.cover_source_url = update["cover_url"]
        book.cover_origin = "tracker"

    # content_type
    if update.get("content_type"):
        br = db.execute(
            select(BookRelease).where(
                BookRelease.book_id == book.id,
                BookRelease.is_primary == 1,
            )
        ).scalar_one_or_none()
        if br is not None:
            br.release.content_type = update["content_type"]
            if update.get("books_hint"):
                br.release.books_hint = update["books_hint"]


# ============================================================
# СЕТЕВЫЕ ХЕЛПЕРЫ
# ============================================================

async def _fetch_details(sem: asyncio.Semaphore, url: str) -> dict | None:
    async with sem:
        await asyncio.sleep(settings.SCRAPE_DELAY_SEC)
        return await rutor.fetch_torrent_details(
            url, http_timeout=settings.SCRAPE_HTTP_TIMEOUT)


async def _fetch_cover(sem: asyncio.Semaphore, book: Book,
                       covers: CoverService) -> None:
    async with sem:
        await asyncio.sleep(settings.SCRAPE_DELAY_SEC)
        if not await covers.fetch_from_url(book, book.cover_source_url):
            book.cover_retry_count += 1


# ============================================================
# НАПОЛНЕНИЕ ГЛАВ ИЗ ТОРРЕНТА
# ============================================================

def ingest_release(book_id: int, files: list[dict], torrent_name: str,
                   covers: CoverService) -> int:
    with _sync_session() as db:
        book = db.get(Book, book_id)
        if book is None:
            return 0

        br = db.execute(
            select(BookRelease).where(
                BookRelease.book_id == book.id,
                BookRelease.is_primary == 1,
            )
        ).scalar_one_or_none()
        if br is None:
            return 0

        release = br.release
        groups = structure.split_torrent_into_books(files, torrent_name)
        if not groups:
            return 0

        if len(groups) > 1 and release.content_type == "single":
            release.content_type = "series"
            release.books_hint = len(groups)

        release.folder_map = json.dumps([
            {"folder": g["folder"], "title": g["title"], "order": i + 1}
            for i, g in enumerate(groups)
        ])

        if len(groups) == 1:
            br.local_title = book.title
            br.local_order = None
        else:
            br.local_title = groups[0].get("title")
            br.local_order = 1

        if book.cover_cache_path is None:
            cover_rel = _first_cover(groups)
            if cover_rel:
                covers.cache_from_torrent(
                    book, settings.downloads_dir / cover_rel)

        created = _append_chapters(book, release, groups)
        book.recompute_status()

        log.info("book=%s: создано %d глав из %d книг раздачи",
                 book_id, created, len(groups))
        return created


def _append_chapters(book: Book, release: Release,
                     groups: list[dict]) -> int:
    existing = {c.ordinal for c in book.chapters}
    created = 0
    ordinal = 0

    for group in groups:
        for f in group["audio"]:
            ordinal += 1
            if ordinal in existing:
                continue

            book.chapters.append(Chapter(
                book_id=book.id,
                release_id=release.id,
                ordinal=ordinal,
                file_index=f["index"],
                rel_path=f["rel_path"],
                size_bytes=f["size_bytes"],
                file_offset=f.get("file_offset", 0),
                status=ChapterStatus.ABSENT.value,
                title=_chapter_title(group, f),
            ))
            created += 1

    return created


def _chapter_title(group: dict, f: dict) -> str:
    stem = f["rel_path"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    title = group.get("title")
    if title and title not in stem:
        return f"{title} — {stem}"
    return stem


def _first_cover(groups: list[dict]) -> str | None:
    for g in groups:
        if g.get("cover"):
            return g["cover"]
    return None


# ============================================================
# ХЕЛПЕРЫ
# ============================================================

def _ensure_source(db: Session):
    from app.db.models import Source
    source = db.execute(
        select(Source).where(Source.name == "rutor.info")
    ).scalar_one_or_none()
    if source is None:
        source = Source(name="rutor.info", base_url=rutor.BASE)
        db.add(source)
        db.flush()
    return source


def _ensure_person(db: Session, name: str) -> Person:
    name_norm = _norm(name)
    person = db.execute(
        select(Person).where(Person.name_norm == name_norm)
    ).scalar_one_or_none()
    if person is None:
        person = Person(name=name, name_norm=name_norm)
        db.add(person)
        db.flush()
    return person


def _ensure_rubric(db: Session, title: str) -> Rubric:
    title_norm = _norm(title)
    rubric = db.execute(
        select(Rubric).where(
            Rubric.kind == "tag",
            Rubric.title_norm == title_norm,
        )
    ).scalar_one_or_none()
    if rubric is None:
        rubric = Rubric(kind="tag", title=title, title_norm=title_norm)
        db.add(rubric)
        db.flush()
    return rubric


def _link_person(db: Session, book: Book, role: str, name: str) -> None:
    if not name:
        return
    person = _ensure_person(db, name)
    for bp in book.persons:
        if bp.role == role and bp.person_id == person.id:
            return
    book.persons.append(BookPerson(
        book_id=book.id, person_id=person.id, role=role))


def _replace_person(db: Session, book: Book, role: str,
                    name: str | None) -> None:
    if not name:
        return
    for bp in list(book.persons):
        if bp.role == role:
            book.persons.remove(bp)
    db.flush()
    _link_person(db, book, role, name)


def _link_rubric(db: Session, book: Book, title: str) -> None:
    rubric = _ensure_rubric(db, title)
    for br in book.rubrics:
        if br.rubric_id == rubric.id:
            return
    book.rubrics.append(BookRubric(
        book_id=book.id, rubric_id=rubric.id))


def _norm(s: str) -> str:
    return " ".join(s.lower().split())