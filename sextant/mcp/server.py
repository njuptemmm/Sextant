"""MCP server: expose the retrieval engine to any MCP-capable agent.

Run standalone::

    python -m sextant.mcp.server --port 8001

The server is intentionally stateless at the HTTP layer; state lives in the
topic workspace on disk, so several agents can share one deployment.
"""

from __future__ import annotations

import argparse
from typing import Any

from sextant.config import VERSION, get_settings
from sextant.pipeline.normalize import normalize
from sextant.pipeline.rank import score_records
from sextant.pipeline.runner import run_pipeline
from sextant.sources_registry import SPECS, active_sources, describe_specs
from sextant.workspace import (
    WorkspaceError,
    create_topic,
    list_topics,
    topic_dir,
    topic_slug,
    topic_summary,
)

INSTRUCTIONS = (
    "Sextant 是研究情报追踪服务。它从 OpenAlex、arXiv、Crossref、DOAJ 等学术源检索，"
    "去重、按主题相关性打分，并生成可筛选的 HTML 证据目录。"
    "典型链路：先 list_sources 了解可用来源；用 search_papers 做一次快速验证；"
    "确认主题后用 run_topic 跑完整链路；再用 get_topic 读取排名靠前的记录。"
)


def _record_brief(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": row.get("record_id"),
        "title": row.get("title"),
        "source": row.get("source"),
        "sources": row.get("sources") or [row.get("source")],
        "published_at": row.get("published_at"),
        "doi": row.get("doi"),
        "url": row.get("url"),
        "citations": row.get("citations"),
        "ranking_score": row.get("ranking_score"),
        "research_direction": row.get("research_direction"),
        "abstract": (row.get("abstract") or "")[:600],
    }


def _search_once(query: str, source_id: str, limit: int, from_date: str, to_date: str) -> list[dict[str, Any]]:
    spec = SPECS.get(source_id)
    if spec is None:
        raise ValueError(f"未知来源：{source_id}。可用：{', '.join(SPECS)}")
    return spec.adapter(
        query,
        limit=limit,
        from_date=from_date or None,
        to_date=to_date or None,
    )


