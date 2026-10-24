"""In-process job registry. One background thread per run, one active run per topic."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Any

from sextant.config import Settings
from sextant.pipeline.runner import new_run_id, run_pipeline
from sextant.runlog import utc_now_iso
from sextant.workspace import WorkspaceError, topic_dir


class JobConflict(RuntimeError):
    """A run is already active for this topic."""


@dataclass
class Job:
    run_id: str
    topic_id: str
    status: str = "queued"
    stage: str = "queued"
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str | None = None
    error: str | None = None
    hint: str | None = None
    records: int = 0
    events: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._active: dict[str, str] = {}

    def start(self, settings: Settings, topic_id: str) -> Job:
        with self._lock:
            active_id = self._active.get(topic_id)
            active_job = self._jobs.get(active_id) if active_id else None
            if active_job is not None and active_job.status in {"queued", "running"}:
                raise JobConflict(f"主题 {topic_id} 已有运行中的任务：{active_id}")
            job = Job(run_id=new_run_id(), topic_id=topic_id)
            self._jobs[job.run_id] = job
            self._active[topic_id] = job.run_id

        # Create the log before the caller can subscribe to the event stream.
        # The worker thread needs a moment to start; without this the console's
        # first SSE connection races the worker and gets a 404.
        try:
            log_dir = topic_dir(settings, topic_id) / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / f"{job.run_id}.jsonl").touch(exist_ok=True)
        except (OSError, WorkspaceError):
            # A real filesystem problem will surface from the run itself.
            pass

        thread = threading.Thread(
            target=self._execute,
            args=(settings, job.run_id, topic_id),
            name=f"sextant-run-{job.run_id}",
            daemon=True,
        )
        thread.start()
        return job

    def _execute(self, settings: Settings, run_id: str, topic_id: str) -> None:
        job = self._jobs[run_id]
        job.status = "running"

        def on_event(event: dict[str, Any]) -> None:
            job.events += 1
            stage = str(event.get("stage") or job.stage)
            if stage != "run":
                job.stage = stage
            if stage == "run" and event.get("status") == "completed":
                job.status = "completed"
                job.stage = "done"
            elif stage == "run" and event.get("status") == "failed":
                job.status = "failed"
                job.error = str(event.get("message") or "")
                data = event.get("data") or {}
                job.hint = str(data.get("hint") or "") or None

        try:
            summary = run_pipeline(settings, topic_id, run_id=run_id, on_event=on_event)
            job.records = int(summary.get("records") or 0)
            job.status = "completed"
        except Exception as exc:  # noqa: BLE001 - the job boundary owns failure reporting
            job.status = "failed"
            job.error = job.error or f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = utc_now_iso()
            with self._lock:
                if self._active.get(topic_id) == run_id:
                    self._active.pop(topic_id, None)

    def get(self, run_id: str) -> Job | None:
        return self._jobs.get(run_id)

    def active_for(self, topic_id: str) -> Job | None:
        with self._lock:
            run_id = self._active.get(topic_id)
        return self._jobs.get(run_id) if run_id else None

    def list(self, topic_id: str | None = None) -> list[Job]:
        jobs = list(self._jobs.values())
        if topic_id:
            jobs = [job for job in jobs if job.topic_id == topic_id]
        return sorted(jobs, key=lambda job: job.started_at, reverse=True)


REGISTRY = JobRegistry()
