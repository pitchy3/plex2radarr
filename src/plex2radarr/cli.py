from __future__ import annotations

import argparse
import logging
from collections import Counter

from .config import ConfigError, load_config
from .reconcile import Reconciler

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plex2radarr",
        description="Safely reconcile Plex movies that are missing from Radarr.",
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
        config = load_config(args.config)
        reconciler = Reconciler(config)
        plan = reconciler.plan()
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        logger.error("Unable to build reconciliation plan: %s", exc)
        return 2

    counts = Counter(item.action for item in plan)
    print(
        f"Planned {len(plan)} Plex movies: "
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
        except Exception:  # noqa: BLE001
            failures += 1
            logger.exception("Failed to reconcile %s", item.movie.title)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
