from pathlib import Path

from plex2radarr.config import RadarrConfig
from plex2radarr.radarr import RadarrClient


def test_manual_import_candidates_scans_folder_without_movie_id():
    client = RadarrClient(
        RadarrConfig(
            url="http://radarr",
            api_key="key",
            root_folder="/movies",
            quality_profile="Any",
        )
    )
    captured = {}

    def fake_get(path, **params):
        captured["path"] = path
        captured["params"] = params
        return []

    client._get = fake_get

    result = client.manual_import_candidates(Path("/data/torrents/Movie"))

    assert result == []
    assert captured["path"] == "/manualimport"
    assert captured["params"] == {
        "folder": "/data/torrents/Movie",
        "filterExistingFiles": "true",
    }
    assert "movieId" not in captured["params"]
