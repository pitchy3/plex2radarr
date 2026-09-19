from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

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
        # save_path is the authoritative qBittorrent storage root for API matching.
        remote = Path(torrent["save_path"]) / rel_name
        return self.mapper.to_local(f"qbittorrent:{self.config.name}", remote)

    def find_matches(self, local_path: Path) -> list[TorrentMatch]:
        wanted = local_path.resolve(strict=False)
        matches: list[TorrentMatch] = []
        for torrent in self.torrents():
            torrent_hash = torrent["hash"]
            for item in self.files(torrent_hash):
                candidate = self._absolute_file(torrent, item["name"]).resolve(strict=False)
                if candidate == wanted:
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
                        )
                    )
        return matches

    def set_location(self, torrent_hash: str, location: Path) -> None:
        r = self.session.post(
            self.base + "/torrents/setLocation",
            data={"hashes": torrent_hash, "location": str(location)},
            timeout=30,
        )
        r.raise_for_status()

    def recheck(self, torrent_hash: str) -> None:
        r = self.session.post(
            self.base + "/torrents/recheck",
            data={"hashes": torrent_hash},
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
