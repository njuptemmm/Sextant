from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

from sextant import llm
from sextant.config import Settings


def _records(count: int) -> list[dict]:
    return [
        {
            "record_id": f"openalex:W{index}",
            "title": f"Paper {index}",
            "abstract": "An abstract about evaluating language model agents.",
            "source": "openalex",
            "published_at": "2025-01-01",
        }
        for index in range(count)
    ]


def _ids_from_prompt(prompt: str) -> list[str]:
    match = re.search(r"请处理以下 \d+ 篇论文：\n(\[.*?\])\n\n", prompt, flags=re.S)
    assert match, prompt
    return [row["record_id"] for row in json.loads(match.group(1))]


@pytest.fixture()
def llm_settings(settings: Settings) -> Settings:
    return replace(settings, llm_base_url="https://example.invalid/v1", llm_api_key="test", llm_model="test-model")


def test_enrich_records_splits_into_batches(llm_settings, monkeypatch):
    calls: list[list[str]] = []

    def fake_chat_json(settings, *, system, user, max_tokens=2048, temperature=0.2):
        ids = _ids_from_prompt(user)
        calls.append(ids)
        assert max_tokens <= llm.ENRICH_MAX_TOKENS
        return {
            "items": [
                {
                    "record_id": record_id,
                    "title_zh": "标题",
                    "abstract_zh": "摘要",
                    "plain_summary_zh": "一句话",
                    "research_direction": "方向",
                }
                for record_id in ids
            ]
        }

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    result = llm.enrich_records(llm_settings, topic={"topic_name": "LLM agents"}, records=_records(25))

    assert len(calls) == 3
    assert [len(batch) for batch in calls] == [10, 10, 5]
    assert len(result) == 25
    assert result["openalex:W24"]["title_zh"] == "标题"


def test_enrich_records_isolates_failing_batch(llm_settings, monkeypatch):
    seen: list[int] = []

    def flaky_chat_json(settings, *, system, user, max_tokens=2048, temperature=0.2):
        seen.append(1)
        if len(seen) == 2:
            raise llm.LLMError("模型返回内容不是合法 JSON")
        return {
            "items": [
                {"record_id": record_id, "title_zh": "标题"} for record_id in _ids_from_prompt(user)
            ]
        }

    monkeypatch.setattr(llm, "chat_json", flaky_chat_json)
    result = llm.enrich_records(llm_settings, topic={"topic_name": "LLM agents"}, records=_records(25))

    assert len(seen) == 3
    assert len(result) == 15
    assert "openalex:W20" in result  # third batch survives the second batch's failure
    assert "openalex:W10" not in result  # failed batch contributes nothing


def test_enrich_records_ignores_unknown_record_ids(llm_settings, monkeypatch):
    def chat_json_returning_foreign_ids(settings, *, system, user, max_tokens=2048, temperature=0.2):
        ids = _ids_from_prompt(user)
        return {
            "items": [
                {"record_id": ids[0], "title_zh": "标题"},
                {"record_id": "ghost", "title_zh": "伪造"},
            ]
        }

    monkeypatch.setattr(llm, "chat_json", chat_json_returning_foreign_ids)
    result = llm.enrich_records(llm_settings, topic={"topic_name": "LLM agents"}, records=_records(3))

    assert set(result) == {"openalex:W0"}


def test_enrich_records_returns_empty_without_llm(settings, monkeypatch):
    def boom(*args, **kwargs):  # pragma: no cover - must never be reached
        raise AssertionError("LLM must not be called when disabled")

    monkeypatch.setattr(llm, "chat_json", boom)
    assert llm.enrich_records(settings, topic={"topic_name": "x"}, records=_records(5)) == {}
