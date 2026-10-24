"""Compile ranked evidence into a bilingual integrated report.

The report is a user-triggered job (:func:`run_report`), not a retrieval
pipeline stage: it costs several LLM calls and the catalog is already usable
without it.

The HTML renderer in :mod:`sextant.engine.render_html` is deliberately strict:
it refuses to emit a report unless every card carries bilingual analysis and
every card belongs to a subtheme. This module produces exactly that structure
from the LLM output and repairs the common model mistakes — unknown ids, short
summaries, unassigned papers — so one bad label cannot discard the report.

Every repair is recorded in ``notes`` and surfaced in the run log; a degraded
report is still honest about which parts the model failed to write.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sextant.config import Settings
from sextant.engine.common import (
    OTHER_DIRECTION,
    contains_cjk,
    paper_cards_file,
    queries_file,
    read_json,
    report_content_en_file,
    report_content_zh_file,
    report_synthesis_input_file,
    write_json,
)
from sextant.engine.render_html import render_report
from sextant.llm import analyze_report_papers, synthesize_report
from sextant.runlog import BUS, EventBus, RunLogger, new_run_id, utc_now_iso
from sextant.workspace import load_topic, now_iso, read_ranked, topic_dir

ProgressCallback = Callable[[str], None]

SECTION_KEYS = (
    "research_question",
    "methods",
    "samples",
    "results",
    "limitations",
    "business_impact",
)

#: A report built from one or two papers is not a synthesis.
MIN_REPORT_PAPERS = 3

#: The report contract asks for 3–6 method families, so groups created without
#: the model must respect the same ceiling.
MAX_REPORT_THEMES = 6
#: A one-paper "family" is usually a per-paper research direction (enrichment
#: labels each paper individually), not a method family; such papers go to the
#: "other" section rather than becoming a one-card section of their own.
MIN_FALLBACK_GROUP_SIZE = 2

FALLBACK_THEME_TITLE = OTHER_DIRECTION

EN_SECTION_FALLBACKS = {
    "research_question": (
        "This report organizes the available evidence and outlines the core research questions, "
        "methodological differences and the scope of its conclusions."
    ),
    "methods": "A synthesized description was not generated; see each paper card for the methods actually used.",
    "samples": (
        "A synthesized summary of samples and datasets was not generated; see the paper cards "
        "for datasets, tasks and experimental settings."
    ),
    "results": "A synthesized result summary was not generated; verify each paper's findings in its card.",
    "limitations": (
        "A synthesized limitation analysis was not generated; assess evidence strength against "
        "the original papers."
    ),
    "business_impact": (
        "A synthesized application-impact analysis was not generated; evaluate feasibility against "
        "the original papers and your own context."
    ),
}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


#: Collocations that describe the pipeline rather than the literature. The
#: skill's report contract forbids them in the report body: a report summarises
#: the papers, never how they were collected.
#:
#: Precision matters more than recall here. A false hit burns a full retry and
#: can replace a good synthesis with a worse one, so only phrases that cannot be
#: research content are listed: report self-reference ("本次检索", "本报告依据")
#: and counts ("N 篇论文/文献/摘要", "纳入篇"). Bare nouns are excluded because
#: they name research subjects — "去重" (record linkage), "时间窗口" (temporal
#: analysis), "开放获取" (open-access publishing), "多源检索" (multi-source
#: retrieval) all belong to the literature, not to the pipeline.
META_TERMS = (
    "本次检索",
    "检索流程",
    "纳入篇",
    "篇论文",
    "篇文献",
    "篇摘要",
    "本报告依据",
    "本报告基于",
)

META_CORRECTION = (
    "上一版输出包含检索流程、来源数量或纳入篇数等元信息（{hits}）。"
    "本次必须重写：只陈述研究内容、方法与结论，不得提及报告的生成过程。"
)


def _meta_hits(*texts: str) -> list[str]:
    return [term for term in META_TERMS if any(term in text for text in texts if text)]


def _synthesis_texts(payload: dict[str, Any]) -> list[str]:
    """Every Chinese passage that ends up in the report body.

    Subtheme summaries are checked too: they sit between the sections and the
    cards, so a count written there leaks into the report just the same.
    """
    sections = payload.get("sections_zh")
    values = [_text(value) for value in sections.values()] if isinstance(sections, dict) else []
    themes = payload.get("subthemes")
    theme_texts: list[str] = []
    for item in themes if isinstance(themes, list) else []:
        if isinstance(item, dict):
            # Titles are skipped on purpose: a subtheme can legitimately be
            # about de-duplication or retrieval as a research topic.
            theme_texts.append(_text(item.get("summary_zh")))
    return [_text(payload.get("executive_summary_zh")), *values, *theme_texts]


def _section_fallbacks(topic: dict[str, Any]) -> dict[str, str]:
    topic_name = _text(topic.get("topic_name")) or "该主题"
    return {
        "research_question": (
            f"围绕「{topic_name}」的核心研究问题、方法差异与结论边界，"
            "本节未能由模型生成综合描述，请以下方论文卡片中的逐篇信息为准。"
        ),
        "methods": "本节未能由模型生成综合描述；各文献采用的方法请以下方论文卡片中的逐篇信息为准。",
        "samples": (
            "本节未能由模型生成样本与数据综述；各文献使用的数据集、实验对象、"
            "任务设置与真实场景请以下方论文卡片为准。"
        ),
        "results": "本节未能由模型生成综合结果；各文献报告的主要结果请在下方论文卡片中逐篇核对。",
        "limitations": "本节未能由模型生成局限分析；请结合论文原文判断证据强度，避免外推。",
        "business_impact": "本节未能由模型生成应用影响分析；请结合论文原文与自身业务场景评估可行性。",
    }


def _sections_zh(raw: Any, fallbacks: dict[str, str]) -> dict[str, str]:
    source = raw if isinstance(raw, dict) else {}
    sections: dict[str, str] = {}
    for key in SECTION_KEYS:
        text = _text(source.get(key))
        sections[key] = text if len(text) >= 20 and contains_cjk(text) else fallbacks[key]
    return sections


def _sections_en(raw: Any) -> dict[str, str]:
    source = raw if isinstance(raw, dict) else {}
    sections: dict[str, str] = {}
    for key in SECTION_KEYS:
        text = _text(source.get(key))
        sections[key] = text if len(text) >= 20 else EN_SECTION_FALLBACKS[key]
    return sections


def _fallback_theme(paper_ids: list[str]) -> dict[str, Any]:
    return {
        "title_zh": FALLBACK_THEME_TITLE,
        "title_en": "Other related work",
        "summary_zh": (
            "该方向保留未能归入主要方法家族的补充证据；"
            "其方法与结论请逐篇查看论文卡片。"
        ),
        "summary_en": (
            "Supplementary evidence that could not be grouped into the main approach families "
            "is kept here. See each card for methods and findings."
        ),
        "paper_ids": paper_ids,
    }


def _category_theme(category: str, paper_ids: list[str]) -> dict[str, Any]:
    """A method-family group built without the model's help.

    Used when the synthesis left papers unassigned (or never returned): the
    analysis stage already labelled every card with a method category, so the
    group is real evidence organisation, not a leftover bucket. The summary
    stays an honest fallback rather than an invented review of the group.
    """
    return {
        "title_zh": category,
        "title_en": category,
        "summary_zh": (
            f"该方向按论文的方法类别「{category}」归并；模型未生成综合评述，"
            "各文献的方法与结论请逐篇查看论文卡片。"
        ),
        "summary_en": (
            "Papers are grouped here by their method category. A synthesized summary was not "
            "generated; see each paper card for methods and findings."
        ),
        "paper_ids": paper_ids,
    }


def _normalize_subthemes(
    raw: Any,
    paper_ids: list[str],
    notes: list[str],
    categories: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Turn model output into subthemes the renderer will accept.

    Unknown ids are dropped, duplicate assignments keep the first theme, and
    short summaries are replaced with an honest fallback instead of deleting
    the group. Papers the model left over are grouped by their method family
    (falling back to "other" only when no category exists), because the
    renderer rejects a report with unassigned cards — and one oversized
    "other" bucket would hide exactly the papers the model failed to cover.
    """
    known = set(paper_ids)
    claimed: set[str] = set()
    themes: list[dict[str, Any]] = []
    for index, item in enumerate(raw if isinstance(raw, list) else [], start=1):
        if not isinstance(item, dict):
            continue
        title_zh = _text(item.get("title_zh") or item.get("title"))
        title_en = _text(item.get("title_en")) or title_zh
        summary_zh = _text(item.get("summary_zh") or item.get("summary"))
        summary_en = _text(item.get("summary_en"))
        ids: list[str] = []
        for value in item.get("paper_ids") or []:
            paper_id = _text(value)
            if paper_id in known and paper_id not in claimed and paper_id not in ids:
                ids.append(paper_id)
        if not ids:
            continue
        if len(title_zh) < 2 or not contains_cjk(title_zh):
            notes.append(f"第 {index} 个子方向缺少中文标题，已并入「{FALLBACK_THEME_TITLE}」")
            continue
        if len(summary_zh) < 20 or not contains_cjk(summary_zh):
            notes.append(f"子方向「{title_zh}」缺少中文评述，已使用降级说明")
            summary_zh = "该方向未能由模型生成完整评述；请以下方论文卡片中的逐篇信息为准。"
        if len(summary_en) < 20:
            notes.append(f"子方向「{title_zh}」缺少英文评述，已使用降级说明")
            summary_en = (
                "A synthesized summary was not generated for this approach family; "
                "see the paper cards for methods and findings."
            )
        claimed.update(ids)
        themes.append(
            {
                "title_zh": title_zh,
                "title_en": title_en,
                "summary_zh": summary_zh,
                "summary_en": summary_en,
                "paper_ids": ids,
            }
        )

    unassigned = [paper_id for paper_id in paper_ids if paper_id not in claimed]
    if unassigned:
        # Recover the leftovers by method family instead of dumping them into
        # a single "other" theme. A truncated synthesis used to strand 15 of
        # 20 papers there; the categories come from the analysis stage (which
        # snaps them to the planned directions), so this costs no extra call.
        buckets: dict[str, list[str]] = {}
        for paper_id in unassigned:
            category = _text((categories or {}).get(paper_id))
            if not contains_cjk(category) or category == OTHER_DIRECTION:
                category = ""
            buckets.setdefault(category, []).append(paper_id)

        leftover: list[str] = list(buckets.pop("", []))
        recovered = 0
        new_groups: list[tuple[str, list[str]]] = []
        # Largest families first keeps the choice deterministic once the cap
        # below starts dropping groups.
        for category, ids in sorted(buckets.items(), key=lambda item: len(item[1]), reverse=True):
            if len(ids) < MIN_FALLBACK_GROUP_SIZE:
                leftover.extend(ids)
                continue
            existing = next((theme for theme in themes if theme["title_zh"] == category), None)
            if existing is not None:
                existing["paper_ids"].extend(pid for pid in ids if pid not in existing["paper_ids"])
                recovered += len(ids)
            else:
                new_groups.append((category, ids))

        room = max(0, MAX_REPORT_THEMES - len(themes))
        for category, ids in new_groups[:room]:
            themes.append(_category_theme(category, ids))
            recovered += len(ids)
        for _category, ids in new_groups[room:]:
            leftover.extend(ids)

        if recovered:
            notes.append(f"{recovered} 篇文献未被模型分配子方向，已按方法家族自动归组")
        if leftover:
            notes.append(
                f"{len(leftover)} 篇文献未能归入方法家族，已并入「{FALLBACK_THEME_TITLE}」"
            )
            # The model may already have produced an "other" theme; merge into
            # it instead of rendering two sections with the same title.
            existing_other = next(
                (theme for theme in themes if theme["title_zh"] == FALLBACK_THEME_TITLE), None
            )
            if existing_other is not None:
                existing_other["paper_ids"].extend(
                    pid for pid in leftover if pid not in existing_other["paper_ids"]
                )
            else:
                themes.append(_fallback_theme(leftover))
    if not themes:
        themes.append(_fallback_theme(list(paper_ids)))
    return themes


