from collections import Counter
from pathlib import Path

from plex2radarr.config import QBittorrentConfig
from plex2radarr.paths import PathMapper
from plex2radarr.qbittorrent import QBittorrentClient


class CountingQBittorrentClient(QBittorrentClient):
    def __init__(self, torrents: list[dict], files_by_hash: dict[str, list[dict]]):
        self.torrent_data = torrents
        self.file_data = files_by_hash
        self.torrent_calls = 0
        self.file_calls: Counter[str] = Counter()
        super().__init__(
            QBittorrentConfig(
                name="main",
                url="http://qbit",
                username="user",
                password="pass",
                relocation_root="/downloads",
            ),
            PathMapper(()),
        )

    def _login(self) -> None:
        return

    def torrents(self) -> list[dict]:
        self.torrent_calls += 1
        return [dict(item) for item in self.torrent_data]

    def files(self, torrent_hash: str) -> list[dict]:
        self.file_calls[torrent_hash] += 1
        return [dict(item) for item in self.file_data[torrent_hash]]


def make_client() -> CountingQBittorrentClient:
    return CountingQBittorrentClient(
        torrents=[
            {
                "hash": "aaa",
                "name": "Movie A",
                "save_path": "/downloads",
                "progress": 1.0,
            },
            {
                "hash": "bbb",
                "name": "Movie B",
                "save_path": "/downloads",
                "progress": 1.0,
            },
        ],
        files_by_hash={
            "aaa": [{"name": "a.mkv", "progress": 1.0}],
            "bbb": [{"name": "b.mkv", "progress": 1.0}],
        },
    )


def test_find_matches_builds_file_index_once_per_run():
    client = make_client()

    assert client.find_matches(Path("/downloads/a.mkv"))[0].torrent_hash == "aaa"
    assert client.find_matches(Path("/downloads/b.mkv"))[0].torrent_hash == "bbb"
    assert client.find_matches(Path("/downloads/missing.mkv")) == []

    assert client.torrent_calls == 1
    assert client.file_calls == Counter({"aaa": 1, "bbb": 1})


def test_refresh_torrent_updates_only_the_moved_torrent():
    client = make_client()

    assert client.find_matches(Path("/downloads/a.mkv"))[0].torrent_hash == "aaa"

    relocated = {
        "hash": "aaa",
        "name": "Movie A",
        "save_path": "/relocated",
        "progress": 1.0,
    }
    client.torrent_data[0] = relocated

    refreshed = client.refresh_torrent("aaa", torrent=relocated)

    assert [match.file_path for match in refreshed] == [Path("/relocated/a.mkv")]
    assert client.find_matches(Path("/downloads/a.mkv")) == []
    assert client.find_matches(Path("/relocated/a.mkv"))[0].torrent_hash == "aaa"
    assert client.find_matches(Path("/downloads/b.mkv"))[0].torrent_hash == "bbb"

    assert client.torrent_calls == 1
    assert client.file_calls == Counter({"aaa": 2, "bbb": 1})
