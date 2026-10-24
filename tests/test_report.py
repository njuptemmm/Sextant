from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from sextant import llm
from sextant.config import Settings
from sextant.engine.common import (
    OTHER_DIRECTION,
    paper_cards_file,
    read_json,
    report_content_en_file,
    report_content_zh_file,
)
from sextant.engine.render_html import summary_file
from sextant.pipeline.report import build_report
from sextant.workspace import create_topic, topic_dir

SECTIONS_ZH = {
    "research_question": "本报告关注大语言模型智能体在规划、工具使用与长程任务中的评测问题与方法差异。",
    "methods": "现有工作主要采用基准套件、容器化任务环境与自动化测试信号来构造可复现的评测流程。",
    "samples": "纳入的文献覆盖多个公开基准与自建任务集，样本规模与任务难度在不同工作之间存在明显差异。",
    "results": "总体来看，仓库级任务仍然是当前智能体能力的主要瓶颈，工具使用与规划策略影响显著。",
    "limitations": "多数工作的评测环境与任务分布有限，结论向真实软件工程场景外推时需要谨慎。",
    "business_impact": "这些评测方法可以帮助团队在引入编码智能体前建立可复现的验收基线并控制回归风险。",
}

SECTIONS_EN = {
    key: f"English synthesis for {key}: the available papers report reproducible benchmarks and baselines."
    for key in SECTIONS_ZH
}


def _row(index: int) -> dict:
    return {
        "record_id": f"openalex:W{index}",
        "source": "openalex",
        "title": f"Benchmarking LLM agents for software engineering {index}",
        "title_zh": f"面向软件工程的智能体评测 {index}",
        "abstract": (
            "We present a reproducible benchmark suite for LLM agents covering planning, tool use "
            "and long-horizon software engineering tasks with reference baselines."
        ),
        "abstract_zh": f"本文提出一个可复现的智能体评测基准，覆盖规划、工具使用与长程软件工程任务，编号 {index}。",
        "abstract_source": "source_metadata",
        "published_at": "2025-06-01",
        "journal": "Example Journal",
        "url": "https://example.org/paper",
        "research_direction": "评测方法",
        "plain_summary_zh": "一个面向软件工程智能体的可复现评测基准。",
        "ranking_score": 90 - index,
        "rank": index,
    }


def _analysis(rows: list[dict]) -> dict[str, dict[str, str]]:
    return {
        row["record_id"]: {
            "impact_zh": "该工作为智能体能力评估提供了可复现的基准与基线，有助于比较不同架构的实际效果。",
            "impact_en": (
                "The work provides a reproducible benchmark and baselines for comparing agent "
                "architectures under identical budgets."
            ),
            "method_category": "评测方法",
        }
        for row in rows
    }


def _synthesis(paper_ids: list[str]) -> dict:
    return {
        "title_zh": "软件工程智能体评测研究综述",
        "title_en": "A Review of Agent Evaluation for Software Engineering",
        "executive_summary_zh": (
            "本报告梳理了软件工程智能体评测的代表性工作，重点比较基准构建、任务设计与评测有效性，"
            "并给出面向实际部署的证据边界。"
        ),
        "executive_summary_en": (
            "This report reviews representative work on evaluating software-engineering agents, "
            "comparing benchmark construction, task design and evaluation validity."
        ),
        "sections_zh": SECTIONS_ZH,
        "sections_en": SECTIONS_EN,
        "subthemes": [
            {
                "title_zh": "基准构建与任务设计",
                "title_en": "Benchmark construction",
                "summary_zh": "这一方向关注任务来源、容器化环境与测试信号的设计，目标是让评测可复现且可比较。",
                "summary_en": (
                    "This family focuses on task sources, containerized environments and test signals "
                    "so that evaluation stays reproducible and comparable."
                ),
                "paper_ids": paper_ids[:2],
            },
            {
                "title_zh": "评测有效性与污染控制",
                "title_en": "Evaluation validity",
                "summary_zh": "这一方向关注基准污染、动态评测与指标设计，讨论评测结论是否真正反映能力。",
                "summary_en": (
                    "This family studies benchmark contamination, dynamic evaluation and metric design, "
                    "asking whether conclusions actually reflect capability."
                ),
                "paper_ids": paper_ids[2:],
            },
        ],
    }


@pytest.fixture()
def llm_settings(settings: Settings) -> Settings:
    return replace(
        settings,
        llm_base_url="https://example.invalid/v1",
        llm_api_key="test",
        llm_model="test-model",
    )


