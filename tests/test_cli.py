from pathlib import Path

import pytest

from plex2radarr.cli import _collect_selected_files, _state_path_for_config, build_parser


def test_parser_accepts_single_file():
    args = build_parser().parse_args(["/movies/The Matrix/movie.mkv"])
    assert args.files == [Path("/movies/The Matrix/movie.mkv")]
    assert args.execute is False


def test_parser_accepts_multiple_files_with_execute():
    args = build_parser().parse_args(
        ["--execute", "/movies/a.mkv", "/movies/b.mkv"]
    )
    assert args.files == [Path("/movies/a.mkv"), Path("/movies/b.mkv")]
    assert args.execute is True


def test_collect_selected_files_reads_list_and_ignores_comments(tmp_path: Path):
    file_list = tmp_path / "movies.txt"
    file_list.write_text(
        "# migration batch\n"
        "/movies/a.mkv\n"
        "\n"
        "/movies/b.mkv\n"
    )

    selected = _collect_selected_files([Path("/movies/direct.mkv")], [file_list])

    assert selected == [
        Path("/movies/direct.mkv"),
        Path("/movies/a.mkv"),
        Path("/movies/b.mkv"),
    ]


def test_collect_selected_files_deduplicates_paths(tmp_path: Path):
    file_list = tmp_path / "movies.txt"
    file_list.write_text("/movies/a.mkv\n")

    selected = _collect_selected_files([Path("/movies/a.mkv")], [file_list])

    assert selected == [Path("/movies/a.mkv")]


def test_collect_selected_files_rejects_missing_list_file(tmp_path: Path):
    with pytest.raises(ValueError, match="File list not found"):
        _collect_selected_files([], [tmp_path / "missing.txt"])


def test_state_path_is_isolated_by_config_filename(tmp_path: Path):
    first = _state_path_for_config(tmp_path / "config.yaml")
    second = _state_path_for_config(tmp_path / "movies-4k.yaml")

    assert first != second
    assert first.name == ".plex2radarr-state.config.yaml.json"
    assert second.name == ".plex2radarr-state.movies-4k.yaml.json"
