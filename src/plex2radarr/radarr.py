from __future__ import annotations

from pathlib import Path
import time

import requests

from .config import RadarrConfig
from .models import PlexMovie


class RadarrError(RuntimeError):
    pass


class RadarrClient:
    def __init__(self, config: RadarrConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()
        self.base = config.url.rstrip("/") + "/api/v3"
        self.headers = {"X-Api-Key": config.api_key}

    def _get(self, path: str, **params):
        r = self.session.get(self.base + path, headers=self.headers, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict):
        r = self.session.post(self.base + path, headers=self.headers, json=payload, timeout=60)
        r.raise_for_status()
        return r.json() if r.content else {}

    def movies(self) -> list[dict]:
        return self._get("/movie")

    def quality_profile_id(self) -> int:
        profiles = self._get("/qualityprofile")
        for profile in profiles:
            if profile.get("name") == self.config.quality_profile:
                return int(profile["id"])
        available = ", ".join(sorted(p.get("name", "") for p in profiles))
        raise RadarrError(
            f"Quality profile {self.config.quality_profile!r} not found. Available: {available}"
        )

    def root_folder(self) -> dict:
        roots = self._get("/rootfolder")
        wanted = self.config.root_folder.rstrip("/")
        for root in roots:
            if str(root.get("path", "")).rstrip("/") == wanted:
                return root
        available = ", ".join(str(r.get("path")) for r in roots)
        raise RadarrError(f"Radarr root folder {wanted!r} not found. Available: {available}")

    def lookup(self, movie: PlexMovie) -> dict:
        if movie.ids.tmdb:
            items = self._get("/movie/lookup", term=f"tmdb:{movie.ids.tmdb}")
        elif movie.ids.imdb:
            items = self._get("/movie/lookup", term=f"imdb:{movie.ids.imdb}")
        else:
            raise RadarrError(f"{movie.title} has no TMDb/IMDb ID")
        if not items:
            raise RadarrError(f"Radarr could not resolve {movie.title}")
        return items[0]

    def add_movie(self, movie: PlexMovie) -> dict:
        data = self.lookup(movie)
        payload = {
            "title": data["title"],
            "qualityProfileId": self.quality_profile_id(),
            "titleSlug": data["titleSlug"],
            "images": data.get("images", []),
            "tmdbId": data["tmdbId"],
            "year": data.get("year"),
            "rootFolderPath": self.config.root_folder,
            "monitored": self.config.monitored,
            "addOptions": {"searchForMovie": False},
        }
        return self._post("/movie", payload)

    def manual_import_candidates(self, folder: Path, movie_id: int) -> list[dict]:
        return self._get(
            "/manualimport",
            folder=str(folder),
            movieId=movie_id,
            filterExistingFiles="true",
            replaceExistingFiles="false",
        )

    def import_file(self, candidate: dict, movie_id: int) -> dict:
        payload_item = dict(candidate)
        payload_item["movieId"] = movie_id
        payload_item["importMode"] = "copy"
        cmd = self._post(
            "/command",
            {"name": "ManualImport", "files": [payload_item], "importMode": "copy"},
        )
        return cmd

    def wait_for_command(self, command_id: int, timeout: int = 180) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cmd = self._get(f"/command/{command_id}")
            if cmd.get("status") in {"completed", "failed", "aborted"}:
                return cmd
            time.sleep(2)
        raise RadarrError(f"Timed out waiting for Radarr command {command_id}")