def _topic(settings: Settings):
    topic = create_topic(settings, name="Agent Evaluation", keywords=["agent", "evaluation"])
    return topic, topic_dir(settings, topic["topic_id"])


def test_build_report_writes_bilingual_content_and_renders(llm_settings, monkeypatch):
    topic, root = _topic(llm_settings)
    rows = [_row(1), _row(2), _row(3)]
    monkeypatch.setattr("sextant.pipeline.report.analyze_report_papers", lambda *a, **k: _analysis(rows))
    monkeypatch.setattr(
        "sextant.pipeline.report.synthesize_report",
        lambda *a, **k: _synthesis([row["record_id"] for row in rows]),
    )
    notes: list[str] = []

    target = build_report(
        root,
        topic=topic,
        plan={"directions": ["评测方法"]},
        ranked=rows,
        settings=llm_settings,
        notes=notes,
    )

    assert target == summary_file(root)
    assert target.exists()
    cards = read_json(paper_cards_file(root), default={})["papers"]
    assert len(cards) == 3
    assert all(card["abstract"] and card["english"]["abstract"] for card in cards)
    assert all(card["research_and_application_impact"] for card in cards)
    content_zh = read_json(report_content_zh_file(root), default={})
    content_en = read_json(report_content_en_file(root), default={})
    assert len(content_zh["subthemes"]) == len(content_en["subthemes"]) == 2
    assert {pid for theme in content_zh["subthemes"] for pid in theme["paper_ids"]} == {
        row["record_id"] for row in rows
    }
    html = Path(target).read_text(encoding="utf-8")
    assert "软件工程智能体评测研究综述" in html
    assert 'class="missing"' not in html
    assert "待补中文" not in html
    assert notes == []


def test_build_report_folds_unassigned_papers_into_other(llm_settings, monkeypatch):
    topic, root = _topic(llm_settings)
    rows = [_row(1), _row(2), _row(3)]
    synthesis = _synthesis([row["record_id"] for row in rows])
    synthesis["subthemes"] = synthesis["subthemes"][:1]  # third paper is left over
    monkeypatch.setattr("sextant.pipeline.report.analyze_report_papers", lambda *a, **k: _analysis(rows))
    monkeypatch.setattr("sextant.pipeline.report.synthesize_report", lambda *a, **k: synthesis)
    notes: list[str] = []

    target = build_report(
        root, topic=topic, plan={}, ranked=rows, settings=llm_settings, notes=notes
    )

    assert target is not None
    content_zh = read_json(report_content_zh_file(root), default={})
    assert content_zh["subthemes"][-1]["title"] == OTHER_DIRECTION
    assert rows[2]["record_id"] in content_zh["subthemes"][-1]["paper_ids"]
    assert any("并入" in note for note in notes)


def test_build_report_repairs_short_sections(llm_settings, monkeypatch):
    topic, root = _topic(llm_settings)
    rows = [_row(1), _row(2), _row(3)]
    synthesis = _synthesis([row["record_id"] for row in rows])
    synthesis["sections_zh"] = {"research_question": "太短"}
    monkeypatch.setattr("sextant.pipeline.report.analyze_report_papers", lambda *a, **k: _analysis(rows))
    monkeypatch.setattr("sextant.pipeline.report.synthesize_report", lambda *a, **k: synthesis)

    target = build_report(root, topic=topic, plan={}, ranked=rows, settings=llm_settings)

    assert target is not None
    content_zh = read_json(report_content_zh_file(root), default={})
    assert "未能由模型生成综合描述" in content_zh["sections"]["methods"]


def test_build_report_skips_without_llm(settings):
    topic, root = _topic(settings)
    notes: list[str] = []

    target = build_report(
        root, topic=topic, plan={}, ranked=[_row(1), _row(2), _row(3)], settings=settings, notes=notes
    )

    assert target is None
    assert notes and "未配置模型" in notes[0]


def test_build_report_skips_when_too_few_papers(llm_settings):
    topic, root = _topic(llm_settings)
    notes: list[str] = []

    target = build_report(root, topic=topic, plan={}, ranked=[_row(1)], settings=llm_settings, notes=notes)

    assert target is None
    assert notes and "不足" in notes[0]


