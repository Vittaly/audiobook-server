import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import mutagen
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.covers import CoverService
from app.config import settings
from app.db.models import (
    Book, BookRelease, BookStatus, Chapter, ChapterStatus, ContentType, Release,
)
from app.db.session import session_scope
from app.torrents import manager

log = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def scan_book(book_id: int, covers: CoverService) -> None:
    """
    После получения метаданных торрента:
    - разбирает структуру на книги (по папкам),
    - создаёт главы со статусом absent,
    - уточняет content_type и folder_map раздачи,
    - при наличии копирует обложку из торрента.
    """
    with session_scope() as db:
        book = db.get(Book, book_id)
        if book is None:
            return

        br = _primary_book_release(db, book)
        if br is None:
            log.warning("book=%s: нет BookRelease", book_id)
            return

        release = br.release
        handle = manager.get_handle(release.torrent_hash)
        if handle is None:
            log.warning("Нет handle для %s", release.torrent_hash)
            return

        files = manager.list_files(handle)
        groups = split_torrent_into_books(files, torrent_name=handle.name())

        # уточняем тип содержимого по факту
        if len(groups) > 1 and release.content_type == ContentType.SINGLE.value:
            release.content_type = ContentType.SERIES.value
            release.books_hint = len(groups)
            log.info("release=%s переклассифицирован в series по %d папкам",
                     release.id, len(groups))

        release.folder_map = json.dumps([
            {"folder": g["folder"], "title": g["title"], "order": i + 1}
            for i, g in enumerate(groups)
        ])

        # BookRelease.local_title / local_order
        if len(groups) == 1:
            br.local_title = book.title
            br.local_order = None
        else:
            br.local_title = groups[0].get("title")
            br.local_order = 1

        # обложка из торрента, если своей нет
        if book.cover_cache_path is None:
            cover_rel = _pick_cover_any(groups)
            if cover_rel:
                abs_path = settings.downloads_dir / cover_rel
                covers.cache_from_torrent(book, abs_path)

        # главы
        _create_chapters(db, book, release, groups)

        if book.status == BookStatus.ANNOUNCED.value:
            book.status = BookStatus.DOWNLOADING.value


def _primary_book_release(db: Session, book: Book) -> BookRelease | None:
    return db.execute(
        select(BookRelease).where(
            BookRelease.book_id == book.id,
            BookRelease.is_primary == 1,
        )
    ).scalar_one_or_none()


def _create_chapters(db: Session, book: Book, release: Release,
                     groups: list[dict]) -> None:
    """Создаёт Chapter-записи со сквозной нумерацией."""
    ordinal = 0
    for group in groups:
        for f in group["audio"]:
            ordinal += 1

            existing = db.execute(
                select(Chapter).where(
                    Chapter.book_id == book.id, Chapter.ordinal == ordinal)
            ).scalar_one_or_none()
            if existing is not None:
                continue

            db.add(Chapter(
                book_id=book.id,
                release_id=release.id,
                ordinal=ordinal,
                file_index=f["index"],
                rel_path=f["rel_path"],
                size_bytes=f["size_bytes"],
                file_offset=f.get("file_offset", 0),
                status=ChapterStatus.ABSENT.value,
                title=_chapter_title(group, f),
                duration_sec=_probe_duration(f["rel_path"]),
            ))


def _chapter_title(group: dict, f: dict) -> str:
    stem = Path(f["rel_path"]).stem
    title = group.get("title")
    if title and title not in stem:
        return f"{title} — {stem}"
    return stem


def _probe_duration(rel_path: str) -> int | None:
    abs_path = settings.downloads_dir / rel_path
    if not abs_path.exists():
        return None
    try:
        tag = mutagen.File(abs_path, easy=True)
        return int(tag.info.length) if tag and tag.info else None
    except Exception:
        return None


def _pick_cover_any(groups: list[dict]) -> str | None:
    for g in groups:
        if g.get("cover"):
            return g["cover"]
    return None


# ============================================================
# РАЗБОР СТРУКТУРЫ ТОРРЕНТА
# ============================================================

