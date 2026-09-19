from pathlib import Path
from types import SimpleNamespace

from plex2radarr.config import (
    AppConfig,
    PlexConfig,
    QBittorrentConfig,
    RadarrConfig,
)
from plex2radarr.models import ExternalIds, PlexMovie, TorrentMatch
from plex2radarr.reconcile import Reconciler


class FakePlex:
    def __init__(self, movies):
        self._movies = movies

    def movies(self):
        return self._movies


class FakeRadarr:
    def __init__(self, movies):
        self._movies = movies

    def movies(self):
        return self._movies


class FakeQbit:
    def __init__(self, name, matches):
        self.config = SimpleNamespace(name=name)
        self._matches = matches

    def find_matches(self, path):
        return list(self._matches.get(path, []))


def config():
    return AppConfig(
        plex=PlexConfig("http://plex", "token", "Movies"),
        radarr=RadarrConfig("http://radarr", "key", "/movies", "Any"),
        qbittorrent=(
            QBittorrentConfig("main", "http://qbit", "u", "p", "/torrents/movies"),
        ),
    )


def test_plan_skips_existing_radarr_movie():
    movie = PlexMovie("The Matrix", 1999, ExternalIds(tmdb=603), Path("/movies/matrix.mkv"))
    r = Reconciler(
        config(),
        plex=FakePlex([movie]),
        radarr=FakeRadarr([{"id": 1, "tmdbId": 603}]),
        qbits=[FakeQbit("main", {})],
    )
    plan = r.plan()
    assert plan[0].action == "skip"
    assert plan[0].radarr_movie_id == 1


def test_plan_relocates_when_qbit_owns_source():
    path = Path("/movies/matrix.mkv")
    movie = PlexMovie("The Matrix", 1999, ExternalIds(tmdb=603), path)
    match = TorrentMatch("main", "abc", "matrix", path, Path("/movies"), 1.0)
    r = Reconciler(
        config(),
        plex=FakePlex([movie]),
        radarr=FakeRadarr([]),
        qbits=[FakeQbit("main", {path: [match]})],
    )
    plan = r.plan()
    assert plan[0].action == "relocate_and_import"
    assert plan[0].torrent == match


def test_plan_imports_when_no_torrent_owns_source():
    path = Path("/movies/matrix.mkv")
    movie = PlexMovie("The Matrix", 1999, ExternalIds(tmdb=603), path)
    r = Reconciler(
        config(),
        plex=FakePlex([movie]),
        radarr=FakeRadarr([]),
        qbits=[FakeQbit("main", {})],
    )
    plan = r.plan()
    assert plan[0].action == "import"
