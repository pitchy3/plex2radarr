from __future__ import annotations

from pathlib import Path

from .config import PathMapping


class PathMappingError(ValueError):
    pass


class PathMapper:
    def __init__(self, mappings: tuple[PathMapping, ...]):
        self._mappings = mappings

    def _service_mappings(self, service: str) -> list[PathMapping]:
        return [mapping for mapping in self._mappings if mapping.service == service]

    @staticmethod
    def _matches_prefix(path: str, prefix: str) -> bool:
        return path == prefix or path.startswith(prefix.rstrip("/") + "/")

    def to_local(self, service: str, path: str | Path) -> Path:
        text = str(path)
        matches = [
            m
            for m in self._mappings
            if m.service == service and self._matches_prefix(text, m.remote)
        ]
        if not matches:
            return Path(text)
        mapping = max(matches, key=lambda m: len(m.remote))
        suffix = text[len(mapping.remote):].lstrip("/")
        return Path(mapping.local) / suffix if suffix else Path(mapping.local)

    def to_local_checked(self, service: str, path: str | Path) -> Path:
        text = str(path)
        service_mappings = self._service_mappings(service)
        if not service_mappings:
            return Path(text)
        matches = [m for m in service_mappings if self._matches_prefix(text, m.remote)]
        if not matches:
            raise PathMappingError(
                f"{service} path {text!r} is not covered by any configured remote path mapping"
            )
        mapping = max(matches, key=lambda m: len(m.remote))
        suffix = text[len(mapping.remote):].lstrip("/")
        return Path(mapping.local) / suffix if suffix else Path(mapping.local)

    def to_remote(self, service: str, path: str | Path) -> Path:
        text = str(path)
        matches = [
            m
            for m in self._mappings
            if m.service == service and self._matches_prefix(text, m.local)
        ]
        if not matches:
            return Path(text)
        mapping = max(matches, key=lambda m: len(m.local))
        suffix = text[len(mapping.local):].lstrip("/")
        return Path(mapping.remote) / suffix if suffix else Path(mapping.remote)

    def to_remote_checked(self, service: str, path: str | Path) -> Path:
        text = str(path)
        service_mappings = self._service_mappings(service)
        if not service_mappings:
            return Path(text)
        matches = [m for m in service_mappings if self._matches_prefix(text, m.local)]
        if not matches:
            raise PathMappingError(
                f"{service} local path {text!r} is not covered by any configured local path mapping"
            )
        mapping = max(matches, key=lambda m: len(m.local))
        suffix = text[len(mapping.local):].lstrip("/")
        return Path(mapping.remote) / suffix if suffix else Path(mapping.remote)
