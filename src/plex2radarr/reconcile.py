from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .config import AppConfig
from .models import PlanItem, PlexMovie, TorrentMatch
from .paths import PathMapper
from .plex import PlexClient
from .qbittorrent import QBittorrentClient
from .radarr import RadarrClient, RadarrError


class Reconciler:
    def __init__(
        self,
        config: AppConfig,
        plex: PlexClient | None = None,
        radarr: RadarrClient | None = None,
        qbits: list[QBittorrentClient] | None = None,
    ):
        self.config = config
        self.mapper = PathMapper(config.path_mappings)
        self.plex = plex or PlexClient(config.plex, self.mapper)
        self.radarr = radarr or RadarrClient(config.radarr)
        self.qbits = qbits or [
            QBittorrentClient(q, self.mapper) for q in config.qbittorrent
        ]
        self.qbit_by_name = {q.config.name: q for q in self.qbits}

    @staticmethod
    def _radarr_indexes(movies: list[dict]):
        tmdb = defaultdict(list)
        imdb = defaultdict(list)
        for movie in movies:
            if movie.get("tmdbId"):
                tmdb[int(movie["tmdbId"])].append(movie)
            if movie.get("imdbId"):
                imdb[str(movie["imdbId"])].append(movie)
        return tmdb, imdb

    @staticmethod
    def _existing(movie: PlexMovie, tmdb_index, imdb_index) -> list[dict]:
        if movie.ids.tmdb:
            return tmdb_index.get(movie.ids.tmdb, [])
        if movie.ids.imdb:
            return imdb_index.get(movie.ids.imdb, [])
        return []

    def _torrent_matches(self, path: Path) -> list[TorrentMatch]:
        matches: list[TorrentMatch] = []
        for qbit in self.qbits:
            matches.extend(qbit.find_matches(path))
        return matches

    def plan(self) -> list[PlanItem]:
        radarr_movies = self.radarr.movies()
        tmdb_index, imdb_index = self._radarr_indexes(radarr_movies)
        plans: list[PlanItem] = []

        for movie in self.plex.movies():
            if not movie.ids.tmdb and not movie.ids.imdb:
                plans.append(PlanItem(movie, "skip", "no usable TMDb or IMDb identifier"))
                continue

            existing = self._existing(movie, tmdb_index, imdb_index)
            if len(existing) == 1:
                plans.append(
                    PlanItem(
                        movie,
                        "skip",
                        "already exists in Radarr",
                        radarr_movie_id=existing[0].get("id"),
                    )
                )
                continue
            if len(existing) > 1:
                plans.append(PlanItem(movie, "skip", "ambiguous duplicate Radarr identity"))
                continue

            matches = self._torrent_matches(movie.file_path)
            if len(matches) > 1:
                plans.append(
                    PlanItem(movie, "skip", "multiple qBittorrent torrents own this file")
                )
                continue

            if len(matches) == 1:
                plans.append(
                    PlanItem(
                        movie,
                        "relocate_and_import",
                        "missing from Radarr; source is owned by qBittorrent",
                        torrent=matches[0],
                    )
                )
            else:
                plans.append(
                    PlanItem(
                        movie,
                        "import",
                        "missing from Radarr; no qBittorrent owner found",
                    )
                )

        return plans

    def execute(self, item: PlanItem) -> PlanItem:
        if item.action == "skip":
            return item
        if not item.movie.file_path.exists():
            item.action = "skip"
            item.reason = "source file does not exist on the local filesystem"
            return item

        source = item.movie.file_path

        if item.action == "relocate_and_import":
            assert item.torrent is not None
            qbit = self.qbit_by_name[item.torrent.client_name]
            qcfg = qbit.config
            relocation_root = Path(qcfg.relocation_root)
            if source.resolve(strict=False).is_relative_to(relocation_root.resolve(strict=False)):
                item.notes.append("source already under configured relocation root")
            else:
                qbit.set_location(item.torrent.torrent_hash, relocation_root)
                qbit.wait_for_save_path(item.torrent.torrent_hash, relocation_root)
                # Re-query exact payload path after qBittorrent has moved it.
                refreshed = qbit.find_matches(
                    self._find_relocated_by_basename(qbit, item.torrent.torrent_hash, source.name)
                )
                if refreshed:
                    source = refreshed[0].file_path
                else:
                    source = self._find_relocated_by_basename(
                        qbit, item.torrent.torrent_hash, source.name
                    )
                qbit.recheck(item.torrent.torrent_hash)
                item.notes.append(f"qBittorrent source relocated to {source}")

        added = self.radarr.add_movie(item.movie)
        movie_id = int(added["id"])
        item.radarr_movie_id = movie_id

        candidates = self.radarr.manual_import_candidates(source.parent, movie_id)
        exact = [c for c in candidates if Path(c.get("path", "")).name == source.name]
        if len(exact) != 1:
            raise RadarrError(
                f"Expected exactly one Radarr manual-import candidate for {source}, got {len(exact)}"
            )

        command = self.radarr.import_file(exact[0], movie_id)
        if command.get("id"):
            result = self.radarr.wait_for_command(int(command["id"]))
            if result.get("status") != "completed":
                raise RadarrError(
                    f"Radarr ManualImport failed for {item.movie.title}: {result}"
                )

        item.notes.append("Radarr manual import requested with importMode=copy")
        return item

    @staticmethod
    def _find_relocated_by_basename(
        qbit: QBittorrentClient, torrent_hash: str, basename: str
    ) -> Path:
        torrents = {t["hash"]: t for t in qbit.torrents()}
        torrent = torrents[torrent_hash]
        matches = []
        for f in qbit.files(torrent_hash):
            if Path(f["name"]).name == basename:
                matches.append(qbit._absolute_file(torrent, f["name"]))
        if len(matches) != 1:
            raise RuntimeError(
                f"Could not uniquely locate {basename} after qBittorrent relocation"
            )
        return matches[0]
