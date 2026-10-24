#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "2.0"
#: Group name for records that fit no planned research direction.
OTHER_DIRECTION = "其他相关研究"
CHANNELS = ("paper_sites", "search", "feeds")
STAGE_DIRS = {
    "intake": "00_intake",
    "queries": "01_queries",
    "raw": "02_raw",
    "processed": "03_processed",
    "summary": "04_summary",
    "manifests": "manifests",
}
GLOBAL_CACHE_DIRNAME = ".sextant-cache"


def contains_cjk(text: str | None) -> bool:
    """Return True when *text* contains at least one CJK Unified Ideograph."""
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))

SOURCE_WEIGHTS = {
    "pubmed": 96,
    "europepmc": 94,
    "clinicaltrials": 92,
    "semantic-scholar": 82,
    "openalex": 80,
    "crossref": 78,
    "medrxiv": 66,
    "biorxiv": 64,
    "arxiv": 58,
    "rss": 42,
}

SOURCE_ID_ALIASES = {
    "semantic_scholar": "semantic-scholar",
}

_WINDOWS_ABS_RE = re.compile(r"^[a-zA-Z]:[\\/]")
_WSL_ABS_RE = re.compile(r"^/mnt/([a-zA-Z])(?:/|$)")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.strip().lower())
    return re.sub(r"-{2,}", "-", value).strip("-") or "topic"


def normalize_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def canonical_source_id(source: str | None) -> str:
    normalized = normalize_text(source).lower().replace("_", "-")
    return SOURCE_ID_ALIASES.get(normalized, normalized)


def truncate_words(text: str | None, max_words: int) -> str:
    words = normalize_text(text).split()
    return " ".join(words if len(words) <= max_words else words[:max_words] + ["..."])


