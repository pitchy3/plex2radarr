from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from .config import AppConfig
from .models import (
    ExternalIds,
    PlanItem,
    PlexMovie,
    PlexScanIssue,
    PlexScanStats,
    TorrentMatch,
)
from .paths import PathMapper
from .plex import PlexClient
from .qbittorrent import QBittorrentClient
from .radarr import RadarrClient, RadarrError
from .state import StateStore, Transaction


class SelectionError(ValueError):
    pass


class Reconciler:
    def __init__(
        self,
        config: AppConfig,
        plex: PlexClient | None = None,
        radarr: RadarrClient | None = None,
        qbits: list[QBittorrentClient] | None = None,
        state: StateStore | None = None,
    ):
        self.config = config
        self.mapper = PathMapper(config.path_mappings)
        self.plex = plex or PlexClient(config.plex, self.mapper)
        self.radarr = radarr or RadarrClient(config.radarr)
        self.qbits = (
            qbits
            if qbits is not None
            else [QBittorrentClient(q, self.mapper) for q in config.qbittorrent]
        )
        self.qbit_by_name = {q.config.name: q for q in self.qbits}
        self.state = state or StateStore()
        self.plex_scan_stats: PlexScanStats | None = None


    def _plex_scan(self) -> tuple[list[PlexMovie], list[PlexScanIssue]]:
        if not hasattr(self.plex, "scan"):
            movies = list(self.plex.movies())
            self.plex_scan_stats = PlexScanStats(
                total_movies=len(movies),
                eligible_movies=len(movies),
                outside_root_movies=0,
                multiple_applicable_files=0,
                no_media_movies=0,
            )
            return movies, []

        radarr_local_root = self.mapper.to_local_checked(
            "radarr", self.config.radarr.root_folder
        )
        scan = self.plex.scan(radarr_local_root)
        self.plex_scan_stats = scan.stats
        return list(scan.movies), list(scan.issues)

    @staticmethod
    def _issue_plan(issue: PlexScanIssue, source: Path | None = None) -> PlanItem:
        path = source or issue.file_paths[0]
        item = PlanItem(
            movie=PlexMovie(
                title=issue.title,
                year=issue.year,
                ids=issue.ids,
                file_path=path,
            ),
            action="skip",
            reason=issue.reason,
        )
        item.notes.extend(f"Plex file: {file_path}" for file_path in issue.file_paths)
        return item

    @staticmethod
    def _radarr_indexes(movies: list[dict]):
        tmdb = defaultdict(list)
        imdb = defaultdict(list)
        by_id = {}
        for movie in movies:
            if movie.get("tmdbId"):
                tmdb[int(movie["tmdbId"])].append(movie)
            if movie.get("imdbId"):
                imdb[str(movie["imdbId"])].append(movie)
            if movie.get("id") is not None:
                by_id[int(movie["id"])] = movie
        return tmdb, imdb, by_id

    @staticmethod
    def _existing(movie: PlexMovie, tmdb_index, imdb_index) -> list[dict]:
        if movie.ids.tmdb:
            return tmdb_index.get(movie.ids.tmdb, [])
        if movie.ids.imdb:
            return imdb_index.get(movie.ids.imdb, [])
        return []

    @staticmethod
    def _movie_from_transaction(transaction: Transaction) -> PlexMovie:
        return PlexMovie(
            title=transaction.title,
            year=transaction.year,
            ids=ExternalIds(
                tmdb=transaction.tmdb_id,
                imdb=transaction.imdb_id,
            ),
            file_path=transaction.original_path,
        )

    @staticmethod
    def _transaction_matches_selection(
        transaction: Transaction, requested: set[Path]
    ) -> bool:
        candidates = {transaction.original_path.resolve(strict=False)}
        if transaction.current_source:
            candidates.add(transaction.current_source.resolve(strict=False))
        return bool(candidates & requested)

    def _select_recovery_transactions(
        self, selected_paths: Iterable[Path] | None
    ) -> tuple[list[Transaction], set[Path]]:
        transactions = self.state.all()
        if selected_paths is None:
            return transactions, set()

        requested = {
            path.expanduser().resolve(strict=False)
            for path in selected_paths
        }
        selected = [
            transaction
            for transaction in transactions
            if self._transaction_matches_selection(transaction, requested)
        ]
        matched: set[Path] = set()
        for transaction in selected:
            if transaction.original_path.resolve(strict=False) in requested:
                matched.add(transaction.original_path.resolve(strict=False))
            if (
                transaction.current_source
                and transaction.current_source.resolve(strict=False) in requested
            ):
                matched.add(transaction.current_source.resolve(strict=False))
        return selected, matched

    @staticmethod
    def _select_movies(
        movies: list[PlexMovie],
        selected_paths: Iterable[Path] | None,
        already_matched: set[Path],
    ) -> tuple[list[PlexMovie], set[Path]]:
        if selected_paths is None:
            return movies, set()

        requested = {
            path.expanduser().resolve(strict=False): path
            for path in selected_paths
        }
        selected: list[PlexMovie] = []
        matched = set(already_matched)

        for movie in movies:
            normalized = movie.file_path.resolve(strict=False)
            if normalized in requested:
                selected.append(movie)
                matched.add(normalized)

        missing = [requested[path] for path in requested.keys() - matched]
        if missing:
            formatted = "\n".join(f"  - {path}" for path in sorted(missing, key=str))
            raise SelectionError(
                "Requested file(s) were not found in Plex or the recovery journal:\n"
                f"{formatted}"
            )

        return selected, matched

    def _torrent_matches(self, path: Path) -> list[TorrentMatch]:
        matches: list[TorrentMatch] = []
        for qbit in self.qbits:
            matches.extend(qbit.find_matches(path))
        return matches

    def _torrent_for_transaction(self, transaction: Transaction) -> TorrentMatch | None:
        if not transaction.qbit_name or not transaction.torrent_hash:
            return None
        qbit = self.qbit_by_name.get(transaction.qbit_name)
        if qbit is None:
            raise SelectionError(
                f"Recovery transaction {transaction.key} references unknown "
                f"qBittorrent instance {transaction.qbit_name!r}"
            )
        matches = qbit.matches_for_hash(transaction.torrent_hash)
        if transaction.torrent_relative_path:
            exact = [
                match
                for match in matches
                if match.relative_path == transaction.torrent_relative_path
            ]
            if len(exact) == 1:
                return exact[0]
        if transaction.current_source:
            exact = [
                match
                for match in matches
                if match.file_path.resolve(strict=False)
                == transaction.current_source.resolve(strict=False)
            ]
            if len(exact) == 1:
                return exact[0]
        if len(matches) == 1:
            return matches[0]
        raise SelectionError(
            f"Could not uniquely recover torrent file for {transaction.title}"
        )

    def _inside_radarr_root(self, path: Path) -> bool:
        root = self.mapper.to_local_checked("radarr", self.config.radarr.root_folder)
        return path.resolve(strict=False).is_relative_to(root.resolve(strict=False))

    def _plan_recovery(
        self,
        transaction: Transaction,
        tmdb_index,
        imdb_index,
        radarr_by_id,
    ) -> PlanItem:
        movie = self._movie_from_transaction(transaction)
        existing = None
        if transaction.radarr_movie_id:
            existing = radarr_by_id.get(transaction.radarr_movie_id)
        if existing is None:
            matches = self._existing(movie, tmdb_index, imdb_index)
            if len(matches) == 1:
                existing = matches[0]

        if existing and existing.get("hasFile"):
            return PlanItem(
                movie=movie,
                action="finalize_recovery",
                reason="recovery journal exists but Radarr already has the movie file",
                radarr_movie_id=int(existing["id"]),
                source_path=transaction.current_source,
                transaction_key=transaction.key,
            )

        torrent = self._torrent_for_transaction(transaction)
        source = torrent.file_path if torrent else transaction.current_source
        if source is None:
            source = transaction.original_path

        return PlanItem(
            movie=movie,
            action=transaction.action,
            reason=f"resume interrupted reconciliation from stage {transaction.stage}",
            torrent=torrent,
            radarr_movie_id=(
                int(existing["id"])
                if existing
                else transaction.radarr_movie_id
            ),
            source_path=source,
            transaction_key=transaction.key,
        )

    def plan(self, selected_paths: Iterable[Path] | None = None) -> list[PlanItem]:
        radarr_movies = self.radarr.movies()
        tmdb_index, imdb_index, radarr_by_id = self._radarr_indexes(radarr_movies)

        recovery_transactions, recovery_matched = self._select_recovery_transactions(
            selected_paths
        )
        recovery_keys = {transaction.key for transaction in recovery_transactions}
        plans = [
            self._plan_recovery(
                transaction,
                tmdb_index,
                imdb_index,
                radarr_by_id,
            )
            for transaction in recovery_transactions
        ]

        all_plex_movies, plex_issues = self._plex_scan()

        selected_issue_plans: list[PlanItem] = []
        issue_matched = set(recovery_matched)
        if selected_paths is None:
            selected_issue_plans = [self._issue_plan(issue) for issue in plex_issues]
        else:
            requested = {
                path.expanduser().resolve(strict=False)
                for path in selected_paths
            }
            for issue in plex_issues:
                matches = [
                    path
                    for path in issue.file_paths
                    if path.resolve(strict=False) in requested
                ]
                if matches:
                    selected_issue_plans.append(self._issue_plan(issue, matches[0]))
                    issue_matched.update(
                        path.resolve(strict=False) for path in matches
                    )

        plex_movies, _ = self._select_movies(
            all_plex_movies,
            selected_paths,
            issue_matched,
        )
        plans.extend(selected_issue_plans)

        for movie in plex_movies:
            if not movie.ids.tmdb and not movie.ids.imdb:
                plans.append(PlanItem(movie, "skip", "no usable TMDb or IMDb identifier"))
                continue

            key = self.state.key_for_movie(movie)
            if key in recovery_keys:
                continue

            existing = self._existing(movie, tmdb_index, imdb_index)
            if len(existing) > 1:
                plans.append(PlanItem(movie, "skip", "ambiguous duplicate Radarr identity"))
                continue

            radarr_movie_id = None
            if len(existing) == 1:
                if existing[0].get("hasFile"):
                    plans.append(
                        PlanItem(
                            movie,
                            "skip",
                            "already exists in Radarr with a movie file",
                            radarr_movie_id=existing[0].get("id"),
                        )
                    )
                    continue
                radarr_movie_id = int(existing[0]["id"])

            matches = self._torrent_matches(movie.file_path)
            if len(matches) > 1:
                item = PlanItem(
                    movie,
                    "skip",
                    "multiple qBittorrent torrents own this file",
                )
                for match in sorted(
                    matches,
                    key=lambda value: (
                        value.client_name,
                        value.torrent_name,
                        value.torrent_hash,
                    ),
                ):
                    item.notes.append(
                        "qBittorrent owner: "
                        f"{match.client_name} / {match.torrent_name} "
                        f"[{match.torrent_hash[:12]}]"
                    )
                plans.append(item)
                continue

            if len(matches) == 1:
                reason = "source is owned by qBittorrent"
                if radarr_movie_id:
                    reason += "; reuse existing missing Radarr movie"
                plans.append(
                    PlanItem(
                        movie,
                        "relocate_and_import",
                        reason,
                        torrent=matches[0],
                        radarr_movie_id=radarr_movie_id,
                    )
                )
            elif self._inside_radarr_root(movie.file_path):
                reason = "unseeded legacy file is inside the library root"
                if radarr_movie_id:
                    reason += "; reuse existing missing Radarr movie"
                plans.append(
                    PlanItem(
                        movie,
                        "move_import",
                        reason,
                        radarr_movie_id=radarr_movie_id,
                    )
                )
            else:
                reason = "no qBittorrent owner found"
                if radarr_movie_id:
                    reason += "; reuse existing missing Radarr movie"
                plans.append(
                    PlanItem(
                        movie,
                        "import",
                        reason,
                        radarr_movie_id=radarr_movie_id,
                    )
                )

        return plans

    def _preflight(self, item: PlanItem) -> tuple[Path, Path]:
        source = item.source_path or item.movie.file_path

        if item.action == "relocate_and_import" and item.torrent:
            qbit = self.qbit_by_name[item.torrent.client_name]
            remote_relocation_root = Path(qbit.config.relocation_root)
            local_relocation_root = self.mapper.to_local_checked(
                f"qbittorrent:{qbit.config.name}",
                remote_relocation_root,
            )
            if source.resolve(strict=False).is_relative_to(
                local_relocation_root.resolve(strict=False)
            ):
                future_source = source
            else:
                if item.torrent.relative_path is None:
                    raise ValueError(
                        f"Cannot predict relocated path for {item.movie.title}: "
                        "qBittorrent relative file path is unavailable"
                    )
                future_source = local_relocation_root / item.torrent.relative_path
        else:
            future_source = source

        radarr_folder = self.mapper.to_remote_checked("radarr", future_source.parent)
        return future_source, radarr_folder

    def preflight(self, items: list[PlanItem]) -> None:
        for item in items:
            if item.action in {"skip", "finalize_recovery"}:
                continue
            future_source, radarr_folder = self._preflight(item)
            item.notes.append(f"preflight source: {future_source}")
            item.notes.append(f"Radarr import folder: {radarr_folder}")

    def _journal_torrent_companions(
        self,
        item: PlanItem,
        qbit: QBittorrentClient,
        local_relocation_root: Path,
    ) -> None:
        assert item.torrent is not None
        radarr_movies = self.radarr.movies()
        tmdb_index, imdb_index, _ = self._radarr_indexes(radarr_movies)

        companions: list[tuple[PlexMovie, TorrentMatch, int | None, Path]] = []
        plex_movies, plex_issues = self._plex_scan()
        for issue in plex_issues:
            for path in issue.file_paths:
                issue_matches = [
                    match
                    for match in qbit.find_matches(path)
                    if match.torrent_hash == item.torrent.torrent_hash
                ]
                if issue_matches:
                    raise SelectionError(
                        f"Cannot safely relocate torrent {item.torrent.torrent_name}: "
                        f"{issue.title} has multiple Plex files in the configured "
                        "Radarr root"
                    )

        for movie in plex_movies:
            matches = [
                match
                for match in qbit.find_matches(movie.file_path)
                if match.torrent_hash == item.torrent.torrent_hash
            ]
            if not matches:
                continue
            if len(matches) != 1:
                raise SelectionError(
                    f"Cannot safely relocate torrent {item.torrent.torrent_name}: "
                    f"{movie.title} has ambiguous torrent ownership"
                )
            match = matches[0]
            if not movie.ids.tmdb and not movie.ids.imdb:
                raise SelectionError(
                    f"Cannot safely relocate torrent {item.torrent.torrent_name}: "
                    f"{movie.title} has no stable TMDb/IMDb ID for recovery"
                )

            existing = self._existing(movie, tmdb_index, imdb_index)
            if len(existing) > 1:
                raise SelectionError(
                    f"Cannot safely relocate torrent {item.torrent.torrent_name}: "
                    f"{movie.title} has ambiguous duplicate Radarr identity"
                )
            radarr_movie_id = None
            if len(existing) == 1:
                if existing[0].get("hasFile"):
                    raise SelectionError(
                        f"Cannot safely relocate torrent {item.torrent.torrent_name}: "
                        f"{movie.title} is already managed by Radarr with a file"
                    )
                radarr_movie_id = int(existing[0]["id"])

            if match.relative_path is None:
                raise SelectionError(
                    f"Cannot safely relocate torrent {item.torrent.torrent_name}: "
                    f"{movie.title} has no qBittorrent relative path"
                )
            future_source = local_relocation_root / match.relative_path
            self.mapper.to_remote_checked("radarr", future_source.parent)
            companions.append((movie, match, radarr_movie_id, future_source))

        current_key = self.state.key_for_movie(item.movie)
        companion_keys = {self.state.key_for_movie(movie) for movie, _, _, _ in companions}
        if current_key not in companion_keys:
            raise SelectionError(
                f"Cannot safely relocate torrent {item.torrent.torrent_name}: "
                "the selected movie was not found among the torrent's Plex files"
            )

        for movie, match, radarr_movie_id, _future_source in companions:
            key = self.state.key_for_movie(movie)
            if key == current_key:
                continue
            self.state.begin(
                movie=movie,
                action="relocate_and_import",
                torrent=match,
                radarr_movie_id=radarr_movie_id,
                source=movie.file_path,
            )

    def execute(self, item: PlanItem) -> PlanItem:
        if item.action == "skip":
            return item

        if item.action == "finalize_recovery":
            if item.transaction_key:
                self.state.remove(item.transaction_key)
            item.notes.append("cleared completed recovery transaction")
            return item

        self._preflight(item)
        source = item.source_path or item.movie.file_path

        if not source.exists() and not (
            item.action == "relocate_and_import" and item.torrent
        ):
            raise FileNotFoundError(f"Source file does not exist: {source}")

        transaction = self.state.begin(
            movie=item.movie,
            action=item.action,
            torrent=item.torrent,
            radarr_movie_id=item.radarr_movie_id,
            source=source,
        )
        item.transaction_key = transaction.key

        import_mode = "move" if item.action == "move_import" else "copy"

        if item.action == "relocate_and_import":
            assert item.torrent is not None
            qbit = self.qbit_by_name[item.torrent.client_name]
            remote_relocation_root = Path(qbit.config.relocation_root)
            local_relocation_root = self.mapper.to_local_checked(
                f"qbittorrent:{qbit.config.name}",
                remote_relocation_root,
            )
            if source.resolve(strict=False).is_relative_to(
                local_relocation_root.resolve(strict=False)
            ):
                item.notes.append("source already under configured relocation root")
            else:
                self._journal_torrent_companions(
                    item,
                    qbit,
                    local_relocation_root,
                )
                qbit.set_location(item.torrent.torrent_hash, remote_relocation_root)
                relocated_torrent = qbit.wait_for_save_path(
                    item.torrent.torrent_hash, local_relocation_root
                )
                refreshed = qbit.refresh_torrent(
                    item.torrent.torrent_hash, torrent=relocated_torrent
                )
                exact = [
                    match
                    for match in refreshed
                    if match.relative_path == item.torrent.relative_path
                ]
                if len(exact) != 1:
                    raise RuntimeError(
                        f"Could not uniquely locate {item.movie.title} "
                        "after qBittorrent relocation"
                    )
                source = exact[0].file_path
                item.torrent = exact[0]
                item.notes.append(f"qBittorrent source relocated to {source}")

            self.state.update(
                transaction.key,
                stage="torrent_relocated",
                current_source=str(source),
            )

        movie_id = item.radarr_movie_id
        if movie_id is None:
            added = self.radarr.add_movie(item.movie)
            movie_id = int(added["id"])
            item.radarr_movie_id = movie_id
            self.state.update(
                transaction.key,
                stage="radarr_movie_added",
                radarr_movie_id=movie_id,
                current_source=str(source),
            )
        else:
            self.state.update(
                transaction.key,
                radarr_movie_id=movie_id,
                current_source=str(source),
            )
            item.notes.append(f"reusing existing Radarr movie ID {movie_id}")

        transaction = self.state.get(transaction.key) or transaction
        if transaction.radarr_command_id is not None:
            result = self.radarr.wait_for_command(transaction.radarr_command_id)
            if result.get("status") != "completed":
                raise RadarrError(
                    f"Radarr ManualImport failed for {item.movie.title}: {result}"
                )
        else:
            radarr_folder = self.mapper.to_remote_checked("radarr", source.parent)
            candidates = self.radarr.manual_import_candidates(radarr_folder)
            exact = [c for c in candidates if Path(c.get("path", "")).name == source.name]
            if len(exact) != 1:
                raise RadarrError(
                    f"Expected exactly one Radarr manual-import candidate for {source}, "
                    f"got {len(exact)}"
                )

            command = self.radarr.import_file(exact[0], movie_id, mode=import_mode)
            if command.get("id"):
                command_id = int(command["id"])
                self.state.update(
                    transaction.key,
                    stage="radarr_import_submitted",
                    current_source=str(source),
                    radarr_movie_id=movie_id,
                    radarr_command_id=command_id,
                )
                result = self.radarr.wait_for_command(command_id)
                if result.get("status") != "completed":
                    raise RadarrError(
                        f"Radarr ManualImport failed for {item.movie.title}: {result}"
                    )

        radarr_movie = self.radarr.movie(movie_id)
        if not radarr_movie.get("hasFile"):
            raise RadarrError(
                f"Radarr import completed but movie {movie_id} still has no file"
            )

        self.state.update(
            transaction.key,
            stage="imported",
            current_source=str(source),
            radarr_movie_id=movie_id,
        )
        self.state.remove(transaction.key)
        item.notes.append(f"Radarr manual import completed with importMode={import_mode}")
        item.notes.append("recovery transaction completed and removed")
        return item
