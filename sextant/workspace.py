"""Topic workspace: filesystem layout, topic metadata and run history.

Layout (one directory per topic)::

    <workspace_root>/<topic_id>/
        00_intake/topic.json
        01_queries/queries.json
        02_raw/search/<source>.jsonl
        03_processed/ranked_articles.jsonl
        03_processed/pending_verification_articles.jsonl
        output/catalog.html
        logs/<run_id>.jsonl
        runs.json
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sextant.config import Settings
from sextant.engine.common import (
    STAGE_DIRS,
    ensure_workspace_structure,
    normalize_text,
    read_json,
    read_jsonl,
    sha1_text,
    write_json,
    write_jsonl,
)

TOPIC_FILE = Path(STAGE_DIRS["intake"]) / "topic.json"
QUERIES_FILE = Path(STAGE_DIRS["queries"]) / "queries.json"
RAW_SEARCH_DIR = Path(STAGE_DIRS["raw"]) / "search"
RANKED_FILE = Path(STAGE_DIRS["processed"]) / "ranked_articles.jsonl"
PENDING_FILE = Path(STAGE_DIRS["processed"]) / "pending_verification_articles.jsonl"
CATALOG_FILE = Path("output") / "catalog.html"
RUNS_FILE = Path("runs.json")

TOPIC_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class WorkspaceError(ValueError):
    """Invalid topic input or unsafe path."""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clean_keywords(values: Iterable[str] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        text = normalize_text(value)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _clean_date(value: str | None) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise WorkspaceError("日期必须使用 YYYY-MM-DD 格式")
    return text


def topic_slug(name: str) -> str:
    """ASCII-friendly, stable id. Pure-CJK names fall back to a short hash."""
    text = normalize_text(name)
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    ascii_slug = re.sub(r"-{2,}", "-", ascii_slug)[:48].strip("-")
    if ascii_slug:
        return ascii_slug
    return f"topic-{sha1_text(text)[:8]}"


def topic_dir(settings: Settings, topic_id: str) -> Path:
    if not TOPIC_ID_RE.fullmatch(topic_id or ""):
        raise WorkspaceError("非法的 topic id")
    root = (settings.workspace_root / topic_id).resolve()
    workspace = settings.workspace_root.resolve()
    if workspace not in root.parents:
        raise WorkspaceError("topic 路径越界")
    return root


def create_topic(
    settings: Settings,
    *,
    name: str,
    keywords: Iterable[str] | None = None,
    question: str = "",
    from_date: str = "",
    to_date: str = "",
    max_results: int | None = None,
) -> dict[str, Any]:
    topic_name = normalize_text(name)
    if not topic_name:
        raise WorkspaceError("主题名称不能为空")
    topic_id = topic_slug(topic_name)
    if not TOPIC_ID_RE.fullmatch(topic_id):
        raise WorkspaceError("主题名称无法生成合法 id")
    root = topic_dir(settings, topic_id)
    if (root / TOPIC_FILE).exists():
        raise WorkspaceError(f"主题已存在：{topic_id}")

    ensure_workspace_structure(settings.workspace_root, topic_id)
    for extra in ("output", "logs"):
        (root / extra).mkdir(parents=True, exist_ok=True)
    topic = {
        "topic_id": topic_id,
        "topic_name": topic_name,
        "keywords": _clean_keywords(keywords),
        "primary_question": normalize_text(question),
        "from_date": _clean_date(from_date),
        "to_date": _clean_date(to_date),
        "max_results_per_source": max(1, min(int(max_results or settings.max_results_per_source), 50)),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    write_json(root / TOPIC_FILE, topic)
    write_json(root / RUNS_FILE, [])
    return topic


def load_topic(root: Path) -> dict[str, Any]:
    payload = read_json(root / TOPIC_FILE, default={})
    return payload if isinstance(payload, dict) else {}


def save_topic(root: Path, topic: dict[str, Any]) -> None:
    topic["updated_at"] = now_iso()
    write_json(root / TOPIC_FILE, topic)


def get_topic(settings: Settings, topic_id: str) -> dict[str, Any]:
    root = topic_dir(settings, topic_id)
    topic = load_topic(root)
    if not topic:
        raise KeyError(topic_id)
    return topic


def list_topics(settings: Settings) -> list[dict[str, Any]]:
    workspace = settings.workspace_root
    if not workspace.exists():
        return []
    topics: list[dict[str, Any]] = []
    for entry in sorted(workspace.iterdir()):
        if not entry.is_dir() or not TOPIC_ID_RE.fullmatch(entry.name):
            continue
        topic = load_topic(entry)
        if not topic:
            continue
        topics.append(
            {
                "topic_id": topic.get("topic_id") or entry.name,
                "topic_name": topic.get("topic_name") or entry.name,
                "keywords": topic.get("keywords") or [],
                "created_at": topic.get("created_at"),
                "updated_at": topic.get("updated_at"),
                "last_run": last_run(entry),
                "catalog_exists": (entry / CATALOG_FILE).exists(),
                "record_count": len(read_jsonl(entry / RANKED_FILE)),
            }
        )
    topics.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return topics


# ---------------------------------------------------------------- runs


def read_runs(root: Path) -> list[dict[str, Any]]:
    payload = read_json(root / RUNS_FILE, default=[])
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def write_runs(root: Path, runs: list[dict[str, Any]]) -> None:
    write_json(root / RUNS_FILE, runs[-50:])


def last_run(root: Path) -> dict[str, Any] | None:
    runs = read_runs(root)
    return runs[-1] if runs else None


def record_run(root: Path, run: dict[str, Any]) -> None:
    runs = [row for row in read_runs(root) if row.get("run_id") != run.get("run_id")]
    runs.append(run)
    write_runs(root, runs)


def update_run(root: Path, run_id: str, **changes: Any) -> dict[str, Any]:
    runs = read_runs(root)
    for row in runs:
        if row.get("run_id") == run_id:
            row.update(changes)
            write_runs(root, runs)
            return row
    row = {"run_id": run_id, **changes}
    runs.append(row)
    write_runs(root, runs)
    return row


# ---------------------------------------------------------------- artifacts


def read_ranked(root: Path) -> list[dict[str, Any]]:
    return read_jsonl(root / RANKED_FILE)


def write_ranked(root: Path, rows: list[dict[str, Any]]) -> None:
    write_jsonl(root / RANKED_FILE, rows)


def write_pending(root: Path, rows: list[dict[str, Any]]) -> None:
    write_jsonl(root / PENDING_FILE, rows)


def read_raw(root: Path, source: str) -> list[dict[str, Any]]:
    return read_jsonl(root / RAW_SEARCH_DIR / f"{source}.jsonl")


def write_raw(root: Path, source: str, rows: list[dict[str, Any]]) -> None:
    write_jsonl(root / RAW_SEARCH_DIR / f"{source}.jsonl", rows)


def catalog_path(root: Path) -> Path:
    return root / CATALOG_FILE


def topic_summary(settings: Settings, topic_id: str) -> dict[str, Any]:
    root = topic_dir(settings, topic_id)
    topic = load_topic(root)
    if not topic:
        raise KeyError(topic_id)
    rows = read_ranked(root)
    return {
        **topic,
        "root": str(root),
        "record_count": len(rows),
        "sources": sorted({str(row.get("source")) for row in rows if row.get("source")}),
        "last_run": last_run(root),
        "catalog_exists": catalog_path(root).exists(),
        "catalog_url": f"/api/topics/{topic_id}/catalog" if catalog_path(root).exists() else None,
    }
