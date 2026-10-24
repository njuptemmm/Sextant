"""Stage 4 — score records and assign a research direction for grouping."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from sextant.engine.common import OTHER_DIRECTION, normalize_text

FALLBACK_DIRECTION = OTHER_DIRECTION

DIRECTION_DESCRIPTION = "围绕「{name}」方向的相关研究，按综合得分排序，保留来源证据。"


def _keywords(topic: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    values = [*(topic.get("keywords") or []), *(plan.get("keywords") or [])]
    seen: set[str] = set()
    keywords: list[str] = []
    for value in values:
        text = normalize_text(value)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            keywords.append(text)
    return keywords


def _hits(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    return [keyword for keyword in keywords if keyword.lower() in lowered]


def _recency_score(published_at: str) -> tuple[float, str]:
    raw = normalize_text(published_at)[:10]
    if not raw:
        return 0.0, "无发表日期"
    try:
        published = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return 0.0, "发表日期无法解析"
    days = (date.today() - published).days
    if days <= 365:
        return 20.0, "近一年内发表"
    if days <= 730:
        return 12.0, "近两年内发表"
    if days <= 1825:
        return 6.0, "五年内发表"
    return 1.0, "发表超过五年"


def _citation_score(citations: int) -> tuple[float, str]:
    if citations <= 0:
        return 0.0, "暂无引用数据"
    score = min(20.0, math.log10(1 + citations) * 6.5)
    return round(score, 1), f"被引 {citations} 次"


def _direction(record: dict[str, Any], enriched: dict[str, Any], keywords: list[str]) -> str:
    explicit = normalize_text(enriched.get("research_direction"))
    if explicit:
        return explicit
    title_hits = _hits(normalize_text(record.get("title")), keywords)
    abstract_hits = _hits(normalize_text(record.get("abstract")), keywords)
    tally: dict[str, int] = {}
    for keyword in title_hits:
        tally[keyword] = tally.get(keyword, 0) + 2
    for keyword in abstract_hits:
        tally[keyword] = tally.get(keyword, 0) + 1
    if not tally:
        return FALLBACK_DIRECTION
    return max(tally, key=lambda keyword: (tally[keyword], -len(keyword)))


def score_records(
    records: list[dict[str, Any]],
    *,
    topic: dict[str, Any],
    plan: dict[str, Any],
    enriched: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    keywords = _keywords(topic, plan)
    enrichment = enriched or {}
    scored: list[dict[str, Any]] = []

    for record in records:
        reasons: list[str] = []
        title = normalize_text(record.get("title"))
        abstract = normalize_text(record.get("abstract"))
        title_hits = _hits(title, keywords)
        abstract_hits = _hits(abstract, keywords)
        matched = sorted({*title_hits, *abstract_hits}, key=len, reverse=True)

        coverage = len(matched) / max(1, len(keywords))
        keyword_score = round(40.0 * coverage, 1)
        if matched:
            reasons.append("命中关键词：" + "、".join(matched[:4]))

        title_bonus = 10.0 if title_hits else 0.0
        if title_hits:
            reasons.append("标题直接命中主题")

        citation_score, citation_reason = _citation_score(int(record.get("citations") or 0))
        reasons.append(citation_reason)

        recency_score_value, recency_reason = _recency_score(str(record.get("published_at") or ""))
        reasons.append(recency_reason)

        corroboration = 5.0 if int(record.get("source_count") or 1) >= 2 else 0.0
        if corroboration:
            reasons.append("多个来源同时命中")

        quality_bonus = 5.0 if record.get("abstract_usable") else 0.0

        total = round(
            min(100.0, keyword_score + title_bonus + citation_score + recency_score_value + corroboration + quality_bonus),
            1,
        )

        item = dict(record)
        item.update(
            {
                "title_zh": normalize_text((enrichment.get(str(record.get("record_id"))) or {}).get("title_zh")),
                "abstract_zh": normalize_text((enrichment.get(str(record.get("record_id"))) or {}).get("abstract_zh")),
                "plain_language_summary_zh": normalize_text(
                    (enrichment.get(str(record.get("record_id"))) or {}).get("plain_summary_zh")
                ),
                "ranking_score": total,
                "ranking_reasons": reasons,
                "research_direction": _direction(record, enrichment.get(str(record.get("record_id"))) or {}, keywords),
            }
        )
        item["research_direction_description"] = DIRECTION_DESCRIPTION.format(name=item["research_direction"])
        scored.append(item)

    scored.sort(key=lambda row: (-float(row.get("ranking_score") or 0), normalize_text(row.get("title"))))
    return scored
