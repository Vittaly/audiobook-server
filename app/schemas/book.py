from datetime import datetime

from pydantic import BaseModel

from app.schemas.chapter import ChapterOut
from app.schemas.person import PersonRef
from app.schemas.series import SeriesRef


class BookOut(BaseModel):
    """Карточка книги для списков и кратких ответов."""
    id: int
    title: str
    author: str | None = None
    narrator: str | None = None
    year: int | None = None
    language: str | None = None

    series: SeriesRef | None = None
    rubrics: list[str] = []

    status: str
    chapters_count: int = 0
    chapters_ready: int = 0
    total_duration_sec: int | None = None
    size_bytes: int | None = None

    cover_url: str | None = None

    added_at: datetime
    last_accessed: datetime | None = None


class BookDetailOut(BookOut):
    """Полная карточка книги с главами и персонами."""
    title_raw: str | None = None
    description: str | None = None
    persons: list[PersonRef] = []
    chapters: list[ChapterOut] = []