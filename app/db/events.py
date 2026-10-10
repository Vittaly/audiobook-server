import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.models.book import Book
from app.db.models.chapter import Chapter

log = logging.getLogger(__name__)


@event.listens_for(Session, "before_flush")
def _recompute_dirty_books(session: Session, flush_context, instances):
    """
    Если кто-то поменял Chapter.status напрямую (в обход set_status),
    перед flush находим затронутые книги и пересчитываем их статус.
    Работает как страховка, не как основной механизм.
    """
    touched: set[int] = set()

    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, Chapter) and obj.book_id is not None:
            touched.add(obj.book_id)

    for book_id in touched:
        book = session.get(Book, book_id)
        if book is not None:
            book.recompute_status()