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
        print("\nDry-run only. Re-run with --execute to perform mutating operations.")
        return 0

    failures = 0
    for item in plan:
        try:
            executed = reconciler.execute(item)
            _print_item(executed, dry_run=False)
        except Exception:
            failures += 1
            logger.exception("Failed to reconcile %s", item.movie.title)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
