"""FastAPI application: console, topic API, SSE progress and catalog delivery."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from sextant.config import VERSION, Settings, get_settings
from sextant.runlog import BUS, iter_live_events
from sextant.workspace import (
    WorkspaceError,
    catalog_path,
    create_topic,
    list_topics,
    load_topic,
    read_ranked,
    read_runs,
    topic_dir,
    topic_summary,
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
INDEX_FILE = WEB_DIR / "index.html"


class TopicCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    keywords: list[str] = Field(default_factory=list)
    question: str = ""
    from_date: str = ""
    to_date: str = ""
    max_results: int | None = Field(default=None, ge=1, le=50)


def _root(settings: Settings, topic_id: str) -> Path:
    try:
        return topic_dir(settings, topic_id)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _require_topic(root: Path, topic_id: str) -> dict[str, Any]:
    topic = load_topic(root)
    if not topic:
        raise HTTPException(status_code=404, detail=f"主题不存在：{topic_id}")
    return topic


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Sextant", version=VERSION, docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(INDEX_FILE, media_type="text/html; charset=utf-8")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        workspace = settings.workspace_root
        try:
            workspace.mkdir(parents=True, exist_ok=True)
            writable = os.access(workspace, os.W_OK)
        except OSError:
            writable = False
        from sextant.api.jobs import REGISTRY

        return {
            "status": "ok" if writable else "degraded",
            "version": VERSION,
            "llm_configured": settings.llm_enabled,
            "llm_model": settings.llm_label,
            "workspace": str(workspace),
            "workspace_writable": writable,
            "active_runs": len([job for job in REGISTRY.list() if job.status == "running"]),
        }

    @app.post("/api/topics", status_code=202)
    def create(body: TopicCreate) -> dict[str, Any]:
        try:
            topic = create_topic(
                settings,
                name=body.name,
                keywords=body.keywords,
                question=body.question,
                from_date=body.from_date,
                to_date=body.to_date,
                max_results=body.max_results,
            )
        except WorkspaceError as exc:
            status = 409 if "已存在" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        from sextant.api.jobs import REGISTRY

        job = REGISTRY.start(settings, topic["topic_id"])
        return {"topic": topic, "job": job.as_dict()}

    @app.get("/api/topics")
    def topics() -> dict[str, Any]:
        return {"topics": list_topics(settings)}

    @app.get("/api/topics/{topic_id}")
    def topic_detail(topic_id: str) -> dict[str, Any]:
        root = _root(settings, topic_id)
        _require_topic(root, topic_id)
        from sextant.api.jobs import REGISTRY

        summary = topic_summary(settings, topic_id)
        summary["runs"] = list(reversed(read_runs(root)))[:10]
        active = REGISTRY.active_for(topic_id)
        summary["active_job"] = active.as_dict() if active else None
        return summary

    @app.post("/api/topics/{topic_id}/rerun", status_code=202)
    def rerun(topic_id: str) -> dict[str, Any]:
        root = _root(settings, topic_id)
        _require_topic(root, topic_id)
        from sextant.api.jobs import REGISTRY, JobConflict

        try:
            job = REGISTRY.start(settings, topic_id)
        except JobConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"job": job.as_dict()}

    @app.get("/api/topics/{topic_id}/records")
    def records(
        topic_id: str,
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        root = _root(settings, topic_id)
        _require_topic(root, topic_id)
        rows = read_ranked(root)
        page = rows[offset : offset + limit]
        return {"total": len(rows), "offset": offset, "limit": limit, "records": page}

    @app.get("/api/topics/{topic_id}/catalog")
    def catalog(topic_id: str) -> FileResponse:
        root = _root(settings, topic_id)
        _require_topic(root, topic_id)
        target = catalog_path(root)
        if not target.exists():
            raise HTTPException(status_code=404, detail="目录尚未生成，请先运行一次检索")
        return FileResponse(target, media_type="text/html; charset=utf-8")

    @app.get("/api/topics/{topic_id}/events")
    def events(topic_id: str, run_id: str | None = None) -> StreamingResponse:
        root = _root(settings, topic_id)
        _require_topic(root, topic_id)
        runs = read_runs(root)
        selected = run_id or (runs[-1].get("run_id") if runs else None)
        if not selected:
            raise HTTPException(status_code=404, detail="该主题还没有运行记录")
        log_file = root / "logs" / f"{selected}.jsonl"
        if not log_file.exists():
            from sextant.api.jobs import REGISTRY

            job = REGISTRY.get(selected)
            running = job is not None and job.status in {"queued", "running"}
            if not running:
                raise HTTPException(status_code=404, detail=f"找不到运行日志：{selected}")
            # A just-started run may not have written its first line yet; stream
            # anyway so the console keeps its connection instead of retrying.

        def stream() -> Iterator[str]:
            try:
                for event in iter_live_events(BUS, topic_id, log_file, run_id=selected):
                    # Client disconnects close this generator (GeneratorExit),
                    # which unsubscribes in iter_live_events' finally block.
                    if event.get("heartbeat"):
                        yield ": ping\n\n"
                        continue
                    seq = event.get("seq")
                    yield f"id: {seq}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            except GeneratorExit:
                return

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/runs/{run_id}")
    def run_status(run_id: str) -> dict[str, Any]:
        from sextant.api.jobs import REGISTRY

        job = REGISTRY.get(run_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"找不到运行：{run_id}")
        return job.as_dict()

    @app.get("/api/topics/{topic_id}/logs/{run_id}", response_class=PlainTextResponse)
    def run_log(topic_id: str, run_id: str) -> PlainTextResponse:
        root = _root(settings, topic_id)
        _require_topic(root, topic_id)
        log_file = root / "logs" / f"{run_id}.jsonl"
        if not log_file.exists():
            raise HTTPException(status_code=404, detail="日志不存在")
        return PlainTextResponse(log_file.read_text(encoding="utf-8"), media_type="application/x-ndjson")

    @app.exception_handler(WorkspaceError)
    def workspace_error(_: Request, exc: WorkspaceError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


app = create_app()
