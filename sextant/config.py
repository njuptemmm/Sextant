"""Central configuration. Every environment variable is read here, nowhere else."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

VERSION = "0.1.0"
PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent

try:  # Optional convenience: a checked-out .env configures local runs.
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:  # pragma: no cover - dotenv is a declared dependency
    pass

USER_AGENT = (
    "sextant/0.1 (research evidence catalog; "
    "+https://github.com/njuptemmm/Sextant)"
)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    workspace_root: Path = field(default_factory=lambda: REPO_ROOT / "workspace")
    host: str = "127.0.0.1"
    port: int = 8000
    mcp_port: int = 8001

    max_results_per_source: int = 20
    request_timeout_seconds: float = 20.0
    source_fetch_concurrency: int = 4

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 45.0
    llm_max_candidates: int = 40

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)

    @property
    def llm_endpoint(self) -> str:
        """Normalize base URL into the chat-completions endpoint."""
        base = self.llm_base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    @property
    def llm_label(self) -> str:
        return self.llm_model if self.llm_enabled else "disabled"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    raw_root = _env("SEXTANT_WORKSPACE_ROOT")
    workspace_root = Path(raw_root).expanduser() if raw_root else REPO_ROOT / "workspace"
    return Settings(
        workspace_root=workspace_root,
        host=_env("SEXTANT_HOST", "127.0.0.1"),
        port=_env_int("SEXTANT_PORT", 8000),
        mcp_port=_env_int("SEXTANT_MCP_PORT", 8001),
        max_results_per_source=_env_int("SEXTANT_MAX_RESULTS_PER_SOURCE", 20),
        request_timeout_seconds=_env_float("SEXTANT_REQUEST_TIMEOUT", 20.0),
        source_fetch_concurrency=_env_int("SEXTANT_SOURCE_CONCURRENCY", 4),
        llm_base_url=_env("SEXTANT_LLM_BASE_URL"),
        llm_api_key=_env("SEXTANT_LLM_API_KEY"),
        llm_model=_env("SEXTANT_LLM_MODEL"),
        llm_timeout_seconds=_env_float("SEXTANT_LLM_TIMEOUT", 45.0),
        llm_max_candidates=_env_int("SEXTANT_LLM_MAX_CANDIDATES", 40),
    )


def reset_settings_cache() -> None:
    """Test helper: re-read the environment on the next get_settings() call."""
    get_settings.cache_clear()