def _method_category(row: dict[str, Any], fields: dict[str, str]) -> str:
    direction = _text(row.get("research_direction"))
    model_category = _text(fields.get("method_category")) or direction
    return model_category if contains_cjk(model_category) else (direction or OTHER_DIRECTION)


def _build_card(row: dict[str, Any], rank: int, analysis: dict[str, dict[str, str]]) -> dict[str, Any]:
    paper_id = _text(row.get("record_id"))
    fields = analysis.get(paper_id, {})
    abstract_en = _text(row.get("abstract"))
    abstract_zh = _text(row.get("abstract_zh")) or _text(fields.get("abstract_zh"))
    if len(abstract_zh) < 6 or not contains_cjk(abstract_zh):
        abstract_zh = "该文献暂无可用中文摘要，请切换到英文视图查看原始摘要。"
    impact_zh = _text(fields.get("impact_zh"))
    if len(impact_zh) < 6 or not contains_cjk(impact_zh):
        impact_zh = "原文摘要未明确说明研究与应用影响，请结合论文原文判断。"
    impact_en = _text(fields.get("impact_en"))
    if len(impact_en) < 6 or contains_cjk(impact_en):
        impact_en = (
            "The available abstract does not state a clear research or application impact; "
            "consult the original paper."
        )
    method_category = _method_category(row, fields)
    abstract_source = _text(row.get("abstract_source"))
    if abstract_source in {"", "unavailable"}:
        abstract_source = "source_metadata"
    return {
        "paper_id": paper_id,
        "record_id": paper_id,
        "rank": rank,
        "title": _text(row.get("title")) or paper_id,
        "title_zh": _text(row.get("title_zh")) or _text(fields.get("title_zh")),
        "abstract": abstract_zh,
        "abstract_en": abstract_en,
        "abstract_source": abstract_source,
        "reading_basis": "abstract_fallback",
        "evidence_mode": "abstract_only",
        "method_category": method_category,
        "plain_summary_zh": _text(row.get("plain_summary_zh")),
        "research_and_application_impact": impact_zh,
        "english": {
            "abstract": abstract_en,
            "research_and_application_impact": impact_en,
        },
        "published_at": _text(row.get("published_at")),
        "journal": _text(row.get("journal")),
        "url": _text(row.get("url")),
        "doi": _text(row.get("doi")),
        "authors": row.get("authors") or [],
        "citations": row.get("citations"),
        "source": _text(row.get("source")),
    }


