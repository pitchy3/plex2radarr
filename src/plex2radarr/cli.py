from __future__ import annotations

import argparse
import logging
from collections import Counter
from pathlib import Path

from .config import ConfigError, load_config
from .reconcile import Reconciler, SelectionError
from .state import StateStore

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plex2radarr",
        description="Safely reconcile Plex movies that are missing from Radarr.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        metavar="FILE",
        help=(
            "Optional movie file path(s) to reconcile. "
            "When omitted, the whole configured Plex library is scanned."
        ),
    )
    parser.add_argument(
        "--files-from",
        action="append",
        type=Path,
        default=[],
        metavar="FILE_LIST",
        help=(
            "Read movie file paths from a text file, one path per line. "
            "May be specified more than once."
        ),
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config file (default: ./config.yaml)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform planned qBittorrent moves and Radarr imports. Default is dry-run.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    return parser


def _collect_selected_files(
    direct_files: list[Path], file_lists: list[Path]
) -> list[Path]:
    selected = [path.expanduser() for path in direct_files]

    for list_path in file_lists:
        list_path = list_path.expanduser()
        if not list_path.is_file():
            raise ValueError(f"File list not found: {list_path}")
        for line in list_path.read_text().splitlines():
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            selected.append(Path(value).expanduser())

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in selected:
        normalized = path.resolve(strict=False)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(path)

    return unique


def _state_path_for_config(config_path: Path) -> Path:
    return config_path.with_name(
        f".plex2radarr-state.{config_path.name}.json"
    )



def _print_summary(
    plan,
    *,
    dry_run: bool,
    plex_scan_stats=None,
    successful_actions: Counter | None = None,
    failures: Counter | None = None,
) -> None:
    action_counts = Counter(item.action for item in plan)
    skip_reasons = Counter(item.reason for item in plan if item.action == "skip")
    total = len(plan)

    print("\nSummary")
    if plex_scan_stats is not None:
        print(f"  Plex library movies seen: {plex_scan_stats.total_movies}")
        print(f"  eligible for this Radarr root: {plex_scan_stats.eligible_movies}")
        excluded = (
            plex_scan_stats.outside_root_movies
            + plex_scan_stats.multiple_applicable_files
            + plex_scan_stats.no_media_movies
        )
        print(f"  excluded before planning: {excluded}")
        if plex_scan_stats.outside_root_movies:
            print(
                "    outside configured Radarr root: "
                f"{plex_scan_stats.outside_root_movies}"
            )
        if plex_scan_stats.multiple_applicable_files:
            print(
                "    multiple applicable Plex files: "
                f"{plex_scan_stats.multiple_applicable_files}"
            )
        if plex_scan_stats.no_media_movies:
            print(f"    no media file: {plex_scan_stats.no_media_movies}")
    print(f"  total planned: {total}")

    for action in (
        "relocate_and_import",
        "move_import",
        "import",
        "finalize_recovery",
        "skip",
    ):
        if action_counts.get(action):
            print(f"  {action}: {action_counts[action]}")

    if skip_reasons:
        print("  skipped by reason:")
        for reason, count in sorted(skip_reasons.items()):
            print(f"    {count} - {reason}")

    if dry_run:
        return

    successful_actions = successful_actions or Counter()
    failures = failures or Counter()

    print("  execution results:")
    print(f"    succeeded: {sum(successful_actions.values())}")
    for action, count in sorted(successful_actions.items()):
        print(f"      {action}: {count}")
    print(f"    failed: {sum(failures.values())}")
    for action, count in sorted(failures.items()):
        print(f"      {action}: {count}")


def _print_item(item, dry_run: bool) -> None:
    prefix = "DRY-RUN" if dry_run else "EXECUTE"
    print(
        f"[{prefix}] {item.movie.title}"
        + (f" ({item.movie.year})" if item.movie.year else "")
        + f": {item.action} - {item.reason}"
    )
    print(f"  source: {item.movie.file_path}")
    if item.torrent:
        print(
            f"  torrent: {item.torrent.client_name} / {item.torrent.torrent_name}"
            f" [{item.torrent.torrent_hash[:12]}]"
        )
    for note in item.notes:
        print(f"  note: {note}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    try:
        selected_files = _collect_selected_files(args.files, args.files_from)
        config_path = Path(args.config).expanduser().resolve()
        config = load_config(config_path)
        state = StateStore(_state_path_for_config(config_path))
        reconciler = Reconciler(config, state=state)
        plan = reconciler.plan(selected_paths=selected_files or None)
        reconciler.preflight(plan)
    except (ConfigError, SelectionError, ValueError, OSError) as exc:
        logger.error("%s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        logger.error("Unable to build reconciliation plan: %s", exc)
        return 2

    counts = Counter(item.action for item in plan)
    scope = "selected Plex file(s)" if selected_files else "Plex movies"
    print(
        f"Planned {len(plan)} {scope}: "
        + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    )

    if not args.execute:
        for item in plan:
            _print_item(item, dry_run=True)
        _print_summary(
            plan,
            dry_run=True,
            plex_scan_stats=reconciler.plex_scan_stats,
        )
        print("\nDry-run only. Re-run with --execute to perform mutating operations.")
        return 0

    failure_count = 0
    successful_actions = Counter()
    failures_by_action = Counter()

    for item in plan:
        try:
            executed = reconciler.execute(item)
            _print_item(executed, dry_run=False)
            if item.action != "skip":
                successful_actions[item.action] += 1
        except Exception:
            failure_count += 1
            failures_by_action[item.action] += 1
            logger.exception("Failed to reconcile %s", item.movie.title)

    _print_summary(
        plan,
        dry_run=False,
        successful_actions=successful_actions,
        failures=failures_by_action,
        plex_scan_stats=reconciler.plex_scan_stats,
    )

    return 1 if failure_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
