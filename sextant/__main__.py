"""Console entry point: ``python -m sextant`` / ``sextant``."""

from __future__ import annotations

import argparse

from sextant.config import VERSION, get_settings


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=f"Sextant {VERSION} — evidence catalog console")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes (development)")
    parser.add_argument(
        "--reload-dir",
        action="append",
        default=[],
        help="Directory to watch for --reload. Repeatable. Defaults to the package only.",
    )
    parser.add_argument(
        "--reload-exclude",
        action="append",
        default=[],
        help="Pattern to ignore while watching. Repeatable.",
    )
    args = parser.parse_args()

    import uvicorn

    # Watching the whole working directory restarts the server whenever a test
    # file is saved, and every restart kills the worker thread of any run in
    # flight — the run then sits in runs.json as "running" forever. Watching
    # only the package, and never the workspace, keeps reloads tied to code.
    reload_dirs = args.reload_dir or ["sextant"]
    reload_excludes = args.reload_exclude or ["workspace", ".venv", "tests", "*.jsonl"]

    uvicorn.run(
        "sextant.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=reload_dirs if args.reload else None,
        reload_excludes=reload_excludes if args.reload else None,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
