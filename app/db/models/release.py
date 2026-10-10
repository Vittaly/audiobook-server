from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, Integer,
    String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.enums import ContentType

if TYPE_CHECKING:
    from app.db.models.book import Book
    from app.db.models.chapter import Chapter


class Release(Base):
    """
    Раздача-источник. Не привязана к одной книге.
    Связь с книгами — через BookRelease (M:N).
    """
    __tablename__ = "release"
    __table_args__ = (
        CheckConstraint(
            "content_type IN ('single','series','collection')",
            name="ck_release_content_type",
        ),
    )

    id:        Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("source.id", ondelete="SET NULL"))

    torrent_hash: Mapped[str] = mapped_column(
        String(40), nullable=False, unique=True, index=True)
    magnet:       Mapped[str] = mapped_column(Text, nullable=False)
    source_url:   Mapped[str | None] = mapped_column(Text)
    save_path:    Mapped[str] = mapped_column(Text, nullable=False)

    title_raw:  Mapped[str | None] = mapped_column(String(500))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)

    content_type: Mapped[str] = mapped_column(
        String(20), default=ContentType.SINGLE.value, nullable=False)
    books_hint:   Mapped[int | None] = mapped_column(Integer)
    folder_map:   Mapped[str | None] = mapped_column(Text)

    added_at:     Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)

    books:    Mapped[list["BookRelease"]] = relationship(
        back_populates="release", cascade="all, delete-orphan")
    chapters: Mapped[list["Chapter"]] = relationship(back_populates="release")


class BookRelease(Base):
    """M:N между книгой и раздачей."""
    __tablename__ = "book_release"

    book_id:    Mapped[int] = mapped_column(
        ForeignKey("book.id", ondelete="CASCADE"), primary_key=True)
    release_id: Mapped[int] = mapped_column(
        ForeignKey("release.id", ondelete="CASCADE"), primary_key=True)

    is_primary: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    local_title: Mapped[str | None] = mapped_column(String(500))
    local_order: Mapped[int | None] = mapped_column(Integer)

    added_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False)

    book:    Mapped["Book"]    = relationship(back_populates="releases")
    release: Mapped["Release"] = relationship(back_populates="books")