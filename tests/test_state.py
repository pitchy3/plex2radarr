from pathlib import Path

from plex2radarr.models import ExternalIds, PlexMovie, TorrentMatch
from plex2radarr.state import StateStore


def test_state_store_persists_and_reloads_transaction(tmp_path: Path):
    path = tmp_path / "state.json"
    movie = PlexMovie(
        "Warm Bodies",
        2013,
        ExternalIds(tmdb=82654, imdb="tt1588173"),
        Path("/library/Warm.Bodies/movie.mkv"),
    )
    torrent = TorrentMatch(
        "main",
        "abc123",
        "Warm.Bodies",
        movie.file_path,
        movie.file_path.parent,
        1.0,
        Path("Warm.Bodies/movie.mkv"),
    )

    store = StateStore(path)
    transaction = store.begin(
        movie,
        "relocate_and_import",
        torrent,
        None,
        movie.file_path,
    )
    store.update(
        transaction.key,
        stage="torrent_relocated",
        current_source="/torrents/Warm.Bodies/movie.mkv",
        radarr_movie_id=677,
    )

    reloaded = StateStore(path)
    restored = reloaded.get(transaction.key)

    assert restored is not None
    assert restored.stage == "torrent_relocated"
    assert restored.radarr_movie_id == 677
    assert restored.current_source == Path("/torrents/Warm.Bodies/movie.mkv")
    assert restored.torrent_hash == "abc123"
    assert restored.torrent_relative_path == Path("Warm.Bodies/movie.mkv")


def test_state_store_removes_file_when_last_transaction_completes(tmp_path: Path):
    path = tmp_path / "state.json"
    movie = PlexMovie(
        "Warm Bodies",
        2013,
        ExternalIds(tmdb=82654),
        Path("/library/Warm.Bodies/movie.mkv"),
    )
    store = StateStore(path)
    transaction = store.begin(
        movie,
        "move_import",
        None,
        None,
        movie.file_path,
    )

    assert path.exists()

    store.remove(transaction.key)

    assert not path.exists()
