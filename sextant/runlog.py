"""Structured run logging plus a small fan-out event bus for SSE.

Every pipeline step emits one JSONL line. The same object is pushed to any
subscribers, so the web console, the API and the on-disk audit trail all see
the same events in the same order.
"""

from __future__ import annotations

import json
import queue
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_run_id() -> str:
    """Sortable, unique run id shared by pipeline and on-demand report jobs."""
    return f"{utc_now_iso()[:19].replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:6]}"


def _topic_key(topic_id: str) -> str:
    return str(topic_id)


class EventBus:
    """Thread-safe fan-out. Subscribers are plain ``queue.Queue`` objects."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[queue.Queue[dict[str, Any]]]] = {}

    def subscribe(self, topic_id: str) -> queue.Queue[dict[str, Any]]:
        channel: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=512)
        with self._lock:
            self._subscribers.setdefault(_topic_key(topic_id), []).append(channel)
        return channel

    def unsubscribe(self, topic_id: str, channel: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            channels = self._subscribers.get(_topic_key(topic_id), [])
            if channel in channels:
                channels.remove(channel)
            if not channels:
                self._subscribers.pop(_topic_key(topic_id), None)

    def publish(self, topic_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            channels = list(self._subscribers.get(_topic_key(topic_id), []))
        for channel in channels:
            try:
                channel.put_nowait(event)
            except queue.Full:
                # Slow consumer: drop the oldest event so the stream stays live.
                try:
                    channel.get_nowait()
                    channel.put_nowait(event)
                except (queue.Empty, queue.Full):
                    pass


@dataclass
class RunLogger:
    topic_root: Path
    run_id: str
    bus: EventBus
    topic_id: str = ""
    seq: int = 0
    on_event: Callable[[dict[str, Any]], None] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        # Create the file up front: the console opens the SSE stream the moment
        # a run is accepted, and an empty log is a valid "no events yet" state.
        # Without this the stream endpoint could not tell "run just started"
        # from "unknown run" and answered 404 on the first connection.
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.touch(exist_ok=True)

    @property
    def log_file(self) -> Path:
        return self.topic_root / "logs" / f"{self.run_id}.jsonl"

    def emit(
        self,
        stage: str,
        status: str,
        message: str = "",
        *,
        duration_ms: int | None = None,
        **data: Any,
    ) -> dict[str, Any]:
        with self._lock:
            self.seq += 1
            event: dict[str, Any] = {
                "seq": self.seq,
                "ts": utc_now_iso(),
                "run_id": self.run_id,
                "topic_id": self.topic_id or self.topic_root.name,
                "stage": stage,
                "status": status,
                "message": message,
            }
            if duration_ms is not None:
                event["duration_ms"] = duration_ms
            if data:
                event["data"] = data
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.bus.publish(self.topic_id or self.topic_root.name, event)
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:  # noqa: BLE001 - observers must never break the run
                pass
        return event


def read_events(log_file: Path) -> list[dict[str, Any]]:
    if not log_file.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in log_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def iter_live_events(
    bus: EventBus,
    topic_id: str,
    log_file: Path,
    *,
    run_id: str | None = None,
    after_seq: int = 0,
    heartbeat_seconds: float = 15.0,
) -> Iterator[dict[str, Any]]:
    """Yield historical events then live ones. Intended for a sync SSE generator.

    Subscribe *before* reading history so nothing emitted in between is lost.
    Duplicates are filtered by the monotonic ``seq``.
    """
    channel = bus.subscribe(topic_id)
    try:
        last_seq = after_seq
        for event in read_events(log_file):
            if run_id and event.get("run_id") != run_id:
                continue
            if int(event.get("seq") or 0) > last_seq:
                last_seq = int(event.get("seq") or 0)
                yield event
            if event.get("stage") == "run" and event.get("status") in TERMINAL_STATUSES:
                return
        while True:
            try:
                event = channel.get(timeout=heartbeat_seconds)
            except queue.Empty:
                yield {"heartbeat": True, "ts": utc_now_iso()}
                continue
            if run_id and event.get("run_id") != run_id:
                continue
            seq = int(event.get("seq") or 0)
            if seq <= last_seq:
                continue
            last_seq = seq
            yield event
            if event.get("stage") == "run" and event.get("status") in TERMINAL_STATUSES:
                return
    finally:
        bus.unsubscribe(topic_id, channel)


# Process-wide bus. Jobs and routes share it.
BUS = EventBus()
