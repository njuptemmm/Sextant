from __future__ import annotations

"""Static catalog of the scholarly sources the engine knows how to reach.

This is descriptive metadata (discipline, authority, time-filter support) used
to explain source selection in the UI and to the MCP agent. The live registry
of adapters is ``sextant.sources_registry``.
"""

import os
import re
from typing import Any

PAPER_SOURCES: list[dict[str, Any]] = [
    {"id": "pubmed", "tool": "pubmed_search", "label": "PubMed / MEDLINE", "kind": "bibliographic_database", "authority_level": "highest", "disciplines": ["biomedicine", "life_science", "medicine"], "default_enabled": True, "supports_time_filter": True, "time_filter_mode": "api_time_filter_with_code_fallback", "notes": "NCBI biomedical literature with MeSH-aware querying."},
    {"id": "europepmc", "tool": "europepmc_search", "label": "Europe PMC", "kind": "literature_and_repository", "authority_level": "highest", "disciplines": ["biomedicine", "life_science", "medicine"], "default_enabled": True, "supports_time_filter": True, "time_filter_mode": "api_time_filter_with_code_fallback", "notes": "Life-science metadata with PMCID, preprints, and open-access indicators."},
    {"id": "doaj", "tool": "doaj_search", "label": "Directory of Open Access Journals", "kind": "open_access_index", "authority_level": "high", "disciplines": ["multidisciplinary"], "default_enabled": True, "supports_time_filter": True, "time_filter_mode": "code_time_filter_only", "notes": "Verified open-access journal and article metadata across disciplines."},
    {"id": "clinicaltrials", "tool": "clinicaltrials_search", "label": "ClinicalTrials.gov", "kind": "trial_registry", "authority_level": "highest", "disciplines": ["medicine", "public_health"], "default_enabled": True, "supports_time_filter": True, "time_filter_mode": "relevance_then_in_window_then_backfill", "notes": "Official study registry; route only for clinical and health-development topics."},
]

SEARCH_SOURCES: list[dict[str, Any]] = [
    {"id": "openalex", "tool": "openalex_search", "label": "OpenAlex", "kind": "scholarly_graph", "authority_level": "high", "disciplines": ["multidisciplinary"], "default_enabled": True, "supports_time_filter": True, "time_filter_mode": "year_filter_plus_code_fallback", "notes": "Broad scholarly graph with relevance and citation metadata."},
    {"id": "crossref", "tool": "crossref_search", "label": "Crossref", "kind": "doi_registry", "authority_level": "high", "disciplines": ["multidisciplinary"], "default_enabled": True, "supports_time_filter": True, "time_filter_mode": "api_time_filter_with_code_fallback", "notes": "Publisher-deposited DOI metadata across disciplines; useful for DOI normalization and version checks."},
    {"id": "semantic-scholar", "tool": "semantic_scholar_search", "label": "Semantic Scholar", "kind": "scholarly_graph", "authority_level": "high", "disciplines": ["multidisciplinary"], "default_enabled": False, "supports_time_filter": True, "time_filter_mode": "relevance_then_in_window_then_backfill", "optional_env": "SEMANTIC_SCHOLAR_API_KEY", "notes": "Fallback source when OpenAlex/Crossref coverage is insufficient; a key improves reliability under rate limits."},
    {"id": "arxiv", "tool": "arxiv_search", "label": "arXiv", "kind": "preprint", "authority_level": "medium", "disciplines": ["computer_science", "mathematics", "physics", "statistics", "engineering", "economics", "quantitative_biology"], "default_enabled": True, "supports_time_filter": True, "time_filter_mode": "relevance_then_in_window_then_backfill", "peer_reviewed": False},
    {"id": "biorxiv", "tool": "biorxiv_search", "label": "bioRxiv", "kind": "preprint", "authority_level": "high", "disciplines": ["biology", "life_science"], "default_enabled": True, "supports_time_filter": True, "time_filter_mode": "api_window_scan_with_code_fallback", "peer_reviewed": False},
    {"id": "medrxiv", "tool": "medrxiv_search", "label": "medRxiv", "kind": "preprint", "authority_level": "high", "disciplines": ["medicine", "public_health"], "default_enabled": True, "supports_time_filter": True, "time_filter_mode": "api_window_scan_with_code_fallback", "peer_reviewed": False},
]

