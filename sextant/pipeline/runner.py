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
from sextant.engine.common import write_json
from sextant.engine.render_html import render_catalog
from sextant.llm import enrich_records
from sextant.pipeline.normalize import normalize
from sextant.pipeline.plan import build_query_plan
from sextant.pipeline.rank import score_records
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
        if settings.llm_enabled:
            logger.emit("enrich", "started", "调用模型生成中文标题与摘要", model=settings.llm_model)
            enriched = enrich_records(settings, topic=topic, records=usable)
            metrics["enrich"] = stage()
            logger.emit(
                "enrich",
                "completed" if enriched else "warning",
                f"完成 {len(enriched)} 篇中文增强" if enriched else "模型未返回可用结果，降级为原文摘要",
                duration_ms=metrics["enrich"],
                enriched=len(enriched),
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
            "llm_model": settings.llm_label,
            "catalog": str(target),
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
