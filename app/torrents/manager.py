"""
Заглушка torrent-менеджера.

Реальная реализация на libtorrent появится позже.
Пока все методы логируют warning и возвращают безопасные значения,
чтобы код, зависящий от manager, импортировался и не падал.
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_NOT_IMPLEMENTED = "torrents.manager: %s ещё не реализовано (заглушка)"


def add_magnet(magnet: str, save_path: Path):
    """Заглушка. В реальности: добавить торрент в libtorrent."""
    log.warning(_NOT_IMPLEMENTED, "add_magnet")
    return None


def remove(torrent_hash: str, delete_files: bool = True) -> None:
    """Заглушка. В реальности: удалить торрент и опционально файлы."""
    log.warning(_NOT_IMPLEMENTED, f"remove({torrent_hash})")


def get_handle(torrent_hash: str):
    """Заглушка. В реальности: получить lt.torrent_handle."""
    log.warning(_NOT_IMPLEMENTED, "get_handle")
    return None


def list_active() -> list[dict]:
    """Заглушка. В реальности: список активных торрентов."""
    log.warning(_NOT_IMPLEMENTED, "list_active")
    return []


def wait_metadata(handle, timeout: int = 60) -> bool:
    """Заглушка. В реальности: ждать получения метаданных торрента."""
    log.warning(_NOT_IMPLEMENTED, "wait_metadata")
    return False


def list_files(handle) -> list[dict]:
    """Заглушка. В реальности: список файлов внутри торрента."""
    log.warning(_NOT_IMPLEMENTED, "list_files")
    return []