from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from sextant.api.app import create_app
from sextant.api.jobs import Job, JobConflict, JobRegistry
from sextant.workspace import catalog_path, topic_dir


@pytest.fixture()
def no_network(monkeypatch):
    def fake_start(self, settings, topic_id):
        return Job(run_id="run-test", topic_id=topic_id)

    monkeypatch.setattr(JobRegistry, "start", fake_start)


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["llm_configured"] is False
    assert body["workspace_writable"] is True


def test_console_supports_persistent_light_dark_theme(client):
    response = client.get("/")

    assert response.status_code == 200
    assert 'id="themeToggle"' in response.text
    assert 'aria-label="切换日间/夜间模式"' in response.text
    assert "sextant-theme" in response.text
    assert 'data-theme="dark"' in response.text


def test_create_and_list_topic(client, no_network):
    response = client.post(
        "/api/topics",
        json={"name": "LLM Agent Evaluation", "keywords": ["LLM agent", "benchmark"]},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["topic"]["topic_id"] == "llm-agent-evaluation"
    assert body["job"]["run_id"] == "run-test"

    listing = client.get("/api/topics").json()["topics"]
    assert listing[0]["topic_name"] == "LLM Agent Evaluation"

    detail = client.get("/api/topics/llm-agent-evaluation").json()
    assert detail["record_count"] == 0
    assert detail["catalog_exists"] is False
    assert detail["runs"] == []
    assert detail["active_job"] is None


def test_duplicate_topic_returns_conflict(client, no_network):
    payload = {"name": "Graph Neural Networks"}
    assert client.post("/api/topics", json=payload).status_code == 202
    assert client.post("/api/topics", json=payload).status_code == 409


def test_missing_topic_returns_404(client):
    assert client.get("/api/topics/does-not-exist").status_code == 404
    assert client.post("/api/topics/does-not-exist/rerun").status_code == 404


def test_catalog_missing_returns_404(client, no_network):
    client.post("/api/topics", json={"name": "No Catalog Yet"})
    response = client.get("/api/topics/no-catalog-yet/catalog")
    assert response.status_code == 404
    assert "尚未生成" in response.json()["detail"]


def test_report_missing_returns_404(client, no_network):
    client.post("/api/topics", json={"name": "No Report Yet"})
    response = client.get("/api/topics/no-report-yet/report")
    assert response.status_code == 404
    assert "尚未生成" in response.json()["detail"]
    detail = client.get("/api/topics/no-report-yet").json()
    assert detail["report_exists"] is False
    assert detail["report_url"] is None


def test_report_served_when_present(client, settings, no_network):
    from sextant.workspace import report_path

    client.post("/api/topics", json={"name": "Has Report"})
    report = report_path(settings.workspace_root / "has-report")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("<html><body>integrated report</body></html>", encoding="utf-8")

    response = client.get("/api/topics/has-report/report")
    assert response.status_code == 200
    assert "integrated report" in response.text

    detail = client.get("/api/topics/has-report").json()
    assert detail["report_exists"] is True
    assert detail["report_url"] == "/api/topics/has-report/report"

    listing = client.get("/api/topics").json()["topics"]
    assert listing[0]["report_exists"] is True
    assert listing[0]["report_url"] == "/api/topics/has-report/report"


def test_invalid_date_returns_400(client, no_network):
    response = client.post(
        "/api/topics",
        json={"name": "Bad Window", "from_date": "2024/01/01"},
    )
    assert response.status_code == 400


def test_start_creates_log_before_worker_runs(settings, monkeypatch):
    """The console subscribes immediately after POST; the log must already exist."""
    import sextant.api.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "run_pipeline", lambda *args, **kwargs: {"records": 0})
    registry = JobRegistry()

    job = registry.start(settings, "fresh-topic")

    log_file = settings.workspace_root / "fresh-topic" / "logs" / f"{job.run_id}.jsonl"
    assert log_file.exists()


def test_events_replays_finished_run(client, settings, no_network):
    client.post("/api/topics", json={"name": "Replay Topic"})
    log_file = settings.workspace_root / "replay-topic" / "logs" / "run-test.jsonl"
    log_file.write_text(
        '{"seq": 1, "run_id": "run-test", "topic_id": "replay-topic", '
        '"stage": "run", "status": "completed", "message": "完成"}\n',
        encoding="utf-8",
    )

    response = client.get("/api/topics/replay-topic/events?run_id=run-test")

    assert response.status_code == 200
    assert "completed" in response.text


def test_events_unknown_run_returns_404(client, no_network):
    client.post("/api/topics", json={"name": "Unknown Run"})
    response = client.get("/api/topics/unknown-run/events?run_id=missing-run")
    assert response.status_code == 404


def _llm_client(settings) -> TestClient:
    llm_settings = replace(
        settings,
        llm_base_url="https://example.invalid/v1",
        llm_api_key="test",
        llm_model="test-model",
    )
    return TestClient(create_app(llm_settings))


def test_report_generation_requires_llm(client, no_network):
    client.post("/api/topics", json={"name": "No Model"})
    response = client.post("/api/topics/no-model/report")
    assert response.status_code == 400
    assert "未配置模型" in response.json()["detail"]


def test_report_generation_requires_catalog(settings, no_network):
    client = _llm_client(settings)
    client.post("/api/topics", json={"name": "No Catalog"})
    response = client.post("/api/topics/no-catalog/report")
    assert response.status_code == 400
    assert "证据目录" in response.json()["detail"]


def test_report_generation_starts_independent_job(settings, monkeypatch, no_network):
    client = _llm_client(settings)
    client.post("/api/topics", json={"name": "Has Catalog"})
    root = topic_dir(settings, "has-catalog")
    catalog = catalog_path(root)
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text("<html>catalog</html>", encoding="utf-8")
    monkeypatch.setattr(
        JobRegistry,
        "start_report",
        lambda self, settings, topic_id: Job(run_id="report-test", topic_id=topic_id, kind="report"),
    )

    response = client.post("/api/topics/has-catalog/report")

    assert response.status_code == 202
    assert response.json()["job"]["kind"] == "report"
    assert response.json()["job"]["run_id"] == "report-test"


def test_report_generation_returns_conflict(settings, monkeypatch, no_network):
    client = _llm_client(settings)
    client.post("/api/topics", json={"name": "Busy Topic"})
    root = topic_dir(settings, "busy-topic")
    catalog = catalog_path(root)
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text("<html>catalog</html>", encoding="utf-8")

    def conflict(self, settings, topic_id):
        raise JobConflict(f"主题 {topic_id} 已有运行中的检索任务：run-x")

    monkeypatch.setattr(JobRegistry, "start_report", conflict)

    response = client.post("/api/topics/busy-topic/report")

    assert response.status_code == 409
    assert "检索任务" in response.json()["detail"]


def test_report_job_conflicts_with_running_pipeline(settings):
    registry = JobRegistry()
    registry._jobs["p1"] = Job(run_id="p1", topic_id="t", kind="pipeline", status="running")
    registry._active[("t", "pipeline")] = "p1"

    with pytest.raises(JobConflict):
        registry.start_report(settings, "t")


def test_pipeline_conflicts_with_running_report(settings):
    registry = JobRegistry()
    registry._jobs["r1"] = Job(run_id="r1", topic_id="t", kind="report", status="running")
    registry._active[("t", "report")] = "r1"

    with pytest.raises(JobConflict):
        registry.start(settings, "t")
