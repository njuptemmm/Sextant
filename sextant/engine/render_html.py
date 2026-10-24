#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import webbrowser
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sextant.engine.abstract_labels import abstract_source_label

SCRIPT_DIR = Path(__file__).resolve().parent

from sextant.engine.common import (
    article_catalog_file,
    deep_read_file,
    contains_cjk,
    focus_catalog_file,
    focus_ranked_records_file,
    load_topic,
    paper_cards_file,
    pending_verification_file,
    persist_status,
    ranked_records_file,
    related_articles_file,
    read_json,
    read_jsonl,
    report_content_en_file,
    report_content_zh_file,
    report_synthesis_input_file,
    summary_file,
    topic_root,
)

ANALYSIS_FIELDS = (
    "abstract",
    "methods",
    "results",
    "limitations",
    "research_and_application_impact",
)

HTML_CHINESE_PLACEHOLDERS = ("\u5f85\u8865\u4e2d\u6587", "\u6682\u65e0\u4e2d\u6587\u5185\u5bb9")

LABELS = {
    "zh": {
        "print": "打印 / 导出 PDF",
        "catalog_eyebrow": "Research intelligence evidence catalog",
        "catalog_subtitle": "按综合排名展示。使用来源、年份和类型筛选，不改变全局排名顺序。",
        "report_eyebrow": "Structured research intelligence report",
        "deep_eyebrow": "Paper deep-reading report",
        "generated": "生成时间",
        "papers": "相关文章",
        "sources": "来源数量",
        "years": "覆盖年份",
        "abstracts": "可用摘要",
        "other_related": "其他相关文章",
        "other_related_note": "这些记录通过了摘要质量门控和主题相关性审查，但未进入主要阅读集。它们作为附录信号而非当前综合的核心证据。",
        "pending": "待核验",
        "pending_note": "以下文章获取摘要和全文失败，但可能与主题相关，请自行核验。",
        "failure_reason": "待核验原因",
        "overview": "结果概览",
        "distribution": "来源分布",
        "catalog": "文章目录",
        "search": "搜索标题、摘要或期刊",
        "all_sources": "全部来源",
        "all_years": "全部年份",
        "all_types": "全部类型",
        "showing": "当前显示",
        "rank": "排名",
        "date": "发布时间",
        "journal": "期刊 / 平台",
        "score": "综合分",
        "abstract": "摘要",
        "view_abstract": "查看摘要",
        "hide_abstract": "收起摘要",
        "view_details": "查看详情",
        "hide_details": "收起详情",
        "choice_title": "请选择下一步",
        "choice_note": "本页面是用户决策断点，选择后再进入对应分支。",
        "integrated": "综合调研报告",
        "subthemes": "方案与观点分类",
        "methods": "提出的方法 / 观点",
        "results": "主要结果",
        "limitations": "局限",
        "research_and_application_impact": "研究与应用影响",
        "research_question": "研究问题",
        "samples": "样本",
        "business_impact": "业务影响",
        "method_category": "方案 / 观点分类",
        "deep_overview": "精读概览",
        "papers_detail": "逐篇精读",
        "comparison": "综合比较",
        "no_matches": "没有符合当前筛选条件的文章。",
        "footer": "由 Sextant 从结构化证据生成；结论请回溯论文原文。",
    },
    "en": {
        "print": "Print / Export PDF",
        "catalog_eyebrow": "Research intelligence evidence catalog",
        "catalog_subtitle": "Global ranking is preserved. Filters narrow the view without changing rank order.",
        "report_eyebrow": "Structured research intelligence report",
        "deep_eyebrow": "Paper deep-reading report",
        "generated": "Generated",
        "papers": "Relevant papers",
        "sources": "Sources",
        "years": "Years covered",
        "abstracts": "Usable abstracts",
        "other_related": "Other related articles",
        "other_related_note": "These records passed abstract quality and topic relevance review but were moved out of the main reading set. They remain appendix signals rather than core evidence for the current synthesis.",
        "pending": "Pending verification",
        "pending_note": "The papers below failed abstract and full-text retrieval, but they may still be relevant to the topic. Please verify them manually.",
        "failure_reason": "Verification reason",
        "overview": "Overview",
        "distribution": "Source distribution",
        "catalog": "Paper catalog",
        "search": "Search title, abstract, or journal",
        "all_sources": "All sources",
        "all_years": "All years",
        "all_types": "All types",
        "showing": "Showing",
        "rank": "Rank",
        "date": "Published",
        "journal": "Journal / platform",
        "score": "Score",
        "abstract": "Abstract",
        "view_abstract": "View abstract",
        "hide_abstract": "Hide abstract",
        "view_details": "View details",
        "hide_details": "Hide details",
        "choice_title": "Choose the next step",
        "choice_note": "This page is a user decision gate. Continue only after a route is selected.",
        "integrated": "Integrated report",
        "subthemes": "Approach and viewpoint families",
        "methods": "Proposed method / viewpoint",
        "results": "Main results",
        "limitations": "Limitations",
        "research_and_application_impact": "Research and application impact",
        "research_question": "Research question",
        "samples": "Samples",
        "business_impact": "Business impact",
        "method_category": "Approach / viewpoint family",
        "deep_overview": "Deep-reading overview",
        "papers_detail": "Paper-by-paper analysis",
        "comparison": "Cross-paper comparison",
        "no_matches": "No papers match the current filters.",
        "footer": "Generated by Sextant from structured evidence; verify conclusions against the source papers.",
    },
}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _esc(value: Any) -> str:
    return html.escape(_text(value), quote=True)


def _block(value: Any) -> str:
    escaped = html.escape(str(value or "").strip(), quote=False)
    return escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


_SENTENCE_RE = re.compile(r"(?<=[.!?。！？；;])\s+")


def _looks_english(value: str) -> bool:
    letters = sum(1 for ch in value if ch.isascii() and ch.isalpha())
    cjk = sum(1 for ch in value if "\u4e00" <= ch <= "\u9fff")
    return letters >= max(12, cjk * 2)


