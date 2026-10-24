from __future__ import annotations

from pathlib import Path

import pytest

from sextant.config import Settings


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        workspace_root=tmp_path / "workspace",
        llm_base_url="",
        llm_api_key="",
        llm_model="",
        max_results_per_source=10,
    )


@pytest.fixture()
def client(settings: Settings):
    from fastapi.testclient import TestClient

    from sextant.api.app import create_app

    return TestClient(create_app(settings))
