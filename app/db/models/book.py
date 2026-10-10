from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.enums import BookStatus, ChapterStatus

if TYPE_CHECKING:
    from app.db.models.chapter import Chapter
    from app.db.models.person import BookPerson
    from app.db.models.release import BookRelease
    from app.db.models.rubric import BookRubric
    from app.db.models.series import Series


class Book(Base):
    """
    Книга в каталоге.
    Статус — агрегат над статусами глав, пересчитывается методом recompute_status.
    """
    __tablename__ = "book"
    __table_args__ = (
        UniqueConstraint("title_norm", "year", name="ux_book_title_year"),
        CheckConstraint(
            "status IN ('announced','downloading','partial','ready',"
            "'deleting','deleted','error')",
            name="ck_book_status",
        ),
    )

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    title:      Mapped[str] = mapped_column(String(500), nullable=False)
    title_norm: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    title_raw:  Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    year:       Mapped[int | None] = mapped_column(Integer, index=True)
    language:   Mapped[str | None] = mapped_column(String(8))

    series_id:    Mapped[int | None] = mapped_column(
        ForeignKey("series.id", ondelete="SET NULL"))
    series_order: Mapped[int | None] = mapped_column(Integer)

    status:        Mapped[str] = mapped_column(
        String(20), default=BookStatus.ANNOUNCED.value,
        nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text)

    cover_origin:      Mapped[str | None] = mapped_column(String(20))
    cover_source_url:  Mapped[str | None] = mapped_column(Text)
    cover_cache_path:  Mapped[str | None] = mapped_column(Text)
    cover_mime:        Mapped[str | None] = mapped_column(String(50))
    cover_sha256:      Mapped[str | None] = mapped_column(String(64), index=True)
    cover_retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    added_at:      Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False, index=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_accessed: Mapped[datetime | None] = mapped_column(DateTime, index=True)

    series:   Mapped["Series | None"] = relationship(back_populates="books")
    releases: Mapped[list["BookRelease"]] = relationship(
        back_populates="book", cascade="all, delete-orphan")
    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="book", cascade="all, delete-orphan",
        order_by="Chapter.ordinal")
    persons:  Mapped[list["BookPerson"]] = relationship(
        back_populates="book", cascade="all, delete-orphan")
    rubrics:  Mapped[list["BookRubric"]] = relationship(
        back_populates="book", cascade="all, delete-orphan")

    # ---------- поведение ----------

    def recompute_status(self) -> None:
        """Пересчитывает статус по главам. Вызывается главой или event'ом."""
        statuses = [c.status for c in self.chapters]
        new = self._aggregate(statuses)

        if self.status == new:
            return

        self.status = new
        if new == BookStatus.READY.value:
            self.downloaded_at = datetime.utcnow()

    @staticmethod
    def _aggregate(statuses: list[str]) -> str:
        if not statuses:
            return BookStatus.ANNOUNCED.value

        if all(s == ChapterStatus.READY.value for s in statuses):
            return BookStatus.READY.value
        if all(s == ChapterStatus.DELETED.value for s in statuses):
            return BookStatus.DELETED.value
        if any(s == ChapterStatus.ERROR.value for s in statuses):
            return BookStatus.ERROR.value

        has_ready = any(s == ChapterStatus.READY.value for s in statuses)
        has_in_progress = any(
            s in (ChapterStatus.DOWNLOADING.value, ChapterStatus.PARTIAL.value)
            for s in statuses
        )

        if has_in_progress:
            return (BookStatus.PARTIAL.value if has_ready
                    else BookStatus.DOWNLOADING.value)
        if has_ready:
            return BookStatus.PARTIAL.value
        return BookStatus.ANNOUNCED.value