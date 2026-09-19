from __future__ import annotations

from plexapi.server import PlexServer

from .config import PlexConfig
from .models import ExternalIds, PlexMovie
from .paths import PathMapper


class PlexClient:
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
