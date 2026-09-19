from __future__ import annotations

from pathlib import Path

from .config import PathMapping


class PathMapper:
    def __init__(self, mappings: tuple[PathMapping, ...]):
        self._mappings = mappings

    def to_local(self, service: str, path: str | Path) -> Path:
        text = str(path)
        matches = [
            m for m in self._mappings
            if m.service == service and (text == m.remote or text.startswith(m.remote.rstrip("/") + "/"))
        ]
        if not matches:
            return Path(text)
        mapping = max(matches, key=lambda m: len(m.remote))
        suffix = text[len(mapping.remote):].lstrip("/")
        return Path(mapping.local) / suffix if suffix else Path(mapping.local)
