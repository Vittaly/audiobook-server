import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.catalog import layout
from app.config import settings
from app.db.models import Book, BookStatus, Chapter, ChapterStatus
from app.db.session import session_scope
from app.torrents import manager

log = logging.getLogger(__name__)


def cleanup_unused() -> int:
    """
    Удаляет книги, все главы которых не стримились дольше TTL_DAYS.
    Работает по главам: смотрим chapter.last_streamed_at и book.last_accessed.
    Удаляем торрент целиком — libtorrent не умеет удалять отдельные файлы.
    """
    threshold = datetime.utcnow() - timedelta(days=settings.TTL_DAYS)
    removed = 0

    with session_scope() as db:
        candidates = db.execute(
            select(Book).where(Book.status.in_([
                BookStatus.READY.value,
                BookStatus.PARTIAL.value,
            ]))
        ).scalars().all()

        for book in candidates:
            last = book.last_accessed

            if last is not None and last >= threshold:
                continue

            # свежие главы?
            fresh = db.execute(
                select(Chapter.id).where(
                    Chapter.book_id == book.id,
                    Chapter.last_streamed_at.is_not(None),
                    Chapter.last_streamed_at >= threshold,
                ).limit(1)
            ).scalar_one_or_none()

            if fresh is not None:
                continue

            _remove_book(db, book)
            removed += 1

    log.info("cleanup_unused: удалено %d книг", removed)
    return removed


def _remove_book(db, book: Book) -> None:
    log.info("Удаляем book_id=%s, last_accessed=%s",
             book.id, book.last_accessed)

    book.status = BookStatus.DELETING.value
    db.commit()

    # останавливаем все торренты этой книги
    for br in book.releases:
        try:
            manager.remove(br.release.torrent_hash, delete_files=True)
        except Exception:
            log.exception("Не удалось удалить торрент %s",
                          br.release.torrent_hash)

    # папка
    layout.remove_book_dir(book.title)

    # главы
    chapters = db.execute(
        select(Chapter).where(Chapter.book_id == book.id)
    ).scalars().all()
    for chapter in chapters:
        chapter.status = ChapterStatus.DELETED.value
        chapter.downloaded_bytes = 0
        chapter.progress = 0.0
        chapter.pieces_have = 0

    book.status = BookStatus.DELETED.value


def cleanup_orphans() -> int:
    """
    При старте: папки в downloads/ без активных торрентов → rmtree.
    Возвращает количество удалённых папок.
    """
    removed = 0
    active = {t["hash"] for t in manager.list_active()}

    for path in settings.downloads_dir.iterdir():
        if not path.is_dir():
            continue

        # проверяем, есть ли торрент с таким save_path
        with session_scope() as db:
            from app.db.models import Release
            exists = db.execute(
                select(Release.id).where(
                    Release.save_path == str(path),
                    Release.torrent_hash.in_(active) if active else False,
                ).limit(1)
            ).scalar_one_or_none()

        if exists is None:
            layout.remove_book_dir(path.name)
            removed += 1

    log.info("cleanup_orphans: удалено %d папок", removed)
    return removed