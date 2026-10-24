#!/usr/bin/env python
"""Run the full pipeline from the command line — useful for smoke tests and demos.

Usage::

    python scripts/demo.py "LLM agent evaluation" --keywords "LLM agent,evaluation"
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sextant.config import get_settings  # noqa: E402
from sextant.pipeline.runner import run_pipeline  # noqa: E402
from sextant.workspace import WorkspaceError, create_topic, list_topics, topic_dir  # noqa: E402

STAGE_LABELS = {
    "plan": "规划",
    "retrieve": "检索",
    "normalize": "归一",
    "enrich": "增强",
    "rank": "排序",
    "render": "渲染",
    "run": "运行",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Sextant retrieval-to-catalog pass.")
    parser.add_argument("topic", help="研究主题")
    parser.add_argument("--keywords", default="", help="逗号分隔的关键词")
    parser.add_argument("--from-date", default="", help="YYYY-MM-DD")
    parser.add_argument("--to-date", default="", help="YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=20, help="每个来源返回上限")
    parser.add_argument("--open", action="store_true", help="完成后打开目录")
    args = parser.parse_args()

    settings = get_settings()
    keywords = [item.strip() for item in args.keywords.replace("，", ",").split(",") if item.strip()]
    try:
        topic = create_topic(
            settings,
            name=args.topic,
            keywords=keywords,
            from_date=args.from_date,
            to_date=args.to_date,
            max_results=args.limit,
        )
    except WorkspaceError as exc:
        if "已存在" not in str(exc):
            print(f"创建主题失败：{exc}", file=sys.stderr)
            return 2
        topic = next(row for row in list_topics(settings) if row["topic_name"] == args.topic)
        print(f"复用已有主题：{topic['topic_id']}")

    def on_event(event):
        stage = STAGE_LABELS.get(str(event.get("stage")), str(event.get("stage")))
        status = event.get("status")
        if status == "started" and event.get("stage") != "run":
            return
        duration = event.get("duration_ms")
        suffix = f" ({duration} ms)" if duration is not None else ""
        marker = {"completed": "✓", "warning": "!", "failed": "✗", "skipped": "·"}.get(str(status), "·")
        print(f"  {marker} [{stage}] {event.get('message', '')}{suffix}")

    print(f"主题 {topic['topic_name']} · {topic['topic_id']}")
    try:
        summary = run_pipeline(settings, topic["topic_id"], on_event=on_event)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"\n运行失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"日志目录：{topic_dir(settings, topic['topic_id']) / 'logs'}", file=sys.stderr)
        return 1

    target = Path(summary["catalog"])
    print(f"\n完成：{summary['records']} 条记录，{summary['pending']} 条待核实，耗时 {summary['duration_ms']} ms")
    print(f"目录：{target}")
    if args.open and target.exists():
        webbrowser.open(target.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