def _sentence_lines(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    lines: list[str] = []
    for raw in re.split(r"\n+", text):
        raw = raw.strip()
        if not raw:
            continue
        parts = [part.strip() for part in _SENTENCE_RE.split(raw) if part.strip()]
        lines.extend(parts or [raw])
    return lines


def _normalize_chinese_text(value: Any, english_hint: Any = None) -> str:
    zh = _text(value)
    en = _text(english_hint)
    if not zh:
        return ""
    if contains_cjk(zh):
        return zh
    if en and zh == en:
        return ""
    if _looks_english(zh):
        return ""
    return zh


def _normalize_line_pairs(value: Any) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        return []
    pairs: list[tuple[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            en = _text(item.get("en") or item.get("english") or item.get("source"))
            zh = _normalize_chinese_text(item.get("zh") or item.get("chinese") or item.get("translation"), en)
            if en or zh:
                pairs.append((en, zh))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            en = _text(item[0])
            zh = _normalize_chinese_text(item[1], en)
            if en or zh:
                pairs.append((en, zh))
    return pairs


def _paired_lines(zh: Any, en: Any, pairs: Any = None) -> list[tuple[str, str]]:
    explicit = _normalize_line_pairs(pairs)
    if explicit:
        return explicit
    zh_lines = _sentence_lines(zh)
    en_lines = _sentence_lines(en)
    if en_lines and zh_lines and len(en_lines) == len(zh_lines):
        return list(zip(en_lines, zh_lines))
    if en_lines and not zh_lines:
        return [(line, "") for line in en_lines]
    if zh_lines and not en_lines:
        return [("", line) for line in zh_lines]
    if en_lines and zh_lines:
        return [(" ".join(en_lines), " ".join(zh_lines))]
    if en_lines:
        return [(line, "") for line in en_lines]
    if zh_lines:
        return [("", line) for line in zh_lines]
    return []


def _bilingual_box(zh: Any, en: Any, pairs: Any = None) -> str:
    en_text = _text(en)
    zh_text = _normalize_chinese_text(zh, en_text)
    both_rows = []
    for en_line, zh_line in _paired_lines(zh_text, en_text, pairs=pairs):
        en_html = _block(en_line) if en_line else '<span class="missing">待补英文</span>'
        zh_html = _block(zh_line) if zh_line else '<span class="missing">待补中文</span>'
        both_rows.append(f'<div class="bilingual-pair"><p class="line-en">{en_html}</p><p class="line-zh">{zh_html}</p></div>')
    if not both_rows:
        both_rows.append('<div class="bilingual-pair"><p class="line-en"><span class="missing">待补英文</span></p><p class="line-zh"><span class="missing">待补中文</span></p></div>')
    zh_html = _block(zh_text) if zh_text else '<span class="missing">待补中文</span>'
    en_html = _block(en_text) if en_text else '<span class="missing">待补英文</span>'
    return f'''
    <div class="bilingual-box" data-view="both">
      <div class="lang-switch" role="group" aria-label="内容语言切换">
        <button type="button" class="active" data-view="both">中英对照</button>
        <button type="button" data-view="zh">中文</button>
        <button type="button" data-view="en">英文</button>
      </div>
      <div class="lang-panel lang-both">{''.join(both_rows)}</div>
      <div class="lang-panel lang-zh"><p>{zh_html}</p></div>
      <div class="lang-panel lang-en"><p>{en_html}</p></div>
    </div>'''

def _single_language_box(text: Any) -> str:
    content = _block(_text(text)) if _text(text) else "暂无摘要。"
    return f'<div class="abstract-single plain-note">{content}</div>'


def _zh_only_box(zh: Any, html_content: str | None = None) -> str:
    zh_text = _normalize_chinese_text(zh)
    zh_html = html_content if html_content is not None else (_block(zh_text) if zh_text else '<span class="missing">待补中文</span>')
    return f'''
    <div class="bilingual-box zh-only" data-view="zh">
      <div class="lang-panel lang-zh"><p>{zh_html}</p></div>
    </div>'''


def _paper_anchor_id(value: Any) -> str:
    return f"paper-{_slug(value)}"


def _normalize_report_links(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    links: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        term = _text(item.get("term"))
        paper_id = _text(item.get("paper_id"))
        if term and paper_id:
            links.append({"term": term, "paper_id": paper_id})
    return links


def _linked_block(value: Any, links: Any, by_id: dict[str, dict[str, Any]]) -> str:
    text = str(value or "").strip()
    normalized_links = [item for item in _normalize_report_links(links) if _text(item.get("paper_id")) in by_id]
    if not text:
        return '<span class="missing">待补中文</span>'
    if not normalized_links:
        return _block(text)
    term_map: dict[str, dict[str, Any]] = {}
    for item in normalized_links:
        term = item["term"]
        if term not in term_map:
            card = by_id[_text(item["paper_id"])]
            term_map[term] = {"paper_id": _text(item["paper_id"]), "title": _text(card.get("title"))}
    if not term_map:
        return _block(text)
    pattern = re.compile("|".join(re.escape(term) for term in sorted(term_map, key=len, reverse=True)))
    output: list[str] = []
    last = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        if start > last:
            output.append(html.escape(text[last:start], quote=False).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>"))
        term = match.group(0)
        meta = term_map[term]
        output.append(
            f'<a class="inline-paper-link" href="#{_esc(_paper_anchor_id(meta["paper_id"]))}" '
            f'title="{_esc(meta["title"] or meta["paper_id"])}">{_esc(term)}</a>'
        )
        last = end
    if last < len(text):
        output.append(html.escape(text[last:], quote=False).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>"))
    return "".join(output)



def _field_pair(card: dict[str, Any], field: str) -> tuple[str, str]:
    english = card.get("english") if isinstance(card.get("english"), dict) else {}
    raw_zh = _text(card.get(field))
    en = _text(english.get(field) or card.get(f"{field}_en"))
    zh = _normalize_chinese_text(raw_zh, en)
    if field == "abstract" and not en and raw_zh and _looks_english(raw_zh):
        en = raw_zh
        zh = _normalize_chinese_text(card.get("abstract_zh") or card.get("summary_zh"), en)
    return zh, en


def _record_abstract_pair(row: dict[str, Any], pending: bool = False) -> tuple[str, str]:
    if pending:
        return "未获得通过质量门控的摘要。", "No quality-approved abstract was available."
    zh = _text(row.get("abstract_zh") or row.get("summary_zh"))
    en = _text(row.get("abstract_en") or row.get("summary_en"))
    english = row.get("english") if isinstance(row.get("english"), dict) else {}
    zh = zh or _text(english.get("abstract_zh"))
    en = en or _text(english.get("abstract"))
    abstract = _text(row.get("abstract"))
    if abstract:
        if _looks_english(abstract):
            if not en or len(abstract) > len(en) * 2:
                en = abstract
        else:
            if not zh or len(abstract) > len(zh) * 2:
                zh = abstract
    zh = _normalize_chinese_text(zh, en)
    return zh, en


def _plain_language_summary_zh(row: dict[str, Any]) -> str:
    english = row.get("english") if isinstance(row.get("english"), dict) else {}
    return _normalize_chinese_text(
        row.get("plain_language_summary_zh")
        or row.get("lay_summary_zh")
        or row.get("plain_summary_zh")
        or english.get("plain_language_summary_zh")
    )


def combined_catalog_file(root: Path) -> Path:
    return article_catalog_file(root)


def combined_summary_file(root: Path) -> Path:
    return summary_file(root)


def _extract_line_pairs(payload: dict[str, Any], field: str) -> list[tuple[str, str]]:
    english = payload.get("english") if isinstance(payload.get("english"), dict) else {}
    candidates = [
        payload.get(f"{field}_bilingual_lines"),
        payload.get(f"{field}_lines"),
        english.get(f"{field}_bilingual_lines"),
        english.get(f"{field}_lines"),
    ]
    for candidate in candidates:
        pairs = _normalize_line_pairs(candidate)
        if pairs:
            return pairs
    return []


def _slug(value: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _text(value).lower()).strip("-")
    return slug or "item"


def _year(row: dict[str, Any]) -> str:
    match = re.search(r"(?:19|20)\d{2}", _text(row.get("published_at") or row.get("updated_at")))
    return match.group(0) if match else "unknown"


def _source(row: dict[str, Any]) -> str:
    return _text(row.get("source") or row.get("journal") or "unknown")


def _evidence_type(row: dict[str, Any]) -> str:
    return _text(row.get("evidence_type") or "unspecified")


def _link(row: dict[str, Any]) -> str:
    url = _text(row.get("url"))
    if not url and _text(row.get("doi")):
        url = f"https://doi.org/{_text(row.get('doi'))}"
    return url


def _template() -> str:
    path = SCRIPT_DIR.parent / "assets" / "research_dashboard_template.html"
    return path.read_text(encoding="utf-8")


def _page(*, lang: str, title: str, subtitle: str, eyebrow: str, body: str, script: str = "", css: str = "") -> str:
    labels = LABELS["zh"]
    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    replacements = {
        "{{LANG}}": "zh-CN",
        "{{DOCUMENT_TITLE}}": _esc(title),
        "{{EYEBROW}}": _esc(eyebrow),
        "{{PAGE_TITLE}}": _esc(title),
        "{{SUBTITLE}}": _esc(subtitle),
        "{{GENERATED_AT}}": f"{labels['generated']}: {_esc(generated)}",
        "{{PRINT_LABEL}}": labels["print"],
        "{{BODY}}": body,
        "{{SCRIPT}}": script,
        "{{FOOTER}}": labels["footer"],
        "{{EXTRA_CSS}}": css,
    }
    output = _template()
    for marker, value in replacements.items():
        output = output.replace(marker, value)
    return output


def _assert_no_empty_chinese_slots(rendered: str, label: str) -> None:
    findings: list[str] = []
    for marker in HTML_CHINESE_PLACEHOLDERS:
        count = rendered.count(marker)
        if count:
            findings.append(f"placeholder '{marker}' appears {count} time(s)")
    missing_spans = rendered.count('<span class="missing">')
    if missing_spans:
        findings.append(f'missing-span marker appears {missing_spans} time(s)')
    fake_chinese = 0
    for match in re.findall(r'<p class="line-zh">(.*?)</p>|<div class="lang-panel lang-zh"><p>(.*?)</p></div>', rendered, flags=re.S):
        fragment = html.unescape(" ".join(part for part in match if part))
        fragment = re.sub(r'<[^>]+>', ' ', fragment)
        fragment = _text(fragment)
        if fragment and not contains_cjk(fragment) and _looks_english(fragment):
            fake_chinese += 1
    if fake_chinese:
        findings.append(f'english text leaked into Chinese panels {fake_chinese} time(s)')
    if findings:
        details = "; ".join(findings)
        raise SystemExit(
            f"{label} still contains invalid Chinese slots: {details}. "
            "Fill the Chinese content for both the bilingual and Chinese-only views, then render again."
        )


def _options(values: list[str], all_label: str) -> str:
    items = [f'<option value="">{_esc(all_label)}</option>']
    items.extend(f'<option value="{_esc(value)}">{_esc(value)}</option>' for value in values)
    return "".join(items)


def _ranked_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        payload = read_json(path, default=[])
        if isinstance(payload, list):
            return [dict(row) for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("records", "results", "articles", "papers"):
                values = payload.get(key)
                if isinstance(values, list):
                    return [dict(row) for row in values if isinstance(row, dict)]
        raise SystemExit(f"Ranked JSON must be an array or contain records/results/articles/papers: {path}")
    return read_jsonl(path)


def _research_direction(row: dict[str, Any], fallback: str = "其他相关研究") -> str:
    explicit = _text(row.get("research_direction") or row.get("method_category") or row.get("primary_category"))
    if explicit:
        return explicit
    aspects = row.get("topic_aspects")
    if isinstance(aspects, list) and aspects:
        return _text(aspects[0]) or fallback
    if _text(row.get("evidence_type")):
        return _text(row.get("evidence_type"))
    return fallback


def _research_direction_description(row: dict[str, Any], name: str) -> str:
    explicit = _text(row.get("research_direction_description") or row.get("category_description") or row.get("method_category_rationale"))
    if explicit:
        return explicit
    return f"围绕“{name}”方向的相关研究，按综合排序保留原始证据顺序。"


def _group_rows(rows: list[dict[str, Any]]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        name = _research_direction(row)
        description = _research_direction_description(row, name)
        if name not in groups:
            groups[name] = {"description": description, "rows": []}
            order.append(name)
        groups[name]["rows"].append(row)
    order.sort(key=lambda name: min(float(row.get("ranking_score") or 0) * -1 for row in groups[name]["rows"]))
    return [(name, groups[name]["description"], groups[name]["rows"]) for name in order]


def _deep_read_panel_html() -> str:
    return """<div class="deep-read-panel">
      <h3>论文精读 Prompt 生成</h3>
      <p class="deep-read-desc">勾选右侧目录中的文章，此处将自动生成精读指令。点击"复制 Prompt"后粘贴到输入框即可启动精读。</p>
      <div class="deep-read-preview" id="deepReadPreview">
        <span class="deep-read-placeholder">尚未勾选文章</span>
      </div>
      <div class="deep-read-actions">
        <button type="button" class="deep-read-clear" id="deepReadClear">清空勾选</button>
        <button type="button" class="deep-read-copy" id="deepReadCopy">复制 Prompt</button>
      </div>
    </div>"""


def _deep_read_css(layout_selector: str = ".catalog-layout") -> str:
    return f"""
    {layout_selector} .side-column {{ position: sticky; top: 14px; display: flex; flex-direction: column; gap: 12px; max-height: calc(100vh - 28px); }}
    {layout_selector} .side-column .side-nav {{ position: static; max-height: none; flex: 1 1 auto; min-height: 0; }}
    .deep-read-panel {{ flex: 0 0 auto; padding: 12px 14px; background: var(--panel); border: 1px solid var(--line); border-left: 4px solid var(--blue); border-radius: 7px; box-shadow: 0 3px 12px rgba(20,35,50,.05); }}
    .deep-read-panel h3 {{ margin: 0 0 6px; color: var(--navy); font-size: 13px; font-weight: 800; }}
    .deep-read-desc {{ margin: 0 0 9px; color: var(--muted); font-size: 11px; line-height: 1.42; }}
    .deep-read-preview {{ min-height: 44px; max-height: 200px; overflow-y: auto; padding: 8px 10px; background: linear-gradient(180deg, #fbfdfe 0%, #f6fafb 100%); border: 1px solid #d8e2e8; border-radius: 5px; font-size: 12px; line-height: 1.5; color: #344454; white-space: pre-wrap; overflow-wrap: anywhere; }}
    .deep-read-placeholder {{ color: #8a98a5; font-style: italic; }}
    .deep-read-actions {{ display: grid; grid-template-columns: 1fr 1fr; gap: 7px; margin-top: 9px; }}
    .deep-read-actions button {{ padding: 7px 10px; border: 0; border-radius: 5px; cursor: pointer; font-size: 12px; font-weight: 700; transition: background .15s, filter .15s; }}
    .deep-read-clear {{ color: #516273; background: #e9f0f3; border: 1px solid #cbd8df; }}
    .deep-read-clear:hover {{ background: #dde8ed; }}
    .deep-read-copy {{ color: #fff; background: linear-gradient(135deg, var(--blue), var(--teal)); }}
    .deep-read-copy:hover {{ filter: brightness(1.08); }}
    .deep-read-copy.copied {{ background: var(--green) !important; }}
    .paper-plain-summary {{ margin-top: 6px; color: #52616f; font-size: 12px; line-height: 1.5; }}
    .paper-row.deep-read-enabled .row-main, .analysis-row.deep-read-enabled .row-main {{ grid-template-columns: 22px 54px minmax(0, 1fr); }}
    .paper-row.deep-read-enabled .row-meta, .analysis-row.deep-read-enabled .row-meta {{ grid-column: 3; }}
    .paper-row.deep-read-enabled .deep-read-check, .analysis-row.deep-read-enabled .deep-read-check {{ width: 15px; height: 15px; margin: 0; cursor: pointer; accent-color: var(--teal); align-self: center; }}
    @media (max-width: 980px) {{
      {layout_selector} .side-column {{ position: static; max-height: none; }}
      {layout_selector} .side-column .side-nav {{ flex: 0 0 auto; min-height: 0; }}
    }}
    @media (max-width: 620px) {{
      .paper-row.deep-read-enabled .row-main, .analysis-row.deep-read-enabled .row-main {{ grid-template-columns: 22px 46px minmax(0, 1fr); }}
    }}
    @media print {{
      {layout_selector} {{ grid-template-columns: 1fr; }}
      {layout_selector} .side-column {{ display: none !important; }}
      .deep-read-panel, .deep-read-check {{ display: none !important; }}
    }}
    """


def _deep_read_script() -> str:
    return """
    (() => {
      const checks = [...document.querySelectorAll('.deep-read-check')];
      const preview = document.getElementById('deepReadPreview');
      const clearBtn = document.getElementById('deepReadClear');
      const copyBtn = document.getElementById('deepReadCopy');
      if (!checks.length || !preview || !clearBtn || !copyBtn) return;
      const buildPrompt = () => {
        const selected = checks.filter(c => c.checked && c.dataset.paperTitle);
        if (!selected.length) return '';
        return '\\u5bf9 ' + selected.map(c => c.dataset.paperTitle).join('\\u3001') + ' \\u8fd9\\u51e0\\u7bc7\\u6587\\u7ae0\\u8fdb\\u884c\\u8bba\\u6587\\u7cbe\\u8bfb';
      };
      const updatePreview = () => {
        const prompt = buildPrompt();
        preview.innerHTML = prompt
          ? prompt.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
          : '<span class="deep-read-placeholder">\\u5c1a\\u672a\\u52fe\\u9009\\u6587\\u7ae0</span>';
      };
      checks.forEach(c => c.addEventListener('change', updatePreview));
      clearBtn.addEventListener('click', () => { checks.forEach(c => { c.checked = false; }); updatePreview(); });
      const showCopied = () => {
        copyBtn.textContent = '\\u5df2\\u590d\\u5236';
        copyBtn.classList.add('copied');
        setTimeout(() => { copyBtn.textContent = '\\u590d\\u5236 Prompt'; copyBtn.classList.remove('copied'); }, 2000);
      };
      const fallbackCopy = (text, onSuccess) => {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;opacity:0;';
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); onSuccess(); } catch (e) {}
        document.body.removeChild(ta);
      };
      copyBtn.addEventListener('click', () => {
        const text = buildPrompt();
        if (!text) return;
        if (navigator.clipboard && window.isSecureContext) {
          navigator.clipboard.writeText(text).then(showCopied).catch(() => fallbackCopy(text, showCopied));
        } else {
          fallbackCopy(text, showCopied);
        }
      });
      updatePreview();
    })();
    """

def _catalog_card(row: dict[str, Any], rank: int | None, labels: dict[str, str], lang: str = "zh", pending: bool = False, deep_read: bool = False) -> str:
    title = _text(row.get("title")) or "Untitled"
    url = _link(row)
    source = _source(row)
    year = _year(row)
    evidence_type = _evidence_type(row)
    abstract_zh, abstract_en = _record_abstract_pair(row, pending=pending)
    journal = _text(row.get("journal")) or source
    source_badge = abstract_source_label("unavailable" if pending else (row.get("abstract_source") or "source_metadata"), "zh")
    rank_text = labels["pending"] if pending else f"#{int(rank or 0):03d}"
    title_html = f'<a class="paper-title-link" href="{_esc(url)}" target="_blank" rel="noopener">{_esc(title)}</a>' if url else f'<span class="paper-title-link no-link">{_esc(title)}</span>'
    plain_summary = _plain_language_summary_zh(row)
    plain_summary_html = f'<div class="paper-plain-summary">{_block(plain_summary)}</div>' if plain_summary and not pending else ""
    search_blob = " ".join([title, abstract_zh, abstract_en, journal, source, evidence_type, _research_direction(row)]).lower()
    reason = _text(row.get("pending_verification_reason"))
    reason_html = f'<div class="detail-field"><strong>{labels["failure_reason"]}</strong><div class="plain-note">{_block(reason)}</div></div>' if pending and reason else ""
    button_label = labels["view_abstract"]
    hide_label = labels["hide_abstract"]
    if abstract_zh:
        abstract_box = _bilingual_box(abstract_zh, abstract_en, pairs=_extract_line_pairs(row, "abstract"))
    else:
        abstract_box = _single_language_box(abstract_en)
    record_id = _text(row.get("record_id") or row.get("paper_id"))
    deep_read_ready = deep_read and not pending and bool(record_id)
    article_class = "paper-row deep-read-enabled" if deep_read_ready else "paper-row"
    checkbox_html = f'<input type="checkbox" class="deep-read-check" data-paper-id="{_esc(record_id)}" data-paper-title="{_esc(title)}" aria-label="精读选择：{_esc(title)}">' if deep_read_ready else ""
    if pending:
        return f"""
    <article class="paper-row pending-card" data-source="{_esc(source)}" data-year="{_esc(year)}" data-type="{_esc(evidence_type)}" data-search="{_esc(search_blob)}">
      <div class="row-main">
        <span class="rank">{_esc(rank_text)}</span>
        <div class="row-title">{title_html}{plain_summary_html}</div>
        <div class="row-meta"><span>{_esc(row.get('published_at') or row.get('updated_at') or '-')}</span><span>{_esc(source)}</span><span>{_esc(journal)}</span><span>{_esc(source_badge)}</span></div>
      </div>
    </article>"""
    return f"""
    <article class="{article_class}" data-source="{_esc(source)}" data-year="{_esc(year)}" data-type="{_esc(evidence_type)}" data-search="{_esc(search_blob)}">
      <div class="row-main">
        {checkbox_html}
        <span class="rank">{_esc(rank_text)}</span>
        <div class="row-title">{title_html}{plain_summary_html}</div>
        <div class="row-meta"><span>{_esc(row.get('published_at') or row.get('updated_at') or '-')}</span><span>{_esc(source)}</span><span>{_esc(journal)}</span><span>{_esc(source_badge)}</span></div>
      </div>
      <details class="row-detail" data-closed="{_esc(button_label)}" data-open="{_esc(hide_label)}"><summary>{_esc(button_label)}</summary><div class="detail-content"><div class="detail-field"><strong>{labels['abstract']}</strong>{abstract_box}</div>{reason_html}</div></details>
    </article>"""

def render_catalog(root: Path, output: Path | None = None, lang: str = "zh", input_path: Path | None = None, strict_chinese: bool = True) -> Path:
    labels = LABELS["zh"]
    topic = load_topic(root)
    rows = _ranked_rows(input_path or ranked_records_file(root))
    is_focus_catalog = bool(input_path and input_path.resolve() == focus_ranked_records_file(root).resolve())
    pending_rows = read_jsonl(pending_verification_file(root)) if input_path is None else []
    if not rows and not pending_rows:
        raise SystemExit("No ranked or pending-verification articles are available; run apply-relevance first.")
    all_catalog_rows = rows
    sources = sorted({_source(row) for row in all_catalog_rows})
    years = sorted({_year(row) for row in all_catalog_rows}, reverse=True)
    types = sorted({_evidence_type(row) for row in all_catalog_rows})
    grouped = _group_rows(rows)
    group_sections: list[str] = []
    nav_groups: list[str] = []
    row_ranks = {id(row): index + 1 for index, row in enumerate(rows)}
    for group_index, (name, description, group_rows) in enumerate(grouped, start=1):
        section_id = f"group-{group_index}"
        nav_groups.append(f'<a class="nav-subitem" href="#{section_id}">{_esc(name)}</a>')
        cards = "".join(_catalog_card(row, row_ranks[id(row)], labels, "zh", deep_read=True) for row in group_rows)
        group_sections.append(f'<section id="{section_id}" class="catalog-group"><div class="group-head"><h2>{_esc(name)}</h2><p>{_block(description)}</p><span>{len(group_rows)} 篇</span></div><div class="paper-list">{cards}</div></section>')
    pending_cards = "".join(_catalog_card(row, None, labels, "zh", pending=True) for row in pending_rows)
    pending_section = f'<section id="pending" class="catalog-group pending-group"><div class="group-head"><h2>{labels["pending"]}</h2><p>{labels["pending_note"]}</p><span>{len(pending_rows)}</span></div><div class="paper-list">{pending_cards}</div></section>' if pending_rows else ""
    pending_nav = f'<a href="#pending">{labels["pending"]}</a>' if pending_rows else ""
    mode = topic.get("mode") or "-"
    has_chinese = any(_record_abstract_pair(row)[0] for row in rows)
    abstract_note = "目录按研究方向分组，摘要支持中英切换。" if has_chinese else "目录按研究方向分组；配置模型后会自动补充中文摘要。"
    question = _text(topic.get("primary_question"))
    if is_focus_catalog:
        title = f"{_text(topic.get('topic_name')) or root.name} - 特定主题报告"
    else:
        title = f"{_text(topic.get('topic_name')) or root.name} - {labels['catalog']}"
    nav_html = f'<nav class="side-nav"><a href="#catalog">{labels["catalog"]}</a>{"".join(nav_groups)}{pending_nav}</nav>'
    side_column = f'<div class="side-column">{nav_html}{_deep_read_panel_html()}</div>'
    body = f"""
    <div class="report-layout catalog-layout">
      {side_column}
      <div class="report-content">
        <section id="catalog"><div class="section-title"><h2>{labels['catalog']}</h2><span class="section-note">{len(rows)} 篇 / {len(grouped)} 类 / mode: {_esc(mode)}</span></div>
          <div class="panel compact-panel"><div class="toolbar"><input id="searchBox" type="search" placeholder="{_esc(labels['search'])}"><select id="sourceFilter">{_options(sources, labels['all_sources'])}</select><select id="yearFilter">{_options(years, labels['all_years'])}</select><select id="typeFilter">{_options(types, labels['all_types'])}</select></div><div class="result-line"><span>{labels['showing']}: <strong id="visibleCount">{len(all_catalog_rows)}</strong> / {len(all_catalog_rows)}</span><span>{abstract_note}</span></div></div>
          {''.join(group_sections)}
          <div class="empty" id="emptyState" hidden>{labels['no_matches']}</div>
        </section>
        {pending_section}
      </div>
    </div>"""
    catalog_css = _deep_read_css(".catalog-layout") + """
    .catalog-layout .abstract-single { padding: 10px 12px; background: #fbfdfe; border: 1px solid #d8e2e8; border-radius: 7px; }"""
    script = _details_toggle_script() + """
    (() => {
      const rows = [...document.querySelectorAll('.paper-row:not(.pending-card)')];
      const groups = [...document.querySelectorAll('.catalog-group:not(.pending-group)')];
      const controls = ['searchBox', 'sourceFilter', 'yearFilter', 'typeFilter'].map(id => document.getElementById(id));
      const apply = () => {
        const [query, source, year, type] = controls.map(control => control.value.trim().toLowerCase());
        let visible = 0;
        rows.forEach(row => {
          const match = (!query || row.dataset.search.includes(query)) && (!source || row.dataset.source.toLowerCase() === source) && (!year || row.dataset.year.toLowerCase() === year) && (!type || row.dataset.type.toLowerCase() === type);
          row.hidden = !match;
          if (match) visible += 1;
        });
        groups.forEach(group => { group.hidden = !group.querySelector('.paper-row:not([hidden])'); });
        document.getElementById('visibleCount').textContent = visible;
        document.getElementById('emptyState').hidden = visible !== 0;
      };
      controls.forEach(control => control.addEventListener('input', apply));
    })();
    """ + _deep_read_script()
    target = output or (focus_catalog_file(root) if is_focus_catalog else combined_catalog_file(root))
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = _page(lang="zh", title=title, subtitle=question or labels["catalog_subtitle"], eyebrow=labels["catalog_eyebrow"], body=body, script=script, css=catalog_css)
    if strict_chinese:
        _assert_no_empty_chinese_slots(rendered, "catalog_html")
    target.write_text(rendered, encoding="utf-8")
    persist_status(root)
    return target

def _analysis(card: dict[str, Any], field: str, lang: str) -> str:
    zh, en = _field_pair(card, field)
    return en if lang == "en" else zh


def _category(card: dict[str, Any], lang: str = "zh") -> str:
    english = card.get("english") if isinstance(card.get("english"), dict) else {}
    if lang == "en":
        return _text(english.get("method_category") or card.get("method_category_en") or card.get("method_category"))
    return _text(card.get("method_category") or english.get("method_category"))


def _validate_cards(cards: list[dict[str, Any]], lang: str = "zh", require_category: bool = True) -> None:
    errors: list[str] = []
    for index, card in enumerate(cards, start=1):
        paper = _text(card.get("title")) or _text(card.get("paper_id")) or str(index)
        if not _text(card.get("reading_basis")):
            errors.append(f"{paper}: missing reading_basis provenance")
        if not _text(card.get("abstract_source")):
            errors.append(f"{paper}: missing abstract_source provenance")
        evidence_mode = _text(card.get("evidence_mode")) or "abstract_only"
        required_fields = {
            "full_text": ANALYSIS_FIELDS,
            "partial_text": ("abstract", "results", "limitations", "research_and_application_impact"),
            "abstract_only": ("abstract", "research_and_application_impact"),
        }.get(evidence_mode, ("abstract",))
        for field in required_fields:
            zh, en = _field_pair(card, field)
            if len(zh) < 6 and len(en) < 6:
                errors.append(f"{paper}: missing or too short bilingual field '{field}'")
        if require_category and len(_category(card, "zh")) < 2:
            errors.append(f"{paper}: missing method/viewpoint category")
        if card.get("reading_basis") == "unavailable" or card.get("abstract_source") == "unavailable":
            errors.append(f"{paper}: unavailable evidence must stay in the pending-verification appendix")
    if errors:
        preview = "\n".join(errors[:20])
        raise SystemExit(f"HTML rendering refused because structured analysis is incomplete ({len(errors)} errors):\n{preview}")


def _basis_tag(card: dict[str, Any], lang: str = "zh") -> tuple[str, str]:
    basis = _text(card.get("reading_basis")) or "unavailable"
    mapping = {
        "readable_full_text": ("full", "全文"),
        "readable_partial_text": ("partial", "部分正文"),
        "abstract_fallback": ("partial", "摘要"),
        "unavailable": ("failed", "不可用"),
    }
    return mapping.get(basis, ("partial", basis))


def _analysis_card(card: dict[str, Any], lang: str = "zh", detailed: bool = False, deep_read: bool = False) -> str:
    labels = LABELS["zh"]
    title = _text(card.get("title")) or "Untitled"
    url = _link(card)
    category = _category(card, "zh")
    css_class, basis_label = _basis_tag(card, "zh")
    source_label = abstract_source_label(card.get("abstract_source") or ("abstract_fallback" if card.get("reading_basis") == "abstract_fallback" else "source_metadata"), "zh")
    title_html = f'<a class="paper-title-link" href="{_esc(url)}" target="_blank" rel="noopener">{_esc(title)}</a>' if url else f'<span class="paper-title-link no-link">{_esc(title)}</span>'
    plain_summary = _plain_language_summary_zh(card)
    plain_summary_html = f'<div class="paper-plain-summary">{_block(plain_summary)}</div>' if plain_summary else ""
    record_id = _text(card.get("paper_id") or card.get("record_id"))
    checkbox_html = f'<input type="checkbox" class="deep-read-check" data-paper-id="{_esc(record_id)}" data-paper-title="{_esc(title)}" aria-label="精读选择：{_esc(title)}">' if deep_read and record_id else ""
    row_class = "paper-row analysis-row deep-read-enabled" if deep_read and record_id else "paper-row analysis-row"
    field_html: list[str] = []
    evidence_mode = _text(card.get("evidence_mode")) or "abstract_only"
    render_fields = {
        "full_text": ANALYSIS_FIELDS,
        "partial_text": ("abstract", "results", "limitations", "research_and_application_impact"),
        "abstract_only": ("abstract", "research_and_application_impact"),
    }.get(evidence_mode, ("abstract",))
    for field in render_fields:
        zh, en = _field_pair(card, field)
        field_html.append(f'<div class="detail-field"><strong>{labels[field]}</strong>{_bilingual_box(zh, en, pairs=_extract_line_pairs(card, field))}</div>')
    button_label = labels["view_details"]
    hide_label = labels["hide_details"]
    return f"""
    <article id="{_esc(_paper_anchor_id(card.get('paper_id') or card.get('record_id') or title))}" class="{row_class}" data-basis="{_esc(card.get('reading_basis'))}">
      <div class="row-main">
        {checkbox_html}
        <span class="rank">#{int(card.get('rank') or 0):03d}</span>
        <div class="row-title">{title_html}{plain_summary_html}</div>
        <div class="row-meta"><span>{_esc(card.get('published_at') or '-')}</span><span>{_esc(card.get('journal') or '-')}</span><span>{_esc(category)}</span><span class="basis {css_class}">{_esc(basis_label)}</span><span>{_esc(source_label)}</span></div>
      </div>
      <details class="row-detail" data-closed="{_esc(button_label)}" data-open="{_esc(hide_label)}"><summary>{_esc(button_label)}</summary><div class="detail-content">{''.join(field_html)}</div></details>
    </article>"""

def _load_cards(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = read_json(path, default={}) or {}
    cards = payload.get("papers") if isinstance(payload, dict) else None
    if not isinstance(cards, list) or not cards:
        raise SystemExit(f"No paper cards found in {path}")
    return payload, [dict(card) for card in cards if isinstance(card, dict)]


def _section_cards(content_zh: dict[str, Any], content_en: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str:
    labels = LABELS["zh"]
    sections_zh = content_zh.get("sections") if isinstance(content_zh.get("sections"), dict) else {}
    section_links = content_zh.get("section_links") if isinstance(content_zh.get("section_links"), dict) else {}
    keys = ("research_question", "methods", "samples", "results", "limitations", "business_impact")
    missing = [key for key in keys if len(_text(sections_zh.get(key))) < 20]
    if missing:
        raise SystemExit(f"Integrated report overview must contain substantive Chinese content for: {', '.join(missing)}")
    cards: list[str] = []
    for key in keys:
        cards.append(
            f'<article class="summary-card wide"><h3>{labels[key]}</h3>'
            f'{_zh_only_box(sections_zh.get(key), html_content=_linked_block(sections_zh.get(key), section_links.get(key), by_id))}'
            f'</article>'
        )
    return "".join(cards)

def render_report(root: Path, lang: str = "zh", cards_path: Path | None = None, content_path: Path | None = None, output: Path | None = None) -> Path:
    labels = LABELS["zh"]
    _, cards = _load_cards(cards_path or paper_cards_file(root))
    _validate_cards(cards, "zh", require_category=True)
    content_zh = read_json(content_path or report_content_zh_file(root), default={}) or {}
    content_en = read_json(report_content_en_file(root), default={}) or {}
    subthemes_zh = content_zh.get("subthemes") if isinstance(content_zh.get("subthemes"), list) else []
    subthemes_en = content_en.get("subthemes") if isinstance(content_en.get("subthemes"), list) else []
    if not subthemes_zh and not subthemes_en:
        raise SystemExit("Report content must include non-empty subthemes grouped by proposed approaches or viewpoints.")
    merged_subthemes: list[tuple[dict[str, Any], dict[str, Any]]] = []
    max_len = max(len(subthemes_zh), len(subthemes_en))
    for index in range(max_len):
        theme_zh = subthemes_zh[index] if index < len(subthemes_zh) and isinstance(subthemes_zh[index], dict) else {}
        theme_en = subthemes_en[index] if index < len(subthemes_en) and isinstance(subthemes_en[index], dict) else {}
        if theme_zh or theme_en:
            merged_subthemes.append((theme_zh, theme_en))
    by_id = {_text(card.get("paper_id")): card for card in cards}
    assigned: set[str] = set()
    theme_html: list[str] = []
    nav_themes: list[str] = []
    for index, (theme, theme_en) in enumerate(merged_subthemes, start=1):
        theme_title = _normalize_chinese_text(theme.get("title") or theme_en.get("title"))
        theme_summary_zh = _normalize_chinese_text(theme.get("summary"), theme_en.get("summary") or theme.get("summary_en"))
        theme_summary_en = _text(theme_en.get("summary") or theme.get("summary_en"))
        paper_ids = [_text(value) for value in ((theme.get("paper_ids") if isinstance(theme.get("paper_ids"), list) else theme_en.get("paper_ids") if isinstance(theme_en.get("paper_ids"), list) else [])) if _text(value)]
        if len(theme_title) < 2 or len(theme_summary_zh) < 20 or len(theme_summary_en) < 20 or not paper_ids:
            raise SystemExit(f"Subtheme {index} needs real Chinese title/summary, English summary, and paper_ids.")
        unknown = [paper_id for paper_id in paper_ids if paper_id not in by_id]
        if unknown:
            raise SystemExit(f"Subtheme '{theme_title}' references unknown paper_ids: {', '.join(unknown[:5])}")
        assigned.update(paper_ids)
        nav_themes.append(f'<a class="nav-subitem" href="#theme-{index}">{_esc(theme_title)}</a>')
        theme_cards = "".join(_analysis_card(by_id[paper_id], "zh", deep_read=True) for paper_id in paper_ids)
        theme_pairs = _normalize_line_pairs(theme.get("summary_bilingual_lines")) or _normalize_line_pairs(theme_en.get("summary_bilingual_lines"))
        theme_summary_links = theme.get("summary_links") if isinstance(theme.get("summary_links"), list) else []
        theme_summary_box = _zh_only_box(theme_summary_zh, html_content=_linked_block(theme_summary_zh, theme_summary_links, by_id))
        theme_html.append(f'<div class="theme" id="theme-{index}"><div class="theme-head"><h2>{_esc(theme_title)}</h2>{theme_summary_box}</div><div class="paper-list">{theme_cards}</div></div>')
    unassigned = sorted(set(by_id) - assigned)
    if unassigned:
        raise SystemExit(f"Every paper card must belong to at least one subtheme; unassigned={len(unassigned)}")
    topic = load_topic(root)
    title = _text(content_zh.get("title") or content_en.get("title")) or f"{_text(topic.get('topic_name'))} Research Intelligence Report"
    executive_zh = _text(content_zh.get("executive_summary"))
    executive_en = _text(content_en.get("executive_summary"))
    if len(executive_zh) < 20:
        raise SystemExit("Integrated report overview must include a substantive Chinese executive_summary.")
    overview_links = content_zh.get("executive_summary_links") if isinstance(content_zh.get("executive_summary_links"), list) else []
    overview = f'<div class="panel" style="margin-bottom:14px"><strong>{labels["overview"]}</strong>{_zh_only_box(executive_zh, html_content=_linked_block(executive_zh, overview_links, by_id))}</div>'
    section_cards = _section_cards(content_zh, content_en, by_id)
    synthesis_input = read_json(report_synthesis_input_file(root), default={}) or {}
    pending_rows = [dict(row) for row in read_jsonl(pending_verification_file(root))]
    unresolved = synthesis_input.get("unresolved_papers") if isinstance(synthesis_input.get("unresolved_papers"), list) else []
    for row in unresolved:
        if isinstance(row, dict):
            pending_rows.append({**row, "record_id": row.get("paper_id"), "abstract": row.get("source_abstract"), "pending_verification_reason": row.get("pending_verification_reason") or row.get("failure_reason")})
    pending_by_id: dict[str, dict[str, Any]] = {}
    for row in pending_rows:
        identity = _text(row.get("record_id") or row.get("paper_id") or row.get("doi") or row.get("title"))
        if identity:
            pending_by_id[identity] = row
    pending_rows = list(pending_by_id.values())
    pending_cards = "".join(_catalog_card(row, None, labels, "zh", pending=True) for row in pending_rows)
    pending_section = f'<section id="pending"><div class="section-title"><h2>{labels["pending"]}</h2><span class="section-note">{len(pending_rows)}</span></div><div class="panel" style="margin-bottom:14px"><p>{labels["pending_note"]}</p></div><div class="paper-list">{pending_cards}</div></section>' if pending_rows else ""
    pending_nav = f'<a href="#pending">{labels["pending"]}</a>' if pending_rows else ""
    report_css = _deep_read_css(".report-layout") + """
    .inline-paper-link { color: #0f5f78; text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 3px; font-weight: 700; }
    .inline-paper-link:hover { color: #0b7b7b; }
    .analysis-row { scroll-margin-top: 18px; }
    """
    side_column = f'<div class="side-column"><nav class="side-nav"><a href="#integrated">{labels["integrated"]}</a><a href="#themes">{labels["subthemes"]}</a>{"".join(nav_themes)}{pending_nav}</nav>{_deep_read_panel_html()}</div>'
    body = f"""
    <div class="report-layout">
      {side_column}
      <div class="report-content">
        <section id="integrated"><div class="section-title"><h2>{labels['integrated']}</h2><span class="section-note">{len(cards)} {labels['papers']}</span></div>{overview}<div class="summary-stack">{section_cards}</div></section>
        <section id="themes"><div class="section-title"><h2>{labels['subthemes']}</h2><span class="section-note">{len(merged_subthemes)}</span></div>{''.join(theme_html)}</section>
        {pending_section}
      </div>
    </div>"""
    target = output or combined_summary_file(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = _page(lang="zh", title=title, subtitle=_text(topic.get("primary_question")), eyebrow=labels["report_eyebrow"], body=body, script=_details_toggle_script() + _deep_read_script(), css=report_css)
    _assert_no_empty_chinese_slots(rendered, "summary_html")
    target.write_text(rendered, encoding="utf-8")
    persist_status(root)
    return target

def render_deep_read(input_path: Path, output: Path, lang: str) -> Path:
    labels = LABELS["zh"]
    payload, cards = _load_cards(input_path)
    _validate_cards(cards, "zh", require_category=False)
    title = _normalize_chinese_text(payload.get("title")) or "论文精读报告"
    overview = _normalize_chinese_text(payload.get("overview"), payload.get("overview_en") or (payload.get("english") or {}).get("overview"))
    comparison = _normalize_chinese_text(payload.get("comparison"), payload.get("comparison_en") or (payload.get("english") or {}).get("comparison"))
    if len(overview) < 20 or len(comparison) < 20:
        raise SystemExit("Deep-reading content requires substantive Chinese overview and comparison fields.")
    cards_html = "".join(_analysis_card(card, "zh", detailed=True) for card in cards)
    body = f"""
    <div class="report-layout">
      <nav class="side-nav"><a href="#overview">{labels['deep_overview']}</a><a href="#papers">{labels['papers_detail']}</a><a href="#comparison">{labels['comparison']}</a></nav>
      <div class="report-content">
        <section id="overview"><div class="section-title"><h2>{labels['deep_overview']}</h2><span class="section-note">{len(cards)} {labels['papers']}</span></div><div class="panel"><p>{_block(overview)}</p></div></section>
    <section id="papers"><div class="section-title"><h2>{labels['papers_detail']}</h2></div><div class="paper-list">{cards_html}</div></section>
        <section id="comparison"><div class="section-title"><h2>{labels['comparison']}</h2></div><div class="panel"><p>{_block(comparison)}</p></div></section>
      </div>
    </div>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    subtitle = _normalize_chinese_text(payload.get("subtitle"), payload.get("subtitle_en") or (payload.get("english") or {}).get("subtitle"))
    rendered = _page(lang="zh", title=title, subtitle=subtitle, eyebrow=labels["deep_eyebrow"], body=body, script=_details_toggle_script())
    _assert_no_empty_chinese_slots(rendered, output.name)
    output.write_text(rendered, encoding="utf-8")
    return output


def _details_toggle_script() -> str:
    return """
    (() => {
      document.querySelectorAll('details.row-detail').forEach(detail => {
        const summary = detail.querySelector('summary');
        const sync = () => { summary.textContent = detail.open ? detail.dataset.open : detail.dataset.closed; };
        detail.addEventListener('toggle', sync);
        sync();
      });
      document.querySelectorAll('.bilingual-box').forEach(box => {
        const buttons = [...box.querySelectorAll('.lang-switch button')];
        const setView = (view) => {
          box.dataset.view = view;
          buttons.forEach(button => button.classList.toggle('active', button.dataset.view === view));
        };
        buttons.forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
        setView(box.dataset.view || 'both');
      });
    })();
    """


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a self-contained evidence catalog HTML file.")
    commands = parser.add_subparsers(dest="command", required=True)

    catalog = commands.add_parser("catalog", help="Render ranked_articles.jsonl as an interactive HTML catalog")
    catalog.add_argument("--workspace-root", required=True)
    catalog.add_argument("--topic-id", required=True)
    catalog.add_argument("--input", help="Optional ranked JSONL, used by focused-topic reranking")
    catalog.add_argument("--output")
    catalog.add_argument("--language", choices=["zh", "en"], default="zh")

    report = commands.add_parser("report", help="Render paper cards and integrated content as a bilingual report page")
    report.add_argument("--workspace-root", required=True)
    report.add_argument("--topic-id", required=True)
    report.add_argument("--language", choices=["zh", "en"], default="zh")
    report.add_argument("--cards")
    report.add_argument("--content")
    report.add_argument("--output")

    deep = commands.add_parser("deep-read", help="Render detailed agent-authored paper analyses as one combined HTML page")
    deep.add_argument("--input", required=True)
    deep.add_argument("--output")
    deep.add_argument("--workspace-root")
    deep.add_argument("--topic-id")
    deep.add_argument("--language", choices=["zh", "en"], default="zh")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = topic_root(args.workspace_root, args.topic_id) if getattr(args, "workspace_root", None) and getattr(args, "topic_id", None) else None
    try:
        if args.command == "catalog":
            root = topic_root(args.workspace_root, args.topic_id)
            target = render_catalog(root, Path(args.output) if args.output else None, args.language, Path(args.input) if args.input else None)
        elif args.command == "report":
            root = topic_root(args.workspace_root, args.topic_id)
            target = render_report(
                root,
                args.language,
                Path(args.cards) if args.cards else None,
                Path(args.content) if args.content else None,
                Path(args.output) if args.output else None,
            )
        else:
            if not root:
                raise SystemExit("deep-read rendering requires --workspace-root and --topic-id to determine the unified output path.")
            target = render_deep_read(
                Path(args.input),
                Path(args.output) if args.output else deep_read_file(root),
                args.language,
            )
    except Exception:
        raise
    try:
        webbrowser.open(target.resolve().as_uri())
    except Exception:
        pass
    stage = {
        "catalog": "article_catalog_gate",
        "report": "final_report",
        "deep-read": "deep_read_result",
    }[args.command]
    files: dict[str, str] = {"output": str(target.resolve())}
    if args.command == "catalog":
        files = {"output": str(target.resolve())}
    if args.command == "report":
        combined_path = combined_summary_file(root)
        files = {"output": str(combined_path.resolve()) if combined_path.exists() else str(target.resolve())}
    if args.command == "deep-read":
        files = {"output": str(target.resolve())}
    payload = {
        "status": "html_ready",
        "stage": stage,
        "user_delivery_required": True,
        "files": files,
        "delivery_instruction": (
            "Verify the files exist, then send the user a visible response with clickable absolute-path HTML links. "
            "Do not treat this terminal output as delivery and do not end in Thinking."
        ),
    }
    if args.command == "catalog" and root is not None:
        catalog_rows = _ranked_rows(Path(args.input) if args.input else ranked_records_file(root))
        catalog_grouped = _group_rows(catalog_rows)
        payload["research_directions"] = [
            {"name": name, "count": len(grp_rows)} for name, _, grp_rows in catalog_grouped
        ]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