def build_report(
    root: Path,
    *,
    topic: dict[str, Any],
    plan: dict[str, Any],
    ranked: list[dict[str, Any]],
    settings: Settings,
    notes: list[str] | None = None,
    on_progress: ProgressCallback | None = None,
) -> Path | None:
    """Write the report inputs and render the integrated HTML.

    Returns the report path, or ``None`` when the report could not be produced.
    ``notes`` always explains why, so the run log never reduces a skipped
    report to a bare count. ``on_progress`` receives short human-readable
    updates for the long LLM phases.
    """
    notes = notes if notes is not None else []
    if not settings.report_enabled:
        notes.append("调研报告已关闭（SEXTANT_REPORT_ENABLED=0）")
        return None
    if not settings.llm_enabled:
        notes.append("未配置模型，跳过调研报告")
        return None

    selected = [row for row in ranked if _text(row.get("record_id"))][: settings.report_max_papers]
    if len(selected) < MIN_REPORT_PAPERS:
        notes.append(f"可用文献不足 {MIN_REPORT_PAPERS} 篇，跳过调研报告")
        return None

    def progress(message: str) -> None:
        if on_progress is not None:
            on_progress(message)

    directions = [str(item) for item in (plan.get("directions") or []) if str(item).strip()]
    progress(f"正在分析 {len(selected)} 篇文献")
    analysis = analyze_report_papers(
        settings,
        topic=topic,
        papers=selected,
        notes=notes,
        directions=directions,
        on_progress=(lambda done, total: progress(f"文献分析 {done}/{total}")) if on_progress else None,
    )
    progress("正在综合写作")
    synthesis = synthesize_report(
        settings, topic=topic, papers=selected, directions=directions, notes=notes
    )
    if synthesis is None:
        notes.append("综合写作未产出可用内容，本次不生成调研报告")
        return None

    # The model sometimes narrates how the report was produced ("based on six
    # paper abstracts…"), which the report contract forbids. Retry once with a
    # targeted correction: prompt wording alone does not always hold, and a
    # report about the pipeline reads like a shell around the evidence.
    hits = _meta_hits(*_synthesis_texts(synthesis))
    if hits:
        notes.append(f"综合写作含流程元信息（{'、'.join(hits)}），已重试一次")
        progress("检测到流程元信息，正在重写")
        retry = synthesize_report(
            settings,
            topic=topic,
            papers=selected,
            directions=directions,
            notes=notes,
            correction=META_CORRECTION.format(hits="、".join(hits)),
        )
        if retry is not None:
            synthesis = retry
            remaining = _meta_hits(*_synthesis_texts(synthesis))
            if remaining:
                notes.append(f"重试后仍含流程元信息（{'、'.join(remaining)}）")

    paper_ids = [_text(row.get("record_id")) for row in selected]
    cards = [
        _build_card(row, index, analysis)
        for index, row in enumerate(selected, start=1)
    ]
    categories = {card["paper_id"]: card["method_category"] for card in cards}
    themes = _normalize_subthemes(synthesis.get("subthemes"), paper_ids, notes, categories)

    topic_name = _text(topic.get("topic_name")) or "研究主题"
    title_zh = _text(synthesis.get("title_zh"))
    if len(title_zh) < 2 or not contains_cjk(title_zh):
        title_zh = f"{topic_name} 研究综述"
    title_en = _text(synthesis.get("title_en")) or f"{topic_name} Research Report"

    executive_zh = _text(synthesis.get("executive_summary_zh"))
    if len(executive_zh) < 20 or not contains_cjk(executive_zh):
        notes.append("执行摘要缺少中文内容，已使用降级说明")
        executive_zh = (
            f"围绕「{topic_name}」的现有证据，模型未能生成完整综述，"
            "请以各小节与论文卡片为准。"
        )
    executive_en = _text(synthesis.get("executive_summary_en")) or (
        f"This report organizes the available evidence on {topic_name}; a full synthesis "
        "was not generated, so refer to the sections and paper cards."
    )

    sections_zh = _sections_zh(synthesis.get("sections_zh"), _section_fallbacks(topic))
    sections_en = _sections_en(synthesis.get("sections_en"))

    write_json(paper_cards_file(root), {"title": title_zh, "papers": cards})
    write_json(
        report_content_zh_file(root),
        {
            "title": title_zh,
            "executive_summary": executive_zh,
            "sections": sections_zh,
            "subthemes": [
                {"title": theme["title_zh"], "summary": theme["summary_zh"], "paper_ids": theme["paper_ids"]}
                for theme in themes
            ],
        },
    )
    write_json(
        report_content_en_file(root),
        {
            "title": title_en,
            "executive_summary": executive_en,
            "sections": sections_en,
            "subthemes": [
                {"title": theme["title_en"], "summary": theme["summary_en"], "paper_ids": theme["paper_ids"]}
                for theme in themes
            ],
        },
    )
    write_json(
        report_synthesis_input_file(root),
        {
            "generated_at": now_iso(),
            "paper_count": len(cards),
            "subthemes": len(themes),
            "unresolved_papers": [],
        },
    )

    progress("正在渲染报告")
    try:
        return render_report(root)
    except SystemExit as exc:
        notes.append(f"报告校验未通过：{exc}")
        return None