def test_build_report_reports_renderer_rejection(llm_settings, monkeypatch):
    """A broken payload must not fail the run; it returns None with a reason."""
    topic, root = _topic(llm_settings)
    rows = [_row(1), _row(2), _row(3)]
    synthesis = _synthesis([row["record_id"] for row in rows])
    synthesis["subthemes"] = []  # normalization builds a fallback, so force a bad title instead
    synthesis["title_zh"] = "标题"
    monkeypatch.setattr("sextant.pipeline.report.analyze_report_papers", lambda *a, **k: _analysis(rows))
    monkeypatch.setattr("sextant.pipeline.report.synthesize_report", lambda *a, **k: synthesis)
    monkeypatch.setattr("sextant.pipeline.report.render_report", lambda root: (_ for _ in ()).throw(
        SystemExit("forced rejection")
    ))
    notes: list[str] = []

    target = build_report(
        root, topic=topic, plan={}, ranked=rows, settings=llm_settings, notes=notes
    )

    assert target is None
    assert any("报告校验未通过" in note for note in notes)


def test_build_report_retries_when_synthesis_mentions_workflow(llm_settings, monkeypatch):
    topic, root = _topic(llm_settings)
    rows = [_row(1), _row(2), _row(3)]
    paper_ids = [row["record_id"] for row in rows]
    dirty = _synthesis(paper_ids)
    dirty["executive_summary_zh"] = (
        "本报告依据 3 篇论文摘要梳理该方向的近期进展，并给出总体结论与证据边界。"
    )
    clean = _synthesis(paper_ids)
    corrections: list[str] = []

    def fake_synthesis(*args, **kwargs):
        corrections.append(kwargs.get("correction", ""))
        return dirty if len(corrections) == 1 else clean

    monkeypatch.setattr("sextant.pipeline.report.analyze_report_papers", lambda *a, **k: _analysis(rows))
    monkeypatch.setattr("sextant.pipeline.report.synthesize_report", fake_synthesis)
    notes: list[str] = []

    target = build_report(root, topic=topic, plan={}, ranked=rows, settings=llm_settings, notes=notes)

    assert target is not None
    assert len(corrections) == 2
    assert corrections[1] and "重写" in corrections[1]
    content = read_json(report_content_zh_file(root), default={})
    assert "篇论文" not in content["executive_summary"]
    assert any("流程元信息" in note for note in notes)


def test_build_report_retries_when_subtheme_summary_mentions_workflow(llm_settings, monkeypatch):
    topic, root = _topic(llm_settings)
    rows = [_row(1), _row(2), _row(3)]
    paper_ids = [row["record_id"] for row in rows]
    dirty = _synthesis(paper_ids)
    dirty["subthemes"][0]["summary_zh"] = (
        "经去重与语义审查后，本节围绕基准构建与任务设计纳入 3 篇论文分别评述。"
    )
    clean = _synthesis(paper_ids)
    calls: list[str] = []

    def fake_synthesis(*args, **kwargs):
        calls.append(kwargs.get("correction", ""))
        return dirty if len(calls) == 1 else clean

    monkeypatch.setattr("sextant.pipeline.report.analyze_report_papers", lambda *a, **k: _analysis(rows))
    monkeypatch.setattr("sextant.pipeline.report.synthesize_report", fake_synthesis)
    notes: list[str] = []

    target = build_report(root, topic=topic, plan={}, ranked=rows, settings=llm_settings, notes=notes)

    assert target is not None
    assert len(calls) == 2
    content = read_json(report_content_zh_file(root), default={})
    assert content["subthemes"][0]["summary"] == clean["subthemes"][0]["summary_zh"]
    assert any("流程元信息" in note for note in notes)


def test_meta_detection_ignores_research_subjects(llm_settings, monkeypatch):
    """A synthesis about retrieval, de-duplication or open access is content, not meta."""
    topic, root = _topic(llm_settings)
    rows = [_row(1), _row(2), _row(3)]
    paper_ids = [row["record_id"] for row in rows]
    synthesis = _synthesis(paper_ids)
    sections = dict(SECTIONS_ZH)
    sections["samples"] = (
        "相关工作覆盖开放获取期刊与多源检索场景，并以滑动时间窗口评估去重算法对评测结果的影响。"
    )
    synthesis["sections_zh"] = sections
    calls: list[int] = []

    def fake_synthesis(*args, **kwargs):
        calls.append(1)
        return synthesis

    monkeypatch.setattr("sextant.pipeline.report.analyze_report_papers", lambda *a, **k: _analysis(rows))
    monkeypatch.setattr("sextant.pipeline.report.synthesize_report", fake_synthesis)
    notes: list[str] = []

    target = build_report(root, topic=topic, plan={}, ranked=rows, settings=llm_settings, notes=notes)

    assert target is not None
    assert len(calls) == 1
    assert not any("流程元信息" in note for note in notes)


