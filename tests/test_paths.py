from plex2radarr.config import PathMapping
from plex2radarr.paths import PathMapper


def test_longest_path_mapping_wins():
    mapper = PathMapper(
        (
            PathMapping("plex", "/media", "/data"),
            PathMapping("plex", "/media/movies", "/data/_Movies"),
        )
    )
    assert (
        str(mapper.to_local("plex", "/media/movies/A/movie.mkv"))
        == "/data/_Movies/A/movie.mkv"
    )


def test_path_mapping_can_translate_back_to_service_path():
    mapper = PathMapper((PathMapping("radarr", "/movies", "/data/_Movies"),))
    assert (
        str(mapper.to_remote("radarr", "/data/_Movies/The Matrix/movie.mkv"))
        == "/movies/The Matrix/movie.mkv"
    )