class ReportError(RuntimeError):
    """Raised when a report job cannot even be started."""


def run_report(
    settings: Settings,
    topic_id: str,
    *,
    run_id: str | None = None,
    bus: EventBus | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Generate the integrated report as an independent, user-triggered job.

    The evidence catalog can exist for minutes or days before anyone asks for a
    report, and a report costs several LLM calls. Running it inside the
    retrieval pipeline made the console wait for it; here it gets its own
    ``run_id``, its own JSONL log and its own SSE stream, so the catalog stays
    usable while the report is written.

    Report jobs are *not* appended to ``runs.json``: that file drives
    ``last_run`` in the console and a report run must not masquerade as a new
    retrieval. The job's state is discoverable through the job registry and its
    log file.

    Returns a summary dict with ``status`` either ``"completed"`` or
    ``"failed"``. Only a missing topic raises.
    """
    root = topic_dir(settings, topic_id)
    topic = load_topic(root)
    if not topic:
        raise ReportError(f"主题不存在：{topic_id}")

    run_id = run_id or new_run_id()
    logger = RunLogger(
        topic_root=root,
        run_id=run_id,
        bus=bus or BUS,
        topic_id=topic_id,
        on_event=on_event,
    )
    started_at = utc_now_iso()
    started = time.perf_counter()
    logger.emit("run", "started", f"开始生成调研报告：{topic.get('topic_name')}", topic_id=topic_id, run_id=run_id)

    ranked = read_ranked(root)
    plan = read_json(queries_file(root), default={}) or {}
    selected = [row for row in ranked if _text(row.get("record_id"))][: settings.report_max_papers]
    notes: list[str] = []
    target: Path | None = None
    error = ""

    if not settings.report_enabled:
        error = "调研报告生成已关闭（SEXTANT_REPORT_ENABLED=0）"
    elif not settings.llm_enabled:
        error = "未配置模型，无法生成调研报告"
    elif not ranked:
        error = "尚无排序结果，请先完成一次检索"
    elif len(selected) < MIN_REPORT_PAPERS:
        error = f"可用文献不足 {MIN_REPORT_PAPERS} 篇，无法生成调研报告"

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    if error:
        logger.emit("report", "warning", error, reasons=[error])
    else:
        logger.emit(
            "report",
            "started",
            f"正在分析 {len(selected)} 篇文献并综合写作",
            model=settings.llm_model,
            papers=len(selected),
            directions=len(plan.get("directions") or []),
        )
        try:
            target = build_report(
                root,
                topic=topic,
                plan=plan,
                ranked=selected,
                settings=settings,
                notes=notes,
                on_progress=lambda message: logger.emit("report", "running", message),
            )
        except Exception as exc:  # noqa: BLE001 - the job must end with a terminal event
            notes.append(f"{type(exc).__name__}: {exc}")
            target = None
        if target:
            logger.emit(
                "report",
                "completed" if not notes else "warning",
                f"调研报告已生成，共 {len(selected)} 篇文献",
                duration_ms=elapsed(),
                output=str(target),
                notes=notes[:8],
                size_bytes=target.stat().st_size if target.exists() else 0,
            )
        else:
            error = notes[-1] if notes else "调研报告未生成"
            logger.emit(
                "report",
                "warning",
                "调研报告未生成",
                duration_ms=elapsed(),
                reasons=notes[:8] or ["未知原因"],
            )

    duration_ms = elapsed()
    summary = {
        "run_id": run_id,
        "topic_id": topic_id,
        "kind": "report",
        "status": "completed" if target else "failed",
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "duration_ms": duration_ms,
        "papers": len(selected) if target else 0,
        "report": str(target) if target else None,
        "report_notes": notes[:8],
        "error": None if target else (error or "调研报告未生成"),
    }
    if target:
        logger.emit(
            "run",
            "completed",
            f"调研报告已生成，共 {len(selected)} 篇文献",
            duration_ms=duration_ms,
            report=str(target),
            papers=len(selected),
            notes=notes[:8],
        )
    else:
        logger.emit(
            "run",
            "failed",
            summary["error"],
            duration_ms=duration_ms,
            error_type="ReportError",
            hint="检查模型配置与网络；修正后点击「重试生成」即可，已生成的历史报告会保留。",
            reasons=notes[:8],
        )
    return summary
