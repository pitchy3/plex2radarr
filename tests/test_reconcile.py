from pathlib import Path
from types import SimpleNamespace

import pytest

from plex2radarr.config import (
    AppConfig,
    PlexConfig,
    QBittorrentConfig,
    RadarrConfig,
)
from plex2radarr.models import ExternalIds, PlexMovie, TorrentMatch
from plex2radarr.reconcile import Reconciler, SelectionError


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


def test_plan_moves_unseeded_legacy_file_inside_library_root():
    path = Path("/movies/legacy/matrix.mkv")
    movie = PlexMovie("The Matrix", 1999, ExternalIds(tmdb=603), path)
    r = Reconciler(
        config(),
        plex=FakePlex([movie]),
        radarr=FakeRadarr([]),
        qbits=[FakeQbit("main", {})],
    )
    plan = r.plan()
    assert plan[0].action == "move_import"


def test_plan_hardlink_imports_unseeded_file_outside_library_root():
    path = Path("/imports/matrix.mkv")
    movie = PlexMovie("The Matrix", 1999, ExternalIds(tmdb=603), path)
    r = Reconciler(
        config(),
        plex=FakePlex([movie]),
        radarr=FakeRadarr([]),
        qbits=[FakeQbit("main", {})],
    )
    plan = r.plan()
    assert plan[0].action == "import"


def test_plan_can_target_one_file():
    matrix = PlexMovie(
        "The Matrix",
        1999,
        ExternalIds(tmdb=603),
        Path("/movies/legacy/matrix.mkv"),
    )
    alien = PlexMovie(
        "Alien",
        1979,
        ExternalIds(tmdb=348),
        Path("/movies/legacy/alien.mkv"),
    )
    r = Reconciler(
        config(),
        plex=FakePlex([matrix, alien]),
        radarr=FakeRadarr([]),
        qbits=[FakeQbit("main", {})],
    )

    plan = r.plan(selected_paths=[matrix.file_path])

    assert [item.movie.title for item in plan] == ["The Matrix"]


def test_plan_can_target_multiple_files():
    matrix = PlexMovie(
        "The Matrix",
        1999,
        ExternalIds(tmdb=603),
        Path("/movies/legacy/matrix.mkv"),
    )
    alien = PlexMovie(
        "Alien",
        1979,
        ExternalIds(tmdb=348),
        Path("/movies/legacy/alien.mkv"),
    )
    r = Reconciler(
        config(),
        plex=FakePlex([matrix, alien]),
        radarr=FakeRadarr([]),
        qbits=[FakeQbit("main", {})],
    )

    plan = r.plan(selected_paths=[alien.file_path, matrix.file_path])

    assert {item.movie.title for item in plan} == {"Alien", "The Matrix"}


def test_plan_rejects_requested_file_not_in_plex():
    movie = PlexMovie(
        "The Matrix",
        1999,
        ExternalIds(tmdb=603),
        Path("/movies/legacy/matrix.mkv"),
    )
    r = Reconciler(
        config(),
        plex=FakePlex([movie]),
        radarr=FakeRadarr([]),
        qbits=[FakeQbit("main", {})],
    )

    with pytest.raises(SelectionError, match="not found"):
        r.plan(selected_paths=[Path("/movies/legacy/not-in-plex.mkv")])
