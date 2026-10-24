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


def test_enrich_records_splits_batch_when_output_is_truncated(llm_settings, monkeypatch):
    """A reasoning model can burn the whole budget; halving the batch must recover."""
    sizes: list[int] = []

    def truncating_chat_json(settings, *, system, user, max_tokens=2048, temperature=0.2):
        ids = _ids_from_prompt(user)
        sizes.append(len(ids))
        if len(ids) > 5:
            raise llm.LLMTruncated("输出达到 max_tokens 上限")
        return {"items": [{"record_id": record_id, "title_zh": "标题"} for record_id in ids]}

    monkeypatch.setattr(llm, "chat_json", truncating_chat_json)
    notes: list[str] = []
    result = llm.enrich_records(
        llm_settings, topic={"topic_name": "LLM agents"}, records=_records(10), notes=notes
    )

    assert sizes == [10, 5, 5]
    assert len(result) == 10
    assert notes and "拆成 5+5" in notes[0]


def test_enrich_records_skips_single_record_that_cannot_fit(llm_settings, monkeypatch):
    def always_truncated(settings, *, system, user, max_tokens=2048, temperature=0.2):
        raise llm.LLMTruncated("输出达到 max_tokens 上限")

    monkeypatch.setattr(llm, "chat_json", always_truncated)
    notes: list[str] = []
    result = llm.enrich_records(
        llm_settings, topic={"topic_name": "LLM agents"}, records=_records(2), notes=notes
    )

    assert result == {}
    assert len(notes) == 3  # one 2-record split, then two single-record failures


def test_enrich_records_returns_empty_without_llm(settings, monkeypatch):
    def boom(*args, **kwargs):  # pragma: no cover - must never be reached
        raise AssertionError("LLM must not be called when disabled")

    monkeypatch.setattr(llm, "chat_json", boom)
    assert llm.enrich_records(settings, topic={"topic_name": "x"}, records=_records(5)) == {}


def test_enrich_records_snaps_labels_to_planned_taxonomy(llm_settings, monkeypatch):
    prompts: list[str] = []

    def fake(settings, *, system, user, max_tokens=2048, temperature=0.2):
        prompts.append(user)
        ids = _ids_from_prompt(user)
        return {
            "items": [
                {"record_id": ids[0], "title_zh": "标题", "research_direction": "基准设计"},
                {"record_id": ids[1], "title_zh": "标题", "research_direction": "我自己发明的方向"},
            ]
        }

    monkeypatch.setattr(llm, "chat_json", fake)
    result = llm.enrich_records(
        llm_settings,
        topic={"topic_name": "DeepSWE"},
        records=_records(2),
        directions=["基准设计", "评测有效性"],
    )

    assert "- 基准设计" in prompts[0] and "- 评测有效性" in prompts[0]
    assert "禁止改写或自创" in prompts[0]
    assert result["openalex:W0"]["research_direction"] == "基准设计"
    assert result["openalex:W1"]["research_direction"] == llm.OTHER_DIRECTION


def test_direction_labels_drop_the_explanatory_tail():
    labels = llm._direction_labels(
        [
            "基准设计与构建：任务来源、容器化、测试信号",
            "数据污染与泄漏: 检测方法、时间切分",
            "实证对比与失败分析",
            "基准设计与构建：重复项",
        ]
    )

    assert labels == ["基准设计与构建", "数据污染与泄漏", "实证对比与失败分析"]


def test_enrich_records_accepts_short_label_from_colon_direction(llm_settings, monkeypatch):
    def fake(settings, *, system, user, max_tokens=2048, temperature=0.2):
        ids = _ids_from_prompt(user)
        return {"items": [{"record_id": rid, "title_zh": "标题", "research_direction": "基准设计与构建"} for rid in ids]}

    monkeypatch.setattr(llm, "chat_json", fake)
    result = llm.enrich_records(
        llm_settings,
        topic={"topic_name": "DeepSWE"},
        records=_records(1),
        directions=["基准设计与构建：任务来源、容器化、测试信号"],
    )

    assert result["openalex:W0"]["research_direction"] == "基准设计与构建"


def test_enrich_records_notes_unexpected_payload_shape(llm_settings, monkeypatch):
    def list_instead_of_object(settings, *, system, user, max_tokens=2048, temperature=0.2):
        return [{"record_id": "openalex:W0", "title_zh": "标题"}]

    monkeypatch.setattr(llm, "chat_json", list_instead_of_object)
    notes: list[str] = []
    result = llm.enrich_records(
        llm_settings, topic={"topic_name": "LLM agents"}, records=_records(2), notes=notes
    )

    assert result == {}
    assert notes and "缺少 items" in notes[0]


def test_plan_queries_uses_reasoning_safe_budget(llm_settings, monkeypatch):
    budgets: list[int] = []

    def fake(settings, *, system, user, max_tokens=2048, temperature=0.2):
        budgets.append(max_tokens)
        return {"keywords": ["DeepSWE"], "queries": [{"source": "arxiv", "query": 'all:"DeepSWE"'}]}

    monkeypatch.setattr(llm, "chat_json", fake)
    result = llm.plan_queries(llm_settings, {"topic_name": "DeepSWE", "keywords": ["DeepSWE"]})

    assert budgets == [llm.PLAN_MAX_TOKENS]
    assert result and result["queries"][0]["source"] == "arxiv"


def test_plan_queries_reports_truncation_reason(llm_settings, monkeypatch):
    def truncated(settings, *, system, user, max_tokens=2048, temperature=0.2):
        raise llm.LLMTruncated("输出达到 max_tokens=900 上限")

    monkeypatch.setattr(llm, "chat_json", truncated)
    notes: list[str] = []
    result = llm.plan_queries(llm_settings, {"topic_name": "DeepSWE"}, notes=notes)

    assert result is None
    assert notes and "模型规划失败" in notes[0]


def test_chat_json_does_not_retry_a_truncated_completion(llm_settings, monkeypatch):
    calls: list[dict] = []

    def fake_post(url, payload, api_key, timeout):
        calls.append(dict(payload))
        return {
            "choices": [{"message": {"content": '{"items": [{"record_id": "x"'}, "finish_reason": "length"}],
            "usage": {"completion_tokens_details": {"reasoning_tokens": 1545}},
        }

    monkeypatch.setattr(llm, "_post_json", fake_post)

    with pytest.raises(llm.LLMTruncated):
        llm.chat_json(llm_settings, system="s", user="u", max_tokens=3200)

    assert len(calls) == 1, "a truncated response cannot be fixed by an identical retry"


def test_chat_json_retries_network_error_once_without_json_mode(llm_settings, monkeypatch):
    import urllib.error

    payloads: list[dict] = []

    def fake_post(url, payload, api_key, timeout):
        payloads.append(dict(payload))
        if len(payloads) == 1:
            raise urllib.error.URLError("connection reset")
        return {"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}]}

    monkeypatch.setattr(llm, "_post_json", fake_post)

    assert llm.chat_json(llm_settings, system="s", user="u") == {"ok": True}
    assert "response_format" in payloads[0]
    assert "response_format" not in payloads[1]