def split_torrent_into_books(files: list[dict],
                             torrent_name: str) -> list[dict]:
    """
    Первый компонент rel_path — папка торрента (её создаёт libtorrent).
    Всё, что глубже — структура раздачи.

    Возвращает список групп-книг:
        [{"title": ..., "folder": ..., "audio": [...],
          "cover": ..., "size_bytes": ...}]
    """
    trimmed = []
    for f in files:
        parts = Path(f["rel_path"]).parts
        sub = Path(*parts[1:]) if len(parts) > 1 else Path(parts[0])
        trimmed.append({**f, "sub_path": sub})

    depths = {len(f["sub_path"].parts) for f in trimmed}

    # плоский торрент — одна книга
    if depths == {1}:
        audio = [f for f in trimmed if _is_audio(f)]
        return [{
            "title": None,
            "folder": None,
            "audio": sorted(audio,
                            key=lambda f: _natural(f["sub_path"].name)),
            "cover": _pick_cover(trimmed),
            "size_bytes": sum(f["size_bytes"] for f in audio),
        }]

    # вложенные папки — группируем по первому компоненту
    groups: dict[str, list[dict]] = defaultdict(list)
    for f in trimmed:
        groups[f["sub_path"].parts[0]].append(f)

    books = []
    for folder, folder_files in sorted(groups.items()):
        audio = [f for f in folder_files if _is_audio(f)]
        if not audio:
            continue
        books.append({
            "title": _clean_folder_name(folder),
            "folder": folder,
            "audio": sorted(audio,
                            key=lambda f: _natural(f["sub_path"].name)),
            "cover": _pick_cover(folder_files),
            "size_bytes": sum(f["size_bytes"] for f in audio),
        })

    if len(books) == 1:
        books[0]["folder"] = None
        books[0]["title"] = None

    return books


def _is_audio(f: dict) -> bool:
    return Path(f["sub_path"].name).suffix.lower() in AUDIO_EXTS


def _is_image(f: dict) -> bool:
    return Path(f["sub_path"].name).suffix.lower() in IMAGE_EXTS


def _natural(name: str) -> list:
    return [
        int(t) if t.isdigit() else t.lower()
        for t in re.split(r"(\d+)", name)
    ]


def _pick_cover(files: list[dict]) -> str | None:
    images = [f for f in files if _is_image(f)]
    if not images:
        return None
    priority = ("cover", "folder", "front", "обложка")
    for p in priority:
        for f in images:
            if p in Path(f["sub_path"].name).stem.lower():
                return f["rel_path"]
    return images[0]["rel_path"]


def _clean_folder_name(folder: str) -> str:
    s = re.sub(r"^\s*(?:книга\s*)?\d+\s*[.)\-–—]?\s*", "",
               folder, flags=re.I)
    return s.strip(" .-–—") or folder


# ============================================================
# АГРЕГАЦИЯ СТАТУСА КНИГИ
# ============================================================

def recompute_book_status(db: Session, book_id: int) -> None:
    """Пересчитывает Book.status по статусам глав."""
    book = db.get(Book, book_id)
    if book is None:
        return

    statuses = list(db.execute(
        select(Chapter.status).where(Chapter.book_id == book_id)
    ).scalars().all())

    if not statuses:
        new_status = BookStatus.ANNOUNCED.value
    elif all(s == ChapterStatus.READY.value for s in statuses):
        new_status = BookStatus.READY.value
    elif all(s == ChapterStatus.DELETED.value for s in statuses):
        new_status = BookStatus.DELETED.value
    elif any(s == ChapterStatus.ERROR.value for s in statuses):
        new_status = BookStatus.ERROR.value
    elif any(s in (ChapterStatus.DOWNLOADING.value,
                   ChapterStatus.PARTIAL.value) for s in statuses):
        new_status = (
            BookStatus.PARTIAL.value
            if any(s == ChapterStatus.READY.value for s in statuses)
            else BookStatus.DOWNLOADING.value
        )
    elif any(s == ChapterStatus.READY.value for s in statuses):
        new_status = BookStatus.PARTIAL.value
    else:
        new_status = BookStatus.ANNOUNCED.value

    if book.status != new_status:
        log.info("book=%s: %s → %s", book_id, book.status, new_status)
        book.status = new_status
        if new_status == BookStatus.READY.value:
            book.downloaded_at = datetime.utcnow()


def update_chapter_from_alert(chapter_id: int, *,
                              status: str | None = None,
                              progress: float | None = None,
                              pieces_have: int | None = None,
                              downloaded_bytes: int | None = None) -> None:
    """Обновляет главу и пересчитывает статус книги."""
    with session_scope() as db:
        chapter = db.get(Chapter, chapter_id)
        if chapter is None:
            return
        if status is not None:
            chapter.status = status
        if progress is not None:
            chapter.progress = progress
        if pieces_have is not None:
            chapter.pieces_have = pieces_have
        if downloaded_bytes is not None:
            chapter.downloaded_bytes = downloaded_bytes
        chapter.last_modified_at = datetime.utcnow()
        recompute_book_status(db, chapter.book_id)