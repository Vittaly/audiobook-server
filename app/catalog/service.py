import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import (
    Book, BookPerson, BookRelease, BookRubric, Chapter,
    Person, Release, Rubric, Series,
)

log = logging.getLogger(__name__)


def list_books(db: Session, *,
               query: str | None = None,
               status: str | None = None,
               rubric: str | None = None,
               author: str | None = None,
               narrator: str | None = None,
               series_id: int | None = None,
               limit: int = 50, offset: int = 0,
               sort: str = "added_at",
               order: str = "desc") -> tuple[list[Book], int]:
    stmt = select(Book)

    if query:
        stmt = stmt.where(Book.title_norm.like(f"{query.lower()}%"))
    if status:
        stmt = stmt.where(Book.status == status)
    if series_id:
        stmt = stmt.where(Book.series_id == series_id)

    if rubric:
        stmt = stmt.join(BookRubric).join(Rubric).where(
            Rubric.title_norm == rubric.lower())

    if author:
        stmt = stmt.join(BookPerson, BookPerson.book_id == Book.id).join(
            Person, Person.id == BookPerson.person_id).where(
            BookPerson.role == "author",
            Person.name_norm == author.lower())

    if narrator:
        stmt = stmt.join(BookPerson, BookPerson.book_id == Book.id).join(
            Person, Person.id == BookPerson.person_id).where(
            BookPerson.role == "narrator",
            Person.name_norm == narrator.lower())

    total = db.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()

    sort_col = {
        "added_at": Book.added_at,
        "title":    Book.title,
        "year":     Book.year,
    }.get(sort, Book.added_at)
    if order == "desc":
        sort_col = sort_col.desc()

    stmt = stmt.order_by(sort_col).limit(limit).offset(offset)
    stmt = stmt.options(
        selectinload(Book.persons),
        selectinload(Book.rubrics),
        selectinload(Book.series),
        selectinload(Book.releases).selectinload(BookRelease.release),
    )

    books = list(db.execute(stmt).scalars().all())
    return books, total


def get_book(db: Session, book_id: int) -> Book | None:
    return db.execute(
        select(Book).where(Book.id == book_id)
        .options(
            selectinload(Book.chapters),
            selectinload(Book.persons),
            selectinload(Book.rubrics),
            selectinload(Book.series),
            selectinload(Book.releases).selectinload(BookRelease.release),
        )
    ).scalar_one_or_none()


def get_chapters(db: Session, book_id: int) -> list[Chapter]:
    return list(db.execute(
        select(Chapter).where(Chapter.book_id == book_id)
        .order_by(Chapter.ordinal)
    ).scalars().all())


def get_chapter_full(db: Session, chapter_id: int) -> Chapter | None:
    return db.execute(
        select(Chapter).where(Chapter.id == chapter_id)
        .options(
            selectinload(Chapter.release),
            selectinload(Chapter.book),
        )
    ).scalar_one_or_none()


def get_primary_release(book: Book) -> Release | None:
    """Основная раздача для книги."""
    for br in book.releases:
        if br.is_primary:
            return br.release
    return book.releases[0].release if book.releases else None


def get_download_status(db: Session, book_id: int) -> dict:
    """Сводка по главам для /books/{id}/status."""
    book = get_book(db, book_id)
    if book is None:
        return {}

    chapters = [
        {
            "id": c.id,
            "ordinal": c.ordinal,
            "title": c.title,
            "status": c.status,
            "progress": round(c.progress, 3),
            "size_bytes": c.size_bytes,
            "downloaded_bytes": c.downloaded_bytes,
        }
        for c in book.chapters
    ]

    total = sum(c.size_bytes for c in book.chapters) or 0
    downloaded = sum(c.downloaded_bytes for c in book.chapters)

    return {
        "book_id": book.id,
        "status": book.status,
        "progress": round(downloaded / total, 3) if total else 0.0,
        "total_bytes": total,
        "downloaded_bytes": downloaded,
        "chapters": chapters,
    }


def resolve_cover_url(book: Book, api_base: str) -> str:
    """Клиент всегда получает один стабильный URL."""
    return f"{api_base}/books/{book.id}/cover"