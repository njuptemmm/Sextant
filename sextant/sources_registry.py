"""Which scholarly adapters a run may use, and why.

The heavy lifting lives in ``sextant.engine.sources``; this module only decides
which adapters are relevant to a topic so a run does not fan out to sources
that cannot possibly match (e.g. ClinicalTrials for a compiler topic).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sextant.engine import sources

BIOMEDICAL_TERMS = (
    "biomedical", "clinical", "medical", "medicine", "patient", "disease", "therapy",
    "drug", "drugs", "protein", "gene", "genes", "genome", "cell", "cells", "cancer",
    "vaccine", "health", "biology",
    "医学", "临床", "患者", "疾病", "药物", "蛋白", "基因", "细胞", "癌", "疫苗", "健康", "生物",
)


@dataclass(frozen=True)
class SourceSpec:
    id: str
    label: str
    adapter: Callable[..., list[dict[str, Any]]]
    discipline: str
    authority: str
    query_style: str = "plain"  # plain | arxiv
    requires_key: bool = False

    def available(self) -> bool:
        if not self.requires_key:
            return True
        return bool(os.getenv("SEMANTIC_SCHOLAR_API_KEY") or os.getenv("S2_API_KEY"))


SPECS: dict[str, SourceSpec] = {
    "openalex": SourceSpec(
        "openalex", "OpenAlex", sources.openalex_search, "multidisciplinary", "high"
    ),
    "arxiv": SourceSpec(
        "arxiv", "arXiv", sources.arxiv_search, "computer_science/physics/math", "medium", "arxiv"
    ),
    "crossref": SourceSpec(
        "crossref", "Crossref", sources.crossref_search, "multidisciplinary", "high"
    ),
    "doaj": SourceSpec(
        "doaj", "DOAJ", sources.doaj_search, "open access", "high"
    ),
    "semantic_scholar": SourceSpec(
        "semantic_scholar", "Semantic Scholar", sources.semantic_scholar_search,
        "multidisciplinary", "high", requires_key=True,
    ),
    "pubmed": SourceSpec("pubmed", "PubMed", sources.pubmed_search, "biomedicine", "highest"),
    "europepmc": SourceSpec(
        "europepmc", "Europe PMC", sources.europepmc_search, "biomedicine", "highest"
    ),
}

CORE_SOURCES = ("openalex", "arxiv")
SECONDARY_SOURCES = ("crossref", "doaj")
MAX_SOURCES_PER_RUN = 5


def _term_present(term: str, text: str) -> bool:
    """Whole-word match for Latin terms, substring match for CJK terms."""
    if re.search(r"[一-鿿]", term):
        return term in text
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def _is_biomedical(topic: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(topic.get("topic_name") or ""),
            " ".join(str(item) for item in topic.get("keywords") or []),
            str(topic.get("primary_question") or ""),
        ]
    ).lower()
    return any(_term_present(term, text) for term in BIOMEDICAL_TERMS)


def active_sources(topic: dict[str, Any]) -> list[SourceSpec]:
    ids: list[str] = list(CORE_SOURCES)
    ids.extend(SECONDARY_SOURCES)
    if SPECS["semantic_scholar"].available():
        ids.append("semantic_scholar")
    if _is_biomedical(topic):
        ids.extend(["pubmed", "europepmc"])
    selected: list[SourceSpec] = []
    for source_id in ids:
        spec = SPECS[source_id]
        if not spec.available() or spec in selected:
            continue
        selected.append(spec)
        if len(selected) >= MAX_SOURCES_PER_RUN:
            break
    return selected


def build_query(spec: SourceSpec, keywords: list[str], topic_name: str) -> str:
    terms = keywords or [topic_name]
    if spec.query_style == "arxiv":
        return " AND ".join(f'all:"{term}"' for term in terms)
    return " AND ".join(terms)


def describe_specs(specs: list[SourceSpec]) -> list[dict[str, Any]]:
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "discipline": spec.discipline,
            "authority": spec.authority,
        }
        for spec in specs
    ]