DISCIPLINE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "medicine": ("clinical", "patient", "therapy", "trial", "disease", "drug", "hospital", "diagnosis", "treatment", "oncology", "cancer", "??", "??", "??", "??", "??", "??"),
    "public_health": ("epidem", "public health", "surveillance", "population", "cohort", "obesity", "nutrition", "??", "???", "????"),
    "life_science": ("protein", "gene", "genome", "cell", "immune", "biology", "biotech", "omics", "??", "??", "??", "??", "??"),
    "computer_science": ("machine learning", "deep learning", "ai", "artificial intelligence", "llm", "vision", "robot", "software", "algorithm", "???", "????", "??", "??"),
    "mathematics": ("algebra", "geometry", "theorem", "proof", "mathemat", "number theory", "??", "??"),
    "statistics": ("bayesian", "causal", "inference", "estimation", "statistics", "statistical", "??", "??"),
    "physics": ("quantum", "particle", "photon", "superconduct", "physics", "relativity", "??", "??"),
    "engineering": ("battery", "chip", "hardware", "semiconductor", "device", "manufactur", "engineering", "??", "??", "???"),
    "chemistry": ("catalyst", "chemistry", "chemical", "molecule", "polymer", "synthesis", "??", "??", "??"),
    "materials_science": ("material", "perovskite", "alloy", "nanoparticle", "membrane", "??", "??", "??"),
    "climate_science": ("climate", "carbon", "emission", "warming", "energy transition", "??", "?", "??"),
    "earth_science": ("geology", "earthquake", "ocean", "geoscience", "hydrology", "??", "??", "??"),
    "astronomy": ("galaxy", "telescope", "astronomy", "space", "cosmic", "exoplanet", "??", "??"),
    "economics": ("economics", "market", "finance", "policy", "macroecon", "microecon", "??", "??", "??"),
    "social_science": ("education", "sociology", "psychology", "behavior", "governance", "??", "??", "??"),
    "humanities": ("history", "philosophy", "linguistics", "ethics", "humanities", "??", "??", "??"),
    "open_science": ("open science", "repository", "research data", "dataset", "software", "benchmark", "eosc", "horizon europe", "????", "???", "??", "??"),
}

CLINICAL_HINTS = ("clinical", "trial", "phase ", "nct", "recruit", "endpoint", "patient", "safety", "efficacy", "??", "??", "??")
OPEN_SCIENCE_HINTS = ("dataset", "benchmark", "repository", "software", "code", "research data", "open science", "horizon", "eosc", "???", "????", "????")
EU_PROJECT_HINTS = ("europe", "eu", "erc", "horizon", "openaire", "eosc")


def _resolved(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in rows:
        copied = dict(item)
        env_name = copied.get("optional_env")
        copied["credential_configured"] = bool(os.getenv(str(env_name))) if env_name else True
        result.append(copied)
    return result


def _topic_text(topic: str | None = None, question: str | None = None, keywords: list[str] | None = None, synonyms: list[str] | None = None) -> str:
    parts = [topic or "", question or "", *(keywords or []), *(synonyms or [])]
    return " ".join(part for part in parts if part).lower()


def _contains_hint(text: str, hint: str) -> bool:
    value = hint.lower().strip()
    if not value:
        return False
    if re.search(r"[一-鿿]", value):
        return value in text
    return re.search(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])", text) is not None


def infer_topic_disciplines(topic: str | None = None, question: str | None = None, keywords: list[str] | None = None, synonyms: list[str] | None = None) -> list[str]:
    text = _topic_text(topic, question, keywords, synonyms)
    matched: list[str] = []
    for discipline, hints in DISCIPLINE_KEYWORDS.items():
        if any(_contains_hint(text, hint) for hint in hints):
            matched.append(discipline)
    if not matched:
        matched = ["multidisciplinary"]
    elif "medicine" in matched and "life_science" not in matched:
        matched.append("life_science")
    return list(dict.fromkeys(matched))


def recommended_paper_source_ids(topic: str | None = None, question: str | None = None, keywords: list[str] | None = None, synonyms: list[str] | None = None) -> list[str]:
    disciplines = set(infer_topic_disciplines(topic, question, keywords, synonyms))
    text = _topic_text(topic, question, keywords, synonyms)
    selected = ["doaj"]
    if disciplines & {"medicine", "public_health", "life_science", "biology", "biomedicine"}:
        selected.extend(["pubmed", "europepmc"])
    if any(hint in text for hint in CLINICAL_HINTS) or disciplines & {"medicine", "public_health"}:
        selected.append("clinicaltrials")
    return list(dict.fromkeys(selected))


def recommended_search_source_ids(topic: str | None = None, question: str | None = None, keywords: list[str] | None = None, synonyms: list[str] | None = None) -> list[str]:
    disciplines = set(infer_topic_disciplines(topic, question, keywords, synonyms))
    text = _topic_text(topic, question, keywords, synonyms)
    selected = ["openalex", "crossref"]
    if disciplines & {"computer_science", "mathematics", "physics", "statistics", "engineering", "economics", "quantitative_biology"}:
        selected.append("arxiv")
    if disciplines & {"life_science", "medicine", "public_health"}:
        selected.append("biorxiv")
    if disciplines & {"medicine", "public_health"}:
        selected.append("medrxiv")
    if ("semantic scholar" in text or "citation network" in text or "similar paper" in text) and os.getenv("SEMANTIC_SCHOLAR_API_KEY"):
        selected.append("semantic-scholar")
    return list(dict.fromkeys(selected))


def list_paper_sources() -> list[dict[str, Any]]:
    return _resolved(PAPER_SOURCES)


def list_search_sources() -> list[dict[str, Any]]:
    return _resolved(SEARCH_SOURCES)
