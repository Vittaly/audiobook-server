"""
Разбор структуры торрента на отдельные книги.

Правило: первый компонент rel_path — папка торрента (её создаёт libtorrent).
Всё, что глубже — структура самой раздачи.

Если файлы лежат плоско — торрент содержит одну книгу.
Если есть вложенные папки — каждая папка = отдельная книга.
"""

import logging
import re
from collections import defaultdict
from pathlib import Path

log = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def split_torrent_into_books(files: list[dict],
                             torrent_name: str) -> list[dict]:
    """
    files: список файлов торрента, каждый с полями:
        index, rel_path, size_bytes, file_offset

    Возвращает список книг:
        [{"title", "folder", "audio": [...], "cover", "size_bytes"}, ...]
    """
    trimmed = _trim_torrent_root(files)
    depths = {len(f["sub_path"].parts) for f in trimmed}

    if depths == {1}:
        audio = [f for f in trimmed if _is_audio(f)]
        if not audio:
            log.warning("Торрент %s не содержит аудио", torrent_name)
            return []
        return [{
            "title": None,
            "folder": None,
            "audio": _normalize_audio(audio),
            "cover": _pick_cover(trimmed),
            "size_bytes": sum(f["size_bytes"] for f in audio),
        }]

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
            "audio": _normalize_audio(audio),
            "cover": _pick_cover(folder_files),
            "size_bytes": sum(f["size_bytes"] for f in audio),
        })

    if len(books) == 1:
        books[0]["folder"] = None
        books[0]["title"] = None

    return books


def _trim_torrent_root(files: list[dict]) -> list[dict]:
    result = []
    for f in files:
        parts = Path(f["rel_path"]).parts
        sub = Path(*parts[1:]) if len(parts) > 1 else Path(parts[0])
        result.append({**f, "sub_path": sub})
    return result


def _normalize_audio(audio: list[dict]) -> list[dict]:
    audio_sorted = sorted(audio, key=lambda f: _natural(f["sub_path"].name))
    return [
        {
            "index": f["index"],
            "rel_path": f["rel_path"],
            "size_bytes": f["size_bytes"],
            "file_offset": f.get("file_offset", 0),
        }
        for f in audio_sorted
    ]


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