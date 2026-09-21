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



def test_quality_profile_id_re_resolves_current_configuration():
    client = RadarrClient(
        RadarrConfig(
            url="http://radarr",
            api_key="key",
            root_folder="/movies",
            quality_profile="Any",
        )
    )
    calls = []

    def fake_get(path, **params):
        calls.append((path, params))
        if len(calls) == 1:
            return [{"id": 7, "name": "Any"}]
        return [{"id": 9, "name": "Any"}]

    client._get = fake_get

    assert client.quality_profile_id() == 7
    assert client.quality_profile_id() == 9
    assert calls == [
        ("/qualityprofile", {}),
        ("/qualityprofile", {}),
    ]
