from plex2radarr.config import PathMapping
from plex2radarr.paths import PathMapper


def test_longest_path_mapping_wins():
    mapper = PathMapper(
        (
            PathMapping("plex", "/media", "/data"),
            PathMapping("plex", "/media/movies", "/data/_Movies"),
        )
    )
    assert str(mapper.to_local("plex", "/media/movies/A/movie.mkv")) == "/data/_Movies/A/movie.mkv"
