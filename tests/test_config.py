from pathlib import Path

import pytest

from plex2radarr.config import ConfigError, load_config


def test_load_config_expands_environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PLEX_TOKEN", "secret")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
plex:
  url: http://plex
  token: ${PLEX_TOKEN}
  library: Movies
radarr:
  url: http://radarr
  api_key: key
  root_folder: /movies
  quality_profile: Any
qbittorrent: []
"""
    )
    loaded = load_config(cfg)
    assert loaded.plex.token == "secret"


def test_missing_environment_variable_fails(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
plex:
  url: http://plex
  token: ${DOES_NOT_EXIST}
  library: Movies
radarr:
  url: http://radarr
  api_key: key
  root_folder: /movies
  quality_profile: Any
"""
    )
    with pytest.raises(ConfigError):
        load_config(cfg)
