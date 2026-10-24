from __future__ import annotations

import html
import re
from difflib import SequenceMatcher
from typing import Any

BOILERPLATE_PATTERNS = (
    r"requires? javascript", r"enable javascript", r"temporarily unavailable",
    r"under maintenance", r"access denied", r"checking your browser",
    r"please wait while we", r"cookie policy", r"sign in to (?:view|continue|access)",
    r"this site uses cookies", r"page not found",
)
SUPPLEMENT_PATTERNS = (
    r"^supplement(?:ary|al)?\s+(?:figure|fig\.?|table|data|material|appendix|file)",
    r"^(?:extended data|additional file)\s+(?:figure|fig\.?|table|\d+)",
    r"^fig(?:ure)?\.?\s*[a-z0-9]+[.:]\s*", r"^table\s*[a-z0-9]+[.:]\s*",
)
SOURCE_LABELS = {
    "source_metadata": {"zh": "来源摘要", "en": "Source abstract"},
    "provided": {"zh": "来源摘要", "en": "Source abstract"},
    "registry_synopsis": {"zh": "注册信息摘要", "en": "Registry synopsis"},
    "article_abstract_section": {"zh": "原文摘要", "en": "Article abstract"},
    "generated_from_full_document": {"zh": "正文生成摘要", "en": "Full-text derived"},
    "abstract_fallback": {"zh": "摘要回退", "en": "Abstract fallback"},
    "unavailable": {"zh": "不可用", "en": "Unavailable"},
}


def clean_content_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2}


def _title_repetition(text: str, title: str) -> bool:
    text_tokens, title_tokens = _tokens(text), _tokens(title)
    if not text_tokens or not title_tokens:
        return False
    overlap = len(text_tokens & title_tokens) / max(1, len(text_tokens | title_tokens))
    ratio = SequenceMatcher(None, text.lower(), title.lower()).ratio()
    return len(text.split()) <= max(45, len(title.split()) * 2) and (overlap >= 0.72 or ratio >= 0.78)


def is_supplementary_record(title: Any, evidence_type: Any = "") -> bool:
    clean_title = clean_content_text(title).lower()
    clean_type = clean_content_text(evidence_type).lower()
    if any(token in clean_type for token in ("component", "supplement", "dataset", "peer-review")):
        return True
    return any(re.search(pattern, clean_title, flags=re.IGNORECASE) for pattern in SUPPLEMENT_PATTERNS)


def assess_abstract_quality(value: Any, *, title: Any = "", source: Any = "", evidence_type: Any = "") -> dict[str, Any]:
    text = clean_content_text(value)
    clean_title = clean_content_text(title)
    words = text.split()
    reasons: list[str] = []
    if not text:
        reasons.append("missing")
    source_name = clean_content_text(source).lower()
    evidence_name = clean_content_text(evidence_type).lower()
    minimum_words = 55 if source_name == "clinicaltrials" or evidence_name == "clinical_trial" else 30 if evidence_name in {"news", "feed", "announcement"} or source_name == "rss" else 45
    if text and len(words) < minimum_words:
        reasons.append("too_short")
    lower = text.lower()
    if text and any(re.search(pattern, lower) for pattern in BOILERPLATE_PATTERNS):
        reasons.append("boilerplate")
    if text and any(re.search(pattern, lower) for pattern in SUPPLEMENT_PATTERNS):
        reasons.append("supplement_caption")
    if is_supplementary_record(clean_title, evidence_name):
        reasons.append("supplementary_record")
    if _title_repetition(text, clean_title):
        reasons.append("title_repetition")
    reasons = list(dict.fromkeys(reasons))
    penalty = {"missing": 100, "boilerplate": 85, "supplementary_record": 80, "supplement_caption": 75, "title_repetition": 60, "too_short": 45}
    score = max(0, 100 - max((penalty.get(reason, 30) for reason in reasons), default=0))
    return {
        "status": "usable" if not reasons else ("missing" if "missing" in reasons else "low_quality"),
        "usable": not reasons,
        "score": score,
        "word_count": len(words),
        "minimum_words": minimum_words,
        "reasons": reasons,
        "clean_text": text,
    }


def abstract_source_label(source: Any, lang: str = "zh") -> str:
    key = clean_content_text(source).lower() or "unavailable"
    labels = SOURCE_LABELS.get(key)
    return labels.get(lang, labels["en"]) if labels else key.replace("_", " ")
