from __future__ import annotations

import pytest

from sextant.workspace import (
    TOPIC_FILE,
    WorkspaceError,
    create_topic,
    list_topics,
    load_topic,
    topic_dir,
    topic_slug,
)


def test_create_topic_writes_layout(settings):
    topic = create_topic(
        settings,
        name="LLM Agent Evaluation",
        keywords=["LLM agent", "benchmark", "LLM agent"],
        question="How are agents evaluated?",
    )
    root = topic_dir(settings, topic["topic_id"])
    assert topic["topic_id"] == "llm-agent-evaluation"
    assert topic["keywords"] == ["LLM agent", "benchmark"]
    assert (root / TOPIC_FILE).exists()
    assert (root / "02_raw" / "search").is_dir()
    assert (root / "output").is_dir()
    assert load_topic(root)["topic_name"] == "LLM Agent Evaluation"


def test_duplicate_topic_rejected(settings):
    create_topic(settings, name="Graph Neural Networks")
    with pytest.raises(WorkspaceError, match="已存在"):
        create_topic(settings, name="Graph Neural Networks")


def test_invalid_date_rejected(settings):
    with pytest.raises(WorkspaceError, match="YYYY-MM-DD"):
        create_topic(settings, name="Bad Date", from_date="2024/01/01")


def test_path_traversal_rejected(settings):
    with pytest.raises(WorkspaceError):
        topic_dir(settings, "../escape")


def test_topic_slug_handles_chinese():
    slug = topic_slug("知识图谱推理")
    assert slug.startswith("topic-")
    assert len(slug) == len("topic-") + 8
    assert topic_slug("LLM Agent Evaluation") == "llm-agent-evaluation"


def test_list_topics_orders_and_reports(settings):
    create_topic(settings, name="Topic A")
    create_topic(settings, name="Topic B")
    rows = list_topics(settings)
    assert {row["topic_id"] for row in rows} == {"topic-a", "topic-b"}
    assert all(row["record_count"] == 0 for row in rows)
