"""Stage 1 — turn a topic into concrete retrieval queries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sextant.config import Settings
from sextant.engine.common import normalize_text, write_json
from sextant.llm import plan_queries
from sextant.runlog import RunLogger, utc_now_iso
from sextant.sources_registry import SPECS, active_sources, build_query, describe_specs
from sextant.workspace import QUERIES_FILE


def _keywords(topic: dict[str, Any]) -> list[str]:
    values = [normalize_text(item) for item in topic.get("keywords") or []]
    keywords = [value for value in values if value]
    return keywords or [normalize_text(topic.get("topic_name")) or "research"]


def _heuristic_queries(topic: dict[str, Any]) -> list[dict[str, str]]:
    keywords = _keywords(topic)
    topic_name = normalize_text(topic.get("topic_name")) or "research"
    queries: list[dict[str, str]] = []
    for spec in active_sources(topic):
        queries.append(
            {
                "source": spec.id,
                "query": build_query(spec, keywords, topic_name),
                "rationale": f"{spec.label}：{spec.discipline}，权威度 {spec.authority}。",
            }
        )
    return queries


def _merge_queries(
    heuristic: list[dict[str, str]],
    planned: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    allowed = {spec.id for spec in SPECS.values()}
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in [*(planned or []), *heuristic]:
        source = normalize_text(row.get("source")).lower().replace("-", "_")
        query = normalize_text(row.get("query"))
        if source not in allowed or not query:
            continue
        key = (source, query.lower())
        if key in seen:
            continue
        seen.add(key)
        merged.append(
            {
                "source": source,
                "query": query,
                "rationale": normalize_text(row.get("rationale")),
            }
        )
    per_source: dict[str, int] = {}
    limited: list[dict[str, str]] = []
    for row in merged:
        count = per_source.get(row["source"], 0)
        if count >= 2:
            continue
        per_source[row["source"]] = count + 1
        limited.append(row)
    return limited


def build_query_plan(root: Path, topic: dict[str, Any], settings: Settings, logger: RunLogger) -> dict[str, Any]:
    heuristic = _heuristic_queries(topic)
    planned = plan_queries(settings, topic)
    queries = _merge_queries(heuristic, planned.get("queries") if planned else None)
    keywords = list(topic.get("keywords") or [])
    if planned and planned.get("keywords"):
        keywords = list(dict.fromkeys([*keywords, *planned["keywords"]]))
    directions = planned.get("directions", []) if planned else []
    specs = [spec for spec in active_sources(topic) if any(row["source"] == spec.id for row in queries)]

    plan = {
        "topic_id": topic.get("topic_id"),
        "topic_name": topic.get("topic_name"),
        "generated_at": utc_now_iso(),
        "planner": "llm+heuristic" if planned else "heuristic",
        "keywords": keywords,
        "directions": directions,
        "sources": describe_specs(specs),
        "queries": queries,
    }
    write_json(root / QUERIES_FILE, plan)
    logger.emit(
        "plan",
        "completed",
        f"选定 {len(specs)} 个来源，生成 {len(queries)} 条检索式",
        planner=plan["planner"],
        keywords=keywords,
        sources=[spec.id for spec in specs],
        queries=[{"source": row["source"], "query": row["query"]} for row in queries],
    )
    return plan
