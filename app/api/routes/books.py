import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.catalog import service
from app.db.models import Book
from app.db.session import get_db
from app.schemas import BookDetailOut, BookOut, ListResponse

log = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# ПРЕОБРАЗОВАНИЕ ORM → СХЕМА
# ============================================================

def _author_of(book: Book) -> str | None:
    for bp in book.persons:
        if bp.role == "author":
            return bp.person.name
    return None


def _narrator_of(book: Book) -> str | None:
    for bp in book.persons:
        if bp.role == "narrator":
            return bp.person.name
    return None


def _book_to_out(book: Book, api_base: str) -> BookOut:
    chapters_total = len(book.chapters)
    chapters_ready = sum(1 for c in book.chapters if c.status == "ready")
    duration = sum(c.duration_sec or 0 for c in book.chapters) or None
    size = sum(c.size_bytes for c in book.chapters) or None

    series_ref = None
    if book.series:
        series_ref = {
            "id": book.series.id,
            "title": book.series.title,
            "order": book.series_order,
        }

    cover_url = None
    if book.cover_cache_path:
        cover_url = f"{api_base}/books/{book.id}/cover"

    return BookOut(
        id=book.id,
        title=book.title,
        author=_author_of(book),
        narrator=_narrator_of(book),
        year=book.year,
        language=book.language,
        series=series_ref,
        rubrics=[br.rubric.title for br in book.rubrics],
        status=book.status,
        chapters_count=chapters_total,
        chapters_ready=chapters_ready,
        total_duration_sec=duration,
        size_bytes=size,
        cover_url=cover_url,
        added_at=book.added_at,
        last_accessed=book.last_accessed,
    )


def _book_detail(book: Book, api_base: str) -> BookDetailOut:
    base = _book_to_out(book, api_base).model_dump()

    base["title_raw"] = book.title_raw
    base["description"] = book.description
    base["persons"] = [
        {"name": bp.person.name, "role": bp.role} for bp in book.persons
    ]
    base["chapters"] = [
        {
            "id": c.id,
            "ordinal": c.ordinal,
            "title": c.title,
            "duration_sec": c.duration_sec,
            "size_bytes": c.size_bytes,
            "status": c.status,
            "progress": round(c.progress, 3),
            "stream_url": f"/books/{book.id}/stream/{c.id}",
        }
        for c in book.chapters
    ]

    return BookDetailOut(**base)


# ============================================================
# РОУТЫ
# ============================================================

@router.get("/books", response_model=ListResponse[BookOut])
def list_books(
    request: Request,
    query: str | None = None,
    status: str | None = None,
    rubric: str | None = None,
    author: str | None = None,
    narrator: str | None = None,
    series_id: int | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    sort: str = "added_at",
    order: str = "desc",
    db: Session = Depends(get_db),
):
    api_base = str(request.base_url).rstrip("/")
    books, total = service.list_books(
        db,
        query=query,
        status=status,
        rubric=rubric,
        author=author,
        narrator=narrator,
        series_id=series_id,
        limit=limit,
        offset=offset,
        sort=sort,
        order=order,
    )

    log.info("GET /books query=%s status=%s total=%d",
             query, status, total)

    return ListResponse[BookOut](
        items=[_book_to_out(b, api_base) for b in books],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/books/{book_id}", response_model=BookDetailOut)
def get_book(
    book_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    book = service.get_book(db, book_id)
    if book is None:
        raise HTTPException(404, "book not found")

    api_base = str(request.base_url).rstrip("/")
    log.info("GET /books/%d", book_id)

    return _book_detail(book, api_base)