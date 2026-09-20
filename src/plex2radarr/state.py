from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import PlexMovie, TorrentMatch


@dataclass(frozen=True)
class Transaction:
    key: str
    title: str
    year: int | None
    tmdb_id: int | None
    imdb_id: str | None
    original_path: Path
    action: str
    stage: str
    qbit_name: str | None = None
    torrent_hash: str | None = None
    torrent_name: str | None = None
    torrent_relative_path: Path | None = None
    current_source: Path | None = None
    radarr_movie_id: int | None = None

    @classmethod
    def from_dict(cls, key: str, data: dict[str, Any]) -> Transaction:
        return cls(
            key=key,
            title=data["title"],
            year=data.get("year"),
            tmdb_id=data.get("tmdb_id"),
            imdb_id=data.get("imdb_id"),
            original_path=Path(data["original_path"]),
            action=data["action"],
            stage=data["stage"],
            qbit_name=data.get("qbit_name"),
            torrent_hash=data.get("torrent_hash"),
            torrent_name=data.get("torrent_name"),
            torrent_relative_path=(
                Path(data["torrent_relative_path"])
                if data.get("torrent_relative_path")
                else None
            ),
            current_source=(
                Path(data["current_source"]) if data.get("current_source") else None
            ),
            radarr_movie_id=data.get("radarr_movie_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "year": self.year,
            "tmdb_id": self.tmdb_id,
            "imdb_id": self.imdb_id,
            "original_path": str(self.original_path),
            "action": self.action,
            "stage": self.stage,
            "qbit_name": self.qbit_name,
            "torrent_hash": self.torrent_hash,
            "torrent_name": self.torrent_name,
            "torrent_relative_path": (
                str(self.torrent_relative_path) if self.torrent_relative_path else None
            ),
            "current_source": str(self.current_source) if self.current_source else None,
            "radarr_movie_id": self.radarr_movie_id,
        }


class StateStore:
    VERSION = 1

    def __init__(self, path: str | Path = ".plex2radarr-state.json"):
        self.path = Path(path)
        self._transactions: dict[str, Transaction] = {}
        self._load()

    @staticmethod
    def key_for_movie(movie: PlexMovie) -> str:
        if movie.ids.tmdb:
            return f"tmdb:{movie.ids.tmdb}"
        if movie.ids.imdb:
            return f"imdb:{movie.ids.imdb}"
        raise ValueError(f"{movie.title} has no stable external ID")

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text())
        if raw.get("version") != self.VERSION:
            raise ValueError(
                f"Unsupported state file version: {raw.get('version')!r}"
            )
        self._transactions = {
            key: Transaction.from_dict(key, value)
            for key, value in raw.get("transactions", {}).items()
        }

    def _write(self) -> None:
        payload = {
            "version": self.VERSION,
            "transactions": {
                key: transaction.to_dict()
                for key, transaction in sorted(self._transactions.items())
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(self.path)

    def all(self) -> list[Transaction]:
        return list(self._transactions.values())

    def get(self, key: str) -> Transaction | None:
        return self._transactions.get(key)

    def begin(
        self,
        movie: PlexMovie,
        action: str,
        torrent: TorrentMatch | None,
        radarr_movie_id: int | None,
        source: Path,
    ) -> Transaction:
        key = self.key_for_movie(movie)
        existing = self.get(key)
        if existing:
            return existing
        transaction = Transaction(
            key=key,
            title=movie.title,
            year=movie.year,
            tmdb_id=movie.ids.tmdb,
            imdb_id=movie.ids.imdb,
            original_path=movie.file_path,
            action=action,
            stage="planned",
            qbit_name=torrent.client_name if torrent else None,
            torrent_hash=torrent.torrent_hash if torrent else None,
            torrent_name=torrent.torrent_name if torrent else None,
            torrent_relative_path=torrent.relative_path if torrent else None,
            current_source=source,
            radarr_movie_id=radarr_movie_id,
        )
        self._transactions[key] = transaction
        self._write()
        return transaction

    def update(self, key: str, **changes: Any) -> Transaction:
        current = self._transactions[key]
        data = current.to_dict()
        data.update(changes)
        transaction = Transaction.from_dict(key, data)
        self._transactions[key] = transaction
        self._write()
        return transaction

    def remove(self, key: str) -> None:
        if key not in self._transactions:
            return
        del self._transactions[key]
        if self._transactions:
            self._write()
        elif self.path.exists():
            self.path.unlink()
