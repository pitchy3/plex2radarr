from pathlib import Path
from types import SimpleNamespace

from plex2radarr.config import PathMapping
from plex2radarr.paths import PathMapper
from plex2radarr.plex import PlexClient


class FakeSection:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class FakeLibrary:
    def __init__(self, items):
        self._items = items

    def section(self, name):
        assert name == "Movies"
        return FakeSection(self._items)


class FakeServer:
    def __init__(self, items):
        self.library = FakeLibrary(items)


def _item(title, year, files):
    media = [
        SimpleNamespace(parts=[SimpleNamespace(file=file_path)])
        for file_path in files
    ]
    return SimpleNamespace(
        title=title,
        year=year,
        media=media,
        guids=[SimpleNamespace(id="tmdb://1")],
        guid="",
    )


def _client(items):
    client = PlexClient.__new__(PlexClient)
    client.config = SimpleNamespace(library="Movies")
    client.mapper = PathMapper(
        (
            PathMapping(
                "plex",
                "/Volume2/Media/_Movies",
                "/Volume2/Media/_Movies",
            ),
            PathMapping(
                "plex",
                "/Volume4/Media_4k/_Movies_4k",
                "/Volume4/Media_4k/_Movies_4k",
            ),
        )
    )
    client.server = FakeServer(items)
    return client


def test_scan_keeps_single_applicable_file_when_other_version_is_outside_root():
    client = _client(
        [
            _item(
                "Movie",
                2020,
                [
                    "/Volume2/Media/_Movies/Movie.1080p.mkv",
                    "/Volume4/Media_4k/_Movies_4k/Movie.2160p.mkv",
                ],
            )
        ]
    )

    scan = client.scan(Path("/Volume2/Media/_Movies"))

    assert len(scan.movies) == 1
    assert scan.movies[0].file_path == Path(
        "/Volume2/Media/_Movies/Movie.1080p.mkv"
    )
    assert scan.issues == ()
    assert scan.stats.total_movies == 1
    assert scan.stats.eligible_movies == 1
    assert scan.stats.outside_root_movies == 0


def test_scan_reports_multiple_applicable_files_in_same_root():
    client = _client(
        [
            _item(
                "Movie",
                2020,
                [
                    "/Volume2/Media/_Movies/Movie.A.mkv",
                    "/Volume2/Media/_Movies/Movie.B.mkv",
                    "/Volume4/Media_4k/_Movies_4k/Movie.2160p.mkv",
                ],
            )
        ]
    )

    scan = client.scan(Path("/Volume2/Media/_Movies"))

    assert scan.movies == ()
    assert len(scan.issues) == 1
    assert scan.issues[0].reason == "multiple Plex files in configured Radarr root"
    assert scan.issues[0].file_paths == (
        Path("/Volume2/Media/_Movies/Movie.A.mkv"),
        Path("/Volume2/Media/_Movies/Movie.B.mkv"),
    )
    assert scan.stats.multiple_applicable_files == 1


def test_scan_counts_outside_root_and_no_media_movies():
    client = _client(
        [
            _item(
                "4K only",
                2020,
                ["/Volume4/Media_4k/_Movies_4k/Movie.2160p.mkv"],
            ),
            _item("No media", 2021, []),
        ]
    )

    scan = client.scan(Path("/Volume2/Media/_Movies"))

    assert scan.movies == ()
    assert scan.issues == ()
    assert scan.stats.total_movies == 2
    assert scan.stats.outside_root_movies == 1
    assert scan.stats.no_media_movies == 1
