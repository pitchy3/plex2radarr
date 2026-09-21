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
class PlexScanIssue:
    title: str
    year: int | None
    ids: ExternalIds
    file_paths: tuple[Path, ...]
    reason: str


@dataclass(frozen=True)
class PlexScanStats:
    total_movies: int
    eligible_movies: int
    outside_root_movies: int
    multiple_applicable_files: int
    no_media_movies: int


@dataclass(frozen=True)
class PlexScanResult:
    movies: tuple[PlexMovie, ...]
    issues: tuple[PlexScanIssue, ...]
    all_files: tuple[PlexMovie, ...]
    stats: PlexScanStats


@dataclass(frozen=True)
class TorrentMatch:
    client_name: str
    torrent_hash: str
    torrent_name: str
    file_path: Path
    save_path: Path
    progress: float
    relative_path: Path | None = None


@dataclass
class PlanItem:
    movie: PlexMovie
    action: str
    reason: str
    torrent: TorrentMatch | None = None
    radarr_movie_id: int | None = None
    source_path: Path | None = None
    transaction_key: str | None = None
    notes: list[str] = field(default_factory=list)
