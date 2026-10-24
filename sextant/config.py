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


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


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
    #: Records per enrichment request. Smaller batches survive reasoning-model
    #: token budgets better; larger batches mean fewer round trips.
    llm_enrich_batch_size: int = 10
    #: Completion budget per enrichment request, reasoning tokens included.
    llm_enrich_max_tokens: int = 8000
    #: Completion budget for the retrieval plan. Reasoning tokens are billed to
    #: the same allowance, so 900 was not enough for a six-query plan.
    llm_plan_max_tokens: int = 2000
    #: Papers analysed per report request. Each item needs a bilingual impact
    #: paragraph, so this batch is smaller than the enrichment batch.
    llm_report_batch_size: int = 5
    #: Completion budget for one report-analysis batch, reasoning included.
    llm_report_max_tokens: int = 8000
    #: Completion budget for the single synthesis call. It writes the executive
    #: summary, six bilingual sections and the subtheme grouping at once.
    llm_report_synthesis_max_tokens: int = 8000
    #: A synthesis call is long; the default 45s request timeout is too short.
    llm_report_timeout_seconds: float = 180.0
    #: How many top-ranked papers enter the integrated report.
    report_max_papers: int = 20
    #: Reports need an LLM; disable this to keep runs fast and cheap.
    report_enabled: bool = True

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
        llm_enrich_batch_size=_env_int("SEXTANT_LLM_ENRICH_BATCH_SIZE", 10),
        llm_enrich_max_tokens=_env_int("SEXTANT_LLM_ENRICH_MAX_TOKENS", 8000),
        llm_plan_max_tokens=_env_int("SEXTANT_LLM_PLAN_MAX_TOKENS", 2000),
        llm_report_batch_size=_env_int("SEXTANT_LLM_REPORT_BATCH_SIZE", 5),
        llm_report_max_tokens=_env_int("SEXTANT_LLM_REPORT_MAX_TOKENS", 8000),
        llm_report_synthesis_max_tokens=_env_int("SEXTANT_LLM_REPORT_SYNTHESIS_MAX_TOKENS", 8000),
        llm_report_timeout_seconds=_env_float("SEXTANT_LLM_REPORT_TIMEOUT", 180.0),
        report_max_papers=_env_int("SEXTANT_REPORT_MAX_PAPERS", 20),
        report_enabled=_env_bool("SEXTANT_REPORT_ENABLED", True),
    )


def reset_settings_cache() -> None:
    """Test helper: re-read the environment on the next get_settings() call."""
    get_settings.cache_clear()
