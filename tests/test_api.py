from __future__ import annotations

import pytest

from sextant.api.jobs import Job, JobRegistry


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


def test_invalid_date_returns_400(client, no_network):
    response = client.post(
        "/api/topics",
        json={"name": "Bad Window", "from_date": "2024/01/01"},
    )
    assert response.status_code == 400
