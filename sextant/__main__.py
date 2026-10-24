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
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "sextant.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