def test_fallbacks_do_not_describe_the_workflow(llm_settings, monkeypatch):
    topic, root = _topic(llm_settings)
    rows = [_row(1), _row(2), _row(3)]
    synthesis = _synthesis([row["record_id"] for row in rows])
    synthesis["executive_summary_zh"] = "太短"
    synthesis["sections_zh"] = {}
    synthesis["sections_en"] = {}
    for theme in synthesis["subthemes"]:
        theme["summary_zh"] = "太短"
        theme["summary_en"] = ""
    monkeypatch.setattr("sextant.pipeline.report.analyze_report_papers", lambda *a, **k: _analysis(rows))
    monkeypatch.setattr("sextant.pipeline.report.synthesize_report", lambda *a, **k: synthesis)

    target = build_report(root, topic=topic, plan={}, ranked=rows, settings=llm_settings)

    assert target is not None
    content = read_json(report_content_zh_file(root), default={})
    joined = " ".join(
        [
            content["executive_summary"],
            *content["sections"].values(),
            *(theme["summary"] for theme in content["subthemes"]),
        ]
    )
    for term in ("纳入", "篇文献", "篇论文", "来源", "检索", "去重"):
        assert term not in joined

    content_en = read_json(report_content_en_file(root), default={})
    joined_en = " ".join(
        [
            content_en["executive_summary"],
            *content_en["sections"].values(),
            *(theme["summary"] for theme in content_en["subthemes"]),
        ]
    )
    assert not re.search(r"\b\d+\s+(papers?|sources?|abstracts?)\b", joined_en)
    assert "covers" not in joined_en
    assert "dedup" not in joined_en


def test_run_pipeline_runs_report_stage_and_records_it(settings, monkeypatch):
    from sextant.pipeline import runner
    from sextant.workspace import read_runs

    llm_settings = replace(
        settings,
        llm_base_url="https://example.invalid/v1",
        llm_api_key="test",
        llm_model="test-model",
    )
    topic = create_topic(llm_settings, name="Agent Evaluation", keywords=["agent"])
    root = topic_dir(llm_settings, topic["topic_id"])
    rows = [
        {
            "record_id": f"openalex:W{index}",
            "source": "openalex",
            "title": f"Agent evaluation paper {index}",
            "abstract": "A reproducible benchmark for LLM agents covering planning and tool use. " * 3,
            "abstract_usable": True,
            "abstract_source": "source_metadata",
            "published_at": "2025-01-01",
            "url": "https://example.org",
            "journal": "Example Journal",
        }
        for index in range(3)
    ]
    monkeypatch.setattr(
        runner,
        "build_query_plan",
        lambda *a, **k: {"keywords": ["agent"], "directions": ["评测方法"], "queries": []},
    )
    monkeypatch.setattr(runner, "retrieve", lambda *a, **k: rows)
    monkeypatch.setattr(runner, "enrich_records", lambda *a, **k: {})
    calls: list[dict] = []

    def fake_report(root_arg, *, topic, plan, ranked, settings, notes=None):
        calls.append({"ranked": len(ranked), "directions": plan.get("directions")})
        target = root_arg / "output" / "report.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("<html>report</html>", encoding="utf-8")
        return target

    monkeypatch.setattr(runner, "build_report", fake_report)

    summary = runner.run_pipeline(llm_settings, topic["topic_id"], run_id="test-report")

    assert calls == [{"ranked": 3, "directions": ["评测方法"]}]
    assert summary["report"] == str(root / "output" / "report.html")
    assert read_runs(root)[-1]["report"] == summary["report"]


