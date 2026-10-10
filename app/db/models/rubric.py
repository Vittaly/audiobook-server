from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.book import Book


class Rubric(Base):
    """Жанр / тег / рубрика."""
    __tablename__ = "rubric"
    __table_args__ = (
        UniqueConstraint("kind", "title_norm", name="ux_rubric_kind_title"),
        CheckConstraint("kind IN ('genre','tag','rubric')", name="ck_rubric_kind"),
    )

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    kind:       Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title:      Mapped[str] = mapped_column(String(255), nullable=False)
    title_norm: Mapped[str] = mapped_column(String(255), nullable=False)

    books: Mapped[list["BookRubric"]] = relationship(
        back_populates="rubric", cascade="all, delete-orphan")


class BookRubric(Base):
    __tablename__ = "book_rubric"

    book_id:   Mapped[int] = mapped_column(
        ForeignKey("book.id", ondelete="CASCADE"), primary_key=True)
    rubric_id: Mapped[int] = mapped_column(
        ForeignKey("rubric.id", ondelete="CASCADE"), primary_key=True)

    book:   Mapped["Book"]   = relationship(back_populates="rubrics")
    rubric: Mapped["Rubric"] = relationship(back_populates="books")