def create_server(host: str = "127.0.0.1", port: int = 8001):
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(
        "sextant",
        instructions=INSTRUCTIONS,
        host=host,
        port=port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
    )
    # FastMCP does not expose a version argument; without this the handshake
    # reports the MCP SDK version (e.g. 1.30.0) instead of ours, which reads
    # like a stale deployment to anyone inspecting the server.
    server._mcp_server.version = VERSION

    @server.tool(name="list_sources", description="List scholarly sources this deployment can query, with discipline and authority.", structured_output=True)
    def list_sources() -> dict[str, Any]:
        specs = active_sources({"topic_name": "", "keywords": []})
        known = describe_specs(list(dict.fromkeys([*specs, *SPECS.values()])))
        return {
            "version": VERSION,
            "default_sources": [spec.id for spec in specs],
            "sources": known,
        }

    @server.tool(name="search_papers", description="Search one scholarly source and return normalized records (no workspace writes).", structured_output=True)
    def search_papers(
        query: str,
        source_id: str = "openalex",
        limit: int = 10,
        from_date: str = "",
        to_date: str = "",
    ) -> dict[str, Any]:
        rows = _search_once(query, source_id, max(1, min(limit, 50)), from_date, to_date)
        usable, pending = normalize(rows)
        return {
            "source": source_id,
            "query": query,
            "returned": len(rows),
            "usable": len(usable),
            "pending_verification": len(pending),
            "records": [_record_brief(row) for row in usable[:limit]],
        }

    @server.tool(name="create_topic", description="Create a tracked topic workspace and start a background run. Returns topic_id and run_id immediately.", structured_output=True)
    def create_topic_tool(
        name: str,
        keywords: list[str] | None = None,
        question: str = "",
        from_date: str = "",
        to_date: str = "",
        max_results: int = 20,
    ) -> dict[str, Any]:
        settings = get_settings()
        try:
            topic = create_topic(
                settings,
                name=name,
                keywords=keywords or [],
                question=question,
                from_date=from_date,
                to_date=to_date,
                max_results=max_results,
            )
        except WorkspaceError as exc:
            return {"ok": False, "error": str(exc)}
        from sextant.api.jobs import REGISTRY, JobConflict

        try:
            job = REGISTRY.start(settings, topic["topic_id"])
        except JobConflict as exc:
            return {"ok": False, "error": str(exc), "topic": topic}
        return {"ok": True, "topic": topic, "run_id": job.run_id, "status_url": f"/api/runs/{job.run_id}"}

    @server.tool(name="run_topic", description="Create (or reuse) a topic and run the full plan→retrieve→rank→render→report pipeline, blocking until the catalog and (when an LLM is configured) the bilingual report are ready.", structured_output=True)
    def run_topic(
        name: str,
        keywords: list[str] | None = None,
        question: str = "",
        from_date: str = "",
        to_date: str = "",
        max_results: int = 20,
    ) -> dict[str, Any]:
        settings = get_settings()
        try:
            topic = create_topic(
                settings,
                name=name,
                keywords=keywords or [],
                question=question,
                from_date=from_date,
                to_date=to_date,
                max_results=max_results,
            )
        except WorkspaceError as exc:
            if "已存在" not in str(exc):
                return {"ok": False, "error": str(exc)}
            topic_id = topic_slug(name)
            topics = {row["topic_id"] for row in list_topics(settings)}
            if topic_id not in topics:
                return {"ok": False, "error": str(exc)}
            topic = {"topic_id": topic_id, "topic_name": name}
        try:
            summary = run_pipeline(settings, str(topic["topic_id"]))
        except Exception as exc:  # noqa: BLE001 - reported to the agent
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "topic_id": topic.get("topic_id")}
        return {
            "ok": True,
            "topic_id": summary["topic_id"],
            "run_id": summary["run_id"],
            "records": summary["records"],
            "pending": summary["pending"],
            "duration_ms": summary["duration_ms"],
            "catalog": summary["catalog"],
            "report": summary.get("report"),
            "report_notes": summary.get("report_notes") or [],
        }

    @server.tool(name="get_topic", description="Read a topic's metadata and its top-ranked records.", structured_output=True)
    def get_topic(topic_id: str, limit: int = 10, min_score: float = 0.0) -> dict[str, Any]:
        settings = get_settings()
        try:
            summary = topic_summary(settings, topic_id)
        except (KeyError, WorkspaceError) as exc:
            return {"ok": False, "error": f"主题不存在：{topic_id}", "detail": str(exc)}
        from sextant.workspace import read_ranked

        rows = [row for row in read_ranked(topic_dir(settings, topic_id)) if float(row.get("ranking_score") or 0) >= min_score]
        return {
            "ok": True,
            "topic": summary,
            "records": [_record_brief(row) for row in rows[: max(1, min(limit, 100))]],
        }

    @server.tool(name="get_run_status", description="Check a background run started by create_topic.", structured_output=True)
    def get_run_status(run_id: str) -> dict[str, Any]:
        from sextant.api.jobs import REGISTRY

        job = REGISTRY.get(run_id)
        if job is None:
            return {"ok": False, "error": f"找不到运行：{run_id}"}
        return {"ok": True, "job": job.as_dict()}

    @server.tool(name="score_records", description="Score already-retrieved records against a topic without writing files. Useful for custom agent loops.", structured_output=True)
    def score_records_tool(topic_name: str, keywords: list[str], records: list[dict[str, Any]]) -> dict[str, Any]:
        topic = {"topic_name": topic_name, "keywords": keywords}
        ranked = score_records(records, topic=topic, plan={"keywords": keywords})
        return {"count": len(ranked), "records": [_record_brief(row) for row in ranked[:20]]}

    return server


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Start the Sextant MCP server (streamable HTTP).")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.mcp_port)
    args = parser.parse_args()
    server = create_server(args.host, args.port)
    server.run(transport="streamable-http")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
