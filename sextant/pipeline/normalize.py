"""Stage 3 — deduplicate across sources and split usable vs. unverifiable rows."""

from __future__ import annotations

import re
from typing import Any

from sextant.engine.common import normalize_text

_PUNCT = re.compile(r"[^a-z0-9一-鿿]+")


def dedupe_key(record: dict[str, Any]) -> str:
    doi = normalize_text(record.get("doi")).lower()
    if doi:
        return f"doi:{doi}"
    title = _PUNCT.sub(" ", normalize_text(record.get("title")).lower()).strip()
    return f"title:{title}"


def _abstract_rank(record: dict[str, Any]) -> tuple[int, int]:
    usable = 1 if record.get("abstract_usable") else 0
    return usable, len(record.get("abstract") or "")


def merge_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    base = max(group, key=_abstract_rank).copy()
    base["sources"] = sorted({str(row.get("source")) for row in group if row.get("source")})
    base["source_count"] = len(base["sources"])
    base["queries"] = sorted({str(row.get("query")) for row in group if row.get("query")})
    base["citations"] = max(int(row.get("citations") or 0) for row in group)
    base["duplicate_count"] = len(group) - 1
    base["merged_record_ids"] = sorted({str(row.get("record_id")) for row in group if row.get("record_id")})
    urls = [normalize_text(row.get("url")) for row in group]
    base["url"] = normalize_text(base.get("url")) or next((url for url in urls if url), "")
    return base


def normalize(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        title = normalize_text(record.get("title"))
        if not title:
            continue
        record["title"] = title
        groups.setdefault(dedupe_key(record), []).append(record)

    merged = [merge_group(group) for group in groups.values()]
    usable: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for record in merged:
        if record.get("abstract_usable"):
            usable.append(record)
            continue
        quality = (record.get("extra") or {}).get("abstract_quality") or {}
        reasons = quality.get("reasons") or ["unusable"]
        record["pending_verification_reason"] = (
            "摘要未通过质量门控：" + "、".join(str(reason) for reason in reasons)
        )
        pending.append(record)
    return usable, pending
