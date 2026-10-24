from __future__ import annotations

import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from sextant.config import USER_AGENT
from sextant.engine.common import (
    canonical_record_id,
    extract_query_terms,
    keyword_hits,
    normalize_text,
    parse_date,
    sha1_text,
    utc_now_iso,
)
from sextant.engine.content_quality import assess_abstract_quality, clean_content_text
from sextant.engine.http_client import RateLimit, request_json, request_text
OPENALEX_RATE_LIMIT = RateLimit(min_interval_seconds=0.25)
ARXIV_RATE_LIMIT = RateLimit(min_interval_seconds=3.0)
SEMANTIC_SCHOLAR_RATE_LIMIT = RateLimit(min_interval_seconds=1.0)
BIORXIV_RATE_LIMIT = RateLimit(min_interval_seconds=0.5)
CROSSREF_RATE_LIMIT = RateLimit(min_interval_seconds=0.25)
DOAJ_RATE_LIMIT = RateLimit(min_interval_seconds=0.25)


class SourceResult(list):
    def __init__(self, rows: list[dict[str, Any]] | None = None, *, diagnostics: dict[str, Any] | None = None):
        super().__init__(rows or [])
        self.diagnostics = diagnostics or {}


def _request_text(url: str, *, rate_limit: RateLimit | None = None, looks_valid: Any | None = None, headers: dict[str, str] | None = None) -> str:
    merged_headers = {"User-Agent": USER_AGENT}
    if headers:
        merged_headers.update(headers)
    return request_text(url, rate_limit=rate_limit, looks_valid=looks_valid, headers=merged_headers)


