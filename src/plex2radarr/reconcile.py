from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from .config import AppConfig
from .models import PlanItem, PlexMovie, TorrentMatch
from .paths import PathMapper
from .plex import PlexClient
from .qbittorrent import QBittorrentClient
from .radarr import RadarrClient, RadarrError


class SelectionError(ValueError):
    pass


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
        self.qbits = qbits or [QBittorrentClient(q, self.mapper) for q in config.qbittorrent]
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

    @staticmethod
    def _select_movies(
        movies: list[PlexMovie], selected_paths: Iterable[Path] | None
    ) -> list[PlexMovie]:
        if selected_paths is None:
            return movies

        requested = {
            path.expanduser().resolve(strict=False): path
            for path in selected_paths
        }
        selected: list[PlexMovie] = []
        matched: set[Path] = set()

        for movie in movies:
            normalized = movie.file_path.resolve(strict=False)
            if normalized in requested:
                selected.append(movie)
                matched.add(normalized)

        missing = [requested[path] for path in requested.keys() - matched]
        if missing:
            formatted = "\n".join(f"  - {path}" for path in sorted(missing, key=str))
            raise SelectionError(
                "Requested file(s) were not found in the configured Plex library:\n"
                f"{formatted}"
            )

        return selected

    def _torrent_matches(self, path: Path) -> list[TorrentMatch]:
        matches: list[TorrentMatch] = []
        for qbit in self.qbits:
            matches.extend(qbit.find_matches(path))
        return matches

    def _inside_radarr_root(self, path: Path) -> bool:
        root = self.mapper.to_local("radarr", self.config.radarr.root_folder)
        return path.resolve(strict=False).is_relative_to(root.resolve(strict=False))

    def plan(self, selected_paths: Iterable[Path] | None = None) -> list[PlanItem]:
        radarr_movies = self.radarr.movies()
        tmdb_index, imdb_index = self._radarr_indexes(radarr_movies)
        plex_movies = self._select_movies(self.plex.movies(), selected_paths)
        plans: list[PlanItem] = []

        for movie in plex_movies:
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
            elif self._inside_radarr_root(movie.file_path):
                plans.append(
                    PlanItem(
                        movie,
                        "move_import",
                        "missing from Radarr; unseeded legacy file is inside the library root",
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
        import_mode = "move" if item.action == "move_import" else "copy"

        if item.action == "relocate_and_import":
            assert item.torrent is not None
            qbit = self.qbit_by_name[item.torrent.client_name]
            remote_relocation_root = Path(qbit.config.relocation_root)
            local_relocation_root = self.mapper.to_local(
                f"qbittorrent:{qbit.config.name}", remote_relocation_root
            )
            if source.resolve(strict=False).is_relative_to(
                local_relocation_root.resolve(strict=False)
            ):
                item.notes.append("source already under configured relocation root")
            else:
                qbit.set_location(item.torrent.torrent_hash, remote_relocation_root)
                relocated_torrent = qbit.wait_for_save_path(
                    item.torrent.torrent_hash, local_relocation_root
                )
                refreshed = qbit.refresh_torrent(
                    item.torrent.torrent_hash, torrent=relocated_torrent
                )
                relocated_files = [
                    match.file_path
                    for match in refreshed
                    if match.file_path.name == source.name
                ]
                if len(relocated_files) != 1:
                    raise RuntimeError(
                        f"Could not uniquely locate {source.name} after "
                        "qBittorrent relocation"
                    )
                source = relocated_files[0]
                item.notes.append(f"qBittorrent source relocated to {source}")

        added = self.radarr.add_movie(item.movie)
        movie_id = int(added["id"])
        item.radarr_movie_id = movie_id

        radarr_folder = self.mapper.to_remote("radarr", source.parent)
        candidates = self.radarr.manual_import_candidates(radarr_folder, movie_id)
        exact = [c for c in candidates if Path(c.get("path", "")).name == source.name]
        if len(exact) != 1:
            raise RadarrError(
                f"Expected exactly one Radarr manual-import candidate for {source}, got {len(exact)}"
            )

        command = self.radarr.import_file(exact[0], movie_id, mode=import_mode)
        if command.get("id"):
            result = self.radarr.wait_for_command(int(command["id"]))
            if result.get("status") != "completed":
                raise RadarrError(
                    f"Radarr ManualImport failed for {item.movie.title}: {result}"
                )

        item.notes.append(f"Radarr manual import requested with importMode={import_mode}")
        return item
