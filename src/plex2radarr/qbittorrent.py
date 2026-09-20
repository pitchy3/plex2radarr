from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import QBittorrentConfig
from .models import TorrentMatch
from .paths import PathMapper


class QBittorrentError(RuntimeError):
    pass


@dataclass(frozen=True)
class TorrentFile:
    name: str
    progress: float


class QBittorrentClient:
    def __init__(
        self,
        config: QBittorrentConfig,
        mapper: PathMapper,
        session: requests.Session | None = None,
    ):
        self.config = config
        self.mapper = mapper
        self.session = session or requests.Session()
        self.base = config.url.rstrip("/") + "/api/v2"
        self._file_index: dict[Path, list[TorrentMatch]] | None = None
        self._paths_by_hash: dict[str, set[Path]] = {}
        self._torrent_by_hash: dict[str, dict] = {}
        self._login()

    def _login(self) -> None:
        r = self.session.post(
            self.base + "/auth/login",
            data={"username": self.config.username, "password": self.config.password},
            timeout=20,
        )
        r.raise_for_status()
        if r.text.strip() != "Ok.":
            raise QBittorrentError(f"Login failed for qBittorrent {self.config.name}")

    def torrents(self) -> list[dict]:
        r = self.session.get(self.base + "/torrents/info", timeout=30)
        r.raise_for_status()
        return r.json()

    def files(self, torrent_hash: str) -> list[dict]:
        r = self.session.get(
            self.base + "/torrents/files", params={"hash": torrent_hash}, timeout=30
        )
        r.raise_for_status()
        return r.json()

    def _absolute_file(self, torrent: dict, rel_name: str) -> Path:
        remote = Path(torrent["save_path"]) / rel_name
        return self.mapper.to_local(f"qbittorrent:{self.config.name}", remote)

    def _matches_for_torrent(
        self, torrent: dict, file_items: list[dict]
    ) -> list[TorrentMatch]:
        torrent_hash = torrent["hash"]
        matches: list[TorrentMatch] = []
        for item in file_items:
            candidate = self._absolute_file(torrent, item["name"])
            matches.append(
                TorrentMatch(
                    client_name=self.config.name,
                    torrent_hash=torrent_hash,
                    torrent_name=torrent.get("name", torrent_hash),
                    file_path=candidate,
                    save_path=self.mapper.to_local(
                        f"qbittorrent:{self.config.name}", torrent["save_path"]
                    ),
                    progress=float(torrent.get("progress", item.get("progress", 0))),
                    relative_path=Path(item["name"]),
                )
            )
        return matches

    def build_file_index(self) -> None:
        if self._file_index is not None:
            return

        index: dict[Path, list[TorrentMatch]] = {}
        paths_by_hash: dict[str, set[Path]] = {}
        torrent_by_hash: dict[str, dict] = {}

        for torrent in self.torrents():
            torrent_hash = torrent["hash"]
            torrent_by_hash[torrent_hash] = torrent
            matches = self._matches_for_torrent(torrent, self.files(torrent_hash))
            paths_by_hash[torrent_hash] = {
                match.file_path.resolve(strict=False) for match in matches
            }
            for match in matches:
                key = match.file_path.resolve(strict=False)
                index.setdefault(key, []).append(match)

        self._file_index = index
        self._paths_by_hash = paths_by_hash
        self._torrent_by_hash = torrent_by_hash

    def find_matches(self, local_path: Path) -> list[TorrentMatch]:
        self.build_file_index()
        assert self._file_index is not None
        wanted = local_path.resolve(strict=False)
        return list(self._file_index.get(wanted, []))

    def refresh_torrent(
        self, torrent_hash: str, torrent: dict | None = None
    ) -> list[TorrentMatch]:
        self.build_file_index()
        assert self._file_index is not None

        if torrent is None:
            current = [item for item in self.torrents() if item["hash"] == torrent_hash]
            if len(current) != 1:
                raise QBittorrentError(
                    f"Expected one qBittorrent torrent for hash {torrent_hash}, "
                    f"found {len(current)}"
                )
            torrent = current[0]

        for old_path in self._paths_by_hash.get(torrent_hash, set()):
            remaining = [
                match
                for match in self._file_index.get(old_path, [])
                if not (
                    match.client_name == self.config.name
                    and match.torrent_hash == torrent_hash
                )
            ]
            if remaining:
                self._file_index[old_path] = remaining
            else:
                self._file_index.pop(old_path, None)

        matches = self._matches_for_torrent(torrent, self.files(torrent_hash))
        self._paths_by_hash[torrent_hash] = {
            match.file_path.resolve(strict=False) for match in matches
        }
        self._torrent_by_hash[torrent_hash] = torrent
        for match in matches:
            key = match.file_path.resolve(strict=False)
            self._file_index.setdefault(key, []).append(match)
        return matches

    def matches_for_hash(self, torrent_hash: str) -> list[TorrentMatch]:
        self.build_file_index()
        assert self._file_index is not None
        torrent = self._torrent_by_hash.get(torrent_hash)
        if torrent is None:
            current = [item for item in self.torrents() if item["hash"] == torrent_hash]
            if len(current) != 1:
                raise QBittorrentError(
                    f"Expected one qBittorrent torrent for hash {torrent_hash}, "
                    f"found {len(current)}"
                )
            torrent = current[0]
            return self.refresh_torrent(torrent_hash, torrent=torrent)

        paths = self._paths_by_hash.get(torrent_hash, set())
        matches: list[TorrentMatch] = []
        for path in paths:
            for match in self._file_index.get(path, []):
                if (
                    match.client_name == self.config.name
                    and match.torrent_hash == torrent_hash
                ):
                    matches.append(match)
        return matches

    def set_location(self, torrent_hash: str, location: Path) -> None:
        r = self.session.post(
            self.base + "/torrents/setLocation",
            data={"hashes": torrent_hash, "location": str(location)},
            timeout=30,
        )
        r.raise_for_status()

    def wait_for_save_path(
        self, torrent_hash: str, expected_root: Path, timeout: int = 300
    ) -> dict:
        deadline = time.monotonic() + timeout
        expected = str(expected_root).rstrip("/")
        while time.monotonic() < deadline:
            for torrent in self.torrents():
                if torrent["hash"] != torrent_hash:
                    continue
                current = str(
                    self.mapper.to_local(
                        f"qbittorrent:{self.config.name}", torrent["save_path"]
                    )
                ).rstrip("/")
                if current == expected:
                    return torrent
            time.sleep(2)
        raise QBittorrentError(
            f"Timed out waiting for {self.config.name} torrent {torrent_hash} relocation"
        )
