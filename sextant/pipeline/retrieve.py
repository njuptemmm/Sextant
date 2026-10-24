"""Stage 2 — call external scholarly sources and persist raw hits.

One worker per source. Queries inside a source run sequentially so the
module-level rate limiter in ``engine.sources`` is never raced. A failing
source does not abort the run: its error is logged and the other source's
results still flow through the pipeline.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from sextant.config import Settings
from sextant.engine.common import write_json
from sextant.runlog import RunLogger, utc_now_iso
from sextant.sources_registry import SPECS
from sextant.workspace import write_raw


class RetrievalError(RuntimeError):
    pass


def _run_source(
    source: str,
    queries: list[str],
    *,
    topic: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    adapter = SPECS[source].adapter
    records: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for query in queries:
        started = time.perf_counter()
        try:
            rows = adapter(
                query,
                limit=topic.get("max_results_per_source") or settings.max_results_per_source,
                from_date=topic.get("from_date") or None,
                to_date=topic.get("to_date") or None,
            )
            diagnostics.append(
                {
                    "query": query,
                    "status": "ok",
                    "count": len(rows),
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                }
            )
            records.extend(rows)
        except Exception as exc:  # noqa: BLE001 - each source must fail independently
            diagnostics.append(
                {
                    "query": query,
                    "status": "error",
                    "count": 0,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {"source": source, "records": records, "diagnostics": diagnostics}


def retrieve(
    root: Path,
    plan: dict[str, Any],
    topic: dict[str, Any],
    settings: Settings,
    logger: RunLogger,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for row in plan.get("queries") or []:
        source = str(row.get("source") or "")
        query = str(row.get("query") or "")
        if source in SPECS and query:
            grouped.setdefault(source, []).append(query)

    if not grouped:
        raise RetrievalError("检索计划为空")

    logger.emit(
        "retrieve",
        "started",
        f"并行检索 {len(grouped)} 个来源",
        sources={source: len(queries) for source, queries in grouped.items()},
    )

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(len(grouped), settings.source_fetch_concurrency)) as pool:
        futures = {
            pool.submit(_run_source, source, queries, topic=topic, settings=settings): source
            for source, queries in grouped.items()
        }
        for future in futures:
            source = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                logger.emit("retrieve", "warning", f"{source} 检索线程异常", source=source, error=str(exc))
                results.append({"source": source, "records": [], "diagnostics": [], "error": str(exc)})

    manifest: dict[str, Any] = {"generated_at": None, "sources": {}}
    all_records: list[dict[str, Any]] = []
    for result in results:
        source = result["source"]
        records = result["records"]
        write_raw(root, source, records)
        all_records.extend(records)
        errors = [row for row in result["diagnostics"] if row.get("status") == "error"]
        for row in result["diagnostics"]:
            error = str(row.get("error") or "")
            suffix = f"（{error[:80]}）" if error else ""
            logger.emit(
                "retrieve",
                "completed" if row.get("status") == "ok" else "warning",
                f"{source} · {row.get('count', 0)} 条{suffix}",
                source=source,
                query=row.get("query"),
                count=row.get("count", 0),
                duration_ms=row.get("duration_ms"),
                error=row.get("error"),
            )
        manifest["sources"][source] = {
            "returned": len(records),
            "failed_queries": len(errors),
            "diagnostics": result["diagnostics"],
        }

    total_ms = int((time.perf_counter() - started) * 1000)
    manifest["duration_ms"] = total_ms
    manifest["total_records"] = len(all_records)
    manifest["generated_at"] = utc_now_iso()
    write_json(root / "manifests" / "retrieval.json", manifest)

    if not all_records:
        raise RetrievalError("所有来源均未返回结果，请检查网络或调整关键词")

    logger.emit(
        "retrieve",
        "completed",
        f"共取得 {len(all_records)} 条原始记录",
        duration_ms=total_ms,
        total_records=len(all_records),
        per_source={source: len(rows) for source, rows in ((r["source"], r["records"]) for r in results)},
    )
    return all_records
