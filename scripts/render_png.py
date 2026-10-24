#!/usr/bin/env python3
"""Rasterise the generated SVGs with a local headless Edge/Chromium.

No extra Python dependency is required.  On Windows the default Edge install
path is used; elsewhere pass ``--browser`` explicitly:

    python scripts/render_png.py
    python scripts/render_png.py --browser /usr/bin/chromium

The screenshot is written next to the SVG under ``docs/diagrams/png/``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

EDGE_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
    Path("/usr/bin/google-chrome"),
]


def find_browser(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise SystemExit(f"browser not found: {path}")
        return path
    for candidate in EDGE_CANDIDATES:
        if candidate.exists():
            return candidate
    raise SystemExit("no Edge/Chromium found; pass --browser <path>")


def dimensions(svg: str) -> tuple[int, int]:
    match = re.search(r'<svg[^>]*width="(\d+)"[^>]*height="(\d+)"', svg)
    if not match:
        raise SystemExit("cannot read SVG width/height")
    return int(match.group(1)), int(match.group(2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", help="path to msedge/chromium/chrome")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    svg_dir = root / "docs" / "diagrams"
    png_dir = svg_dir / "png"
    png_dir.mkdir(parents=True, exist_ok=True)
    browser = find_browser(args.browser)

    for svg_path in sorted(svg_dir.glob("*.svg")):
        width, height = dimensions(svg_path.read_text(encoding="utf-8"))
        png_path = (png_dir / f"{svg_path.stem}.png").resolve()
        command = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={width},{height}",
            f"--screenshot={png_path}",
            svg_path.resolve().as_uri(),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0 or not png_path.exists():
            print(f"failed: {svg_path.name}\n{result.stdout}\n{result.stderr}", file=sys.stderr)
            raise SystemExit(1)
        print(f"wrote {png_path.relative_to(root)} ({png_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
