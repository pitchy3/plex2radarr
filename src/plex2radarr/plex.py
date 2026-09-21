from __future__ import annotations

from pathlib import Path

from plexapi.server import PlexServer

from .config import PlexConfig
from .models import (
    ExternalIds,
    PlexMovie,
    PlexScanIssue,
    PlexScanResult,
    PlexScanStats,
)
from .paths import PathMapper


class PlexClient:
    MULTIPLE_APPLICABLE_REASON = "multiple Plex files in configured Radarr root"

    def __init__(self, config: PlexConfig, mapper: PathMapper):
        self.config = config
        self.mapper = mapper
        self.server = PlexServer(config.url.rstrip("/"), config.token)

    @staticmethod
    def _ids(item) -> ExternalIds:
        tmdb = None
        imdb = None
        for guid in getattr(item, "guids", []) or []:
            value = getattr(guid, "id", "")
            if value.startswith("tmdb://"):
                try:
                    tmdb = int(value.split("://", 1)[1])
                except ValueError:
                    pass
            elif value.startswith("imdb://"):
                imdb = value.split("://", 1)[1]
        value = getattr(item, "guid", "") or ""
        if tmdb is None and value.startswith("tmdb://"):
            try:
                tmdb = int(value.split("://", 1)[1])
            except ValueError:
                pass
        if imdb is None and value.startswith("imdb://"):
            imdb = value.split("://", 1)[1]
        return ExternalIds(tmdb=tmdb, imdb=imdb)

    def scan(self, radarr_local_root: Path) -> PlexScanResult:
        section = self.server.library.section(self.config.library)
        items = list(section.all())
        root = radarr_local_root.resolve(strict=False)

        movies: list[PlexMovie] = []
        issues: list[PlexScanIssue] = []
        all_files: list[PlexMovie] = []
        outside_root_movies = 0
        multiple_applicable_files = 0
        no_media_movies = 0

        for item in items:
            files: list[str] = []
            for media in getattr(item, "media", []) or []:
                for part in getattr(media, "parts", []) or []:
                    if getattr(part, "file", None):
                        files.append(part.file)

            local_paths = tuple(
                self.mapper.to_local("plex", path)
                for path in sorted(set(files))
            )
            applicable = tuple(
                path
                for path in local_paths
                if path.resolve(strict=False).is_relative_to(root)
            )
            ids = self._ids(item)
            year = getattr(item, "year", None)

            all_files.extend(
                PlexMovie(
                    title=item.title,
                    year=year,
                    ids=ids,
                    file_path=path,
                )
                for path in local_paths
            )

            if len(applicable) == 1:
                movies.append(
                    PlexMovie(
                        title=item.title,
                        year=year,
                        ids=ids,
                        file_path=applicable[0],
                    )
                )
            elif len(applicable) > 1:
                multiple_applicable_files += 1
                issues.append(
                    PlexScanIssue(
                        title=item.title,
                        year=year,
                        ids=ids,
                        file_paths=applicable,
                        reason=self.MULTIPLE_APPLICABLE_REASON,
                    )
                )
            elif local_paths:
                outside_root_movies += 1
            else:
                no_media_movies += 1

        return PlexScanResult(
            movies=tuple(movies),
            issues=tuple(issues),
            all_files=tuple(all_files),
            stats=PlexScanStats(
                total_movies=len(items),
                eligible_movies=len(movies),
                outside_root_movies=outside_root_movies,
                multiple_applicable_files=multiple_applicable_files,
                no_media_movies=no_media_movies,
            ),
        )

    def movies(self) -> list[PlexMovie]:
        section = self.server.library.section(self.config.library)
        result: list[PlexMovie] = []
        for item in section.all():
            files = []
            for media in getattr(item, "media", []) or []:
                for part in getattr(media, "parts", []) or []:
                    if getattr(part, "file", None):
                        files.append(part.file)
            unique = sorted(set(files))
            if len(unique) != 1:
                continue
            result.append(
                PlexMovie(
                    title=item.title,
                    year=getattr(item, "year", None),
                    ids=self._ids(item),
                    file_path=self.mapper.to_local("plex", unique[0]),
                )
            )
        return result
