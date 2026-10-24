"""Pipeline orchestration: plan → retrieve → normalize → enrich → rank → render.

Every stage reads from and writes to the topic workspace, so a failed run can
be inspected (and partially re-run) from disk alone.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from sextant.config import Settings
from sextant.engine.common import summary_file, write_json
from sextant.engine.render_html import render_catalog
from sextant.llm import enrich_records
from sextant.pipeline.normalize import normalize
from sextant.pipeline.plan import build_query_plan
from sextant.pipeline.rank import score_records
from sextant.pipeline.report import build_report
from sextant.pipeline.retrieve import retrieve
from sextant.runlog import BUS, EventBus, RunLogger, utc_now_iso
from sextant.workspace import (
    catalog_path,
    load_topic,
    record_run,
    save_topic,
    topic_dir,
    update_run,
    write_pending,
    write_ranked,
)


class PipelineError(RuntimeError):
    """Raised when a run cannot produce a usable catalog."""


def new_run_id() -> str:
    return f"{utc_now_iso()[:19].replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:6]}"


def _timer() -> Callable[[], int]:
    started = time.perf_counter()
    return lambda: int((time.perf_counter() - started) * 1000)


def _enrichment_candidates(
    usable: list[dict[str, Any]],
    *,
    topic: dict[str, Any],
    plan: dict[str, Any],
    settings: Settings,
) -> list[dict[str, Any]]:
    """Pick the records most worth translating, not the first N in source order.

    Enrichment runs before the final ranking, but relevance is already
    computable — score once cheaply, translate the leaders, then score again
    with the translations attached. Without this the model spends its budget
    on whichever source happened to return first.
    """
    limit = max(1, settings.llm_max_candidates)
    if len(usable) <= limit:
        return usable
    provisional = score_records(usable, topic=topic, plan=plan)
    return provisional[:limit]


def run_pipeline(
    settings: Settings,
    topic_id: str,
    *,
    run_id: str | None = None,
    bus: EventBus | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    root = topic_dir(settings, topic_id)
    topic = load_topic(root)
    if not topic:
        raise PipelineError(f"主题不存在：{topic_id}")

    run_id = run_id or new_run_id()
    logger = RunLogger(
        topic_root=root,
        run_id=run_id,
        bus=bus or BUS,
        topic_id=topic_id,
        on_event=on_event,
    )
    record_run(
        root,
        {
            "run_id": run_id,
            "status": "running",
            "started_at": utc_now_iso(),
            "stage": "plan",
            "catalog": str(catalog_path(root)),
        },
    )
    logger.emit("run", "started", f"开始检索：{topic.get('topic_name')}", topic_id=topic_id, run_id=run_id)

    metrics: dict[str, Any] = {}
    total = _timer()
    try:
        # 1. plan
        stage = _timer()
        plan = build_query_plan(root, topic, settings, logger)
        metrics["plan"] = stage()

        # 2. retrieve
        stage = _timer()
        records = retrieve(root, plan, topic, settings, logger)
        metrics["retrieve"] = stage()

        # 3. normalize
        stage = _timer()
        usable, pending = normalize(records)
        write_pending(root, pending)
        metrics["normalize"] = stage()
        logger.emit(
            "normalize",
            "completed",
            f"去重后 {len(usable) + len(pending)} 篇，其中 {len(pending)} 篇待核实",
            duration_ms=metrics["normalize"],
            usable=len(usable),
            pending=len(pending),
            raw=len(records),
        )
        if not usable:
            raise PipelineError("没有通过摘要质量门控的记录，请更换关键词或放宽时间范围")

        # 4. optional LLM enrichment
        stage = _timer()
        enriched: dict[str, dict[str, Any]] = {}
        enrich_notes: list[str] = []
        if settings.llm_enabled:
            candidates = _enrichment_candidates(usable, topic=topic, plan=plan, settings=settings)
            logger.emit(
                "enrich",
                "started",
                "调用模型生成中文标题与摘要",
                model=settings.llm_model,
                candidates=len(candidates),
                records=len(usable),
                directions=len(plan.get("directions") or []),
            )
            enriched = enrich_records(
                settings,
                topic=topic,
                records=candidates,
                notes=enrich_notes,
                directions=plan.get("directions") or [],
            )
            metrics["enrich"] = stage()
            if enriched and not enrich_notes:
                status = "completed"
                message = f"完成 {len(enriched)} 篇中文增强"
            elif enriched:
                status = "warning"
                message = (
                    f"完成 {len(enriched)} 篇中文增强，"
                    f"{len(enrich_notes)} 个批次降级为原文摘要"
                )
            else:
                status = "warning"
                message = "模型未返回可用结果，降级为原文摘要"
            logger.emit(
                "enrich",
                status,
                message,
                duration_ms=metrics["enrich"],
                enriched=len(enriched),
                degraded_batches=len(enrich_notes),
                reasons=enrich_notes[:5],
            )
        else:
            metrics["enrich"] = stage()
            logger.emit("enrich", "skipped", "未配置模型，跳过中文增强", duration_ms=metrics["enrich"])

        # 5. rank
        stage = _timer()
        ranked = score_records(usable, topic=topic, plan=plan, enriched=enriched)
        for position, row in enumerate(ranked, start=1):
            row["rank"] = position
        write_ranked(root, ranked)
        metrics["rank"] = stage()
        logger.emit(
            "rank",
            "completed",
            f"排序完成，首位得分 {ranked[0]['ranking_score']}",
            duration_ms=metrics["rank"],
            records=len(ranked),
            top_score=ranked[0]["ranking_score"],
            directions=len({row["research_direction"] for row in ranked}),
        )

        # 6. render (quality gate first, graceful downgrade)
        stage = _timer()
        target = catalog_path(root)
        try:
            render_catalog(root, output=target, strict_chinese=settings.llm_enabled)
            strict = settings.llm_enabled
        except SystemExit as exc:
            logger.emit("render", "warning", f"中英文校验未通过，降级渲染：{exc}", error_type="chinese_gate")
            render_catalog(root, output=target, strict_chinese=False)
            strict = False
        metrics["render"] = stage()
        logger.emit(
            "render",
            "completed",
            "目录已生成",
            duration_ms=metrics["render"],
            output=str(target),
            strict_chinese=strict,
            size_bytes=target.stat().st_size if target.exists() else 0,
        )

        # 7. integrated report (LLM-only; the catalog above already succeeded)
        stage = _timer()
        report_path = None
        report_notes: list[str] = []
        if settings.report_enabled and settings.llm_enabled:
            logger.emit(
                "report",
                "started",
                "生成中英双语调研报告",
                model=settings.llm_model,
                papers=min(len(ranked), settings.report_max_papers),
                directions=len(plan.get("directions") or []),
            )
            try:
                report_path = build_report(
                    root,
                    topic=topic,
                    plan=plan,
                    ranked=ranked,
                    settings=settings,
                    notes=report_notes,
                )
            except Exception as exc:  # noqa: BLE001 - report is optional, catalog stays valid
                report_notes.append(f"{type(exc).__name__}: {exc}")
                report_path = None
            metrics["report"] = stage()
            if report_path:
                logger.emit(
                    "report",
                    "completed" if not report_notes else "warning",
                    f"调研报告已生成，共 {len(ranked[: settings.report_max_papers])} 篇文献",
                    duration_ms=metrics["report"],
                    output=str(report_path),
                    notes=report_notes[:8],
                    size_bytes=report_path.stat().st_size if report_path.exists() else 0,
                )
            else:
                logger.emit(
                    "report",
                    "warning",
                    "调研报告未生成",
                    duration_ms=metrics["report"],
                    reasons=report_notes[:8] or ["未知原因"],
                )
        else:
            metrics["report"] = stage()
            logger.emit(
                "report",
                "skipped",
                "未配置模型或报告生成已关闭，跳过调研报告",
                duration_ms=metrics["report"],
            )

        # A report from a previous run describes a different ranked set. Keeping
        # it would make the console advertise a report this run never produced.
        if not report_path:
            summary_file(root).unlink(missing_ok=True)

        metrics["total"] = total()
        summary = {
            "run_id": run_id,
            "topic_id": topic_id,
            "status": "completed",
            "finished_at": utc_now_iso(),
            "duration_ms": metrics["total"],
            "stage_duration_ms": metrics,
            "raw_records": len(records),
            "records": len(ranked),
            "pending": len(pending),
            "enriched": len(enriched),
            "enrich_degraded_batches": len(enrich_notes),
            "llm_model": settings.llm_label,
            "catalog": str(target),
            "report": str(report_path) if report_path else None,
            "report_notes": report_notes[:8],
        }
        write_json(root / "output" / "run_summary.json", summary)
        save_topic(root, topic)
        update_run(
            root,
            run_id,
            status="completed",
            finished_at=summary["finished_at"],
            duration_ms=metrics["total"],
            records=len(ranked),
            pending=len(pending),
            stage="done",
            catalog=str(target),
            report=str(report_path) if report_path else None,
        )
        logger.emit(
            "run",
            "completed",
            f"完成，共 {len(ranked)} 条记录",
            duration_ms=metrics["total"],
            records=len(ranked),
            pending=len(pending),
            enriched=len(enriched),
            catalog=str(target),
            report=str(report_path) if report_path else None,
            stage_duration_ms=metrics,
        )
        return summary

    except Exception as exc:  # noqa: BLE001 - surfaced to the caller and the UI
        metrics["total"] = total()
        error_type = type(exc).__name__
        hint = _hint_for(exc)
        logger.emit(
            "run",
            "failed",
            f"{error_type}: {exc}",
            duration_ms=metrics["total"],
            error_type=error_type,
            hint=hint,
            stage_duration_ms=metrics,
        )
        update_run(
            root,
            run_id,
            status="failed",
            finished_at=utc_now_iso(),
            duration_ms=metrics["total"],
            error=f"{error_type}: {exc}",
            hint=hint,
        )
        raise


def _hint_for(exc: Exception) -> str:
    text = str(exc).lower()
    if "网络" in str(exc) or "urlopen" in text or "timed out" in text or "connection" in text:
        return "检查网络连通性或稍后重试；OpenAlex / arXiv 在部分网络环境下会超时。"
    if "412" in text or "429" in text:
        return "外部接口限流，降低并发或缩短关键词后重试。"
    if "质量门控" in str(exc):
        return "摘要过短或缺失，尝试放宽时间范围或换成更通用的英文关键词。"
    return "查看 logs 下本次 run 的 JSONL 记录定位失败阶段。"
