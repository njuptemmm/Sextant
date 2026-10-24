from __future__ import annotations

from pathlib import Path

from sextant.engine.render_html import render_catalog
from sextant.pipeline.normalize import normalize
from sextant.pipeline.plan import build_query_plan
from sextant.pipeline.rank import score_records
from sextant.runlog import EventBus, RunLogger
from sextant.workspace import RANKED_FILE, catalog_path, create_topic, topic_dir, write_ranked


def _record(**overrides):
    base = {
        "record_id": "openalex:W1",
        "source": "openalex",
        "title": "Evaluating LLM agents with benchmarks",
        "abstract": (
            "We present a benchmark suite for evaluating LLM agent behaviour across planning, "
            "tool use and long-horizon tasks. The suite contains reproducible environments and "
            "reference baselines for comparing agent architectures under identical budgets."
        ),
        "abstract_source": "source_metadata",
        "abstract_usable": True,
        "published_at": "2025-06-01",
        "doi": "10.1000/example",
        "url": "https://example.org/paper",
        "journal": "Example Journal",
        "authors": ["A. Author"],
        "citations": 12,
        "evidence_type": "scholarly_work",
        "query": "LLM agent evaluation",
        "extra": {"abstract_quality": {"usable": True, "reasons": [], "score": 100}},
    }
    base.update(overrides)
    return base


def test_normalize_merges_cross_source_duplicates():
    rows = [
        _record(),
        _record(record_id="arxiv:2401.00001", source="arxiv", citations=3),
        _record(record_id="openalex:W2", doi="10.1000/other", title="A different paper"),
    ]
    usable, pending = normalize(rows)
    assert len(usable) == 2
    assert pending == []
    merged = next(row for row in usable if row["doi"] == "10.1000/example")
    assert merged["sources"] == ["arxiv", "openalex"]
    assert merged["source_count"] == 2
    assert merged["duplicate_count"] == 1
    assert merged["citations"] == 12


def test_normalize_routes_low_quality_abstracts_to_pending():
    rows = [_record(record_id="x", doi="", abstract="", abstract_usable=False, title="No abstract here")]
    usable, pending = normalize(rows)
    assert usable == []
    assert len(pending) == 1
    assert "质量门控" in pending[0]["pending_verification_reason"]


def test_rank_prefers_stronger_match_and_recency():
    topic = {"topic_name": "LLM agent evaluation", "keywords": ["LLM agent", "evaluation"]}
    plan = {"keywords": []}
    strong = _record(record_id="strong", title="LLM agent evaluation benchmark")
    weak = _record(
        record_id="weak",
        doi="10.1000/weak",
        title="Unrelated marine biology survey",
        abstract="A survey of coral reef ecology with no relation to software systems.",
        published_at="2015-01-01",
        citations=0,
    )
    ranked = score_records([weak, strong], topic=topic, plan=plan)
    assert [row["record_id"] for row in ranked][0] == "strong"
    assert ranked[0]["ranking_score"] > ranked[1]["ranking_score"]
    assert ranked[0]["research_direction"] == "LLM agent"
    assert ranked[0]["ranking_reasons"]


def test_biomedical_detection_uses_whole_words():
    from sextant.sources_registry import _is_biomedical

    assert _is_biomedical({"topic_name": "retrieval augmented generation", "keywords": ["RAG"]}) is False
    assert _is_biomedical({"topic_name": "cancer therapy", "keywords": []}) is True
    assert _is_biomedical({"topic_name": "单细胞测序", "keywords": []}) is True


def test_plan_selects_core_sources_and_caps_queries(settings):
    topic = create_topic(settings, name="LLM Agent Evaluation", keywords=["LLM agent", "evaluation"])
    root = topic_dir(settings, topic["topic_id"])
    logger = RunLogger(topic_root=root, run_id="test-plan", bus=EventBus(), topic_id=topic["topic_id"])
    plan = build_query_plan(root, topic, settings, logger)
    sources = {row["source"] for row in plan["queries"]}
    assert {"openalex", "arxiv"} <= sources
    per_source: dict[str, int] = {}
    for row in plan["queries"]:
        per_source[row["source"]] = per_source.get(row["source"], 0) + 1
    assert max(per_source.values()) <= 2
    assert (root / "01_queries" / "queries.json").exists()


def test_render_catalog_produces_html(settings):
    topic = create_topic(settings, name="Agent Evaluation", keywords=["agent", "evaluation"])
    root = topic_dir(settings, topic["topic_id"])
    rows = [
        {
            "record_id": "openalex:W1",
            "source": "openalex",
            "title": "Evaluating LLM agents with benchmarks",
            "abstract": "A reproducible benchmark suite for LLM agents covering planning and tool use.",
            "abstract_source": "source_metadata",
            "published_at": "2025-06-01",
            "url": "https://example.org/paper",
            "journal": "Example Journal",
            "evidence_type": "scholarly_work",
            "ranking_score": 88.5,
            "research_direction": "Agent Evaluation",
            "ranking_reasons": ["命中关键词：agent、evaluation"],
        }
    ]
    write_ranked(root, rows)
    target = render_catalog(root, output=catalog_path(root), strict_chinese=False)
    html = Path(target).read_text(encoding="utf-8")
    assert "Evaluating LLM agents with benchmarks" in html
    assert "Agent Evaluation" in html
    assert (root / RANKED_FILE).exists()
