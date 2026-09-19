from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ExternalIds:
    tmdb: int | None = None
    imdb: str | None = None


@dataclass(frozen=True)
class PlexMovie:
    title: str
    year: int | None
    ids: ExternalIds
    file_path: Path


@dataclass(frozen=True)
class TorrentMatch:
    client_name: str
    torrent_hash: str
    torrent_name: str
    file_path: Path
    save_path: Path
    progress: float


@dataclass
class PlanItem:
    movie: PlexMovie
    action: str
    reason: str
    torrent: TorrentMatch | None = None
    radarr_movie_id: int | None = None
    notes: list[str] = field(default_factory=list)