def _request_json(url: str, *, rate_limit: RateLimit | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    merged_headers = {"User-Agent": USER_AGENT}
    if headers:
        merged_headers.update(headers)
    return request_json(url, rate_limit=rate_limit, headers=merged_headers)


def _semantic_scholar_headers() -> dict[str, str] | None:
    api_key = normalize_text(os.getenv("SEMANTIC_SCHOLAR_API_KEY") or os.getenv("S2_API_KEY"))
    return {"x-api-key": api_key} if api_key else None


def _itertext(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return normalize_text("".join(element.itertext()))


def _date_window(from_date: str | None, to_date: str | None, default_days: int = 90) -> tuple[str, str]:
    today = datetime.now(timezone.utc).date()
    end_date = parse_date(to_date) or today.isoformat()
    start_date = parse_date(from_date) or (today - timedelta(days=default_days)).isoformat()
    return start_date, end_date


def _record_date_value(row: dict[str, Any]) -> datetime.date | None:
    parsed = parse_date(row.get("published_at") or row.get("updated_at"))
    if not parsed:
        return None
    return datetime.strptime(parsed, "%Y-%m-%d").date()


def _record_in_window(row: dict[str, Any], from_date: str | None, to_date: str | None) -> bool:
    start = parse_date(from_date)
    end = parse_date(to_date)
    if not start and not end:
        return True
    published = _record_date_value(row)
    if not published:
        return False
    if start and published < datetime.strptime(start, "%Y-%m-%d").date():
        return False
    if end and published > datetime.strptime(end, "%Y-%m-%d").date():
        return False
    return True


def _filter_records_by_window(records: list[dict[str, Any]], from_date: str | None, to_date: str | None) -> list[dict[str, Any]]:
    if not from_date and not to_date:
        return records
    return [row for row in records if _record_in_window(row, from_date, to_date)]


def _overfetch_limit(limit: int, multiplier: int = 2, hard_cap: int = 100) -> int:
    requested = max(int(limit or 0), 1)
    return min(hard_cap, max(requested, requested * multiplier))


def _select_window_priority(records: list[dict[str, Any]], limit: int, from_date: str | None, to_date: str | None, *, strict_time_window: bool = False) -> list[dict[str, Any]]:
    target = max(int(limit or 0), 1)
    if not records:
        return []
    if not from_date and not to_date:
        return records[:target]
    in_window = [row for row in records if _record_in_window(row, from_date, to_date)]
    if len(in_window) >= target:
        return in_window[:target]
    if strict_time_window:
        return in_window[:target]
    selected = list(in_window)
    seen = {str(id(row)) for row in selected}
    for row in records:
        marker = str(id(row))
        if marker in seen:
            continue
        selected.append(row)
        seen.add(marker)
        if len(selected) >= target:
            break
    return selected[:target]


def _window_priority_result(records: list[dict[str, Any]], limit: int, from_date: str | None, to_date: str | None, *, strategy: str, source_fetch_limit: int | None = None, strict_time_window: bool = False) -> SourceResult:
    target = max(int(limit or 0), 1)
    in_window = [row for row in records if _record_in_window(row, from_date, to_date)]
    selected = _select_window_priority(records, target, from_date, to_date, strict_time_window=strict_time_window)
    backfill_count = max(0, len(selected) - min(len(in_window), target))
    return SourceResult(selected, diagnostics={
        "requested_limit": target,
        "source_fetch_limit": source_fetch_limit if source_fetch_limit is not None else target,
        "raw_fetched_count": len(records),
        "in_window_count": len(in_window),
        "backfill_count": backfill_count,
        "returned_count": len(selected),
        "window_strategy": strategy,
        "strict_time_window": strict_time_window,
        "from_date": parse_date(from_date),
        "to_date": parse_date(to_date),
    })


def _filtered_result(records: list[dict[str, Any]], limit: int, from_date: str | None, to_date: str | None, *, strategy: str, source_fetch_limit: int | None = None, strict_time_window: bool = False) -> SourceResult:
    filtered = _filter_records_by_window(records, from_date, to_date)
    target = max(int(limit or 0), 1)
    selected = filtered[:target]
    return SourceResult(selected, diagnostics={
        "requested_limit": target,
        "source_fetch_limit": source_fetch_limit if source_fetch_limit is not None else target,
        "raw_fetched_count": len(records),
        "in_window_count": len(filtered),
        "backfill_count": 0,
        "returned_count": len(selected),
        "window_strategy": strategy,
        "strict_time_window": strict_time_window,
        "from_date": parse_date(from_date),
        "to_date": parse_date(to_date),
    })


def _source_url(base: str, params: dict[str, Any]) -> str:
    return f"{base}?{urllib.parse.urlencode(params, doseq=True)}"


def _looks_like_doi(value: str) -> bool:
    return bool(normalize_text(value)) and normalize_text(value).lower().startswith("10.")


def _unique_urls(*values: Any) -> list[str]:
    urls: list[str] = []
    for value in values:
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            url = normalize_text(candidate)
            if url.startswith(("http://", "https://")) and url not in urls:
                urls.append(url)
    return urls


def _make_record(*, source: str, channel: str, query: str, source_id: str | None, title: str | None, abstract: str | None = None, summary: str | None = None, authors: list[str] | None = None, published_at: str | None = None, updated_at: str | None = None, doi: str | None = None, url: str | None = None, journal: str | None = None, citations: int | None = None, evidence_type: str | None = None, abstract_source: str = "source_metadata", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    clean_abstract = clean_content_text(abstract)
    quality = assess_abstract_quality(clean_abstract, title=title, source=source, evidence_type=evidence_type)
    record_extra = dict(extra or {})
    record_extra["abstract_quality"] = {key: value for key, value in quality.items() if key != "clean_text"}
    payload = {
        "record_id": canonical_record_id(source, source_id, title),
        "source": source,
        "channel": channel,
        "source_id": source_id or "",
        "query": query,
        "title": normalize_text(title),
        "abstract": clean_abstract,
        "abstract_source": abstract_source if clean_abstract else "unavailable",
        "abstract_usable": quality["usable"],
        "summary": normalize_text(summary),
        "authors": authors or [],
        "published_at": parse_date(published_at),
        "updated_at": parse_date(updated_at),
        "doi": normalize_text(doi),
        "url": normalize_text(url),
        "journal": normalize_text(journal),
        "citations": citations or 0,
        "evidence_type": evidence_type or "literature",
        "extra": record_extra,
    }
    payload["query_terms"] = extract_query_terms(query)
    return payload

def pubmed_search(query: str, limit: int = 20, from_date: str | None = None, to_date: str | None = None, strict_time_window: bool = False) -> list[dict[str, Any]]:
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    params = {"db": "pubmed", "retmode": "json", "retmax": min(limit, 50), "sort": "relevance", "term": query}
    start_date, end_date = _date_window(from_date, to_date)
    params.update({"datetype": "pdat", "mindate": start_date, "maxdate": end_date})
    search = _request_json(_source_url(f"{base}/esearch.fcgi", params))
    ids = search.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    summary = _request_json(_source_url(f"{base}/esummary.fcgi", {"db": "pubmed", "retmode": "json", "id": ",".join(ids)}))
    xml_text = _request_text(_source_url(f"{base}/efetch.fcgi", {"db": "pubmed", "retmode": "xml", "rettype": "abstract", "id": ",".join(ids)}))
    root = ET.fromstring(xml_text)
    article_map: dict[str, dict[str, str]] = {}
    for article in root.findall(".//PubmedArticle"):
        pmid = _itertext(article.find(".//MedlineCitation/PMID"))
        title = _itertext(article.find(".//ArticleTitle"))
        abstract_parts = [_itertext(node) for node in article.findall(".//Abstract/AbstractText")]
        doi, pmcid = "", ""
        for article_id in article.findall(".//PubmedData/ArticleIdList/ArticleId"):
            id_type = article_id.attrib.get("IdType")
            if id_type == "doi":
                doi = _itertext(article_id)
            elif id_type == "pmc":
                pmcid = _itertext(article_id)
        article_map[pmid] = {"title": title, "abstract": " ".join(part for part in abstract_parts if part), "doi": doi, "pmcid": pmcid}
    results: list[dict[str, Any]] = []
    summary_rows = summary.get("result", {})
    for pmid in ids:
        row = summary_rows.get(pmid, {})
        if not isinstance(row, dict):
            continue
        authors = [item.get("name", "") for item in row.get("authors", []) if item.get("name")]
        details = article_map.get(pmid, {})
        doi, pmcid = details.get("doi") or normalize_text(row.get("elocationid")).removeprefix("doi: "), details.get("pmcid") or ""
        landing = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        candidates = _unique_urls(
            f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML" if pmcid else "",
            f"https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_xml/{pmcid or pmid}/unicode",
            f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/" if pmcid else "",
            f"https://doi.org/{doi}" if doi else "",
            landing,
        )
        results.append(_make_record(
            source="pubmed", channel="paper_sites", query=query, source_id=pmid,
            title=details.get("title") or row.get("title"), abstract=details.get("abstract"),
            summary=f"{row.get('fulljournalname') or row.get('source', '')} {row.get('pubdate', '')}",
            authors=authors, published_at=row.get("pubdate"), doi=doi, url=landing,
            journal=row.get("fulljournalname") or row.get("source"), evidence_type="journal_article",
            extra={"volume": row.get("volume"), "issue": row.get("issue"), "pages": row.get("pages"), "pmid": pmid, "pmcid": pmcid, "candidate_urls": candidates},
        ))
    return _filtered_result(results, limit, from_date, to_date, strategy="api_time_filter_with_code_fallback", source_fetch_limit=min(limit, 50), strict_time_window=strict_time_window)

def europepmc_search(query: str, limit: int = 20, from_date: str | None = None, to_date: str | None = None, strict_time_window: bool = False) -> list[dict[str, Any]]:
    base = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    start_date, end_date = _date_window(from_date, to_date)
    composite_query = f"({query}) AND FIRST_PDATE:[{start_date} TO {end_date}]"
    payload = _request_json(_source_url(base, {"query": composite_query, "format": "json", "resultType": "core", "pageSize": min(limit, 50)}))
    results: list[dict[str, Any]] = []
    for row in payload.get("resultList", {}).get("result", []):
        source_id = f"{row.get('source', '')}/{row.get('id', '')}".strip("/")
        landing = f"https://europepmc.org/article/{row['source']}/{row['id']}" if row.get("source") and row.get("id") else ""
        pmid, pmcid, doi = row.get("pmid") or "", row.get("pmcid") or "", row.get("doi") or ""
        candidates = _unique_urls(
            f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML" if pmcid else "",
            f"https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_xml/{pmcid or pmid}/unicode" if pmcid or pmid else "",
            f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/" if pmcid else "",
            f"https://doi.org/{doi}" if doi else "",
            landing,
        )
        results.append(_make_record(
            source="europepmc", channel="paper_sites", query=query, source_id=source_id,
            title=row.get("title"), abstract=row.get("abstractText"), summary=row.get("authorString"),
            authors=[name.strip() for name in normalize_text(row.get("authorString")).split(",") if name.strip()],
            published_at=str(row.get("firstPublicationDate") or row.get("pubYear", "")), doi=doi,
            url=landing or (f"https://doi.org/{doi}" if doi else ""), journal=row.get("journalTitle"),
            citations=int(row.get("citedByCount") or 0), evidence_type="journal_article",
            extra={"pmid": pmid, "pmcid": pmcid, "candidate_urls": candidates, "is_open_access": row.get("isOpenAccess"), "has_pdf": row.get("hasPDF")},
        ))
    return _filtered_result(results, limit, from_date, to_date, strategy="api_time_filter_with_code_fallback", source_fetch_limit=min(limit, 50), strict_time_window=strict_time_window)

def _openalex_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    max_position = max((max(positions) for positions in index.values() if positions), default=-1)
    if max_position < 0:
        return ""
    words = [""] * (max_position + 1)
    for word, positions in index.items():
        for position in positions:
            if 0 <= position < len(words):
                words[position] = word
    return normalize_text(" ".join(word for word in words if word))


def openalex_search(query: str, limit: int = 20, from_date: str | None = None, to_date: str | None = None, strict_time_window: bool = False) -> list[dict[str, Any]]:
    base = "https://api.openalex.org/works"
    params: dict[str, Any] = {"search": query, "per-page": min(limit, 50), "mailto": "local-user@example.invalid"}
    start_date, end_date = _date_window(from_date, to_date)
    if start_date and end_date:
        params["filter"] = f"publication_year:{start_date[:4]}-{end_date[:4]}"
    payload = _request_json(_source_url(base, params), rate_limit=OPENALEX_RATE_LIMIT)
    results: list[dict[str, Any]] = []
    for row in payload.get("results", []):
        authors = []
        for authorship in row.get("authorships", []):
            author_name = ((authorship or {}).get("author") or {}).get("display_name")
            if author_name:
                authors.append(author_name)
        primary_location = row.get("primary_location") or {}
        best_oa_location = row.get("best_oa_location") or {}
        locations = [primary_location, best_oa_location, *(row.get("locations") or [])]
        candidate_urls = _unique_urls(
            [location.get("pdf_url") for location in locations if isinstance(location, dict)],
            [location.get("landing_page_url") for location in locations if isinstance(location, dict)],
            f"https://doi.org/{str(row.get('doi') or '').replace('https://doi.org/', '')}" if row.get("doi") else "",
            row.get("id"),
        )
        source_name = ((primary_location.get("source") or {}).get("display_name")) or ""
        primary_url = normalize_text(best_oa_location.get("landing_page_url") or best_oa_location.get("pdf_url") or primary_location.get("landing_page_url") or primary_location.get("pdf_url") or row.get("id"))
        ids = row.get("ids") or {}
        results.append(_make_record(
            source="openalex", channel="search", query=query,
            source_id=str(row.get("id", "")).rsplit("/", 1)[-1],
            title=row.get("display_name") or row.get("title"),
            abstract=_openalex_abstract(row.get("abstract_inverted_index")),
            summary=source_name, authors=authors,
            published_at=str(row.get("publication_date") or row.get("publication_year", "")),
            doi=(row.get("doi") or "").replace("https://doi.org/", ""),
            url=primary_url, journal=source_name,
            citations=int(row.get("cited_by_count") or 0), evidence_type="scholarly_work",
            extra={
                "relevance_score": row.get("relevance_score"),
                "pmid": str(ids.get("pmid") or "").rsplit("/", 1)[-1],
                "pmcid": str(ids.get("pmcid") or "").rsplit("/", 1)[-1],
                "openalex_id": str(row.get("id") or "").rsplit("/", 1)[-1],
                "pdf_url": best_oa_location.get("pdf_url") or primary_location.get("pdf_url") or "",
                "candidate_urls": candidate_urls,
                "oa_locations": [location for location in locations if isinstance(location, dict) and (location.get("landing_page_url") or location.get("pdf_url"))],
            },
        ))
    return _filtered_result(results, limit, from_date, to_date, strategy="year_filter_plus_code_fallback", source_fetch_limit=min(limit, 50), strict_time_window=strict_time_window)

def _trial_synopsis(protocol: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    description = protocol.get("descriptionModule") or {}
    design = protocol.get("designModule") or {}
    arms = protocol.get("armsInterventionsModule") or {}
    outcomes = protocol.get("outcomesModule") or {}
    eligibility = protocol.get("eligibilityModule") or {}
    conditions = protocol.get("conditionsModule") or {}
    phases = design.get("phases") or []
    enrollment = (design.get("enrollmentInfo") or {}).get("count")
    design_info = design.get("designInfo") or {}
    interventions = [clean_content_text(item.get("name")) for item in (arms.get("interventions") or []) if clean_content_text(item.get("name"))]
    primary_outcomes = [clean_content_text(item.get("measure")) for item in (outcomes.get("primaryOutcomes") or []) if clean_content_text(item.get("measure"))]
    secondary_outcomes = [clean_content_text(item.get("measure")) for item in (outcomes.get("secondaryOutcomes") or []) if clean_content_text(item.get("measure"))]
    population = clean_content_text(eligibility.get("studyPopulation") or eligibility.get("eligibilityCriteria"))
    parts = [clean_content_text(description.get("briefSummary")), clean_content_text(description.get("detailedDescription"))]
    design_terms = [clean_content_text(design.get("studyType")), *[clean_content_text(value) for value in phases], clean_content_text(design_info.get("allocation")), clean_content_text(design_info.get("interventionModel")), clean_content_text(design_info.get("maskingInfo", {}).get("masking")), clean_content_text(design_info.get("primaryPurpose"))]
    design_sentence = "; ".join(value for value in design_terms if value)
    if design_sentence or enrollment:
        parts.append(f"Study design: {design_sentence}. Planned enrollment: {enrollment or 'not reported'}." )
    if interventions:
        parts.append("Interventions: " + "; ".join(interventions[:12]) + ".")
    if primary_outcomes:
        parts.append("Primary outcomes: " + "; ".join(primary_outcomes[:10]) + ".")
    if secondary_outcomes:
        parts.append("Secondary outcomes: " + "; ".join(secondary_outcomes[:8]) + ".")
    if population:
        parts.append("Population and eligibility: " + " ".join(population.split()[:180]) + ".")
    synopsis = clean_content_text(" ".join(part for part in parts if part))
    structured = {
        "conditions": conditions.get("conditions") or [],
        "keywords": conditions.get("keywords") or [],
        "study_type": design.get("studyType"),
        "phases": phases,
        "enrollment": enrollment,
        "design": design_info,
        "interventions": interventions,
        "primary_outcomes": primary_outcomes,
        "secondary_outcomes": secondary_outcomes,
        "population_and_eligibility": population,
    }
    return synopsis, structured


def clinicaltrials_search(query: str, limit: int = 20, status: str | None = None, from_date: str | None = None, to_date: str | None = None, strict_time_window: bool = False) -> list[dict[str, Any]]:
    base = "https://clinicaltrials.gov/api/v2/studies"
    params: dict[str, Any] = {"format": "json", "pageSize": _overfetch_limit(limit), "query.cond": query}
    if status:
        params["filter.overallStatus"] = status
    payload = _request_json(_source_url(base, params))
    results: list[dict[str, Any]] = []
    for row in payload.get("studies", []):
        protocol = row.get("protocolSection", {})
        identification = protocol.get("identificationModule", {})
        status_module = protocol.get("statusModule", {})
        design_module = protocol.get("designModule", {})
        references = protocol.get("referencesModule", {})
        source_id = identification.get("nctId")
        phases = design_module.get("phases", [])
        title = identification.get("officialTitle") or identification.get("briefTitle")
        synopsis, structured = _trial_synopsis(protocol)
        landing = f"https://clinicaltrials.gov/study/{source_id}" if source_id else ""
        reference_urls = [item.get("url") for item in (references.get("seeAlsoLinks") or []) if isinstance(item, dict)]
        results.append(_make_record(
            source="clinicaltrials", channel="paper_sites", query=query, source_id=source_id,
            title=title, abstract=synopsis, abstract_source="registry_synopsis",
            summary=status_module.get("overallStatus"), authors=[],
            published_at=status_module.get("studyFirstPostDateStruct", {}).get("date"),
            updated_at=status_module.get("lastUpdatePostDateStruct", {}).get("date"),
            url=landing, journal="ClinicalTrials.gov", evidence_type="clinical_trial",
            extra={
                "status": status_module.get("overallStatus"), "phase": phases,
                "enrollment": (design_module.get("enrollmentInfo") or {}).get("count"),
                "registry_sections": structured,
                "candidate_urls": _unique_urls(reference_urls, landing),
                "nct_id": source_id,
            },
        ))
    source_fetch_limit = _overfetch_limit(limit)
    return _window_priority_result(results, limit, from_date, to_date, strategy="relevance_then_in_window_then_backfill", source_fetch_limit=source_fetch_limit, strict_time_window=strict_time_window)

def semantic_scholar_search(query: str, limit: int = 20, from_date: str | None = None, to_date: str | None = None, strict_time_window: bool = False) -> list[dict[str, Any]]:
    base = "https://api.semanticscholar.org/graph/v1/paper/search"
    fields = "title,abstract,url,year,venue,citationCount,authors.name,externalIds,openAccessPdf"
    payload = _request_json(_source_url(base, {"query": query, "limit": min(_overfetch_limit(limit, hard_cap=50), 50), "fields": fields}), rate_limit=SEMANTIC_SCHOLAR_RATE_LIMIT, headers=_semantic_scholar_headers())
    results: list[dict[str, Any]] = []
    for row in payload.get("data", []):
        authors = [item.get("name", "") for item in row.get("authors", []) if item.get("name")]
        author_text = ", ".join(authors[:4]) + (" et al." if len(authors) > 4 else "") if authors else ""
        summary = ". ".join(part for part in (author_text, row.get("venue", ""), str(row.get("year", ""))) if normalize_text(part))
        ids = row.get("externalIds") or {}
        doi = str(ids.get("DOI") or "")
        landing = row.get("url") or (f"https://www.semanticscholar.org/paper/{row.get('paperId')}" if row.get("paperId") else "")
        pdf_url = (row.get("openAccessPdf") or {}).get("url") or ""
        candidates = _unique_urls(pdf_url, f"https://doi.org/{doi}" if doi else "", f"https://arxiv.org/pdf/{ids.get('ArXiv')}.pdf" if ids.get("ArXiv") else "", landing)
        results.append(_make_record(
            source="semantic-scholar", channel="search", query=query, source_id=row.get("paperId"),
            title=row.get("title"), abstract=row.get("abstract"), summary=summary, authors=authors,
            published_at=str(row.get("year", "")), doi=doi, url=pdf_url or landing,
            journal=row.get("venue"), citations=int(row.get("citationCount") or 0), evidence_type="scholarly_work",
            extra={"pmid": ids.get("PubMed") or "", "arxiv_id": ids.get("ArXiv") or "", "pdf_url": pdf_url, "candidate_urls": candidates},
        ))
    source_fetch_limit = min(_overfetch_limit(limit, hard_cap=50), 50)
    return _window_priority_result(results, limit, from_date, to_date, strategy="relevance_then_in_window_then_backfill", source_fetch_limit=source_fetch_limit, strict_time_window=strict_time_window)

def _is_arxiv_fielded_query(query: str) -> bool:
    return ":" in query and any(tag in query for tag in ("ti:", "au:", "abs:", "cat:", "all:"))


def _is_atom_feed(xml_text: str) -> bool:
    return "<feed" in xml_text.lower()


def _is_arxiv_error_entry(entry_id: str, title: str) -> bool:
    return entry_id.startswith("http://arxiv.org/api/errors") or title == "Error"


def arxiv_search(query: str, limit: int = 20, from_date: str | None = None, to_date: str | None = None, strict_time_window: bool = False) -> list[dict[str, Any]]:
    base = "https://export.arxiv.org/api/query"
    search_query = query if _is_arxiv_fielded_query(query) else f"all:{query}"
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    results: list[dict[str, Any]] = []
    remaining = min(_overfetch_limit(limit, hard_cap=200), 200)
    start = 0
    while remaining > 0:
        batch_size = min(remaining, 50)
        url = _source_url(base, {"search_query": search_query, "start": start, "max_results": batch_size, "sortBy": "relevance"})
        xml_text = _request_text(url, rate_limit=ARXIV_RATE_LIMIT, looks_valid=_is_atom_feed)
        root = ET.fromstring(xml_text)
        entries = root.findall("atom:entry", ns)
        if not entries:
            break
        for entry in entries:
            entry_id = _itertext(entry.find("atom:id", ns))
            title = _itertext(entry.find("atom:title", ns))
            if _is_arxiv_error_entry(entry_id, title):
                continue
            authors = [_itertext(node.find("atom:name", ns)) for node in entry.findall("atom:author", ns)]
            results.append(_make_record(source="arxiv", channel="search", query=query, source_id=entry_id.rsplit("/", 1)[-1], title=title, abstract=_itertext(entry.find("atom:summary", ns)), summary="arXiv preprint", authors=[name for name in authors if name], published_at=_itertext(entry.find("atom:published", ns)), updated_at=_itertext(entry.find("atom:updated", ns)), url=entry_id, journal="arXiv", evidence_type="preprint"))
        if len(entries) < batch_size:
            break
        start += batch_size
        remaining -= batch_size
    source_fetch_limit = _overfetch_limit(limit, hard_cap=200)
    return _window_priority_result(results, limit, from_date, to_date, strategy="relevance_then_in_window_then_backfill", source_fetch_limit=source_fetch_limit, strict_time_window=strict_time_window)

def _strip_jats(value: str | None) -> str:
    return clean_content_text(value)


def _crossref_relation_urls(relation: Any) -> list[str]:
    urls: list[str] = []
    if not isinstance(relation, dict):
        return urls
    for entries in relation.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            identifier = normalize_text(entry.get("id"))
            if identifier.startswith("10."):
                urls.append(f"https://doi.org/{identifier}")
            elif identifier.startswith(("http://", "https://")):
                urls.append(identifier)
    return urls


def crossref_search(query: str, limit: int = 20, from_date: str | None = None, to_date: str | None = None, strict_time_window: bool = False) -> list[dict[str, Any]]:
    fields = "DOI,title,subtitle,abstract,author,container-title,publisher,type,URL,score,is-referenced-by-count,issued,link,resource,relation"
    params: dict[str, Any] = {"query": query, "rows": min(limit, 50), "mailto": "local-user@example.invalid", "select": fields}
    if from_date:
        params["filter"] = f"from-pub-date:{from_date}"
        if to_date:
            params["filter"] += f",until-pub-date:{to_date}"
    payload = _request_json("https://api.crossref.org/works?" + urllib.parse.urlencode(params, doseq=True), rate_limit=CROSSREF_RATE_LIMIT)
    rows = ((payload.get("message") or {}).get("items")) or []
    results: list[dict[str, Any]] = []
    for row in rows:
        authors: list[str] = []
        for author in row.get("author", []) or []:
            if not isinstance(author, dict):
                continue
            name = normalize_text(author.get("name")) or normalize_text(" ".join(part for part in [author.get("given"), author.get("family")] if part))
            if name:
                authors.append(name)
        issued = (((row.get("issued") or {}).get("date-parts")) or [[None]])[0]
        published_at = None
        if issued and isinstance(issued, list) and issued[0]:
            published_at = f"{int(issued[0]):04d}-{int(issued[1]) if len(issued) > 1 else 1:02d}-{int(issued[2]) if len(issued) > 2 else 1:02d}"
        title = ": ".join(part for part in [((row.get("title") or [""])[0]), ((row.get("subtitle") or [""])[0])] if normalize_text(str(part)))
        journal = ((row.get("container-title") or [""])[0]) or row.get("publisher") or ""
        doi = normalize_text(row.get("DOI"))
        link_urls = [item.get("URL") for item in (row.get("link") or []) if isinstance(item, dict)]
        resource_url = (((row.get("resource") or {}).get("primary") or {}).get("URL"))
        candidates = _unique_urls(link_urls, resource_url, _crossref_relation_urls(row.get("relation")), row.get("URL"), f"https://doi.org/{doi}" if doi else "")
        results.append(_make_record(
            source="crossref", channel="search", query=query, source_id=doi, title=title,
            abstract=_strip_jats(row.get("abstract")), summary=str(journal), authors=authors,
            published_at=published_at, doi=doi, url=resource_url or row.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
            journal=str(journal), citations=int(row.get("is-referenced-by-count") or 0),
            evidence_type=str(row.get("type") or "scholarly_work"),
            extra={"score": row.get("score"), "candidate_urls": candidates, "content_links": row.get("link") or [], "resource_url": resource_url or "", "relations": row.get("relation") or {}},
        ))
    return _filtered_result(results, limit, from_date, to_date, strategy="api_time_filter_with_code_fallback", source_fetch_limit=min(limit, 50), strict_time_window=strict_time_window)

def doaj_search(query: str, limit: int = 20, from_date: str | None = None, to_date: str | None = None, strict_time_window: bool = False) -> list[dict[str, Any]]:
    encoded_query = urllib.parse.quote(query, safe="")
    url = f"https://doaj.org/api/search/articles/{encoded_query}?{urllib.parse.urlencode({'pageSize': min(limit, 50)})}"
    payload = _request_json(url, rate_limit=DOAJ_RATE_LIMIT)
    results: list[dict[str, Any]] = []
    start = parse_date(from_date)
    end = parse_date(to_date)
    for row in payload.get("results", []):
        bib = row.get("bibjson") or {}
        published_at = parse_date("-".join(part for part in [str(bib.get("year") or ""), str(bib.get("month") or "").zfill(2)] if part))
        if start and published_at and published_at < start:
            continue
        if end and published_at and published_at > end:
            continue
        identifiers = bib.get("identifier") or []
        doi = next((normalize_text(item.get("id")) for item in identifiers if item.get("type") == "doi"), "")
        links = bib.get("link") or []
        article_url = next((normalize_text(item.get("url")) for item in links if item.get("type") == "fulltext"), "")
        if not article_url and doi:
            article_url = f"https://doi.org/{doi}"
        authors = [normalize_text(item.get("name")) for item in (bib.get("author") or []) if normalize_text(item.get("name"))]
        journal = (bib.get("journal") or {}).get("title") or ""
        results.append(_make_record(
            source="doaj",
            channel="paper_sites",
            query=query,
            source_id=row.get("id") or doi,
            title=bib.get("title"),
            abstract=bib.get("abstract"),
            summary=journal,
            authors=authors,
            published_at=published_at,
            doi=doi,
            url=article_url,
            journal=journal,
            evidence_type="open_access_journal_article",
            extra={"keywords": bib.get("keywords") or [], "license": bib.get("license") or []},
        ))
        if len(results) >= limit:
            break
    return _filtered_result(results, limit, from_date, to_date, strategy="code_time_filter_only", source_fetch_limit=min(limit, 50), strict_time_window=strict_time_window)


def biorxiv_like_search(source_name: str, query: str, limit: int = 20, from_date: str | None = None, to_date: str | None = None, strict_time_window: bool = False) -> list[dict[str, Any]]:
    server = "medrxiv" if source_name == "medrxiv" else "biorxiv"
    query_terms = extract_query_terms(query)
    results: list[dict[str, Any]] = []

    if _looks_like_doi(query):
        urls = [f"https://api.biorxiv.org/details/{server}/{query}"]
    else:
        start_date, end_date = _date_window(from_date, to_date, default_days=90)
        # The API is chronological, not a text-search endpoint. Scan several
        # pages so a relevant record need not happen to be in the first 100.
        urls = [f"https://api.biorxiv.org/details/{server}/{start_date}/{end_date}/{cursor}" for cursor in range(0, 1000, 100)]

    for url in urls:
        payload = _request_json(url, rate_limit=BIORXIV_RATE_LIMIT)
        items = payload.get("collection", []) if isinstance(payload, dict) else []
        if not items:
            break
        for row in items:
            title = normalize_text(row.get("title"))
            abstract = normalize_text(row.get("abstract"))
            haystack = f"{title} {abstract}".lower()
            if query_terms and not any(term in haystack for term in query_terms):
                continue
            authors = [name.strip() for name in normalize_text(row.get("authors")).split(";") if name.strip()]
            results.append(_make_record(
                source=source_name,
                channel="search",
                query=query,
                source_id=row.get("doi") or row.get("rel_doi") or title,
                title=title,
                abstract=abstract,
                summary=row.get("category") or source_name,
                authors=authors,
                published_at=row.get("date"),
                doi=row.get("doi"),
                url=f"https://www.{source_name}.org/content/{row.get('doi')}v{row.get('version', '1')}" if row.get("doi") else "",
                journal=source_name,
                evidence_type="preprint",
                extra={"version": row.get("version"), "license": row.get("license") or "", "peer_reviewed": False},
            ))
            if len(results) >= limit:
                return _filtered_result(results[:limit], limit, from_date, to_date, strategy="api_window_scan_with_code_fallback", source_fetch_limit=limit, strict_time_window=strict_time_window)
        messages = payload.get("messages") or []
        total = int((messages[0] if messages else {}).get("total") or 0)
        cursor_text = url.rsplit("/", 1)[-1]
        if cursor_text.isdigit() and int(cursor_text) + len(items) >= total:
            break
    return _filtered_result(results[:limit], limit, from_date, to_date, strategy="api_window_scan_with_code_fallback", source_fetch_limit=limit, strict_time_window=strict_time_window)

def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1].lower()


def _xml_children(root: ET.Element, *names: str) -> list[ET.Element]:
    wanted = {name.lower() for name in names}
    return [node for node in root.iter() if _xml_local_name(str(node.tag)) in wanted]


def _xml_child_text(row: ET.Element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for child in row:
        if _xml_local_name(str(child.tag)) in wanted:
            value = _itertext(child)
            if value:
                return _strip_jats(value)
    return ""


def _repair_feed_xml(xml_text: str) -> str:
    cleaned = "".join(character for character in xml_text if character in "\t\n\r" or ord(character) >= 32)
    return re.sub(
        r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9A-Fa-f]+;)",
        "&amp;",
        cleaned,
    )


def rss_fetch(feed_url: str, query: str | None = None, limit: int = 20, from_date: str | None = None, to_date: str | None = None, strict_time_window: bool = False) -> list[dict[str, Any]]:
    xml_text = _request_text(
        feed_url,
        headers={
            "User-Agent": "sextant/2.0 (+local research feed reader)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
        },
    )
    root = ET.fromstring(_repair_feed_xml(xml_text))
    rows = _xml_children(root, "item")
    if not rows:
        rows = _xml_children(root, "entry")
    query_terms = extract_query_terms(query or "") if query else []
    results: list[dict[str, Any]] = []
    for row in rows:
        title = _xml_child_text(row, "title")
        summary = _xml_child_text(row, "description", "summary", "abstract", "encoded", "content")
        link = _xml_child_text(row, "link")
        if not link:
            for child in row:
                if _xml_local_name(str(child.tag)) == "link" and child.attrib.get("href"):
                    link = normalize_text(child.attrib.get("href"))
                    break
        if not link:
            link = _xml_child_text(row, "guid", "id")
        published = _xml_child_text(row, "pubdate", "published", "updated", "date", "issued")
        published_iso = None
        if published:
            try:
                published_iso = parsedate_to_datetime(published).date().isoformat()
            except Exception:  # noqa: BLE001
                published_iso = parse_date(published)
        haystack = f"{title} {summary}".lower()
        if query_terms and not any(term in haystack for term in query_terms):
            continue
        if not title and not summary:
            continue
        source_id = link or title or sha1_text(f"{title}|{published}")
        record = _make_record(
            source="rss",
            channel="feeds",
            query=query or feed_url,
            source_id=source_id,
            title=title,
            abstract=summary,
            summary=feed_url,
            authors=[],
            published_at=published_iso,
            url=link,
            journal=feed_url,
            evidence_type="feed_item",
        )
        if not _record_in_window(record, from_date, to_date):
            continue
        results.append(record)
        if len(results) >= limit:
            break
    return _filtered_result(results, limit, from_date, to_date, strategy="feed_order_then_hard_window_filter", source_fetch_limit=limit, strict_time_window=strict_time_window)


def annotate_hits(records: list[dict[str, Any]], keywords: list[str], synonyms: list[str]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for record in records:
        copied = dict(record)
        copied["keyword_hits"] = keyword_hits(copied, keywords, synonyms)
        enriched.append(copied)
    return enriched


def feed_monitor_sync(registry: dict[str, Any], previous_state: dict[str, Any] | None, *, limit_per_feed: int = 20, max_known_entries_per_feed: int = 500) -> dict[str, Any]:
    subscriptions = registry.get("subscriptions", []) if isinstance(registry.get("subscriptions"), list) else []
    prior = previous_state or {}
    prior_subscriptions = prior.get("subscriptions", {}) if isinstance(prior.get("subscriptions"), dict) else {}
    next_state = {"last_polled_at": utc_now_iso(), "last_success_at": None, "last_new_record_count": 0, "subscriptions": {}}
    new_records: list[dict[str, Any]] = []
    successful_feeds: list[dict[str, Any]] = []
    failed_feeds: list[dict[str, Any]] = []
    snapshot_count = 0
    for subscription in subscriptions:
        if not subscription.get("enabled", True):
            continue
        feed_id = normalize_text(subscription.get("feed_id")) or f"feed-{snapshot_count+1}"
        feed_url = normalize_text(subscription.get("url"))
        previous_entries = prior_subscriptions.get(feed_id, {}).get("known_entry_ids", []) if isinstance(prior_subscriptions.get(feed_id), dict) else []
        known_set = set(previous_entries)
        try:
            rows = rss_fetch(feed_url, limit=limit_per_feed)
            snapshot_count += len(rows)
            current_ids: list[str] = []
            feed_new = 0
            for row in rows:
                entry_id = normalize_text(row.get("url")) or normalize_text(row.get("source_id")) or sha1_text(row.get("title", ""))
                current_ids.append(entry_id)
                if entry_id not in known_set:
                    copied = dict(row)
                    copied["feed_id"] = feed_id
                    copied["feed_label"] = subscription.get("label") or feed_url
                    new_records.append(copied)
                    feed_new += 1
            next_state["subscriptions"][feed_id] = {
                "feed_id": feed_id,
                "url": feed_url,
                "label": subscription.get("label") or feed_url,
                "known_entry_ids": current_ids[:max_known_entries_per_feed],
                "last_success_at": utc_now_iso(),
                "last_new_record_count": feed_new,
            }
            successful_feeds.append({"feed_id": feed_id, "new_record_count": feed_new})
        except Exception as exc:  # noqa: BLE001
            next_state["subscriptions"][feed_id] = prior_subscriptions.get(feed_id, {"feed_id": feed_id, "url": feed_url, "label": subscription.get("label") or feed_url})
            failed_feeds.append({"feed_id": feed_id, "error": str(exc)})
    if successful_feeds:
        next_state["last_success_at"] = utc_now_iso()
    next_state["last_new_record_count"] = len(new_records)
    return {
        "new_records": new_records,
        "snapshot_count": snapshot_count,
        "successful_feeds": successful_feeds,
        "failed_feeds": failed_feeds,
        "next_state": next_state,
    }