def parse_date(value: str | None) -> str | None:
    candidate = normalize_text(value)
    if not candidate:
        return None
    for fmt in (
        "%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y/%m", "%Y",
        "%d %B %Y", "%B %d, %Y", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            parsed = datetime.strptime(candidate, fmt)
            if fmt in ("%Y-%m", "%Y/%m"):
                return parsed.strftime("%Y-%m-01")
            if fmt == "%Y":
                return parsed.strftime("%Y-01-01")
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            pass
    match = re.search(r"(\d{4}-\d{2}-\d{2})", candidate)
    return match.group(1) if match else None


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> Path:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> Path:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _windows_path_from_wsl(raw: str) -> Path:
    match = _WSL_ABS_RE.match(raw)
    if not match:
        return Path(raw)
    rest = raw[match.end():].lstrip("/").replace("/", "\\")
    return Path(f"{match.group(1).upper()}:\\{rest}")


def _wsl_path_from_windows(raw: str) -> Path:
    if not _WINDOWS_ABS_RE.match(raw):
        return Path(raw)
    rest = raw[2:].replace("\\", "/").lstrip("/")
    return Path(f"/mnt/{raw[0].lower()}/{rest}")


def _path_candidates(workspace_root: str | Path) -> list[Path]:
    raw = normalize_text(str(workspace_root))
    if not raw:
        raise ValueError("workspace_root must not be empty")
    candidates: list[Path] = []
    for candidate in (
        _windows_path_from_wsl(raw) if _WSL_ABS_RE.match(raw) else None,
        Path(raw).expanduser(),
        _wsl_path_from_windows(raw) if _WINDOWS_ABS_RE.match(raw) else None,
        (project_root() / raw).absolute() if not Path(raw).is_absolute() else None,
        (Path.cwd() / raw).absolute() if not Path(raw).is_absolute() else None,
    ):
        if candidate is not None and str(candidate) not in {str(item) for item in candidates}:
            candidates.append(candidate)
    return candidates


def _looks_like_topic_root(path: Path) -> bool:
    return any((path / STAGE_DIRS[name]).exists() for name in ("intake", "queries", "raw"))


def topic_root(workspace_root: str | Path, topic_id: str) -> Path:
    topic_slug = slugify(topic_id)
    candidates = _path_candidates(workspace_root)
    for candidate in candidates:
        if candidate.name == topic_slug and _looks_like_topic_root(candidate):
            return candidate
        if _looks_like_topic_root(candidate / topic_slug):
            return candidate / topic_slug
    for candidate in candidates:
        if candidate.exists():
            return candidate if candidate.name == topic_slug else candidate / topic_slug
    return candidates[0] if candidates[0].name == topic_slug else candidates[0] / topic_slug


def ensure_workspace_structure(workspace_root: str | Path, topic_id: str) -> Path:
    root = topic_root(workspace_root, topic_id)
    for stage_name, relative in STAGE_DIRS.items():
        base = ensure_dir(root / relative)
        if stage_name == "raw":
            for channel in CHANNELS:
                ensure_dir(base / channel)
    ensure_dir(global_cache_dir(root))
    return root


def topic_file(root: Path) -> Path:
    return root / STAGE_DIRS["intake"] / "topic.json"


def workspace_topics_root(root: Path) -> Path:
    return root.parent


def global_cache_dir(root: Path) -> Path:
    return workspace_topics_root(root) / GLOBAL_CACHE_DIRNAME


def global_abstract_cache_file(root: Path) -> Path:
    return global_cache_dir(root) / "abstract_cache.json"


def global_reading_cache_file(root: Path) -> Path:
    return global_cache_dir(root) / "reading_cache.json"


def global_translation_cache_file(root: Path) -> Path:
    return global_cache_dir(root) / "translation_cache.json"


def route_file(root: Path) -> Path:
    return root / STAGE_DIRS["intake"] / "followup_decision.json"


def queries_file(root: Path) -> Path:
    return root / STAGE_DIRS["queries"] / "queries.json"


def output_dir(root: Path) -> Path:
    return root / "output"


def _safe_output_name(value: str, fallback: str) -> str:
    text = normalize_text(value)
    if not text:
        text = fallback
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        text = fallback
    return text[:120].rstrip(" .") or fallback


def _topic_output_subject(root: Path) -> str:
    topic = load_topic(root)
    return _safe_output_name(str(topic.get("topic_name") or root.name), root.name)


def _focus_output_subject(root: Path) -> str:
    route = read_json(route_file(root), default={}) or {}
    focus_topic = normalize_text(route.get("focus_topic"))
    if focus_topic:
        return _safe_output_name(focus_topic, _topic_output_subject(root))
    return _topic_output_subject(root)


def _deep_read_output_subject(root: Path) -> str:
    route = read_json(route_file(root), default={}) or {}
    selected_ids = [normalize_text(item) for item in route.get("paper_ids", []) if normalize_text(item)]
    title_map: dict[str, str] = {}
    for path in (ranked_records_file(root), pending_verification_file(root)):
        for row in read_jsonl(path):
            paper_id = normalize_text(row.get("record_id"))
            title = normalize_text(row.get("title"))
            if paper_id and title and paper_id not in title_map:
                title_map[paper_id] = title
    selected_titles = [title_map[paper_id] for paper_id in selected_ids if paper_id in title_map]
    if selected_titles:
        return _safe_output_name("、".join(selected_titles), _topic_output_subject(root))
    payload = read_json(deep_read_requests_file(root), default={}) or {}
    selected_papers = payload.get("selected_papers") if isinstance(payload.get("selected_papers"), list) else []
    fallback_titles = [normalize_text(item.get("title")) for item in selected_papers if isinstance(item, dict) and normalize_text(item.get("title"))]
    if fallback_titles:
        return _safe_output_name("、".join(fallback_titles), _topic_output_subject(root))
    return _topic_output_subject(root)


def abstract_requests_file(root: Path) -> Path:
    return root / STAGE_DIRS["queries"] / "missing_abstract_requests.json"


def abstract_supplements_file(root: Path) -> Path:
    return root / STAGE_DIRS["raw"] / "abstract_supplements.jsonl"


def report_read_requests_file(root: Path) -> Path:
    return root / STAGE_DIRS["queries"] / "report_read_requests.json"


def prefetch_read_requests_file(root: Path) -> Path:
    return root / STAGE_DIRS["queries"] / "prefetch_read_requests.json"


def report_read_supplements_file(root: Path) -> Path:
    return root / STAGE_DIRS["raw"] / "report_read_supplements.jsonl"


def prefetch_read_supplements_file(root: Path) -> Path:
    return root / STAGE_DIRS["raw"] / "prefetch_read_supplements.jsonl"


def report_synthesis_input_file(root: Path) -> Path:
    return root / STAGE_DIRS["queries"] / "report_synthesis_input.json"


def deep_read_requests_file(root: Path) -> Path:
    return root / STAGE_DIRS["queries"] / "deep_read_requests.json"


def deep_read_supplements_file(root: Path) -> Path:
    return root / STAGE_DIRS["raw"] / "deep_read_supplements.jsonl"


def deep_read_synthesis_input_file(root: Path) -> Path:
    return root / STAGE_DIRS["queries"] / "deep_read_synthesis_input.json"


def deep_read_content_zh_file(root: Path) -> Path:
    return root / STAGE_DIRS["summary"] / "deep_read_content_zh.json"


def deep_read_content_en_file(root: Path) -> Path:
    return root / STAGE_DIRS["summary"] / "deep_read_content_en.json"


def deep_read_file(root: Path) -> Path:
    return output_dir(root) / f"论文精读：{_deep_read_output_subject(root)}.html"


def relevance_candidates_file(root: Path) -> Path:
    return root / STAGE_DIRS["processed"] / "relevance_candidates.jsonl"


def relevance_decisions_file(root: Path) -> Path:
    return root / STAGE_DIRS["processed"] / "relevance_decisions.jsonl"


def ranked_records_file(root: Path) -> Path:
    return root / STAGE_DIRS["processed"] / "ranked_articles.jsonl"


def related_articles_file(root: Path) -> Path:
    return root / STAGE_DIRS["processed"] / "other_related_articles.jsonl"


def pending_verification_file(root: Path) -> Path:
    return root / STAGE_DIRS["processed"] / "pending_verification_articles.jsonl"


def article_catalog_file(root: Path) -> Path:
    return output_dir(root) / f"检索报告：{_topic_output_subject(root)}.html"


def focus_relevance_candidates_file(root: Path) -> Path:
    return root / STAGE_DIRS["processed"] / "focus_relevance_candidates.jsonl"


def focus_relevance_decisions_file(root: Path) -> Path:
    return root / STAGE_DIRS["processed"] / "focus_relevance_decisions.jsonl"


def focus_ranked_records_file(root: Path) -> Path:
    return root / STAGE_DIRS["processed"] / "focus_ranked_articles.jsonl"


def focus_catalog_file(root: Path) -> Path:
    return output_dir(root) / f"特定主题报告：{_focus_output_subject(root)}.html"


def process_manifest_file(root: Path) -> Path:
    return root / STAGE_DIRS["processed"] / "process_manifest.json"


def paper_cards_file(root: Path) -> Path:
    return root / STAGE_DIRS["summary"] / "paper_cards.json"


def report_content_zh_file(root: Path) -> Path:
    return root / STAGE_DIRS["summary"] / "report_content_zh.json"


def report_content_en_file(root: Path) -> Path:
    return root / STAGE_DIRS["summary"] / "report_content_en.json"


def summary_zh_file(root: Path) -> Path:
    return root / STAGE_DIRS["summary"] / "structured_summary_zh.html"


def summary_en_file(root: Path) -> Path:
    return root / STAGE_DIRS["summary"] / "structured_summary_en.html"


def summary_file(root: Path) -> Path:
    return output_dir(root) / f"综合调研报告：{_topic_output_subject(root)}.html"


def status_file(root: Path) -> Path:
    return root / STAGE_DIRS["manifests"] / "status.json"


def feed_monitor_file(root: Path) -> Path:
    return root / STAGE_DIRS["manifests"] / "feed_monitor.json"


def retrieval_log_file(root: Path) -> Path:
    return root / STAGE_DIRS["raw"] / "retrieval_log.jsonl"


def load_topic(root: Path) -> dict[str, Any]:
    value = read_json(topic_file(root), default={})
    return value if isinstance(value, dict) else {}


def load_queries(root: Path) -> dict[str, Any]:
    value = read_json(queries_file(root), default={})
    return value if isinstance(value, dict) else {}


def load_feed_monitor_state(root: Path) -> dict[str, Any]:
    value = read_json(feed_monitor_file(root), default={})
    return value if isinstance(value, dict) else {}


def list_channel_files(root: Path, channel: str) -> list[Path]:
    folder = root / STAGE_DIRS["raw"] / channel
    return sorted(folder.glob("*.json")) if folder.exists() else []


def configured_sources(queries: dict[str, Any]) -> dict[str, list[str]]:
    settings = queries.get("source_settings") if isinstance(queries.get("source_settings"), dict) else {}
    if settings:
        configured: dict[str, list[str]] = {}
        for channel in CHANNELS:
            channel_settings = settings.get(channel) if isinstance(settings.get(channel), dict) else {}
            configured[channel] = [
                normalize_text(str(source_id))
                for source_id, config in channel_settings.items()
                if normalize_text(str(source_id)) and (not isinstance(config, dict) or config.get("enabled", True))
            ]
        return configured
    payload = queries.get("sources") if isinstance(queries.get("sources"), dict) else {}
    return {
        channel: [normalize_text(str(item)) for item in payload.get(channel, []) if normalize_text(str(item))]
        for channel in CHANNELS
    }


def bool_query(keywords: list[str], synonyms: list[str], excludes: list[str]) -> str:
    include = " OR ".join(f'"{term}"' for term in keywords + synonyms if normalize_text(term))
    exclude = " OR ".join(f'"{term}"' for term in excludes if normalize_text(term))
    return f"({include}) NOT ({exclude})" if include and exclude else include


def plain_query(keywords: list[str], synonyms: list[str], excludes: list[str]) -> str:
    return " ".join([term for term in keywords + synonyms if normalize_text(term)] + [f"-{term}" for term in excludes if normalize_text(term)])


def extract_query_terms(*values: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for value in values:
        for part in re.split(r"[^a-zA-Z0-9]+", value.lower()):
            if len(part) >= 3 and part not in seen:
                seen.add(part)
                terms.append(part)
    return terms


def keyword_hits(record: dict[str, Any], keywords: list[str], synonyms: list[str]) -> list[str]:
    text = " ".join(normalize_text(record.get(key)) for key in ("title", "abstract", "summary")).lower()
    hits: list[str] = []
    for term in keywords + synonyms:
        token = normalize_text(term).lower().strip('"')
        if token and token in text and token not in hits:
            hits.append(token)
    return hits


def source_weight(source: str) -> int:
    return SOURCE_WEIGHTS.get(canonical_source_id(source), 45)


def canonical_record_id(source: str, source_id: str | None, title: str | None) -> str:
    seed = source_id or title or utc_now_iso()
    return f"{source}:{sha1_text(seed)[:12]}"


def compute_stage_status(root: Path) -> dict[str, Any]:
    topic = load_topic(root)
    queries = load_queries(root)
    planning = queries.get("planning") if isinstance(queries.get("planning"), dict) else {}
    time_window_required = True
    time_window_confirmed = bool(planning.get("time_window_confirmed"))
    retrieval_preference_confirmed = bool(planning.get("retrieval_preference_confirmed"))
    source_scope_confirmed = bool(planning.get("source_scope_confirmed"))
    configured = configured_sources(queries)
    enabled = [channel for channel in CHANNELS if configured.get(channel)]
    raw_counts = {channel: len(list_channel_files(root, channel)) for channel in CHANNELS}
    logs = read_jsonl(retrieval_log_file(root))

    completed_channels: list[str] = []
    for channel in enabled:
        attempts = [row for row in logs if normalize_text(row.get("channel")) == channel]
        if attempts:
            if channel == "feeds":
                attempted = {normalize_text(row.get("object_id")) for row in attempts if normalize_text(row.get("object_id"))}
            else:
                attempted = {canonical_source_id(row.get("source")) for row in attempts if normalize_text(row.get("source"))}
            expected = {canonical_source_id(item) for item in configured.get(channel, [])}
            if expected.issubset(attempted):
                completed_channels.append(channel)
        elif raw_counts[channel] > 0:
            # Migration fallback for artifacts created before the per-request log contract.
            completed_channels.append(channel)
    retrieval_complete = bool(enabled) and set(enabled).issubset(completed_channels)

    requests = read_json(abstract_requests_file(root), default={}) or {}
    required_ids = {str(item.get("paper_id")) for item in requests.get("papers", []) if item.get("requestable")}
    supplement_rows = read_jsonl(abstract_supplements_file(root))
    completed_ids = {str(item.get("paper_id")) for item in supplement_rows}
    successful_ids = {str(item.get("paper_id")) for item in supplement_rows if item.get("paper_id") and item.get("status") == "ok" and item.get("abstract_usable") is True}
    abstract_ready = abstract_requests_file(root).exists() and required_ids.issubset(completed_ids)
    candidate_ids = {str(item.get("record_id")) for item in read_jsonl(relevance_candidates_file(root)) if item.get("record_id")}
    decisions = read_jsonl(relevance_decisions_file(root))
    decision_ids = {
        str(item.get("paper_id")) for item in decisions
        if item.get("paper_id") and item.get("decision") in {"include", "exclude"}
    }
    relevance_prepared = relevance_candidates_file(root).exists()
    relevance_complete = relevance_prepared and candidate_ids.issubset(decision_ids)
    processed = ranked_records_file(root).exists() and article_catalog_file(root).exists()
    route = read_json(route_file(root), default={}) or {}
    report_requests = read_json(report_read_requests_file(root), default={}) or {}
    report_required_ids = {
        str(item) for item in report_requests.get("selected_paper_ids", []) if normalize_text(str(item))
    } or {
        str(item.get("paper_id")) for item in report_requests.get("papers", []) if item.get("paper_id")
    }
    report_completed_ids = {str(item.get("paper_id")) for item in read_jsonl(report_read_supplements_file(root)) if item.get("paper_id")}
    report_read_complete = bool(report_required_ids) and report_required_ids.issubset(report_completed_ids)
    report_outputs_complete = paper_cards_file(root).exists() and summary_file(root).exists()
    deep_requests = read_json(deep_read_requests_file(root), default={}) or {}
    deep_required_ids = {
        str(item) for item in deep_requests.get("selected_paper_ids", []) if normalize_text(str(item))
    } or {
        str(item.get("paper_id")) for item in deep_requests.get("papers", []) if item.get("paper_id")
    }
    deep_completed_ids = {str(item.get("paper_id")) for item in read_jsonl(deep_read_supplements_file(root)) if item.get("paper_id")}
    deep_read_complete = bool(deep_required_ids) and deep_required_ids.issubset(deep_completed_ids)
    deep_outputs_complete = deep_read_file(root).exists()
    focus_outputs_complete = focus_catalog_file(root).exists()
    active_route = normalize_text(route.get("action"))
    route_cycle_complete = (
        (active_route == "report" and report_outputs_complete) or
        (active_route == "deep-read" and deep_outputs_complete) or
        (active_route == "topic-read" and focus_outputs_complete)
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "topic_id": root.name,
        "topic_root": str(root),
        "generated_at": utc_now_iso(),
        "current_gate": (
            "topic_initialization" if not topic.get("topic_name") else
            "time_window_selection" if not time_window_confirmed else
            "retrieval_preference_selection" if not retrieval_preference_confirmed else
            "source_scope_selection" if not source_scope_confirmed else
            "retrieval" if not retrieval_complete else
            "abstract_completion" if not abstract_ready else
            "processing" if not relevance_prepared else
            "relevance_review" if not relevance_complete else
            "ranking" if not processed else
            "followup_decision" if not active_route or route_cycle_complete else
            "route_execution"
        ),
        "stages": {
            "intake": {
                "complete": bool(topic.get("topic_name")),
                "mode": topic.get("mode"),
                "time_window": topic.get("time_window"),
                "from_date": topic.get("from_date"),
                "to_date": topic.get("to_date"),
            },
            "time_window_selection": {
                "ready": bool(topic.get("topic_name")) and time_window_required,
                "required": time_window_required,
                "complete": time_window_confirmed,
                "time_window": topic.get("time_window"),
                "from_date": topic.get("from_date"),
                "to_date": topic.get("to_date"),
            },
            "retrieval_preference_selection": {
                "ready": bool(topic.get("topic_name")) and time_window_confirmed,
                "required": True,
                "complete": retrieval_preference_confirmed,
                "retrieval_preference": topic.get("retrieval_preference") or "",
            },
            "source_scope_selection": {
                "ready": bool(topic.get("topic_name")) and time_window_confirmed and retrieval_preference_confirmed,
                "required": True,
                "complete": source_scope_confirmed,
                "enabled_sources": configured,
                "default_limit": planning.get("default_limit"),
            },
            "retrieval": {
                "ready": bool(topic.get("topic_name")) and time_window_confirmed and source_scope_confirmed,
                "complete": retrieval_complete,
                "enabled_channels": enabled,
                "completed_channels": completed_channels,
                "raw_counts": raw_counts,
            },
            "abstract_completion": {"ready": retrieval_complete, "complete": abstract_ready, "required": len(required_ids), "attempted": len(required_ids & completed_ids), "content_succeeded": len(required_ids & successful_ids), "content_failed": len(required_ids & completed_ids) - len(required_ids & successful_ids)},
            "relevance_review": {
                "ready": abstract_ready,
                "prepared": relevance_prepared,
                "complete": relevance_complete,
                "candidates": len(candidate_ids),
                "decisions": len(candidate_ids & decision_ids),
                "candidate_file": str(relevance_candidates_file(root)),
            },
            "processed": {
                "ready": relevance_complete,
                "complete": processed,
                "ranked_records": str(ranked_records_file(root)),
                "pending_verification": str(pending_verification_file(root)),
                "pending_count": len(read_jsonl(pending_verification_file(root))),
                "article_catalog": str(article_catalog_file(root)),
            },
            "followup_decision": {
                "ready": processed,
                "complete": bool(active_route),
                "action": active_route,
                "route_cycle_complete": route_cycle_complete,
                "next_options": ["report", "deep-read", "topic-read", "requery"],
            },
            "output": {
                "ready": bool(route.get("action")),
                "summary_dir": str(root / STAGE_DIRS["summary"]),
                "report_read_required": len(report_required_ids),
                "report_read_completed": len(report_required_ids & report_completed_ids),
                "report_read_complete": report_read_complete,
                "report_outputs_complete": report_outputs_complete,
                "deep_read_required": len(deep_required_ids),
                "deep_read_completed": len(deep_required_ids & deep_completed_ids),
                "deep_read_complete": deep_read_complete,
                "deep_outputs_complete": deep_outputs_complete,
                "focus_outputs_complete": focus_outputs_complete,
                "deep_read": str(deep_read_file(root)),
                "summary": str(summary_file(root)),
                "summary_zh_compat": str(summary_zh_file(root)),
                "summary_en_compat": str(summary_en_file(root)),
            },
        },
    }


def persist_status(root: Path) -> dict[str, Any]:
    payload = compute_stage_status(root)
    status_file(root).unlink(missing_ok=True)
    return payload


def assert_stage_ready(root: Path, stage_name: str) -> None:
    status = compute_stage_status(root)
    stage = status["stages"].get(stage_name)
    if not stage or not stage.get("ready"):
        current_gate = status.get("current_gate", "unknown")
        hints: list[str] = [f"Current gate: '{current_gate}'."]
        if current_gate == "time_window_selection":
            hints.append("Latest mode must stop and wait for the user to confirm a retrieval time window before any retrieval step.")
        if current_gate == "retrieval_preference_selection":
            hints.append("After the user confirms the time window, ask whether to allow highly relevant out-of-window articles or to keep a strict time-only window before selecting sources.")
        if current_gate == "source_scope_selection":
            hints.append("Retrieval source selection is not confirmed. Show the selected source list to the user and collect per-source counts first.")
        retrieval = status["stages"].get("retrieval", {})
        enabled = retrieval.get("enabled_channels", [])
        completed = retrieval.get("completed_channels", [])
        missing = [ch for ch in enabled if ch not in completed]
        if missing:
            hints.append(f"Retrieval not complete. Missing channels: {missing}. Completed: {completed}. Ensure all MCP search results are saved to 02_raw/ before retrying.")
        raw_counts = retrieval.get("raw_counts", {})
        hints.append(f"Raw artifact counts: {raw_counts}.")
        raise SystemExit(
            f"Stage '{stage_name}' is not ready. " + " ".join(hints) + " "
            "Run workflow.py status for full details."
        )