def test_run_pipeline_removes_stale_report_when_stage_is_skipped(settings, monkeypatch):
    from sextant.pipeline import runner

    llm_settings = replace(
        settings,
        llm_base_url="https://example.invalid/v1",
        llm_api_key="test",
        llm_model="test-model",
        report_enabled=False,
    )
    topic = create_topic(llm_settings, name="Agent Evaluation", keywords=["agent"])
    root = topic_dir(llm_settings, topic["topic_id"])
    stale = summary_file(root)
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("<html>old report</html>", encoding="utf-8")
    rows = [
        {
            "record_id": "openalex:W1",
            "source": "openalex",
            "title": "Agent evaluation paper",
            "abstract": "A reproducible benchmark for LLM agents covering planning and tool use. " * 3,
            "abstract_usable": True,
            "abstract_source": "source_metadata",
            "published_at": "2025-01-01",
            "url": "https://example.org",
            "journal": "Example Journal",
        }
    ]
    monkeypatch.setattr(
        runner, "build_query_plan", lambda *a, **k: {"keywords": ["agent"], "directions": [], "queries": []}
    )
    monkeypatch.setattr(runner, "retrieve", lambda *a, **k: rows)
    monkeypatch.setattr(runner, "enrich_records", lambda *a, **k: {})

    summary = runner.run_pipeline(llm_settings, topic["topic_id"], run_id="test-no-report")

    assert summary["report"] is None
    assert not stale.exists()


def _analysis_ids(prompt: str) -> list[str]:
    marker = "请为以下 "
    start = prompt.index(marker)
    payload = prompt[prompt.index("[", start) : prompt.index("]\n\n", start) + 1]
    return [row["record_id"] for row in json.loads(payload)]


def test_analyze_report_papers_batches_and_snaps_category(llm_settings, monkeypatch):
    sizes: list[int] = []

    def fake_chat_json(settings, *, system, user, max_tokens=2048, temperature=0.2, timeout=None):
        ids = _analysis_ids(user)
        sizes.append(len(ids))
        return {
            "items": [
                {
                    "record_id": record_id,
                    "impact_zh": "该工作给出了可复现的评测基线。",
                    "impact_en": "The work provides a reproducible evaluation baseline.",
                    "method_category": "基准设计" if index == 0 else "自创分类",
                }
                for index, record_id in enumerate(ids)
            ]
        }

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    papers = [_row(index) for index in range(12)]

    result = llm.analyze_report_papers(
        llm_settings, topic={"topic_name": "DeepSWE"}, papers=papers, directions=["基准设计"]
    )

    assert sizes == [5, 5, 2]
    assert result["openalex:W0"]["method_category"] == "基准设计"
    assert result["openalex:W1"]["method_category"] == llm.OTHER_DIRECTION
    assert "impact_en" in result["openalex:W11"]


def _synthesis_ids(prompt: str) -> list[str]:
    start = prompt.index("以下是 ")
    payload = prompt[prompt.index("[", start) : prompt.index("]\n\n", start) + 1]
    return [row["record_id"] for row in json.loads(payload)]


def test_synthesize_report_unwraps_array_response(llm_settings, monkeypatch):
    def wrapped(settings, *, system, user, max_tokens=2048, temperature=0.2, timeout=None):
        return [{"title_zh": "综述", "executive_summary_zh": "摘要", "subthemes": []}]

    monkeypatch.setattr(llm, "chat_json", wrapped)
    notes: list[str] = []

    result = llm.synthesize_report(
        llm_settings, topic={"topic_name": "DeepSWE"}, papers=[_row(0)], notes=notes
    )

    assert result is not None
    assert result["title_zh"] == "综述"
    assert notes and "已解包" in notes[0]


def test_synthesize_report_rejects_unrelated_json(llm_settings, monkeypatch):
    def unrelated(settings, *, system, user, max_tokens=2048, temperature=0.2, timeout=None):
        return {"error": "model refused"}

    monkeypatch.setattr(llm, "chat_json", unrelated)
    notes: list[str] = []

    result = llm.synthesize_report(
        llm_settings, topic={"topic_name": "DeepSWE"}, papers=[_row(0)], notes=notes
    )

    assert result is None
    assert notes and "不符合预期" in notes[0]


def test_synthesize_report_halves_papers_on_truncation(llm_settings, monkeypatch):
    sizes: list[int] = []

    def fake_chat_json(settings, *, system, user, max_tokens=2048, temperature=0.2, timeout=None):
        sizes.append(len(_synthesis_ids(user)))
        if len(sizes) == 1:
            raise llm.LLMTruncated("输出达到 max_tokens 上限")
        return {"title_zh": "综述", "subthemes": []}

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    notes: list[str] = []

    result = llm.synthesize_report(
        llm_settings, topic={"topic_name": "DeepSWE"}, papers=[_row(index) for index in range(20)], notes=notes
    )

    assert result is not None
    assert sizes == [20, 10]
    assert notes and "前 10 篇" in notes[0]
