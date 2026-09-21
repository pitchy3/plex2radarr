from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from plex2radarr.cli import (
    _collect_selected_files,
    _print_summary,
    _state_path_for_config,
    build_parser,
)


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


def _summary_item(action: str, reason: str):
    return SimpleNamespace(action=action, reason=reason)


def test_print_summary_reports_actions_and_skip_reasons(capsys):
    plan = [
        _summary_item("relocate_and_import", "source is owned by qBittorrent"),
        _summary_item("relocate_and_import", "source is owned by qBittorrent"),
        _summary_item("move_import", "unseeded legacy file is inside the library root"),
        _summary_item("skip", "already exists in Radarr with a movie file"),
        _summary_item("skip", "multiple qBittorrent torrents own this file"),
        _summary_item("skip", "multiple qBittorrent torrents own this file"),
    ]

    _print_summary(plan, dry_run=True)

    output = capsys.readouterr().out
    assert "total planned: 6" in output
    assert "relocate_and_import: 2" in output
    assert "move_import: 1" in output
    assert "skip: 3" in output
    assert "1 - already exists in Radarr with a movie file" in output
    assert "2 - multiple qBittorrent torrents own this file" in output
    assert "execution results:" not in output


def test_print_summary_reports_execution_results(capsys):
    plan = [
        _summary_item("relocate_and_import", "source is owned by qBittorrent"),
        _summary_item("move_import", "unseeded legacy file is inside the library root"),
        _summary_item("skip", "already exists in Radarr with a movie file"),
    ]

    _print_summary(
        plan,
        dry_run=False,
        successful_actions=Counter({"relocate_and_import": 1}),
        failures=Counter({"move_import": 1}),
    )

    output = capsys.readouterr().out
    assert "execution results:" in output
    assert "succeeded: 1" in output
    assert "relocate_and_import: 1" in output
    assert "failed: 1" in output
    assert "move_import: 1" in output


def test_print_summary_reports_plex_scan_classifications(capsys):
    plan = [
        _summary_item("move_import", "unseeded legacy file is inside the library root"),
        _summary_item("skip", "multiple Plex files in configured Radarr root"),
    ]
    stats = SimpleNamespace(
        total_movies=1100,
        eligible_movies=785,
        outside_root_movies=300,
        multiple_applicable_files=14,
        no_media_movies=1,
    )

    _print_summary(
        plan,
        dry_run=True,
        plex_scan_stats=stats,
    )

    output = capsys.readouterr().out
    assert "Plex library movies seen: 1100" in output
    assert "eligible for this Radarr root: 785" in output
    assert "excluded from automatic import: 315" in output
    assert "outside configured Radarr root: 300" in output
    assert "multiple applicable Plex files: 14" in output
    assert "no media file: 1" in output
