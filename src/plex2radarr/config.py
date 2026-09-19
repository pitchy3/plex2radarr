from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class PlexConfig:
    url: str
    token: str
    library: str


@dataclass(frozen=True)
class RadarrConfig:
    url: str
    api_key: str
    root_folder: str
    quality_profile: str
    monitored: bool = False


@dataclass(frozen=True)
class QBittorrentConfig:
    name: str
    url: str
    username: str
    password: str
    relocation_root: str


@dataclass(frozen=True)
class PathMapping:
    service: str
    remote: str
    local: str


@dataclass(frozen=True)
class AppConfig:
    plex: PlexConfig
    radarr: RadarrConfig
    qbittorrent: tuple[QBittorrentConfig, ...]
    path_mappings: tuple[PathMapping, ...] = ()


def _expand_env(value):
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in os.environ:
                raise ConfigError(f"Environment variable {key} is not set")
            return os.environ[key]
        return _ENV_RE.sub(repl, value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text()) or {}
    raw = _expand_env(raw)
    try:
        plex_raw = raw["plex"]
        radarr_raw = raw["radarr"]
    except KeyError as exc:
        raise ConfigError(f"Missing required config section: {exc.args[0]}") from exc

    plex = PlexConfig(**plex_raw)
    radarr = RadarrConfig(**radarr_raw)
    qbits = tuple(QBittorrentConfig(**item) for item in raw.get("qbittorrent", []))
    mappings = tuple(PathMapping(**item) for item in raw.get("path_mappings", []))

    names = [q.name for q in qbits]
    if len(names) != len(set(names)):
        raise ConfigError("qBittorrent instance names must be unique")

    return AppConfig(plex=plex, radarr=radarr, qbittorrent=qbits, path_mappings=mappings)
