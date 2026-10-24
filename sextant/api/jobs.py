"""In-process job registry. One background thread per job, one active job per kind.

A topic can have a retrieval pipeline running and, once the catalog exists, a
report job. They are separate kinds because they have different costs, different
concurrency rules and different UI surfaces: a pipeline owns the topic's
``runs.json`` entry, a report owns only its own log file.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Any

from sextant.config import Settings
from sextant.pipeline.report import run_report
from sextant.pipeline.runner import run_pipeline
from sextant.runlog import new_run_id, utc_now_iso
from sextant.workspace import WorkspaceError, topic_dir

KINDS = ("pipeline", "report")
KIND_LABELS = {"pipeline": "检索", "report": "调研报告"}


class JobConflict(RuntimeError):
    """Another job of this topic would compete for the same workspace."""


@dataclass
class Job:
    run_id: str
    topic_id: str
    kind: str = "pipeline"
    status: str = "queued"
    stage: str = "queued"
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str | None = None
    error: str | None = None
    hint: str | None = None
    records: int = 0
    papers: int = 0
    events: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._active: dict[tuple[str, str], str] = {}

    # ------------------------------------------------------------ starting
    def start(self, settings: Settings, topic_id: str) -> Job:
        """Start a retrieval pipeline. Refused while any job holds the topic."""
        return self._start(settings, topic_id, "pipeline")

    def start_report(self, settings: Settings, topic_id: str) -> Job:
        """Start an on-demand report job. Refused while the pipeline runs."""
        return self._start(settings, topic_id, "report")

    def _start(self, settings: Settings, topic_id: str, kind: str) -> Job:
        with self._lock:
            for other in KINDS:
                active_id = self._active.get((topic_id, other))
                active_job = self._jobs.get(active_id) if active_id else None
                if active_job is not None and active_job.status in {"queued", "running"}:
                    label = KIND_LABELS[other]
                    raise JobConflict(f"主题 {topic_id} 已有运行中的{label}任务：{active_id}")
            job = Job(run_id=new_run_id(), topic_id=topic_id, kind=kind)
            self._jobs[job.run_id] = job
            self._active[(topic_id, kind)] = job.run_id

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
            name=f"sextant-{kind}-{job.run_id}",
            daemon=True,
        )
        thread.start()
        return job

    # ------------------------------------------------------------ workers
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
            if job.kind == "report":
                summary = run_report(settings, topic_id, run_id=run_id, on_event=on_event)
                job.papers = int(summary.get("papers") or 0)
                if summary.get("status") == "completed":
                    job.status = "completed"
                    job.stage = "done"
                else:
                    job.status = "failed"
                    job.error = job.error or str(summary.get("error") or "")
            else:
                summary = run_pipeline(settings, topic_id, run_id=run_id, on_event=on_event)
                job.records = int(summary.get("records") or 0)
                job.status = "completed"
        except Exception as exc:  # noqa: BLE001 - the job boundary owns failure reporting
            job.status = "failed"
            job.error = job.error or f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = utc_now_iso()
            with self._lock:
                if self._active.get((topic_id, job.kind)) == run_id:
                    self._active.pop((topic_id, job.kind), None)

    # ------------------------------------------------------------ queries
    def get(self, run_id: str) -> Job | None:
        return self._jobs.get(run_id)

    def active_for(self, topic_id: str, kind: str = "pipeline") -> Job | None:
        with self._lock:
            run_id = self._active.get((topic_id, kind))
        return self._jobs.get(run_id) if run_id else None

    def list(self, topic_id: str | None = None) -> list[Job]:
        jobs = list(self._jobs.values())
        if topic_id:
            jobs = [job for job in jobs if job.topic_id == topic_id]
        return sorted(jobs, key=lambda job: job.started_at, reverse=True)


REGISTRY = JobRegistry()
