"""Optional LLM enrichment over an OpenAI-compatible chat-completions API.

The whole application must work without an LLM. Every function here degrades
to a deterministic fallback or an empty result instead of raising.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from sextant.config import Settings
from sextant.engine.common import OTHER_DIRECTION


class LLMError(RuntimeError):
    pass


class LLMTruncated(LLMError):
    """The completion budget ran out before the model produced any JSON.

    Reasoning models bill their chain-of-thought to the same ``max_tokens``
    budget as the answer, so a large prompt can consume the entire allowance
    and return an empty ``content`` with ``finish_reason="length"``. Retrying
    the identical request cannot help; the caller has to change it — raise the
    budget for one long-form document, or split a per-record batch in two.
    """


def _post_json(url: str, payload: dict[str, Any], api_key: str, timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _strip_code_fence(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    return fenced.group(1).strip() if fenced else text.strip()


def _extract_json(text: str) -> Any:
    cleaned = _strip_code_fence(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError("模型返回内容不是合法 JSON")


def chat_json(
    settings: Settings,
    *,
    system: str,
    user: str,
    max_tokens: int = 2048,
    temperature: float = 0.2,
    timeout: float | None = None,
) -> Any:
    if not settings.llm_enabled:
        raise LLMError("LLM 未配置")
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = _post_json(
                settings.llm_endpoint,
                payload,
                settings.llm_api_key,
                timeout or settings.llm_timeout_seconds,
            )
            choice = (response.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
            if choice.get("finish_reason") == "length":
                reasoning = (
                    ((response.get("usage") or {}).get("completion_tokens_details") or {})
                    .get("reasoning_tokens")
                )
                raise LLMTruncated(
                    f"输出达到 max_tokens={max_tokens} 上限"
                    f"（其中 reasoning_tokens={reasoning}），未产出完整 JSON"
                )
            return _extract_json(content)
        except LLMTruncated:
            raise
        except (urllib.error.URLError, TimeoutError, LLMError, KeyError, IndexError) as exc:
            last_error = exc
            if attempt == 0:
                payload.pop("response_format", None)
    raise LLMError(str(last_error) if last_error else "LLM 调用失败")


# ------------------------------------------------------------------ prompts

PLAN_SYSTEM = (
    "你是学术检索策略专家。只输出 JSON，不要解释。"
    "目标是让检索覆盖主题的核心工作、相邻方法和最新进展。"
)

PLAN_SCHEMA = """{
  "keywords": ["核心关键词，3-6 个，含英文术语"],
  "queries": [
    {"source": "openalex", "query": "英文检索式", "rationale": "一句话说明"},
    {"source": "arxiv", "query": "英文检索式", "rationale": "一句话说明"}
  ],
  "directions": ["预期会出现的 3-5 个研究方向，用于后续归类"]
}"""

#: Completion budget for the plan. Measured on deepseek-flash: a six-query
#: plan needs ~550 reasoning + ~650 answer tokens; 900 truncated the JSON.
PLAN_MAX_TOKENS = 2000


def plan_queries(
    settings: Settings,
    topic: dict[str, Any],
    notes: list[str] | None = None,
) -> dict[str, Any] | None:
    """Ask the model for retrieval variants. Returns None when unavailable.

    ``notes`` collects the reason a degradation happened so the run log can
    distinguish "no model configured" from "model answered with truncated
    JSON" instead of only recording ``planner=heuristic``.
    """
    if not settings.llm_enabled:
        return None
    prompt = (
        f"研究主题：{topic.get('topic_name', '')}\n"
        f"用户给出的关键词：{', '.join(topic.get('keywords') or []) or '（无）'}\n"
        f"想回答的问题：{topic.get('primary_question') or '（无）'}\n\n"
        f"按这个 JSON 结构输出：\n{PLAN_SCHEMA}"
    )
    try:
        result = chat_json(
            settings,
            system=PLAN_SYSTEM,
            user=prompt,
            max_tokens=settings.llm_plan_max_tokens or PLAN_MAX_TOKENS,
        )
    except LLMError as exc:
        if notes is not None:
            notes.append(f"模型规划失败：{exc}")
        return None
    if not isinstance(result, dict):
        return None
    queries = [
        row for row in result.get("queries") or []
        if isinstance(row, dict) and row.get("source") in {"openalex", "arxiv"} and str(row.get("query") or "").strip()
    ]
    keywords = [str(item).strip() for item in result.get("keywords") or [] if str(item).strip()]
    directions = [str(item).strip() for item in result.get("directions") or [] if str(item).strip()]
    if not queries:
        return None
    return {"queries": queries[:6], "keywords": keywords[:8], "directions": directions[:6]}


ENRICH_SYSTEM = (
    "你是中文科研情报编辑。基于给出的英文论文元数据，输出简洁、准确、不夸大的中文信息。"
    "严禁编造原文没有的结论、数字或方法。只输出 JSON。"
)

ENRICH_SCHEMA = """{
  "items": [
    {
      "record_id": "原文 record_id",
      "title_zh": "中文标题",
      "abstract_zh": "摘要中文翻译，保留关键方法、数据和结论，200-350 字",
      "plain_summary_zh": "一句话讲清这篇论文做了什么、有什么用，40 字以内",
      "research_direction": "研究方向，4-10 字"
    }
  ]
}"""


#: How many records one enrichment request may carry. A 350-character Chinese
#: abstract costs a few hundred tokens, so a single request covering every
#: candidate would exceed the completion budget and come back truncated (which
#: parses as invalid JSON and silently drops *all* enrichment). Batching keeps
#: each response comfortably inside its budget and localises failures: a bad
#: batch loses its own records, not the whole run.
ENRICH_BATCH_SIZE = 10
#: Completion budget per batch. Must leave room for the answer *after* the
#: model's reasoning tokens, which are billed to the same allowance: with a
#: reasoning model, 3200 tokens is entirely consumed by thinking on a 10-record
#: batch and ``content`` comes back empty. Measured on deepseek-flash: a
#: 10-record batch needs ~1000 reasoning + ~2600 answer tokens.
ENRICH_MAX_TOKENS = 8000


def enrich_records(
    settings: Settings,
    *,
    topic: dict[str, Any],
    records: list[dict[str, Any]],
    notes: list[str] | None = None,
    directions: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Translate and classify the top records. Returns ``{record_id: fields}``.

    Records are enriched in independent batches; a failing batch contributes
    nothing while the remaining batches still succeed. ``notes`` collects one
    human-readable line per degraded batch so the run log can explain *why*
    the Chinese enrichment was partial instead of just reporting a count.
    """
    if not settings.llm_enabled or not records:
        return {}
    limit = max(1, min(settings.llm_max_candidates, len(records)))
    selected = records[:limit]
    batch_size = max(1, settings.llm_enrich_batch_size or ENRICH_BATCH_SIZE)
    enriched: dict[str, dict[str, Any]] = {}
    for start in range(0, len(selected), batch_size):
        enriched.update(
            _enrich_batch(
                settings,
                topic=topic,
                batch=selected[start : start + batch_size],
                notes=notes,
                directions=directions,
            )
        )
    return enriched


_DIRECTION_PUNCTUATION = " \t\r\n：:，,。.、；;（）()【】[]"


def _squash(text: str) -> str:
    return "".join(char for char in text.lower() if char not in _DIRECTION_PUNCTUATION)


def _direction_labels(directions: list[str] | None) -> list[str]:
    """Reduce planning directions to the short label the model can echo back.

    The planner writes ``"基准设计与构建：任务来源、容器化、测试信号…"``; only the
    part before the colon is a usable category name (and it fits the 4–10
    character budget the enrichment schema asks for).
    """
    labels: list[str] = []
    for item in directions or []:
        text = " ".join(str(item).split())
        for separator in ("：", ":"):
            if separator in text:
                text = text.split(separator, 1)[0].strip()
                break
        if text and text not in labels:
            labels.append(text)
    return labels


def _canonical_direction(value: str, allowed: list[str]) -> str:
    """Snap a model label onto the planned taxonomy; anything else is 'other'."""
    text = _squash(value)
    if not text:
        return OTHER_DIRECTION
    for item in allowed:
        if _squash(item) == text:
            return item
    # Models often drop a qualifier ("基准设计与构建" for "基准设计与构建方法"),
    # so accept containment in either direction before giving up.
    for item in allowed:
        squashed = _squash(item)
        if squashed and (squashed in text or text in squashed):
            return item
    return OTHER_DIRECTION


def _enrich_batch(
    settings: Settings,
    *,
    topic: dict[str, Any],
    batch: list[dict[str, Any]],
    notes: list[str] | None = None,
    directions: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    if not batch:
        return {}
    allowed = _direction_labels(directions)
    compact = [
        {
            "record_id": row.get("record_id"),
            "title": row.get("title"),
            "abstract": (row.get("abstract") or "")[:1200],
            "source": row.get("source"),
            "published_at": row.get("published_at"),
        }
        for row in batch
    ]
    direction_rule = ""
    if allowed:
        direction_rule = (
            "research_direction 必须从下列清单中原样选择，禁止改写或自创；"
            f"都不合适时填「{OTHER_DIRECTION}」：\n"
            + "\n".join(f"- {item}" for item in allowed)
            + "\n\n"
        )
    prompt = (
        f"研究主题：{topic.get('topic_name', '')}\n"
        f"用户关注点：{topic.get('primary_question') or '（无）'}\n"
        f"{direction_rule}"
        f"请处理以下 {len(compact)} 篇论文：\n"
        f"{json.dumps(compact, ensure_ascii=False)}\n\n"
        f"按这个 JSON 结构输出，items 数量必须与输入一致：\n{ENRICH_SCHEMA}"
    )
    try:
        result = chat_json(
            settings,
            system=ENRICH_SYSTEM,
            user=prompt,
            max_tokens=settings.llm_enrich_max_tokens or ENRICH_MAX_TOKENS,
        )
    except LLMTruncated as exc:
        if len(batch) <= 1:
            if notes is not None:
                notes.append(f"单篇批次输出被截断（{exc}），已跳过")
            return {}
        # Budget scales with prompt size, so halving the batch is the retry
        # that actually changes the outcome.
        mid = len(batch) // 2
        if notes is not None:
            notes.append(f"{len(batch)} 篇批次输出被截断，拆成 {mid}+{len(batch) - mid} 重试")
        left = _enrich_batch(settings, topic=topic, batch=batch[:mid], notes=notes)
        right = _enrich_batch(settings, topic=topic, batch=batch[mid:], notes=notes)
        return {**left, **right}
    except LLMError as exc:
        if notes is not None:
            notes.append(f"{len(batch)} 篇批次调用失败：{exc}")
        return {}
    items = result.get("items") if isinstance(result, dict) else None
    if not isinstance(items, list):
        if notes is not None:
            notes.append(f"{len(batch)} 篇批次返回结构不符合预期（缺少 items 数组），已跳过")
        return {}
    known = {str(row.get("record_id")) for row in batch}
    enriched: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        record_id = str(item.get("record_id") or "")
        if record_id not in known:
            continue
        fields = {
            key: str(item.get(key) or "").strip()
            for key in ("title_zh", "abstract_zh", "plain_summary_zh", "research_direction")
        }
        if allowed:
            fields["research_direction"] = _canonical_direction(fields.get("research_direction", ""), allowed)
        enriched[record_id] = {key: value for key, value in fields.items() if value}
    return enriched


# ------------------------------------------------------------------ report

REPORT_ANALYSIS_SYSTEM = (
    "你是中文科研情报编辑。只依据给定论文摘要写作，严禁编造原文没有的方法、数据或结论。"
    "中文栏位只写中文，英文栏位只写英文。只输出 JSON。"
)

REPORT_ANALYSIS_SCHEMA = """{
  "items": [
    {
      "record_id": "原文 record_id",
      "abstract_zh": "仅当输入缺少中文摘要时提供，120-200 字；已有则省略",
      "impact_zh": "研究与应用影响，80-150 字中文",
      "impact_en": "Research and application impact, 60-120 words",
      "method_category": "研究方法或观点分类，4-10 字中文"
    }
  ]
}"""

#: Papers per report-analysis request. Each item returns two impact paragraphs,
#: so the batch is half the enrichment batch.
REPORT_BATCH_SIZE = 5
REPORT_MAX_TOKENS = 8000

REPORT_ANALYSIS_RULE = (
    "impact_zh 与 impact_en 必须基于摘要中真实出现的内容；摘要没有提到应用场景时，"
    "如实说明“摘要未展开应用影响”。items 数量必须与输入一致。\n\n"
)


def analyze_report_papers(
    settings: Settings,
    *,
    topic: dict[str, Any],
    papers: list[dict[str, Any]],
    notes: list[str] | None = None,
    directions: list[str] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, dict[str, str]]:
    """Extract the bilingual report fields that enrichment does not produce.

    Enrichment already gives ``title_zh`` / ``abstract_zh``; the integrated
    report additionally needs a research-and-application impact paragraph in
    both languages and a category label per paper. Batches are independent so
    one bad request only loses its own papers.

    ``on_progress(done, total)`` fires after each batch so the console can show
    real progress instead of a spinner with no idea how long the wait is.
    """
    if not settings.llm_enabled or not papers:
        return {}
    batch_size = max(1, settings.llm_report_batch_size or REPORT_BATCH_SIZE)
    analysis: dict[str, dict[str, str]] = {}
    for start in range(0, len(papers), batch_size):
        analysis.update(
            _analyze_report_batch(
                settings,
                topic=topic,
                batch=papers[start : start + batch_size],
                notes=notes,
                directions=directions,
            )
        )
        if on_progress is not None:
            on_progress(min(start + batch_size, len(papers)), len(papers))
    return analysis


def _analyze_report_batch(
    settings: Settings,
    *,
    topic: dict[str, Any],
    batch: list[dict[str, Any]],
    notes: list[str] | None = None,
    directions: list[str] | None = None,
) -> dict[str, dict[str, str]]:
    if not batch:
        return {}
    allowed = _direction_labels(directions)
    compact = [
        {
            "record_id": row.get("record_id"),
            "title": row.get("title"),
            "abstract": (row.get("abstract") or "")[:1200],
            "abstract_zh": (row.get("abstract_zh") or "")[:600],
            "method_category": row.get("research_direction") or "",
        }
        for row in batch
    ]
    direction_rule = ""
    if allowed:
        direction_rule = (
            "method_category 必须从下列清单中原样选择，禁止改写或自创；"
            f"都不合适时填「{OTHER_DIRECTION}」：\n"
            + "\n".join(f"- {item}" for item in allowed)
            + "\n\n"
        )
    prompt = (
        f"研究主题：{topic.get('topic_name', '')}\n"
        f"用户关注点：{topic.get('primary_question') or '（无）'}\n"
        f"{direction_rule}"
        f"请为以下 {len(compact)} 篇论文提炼报告字段：\n"
        f"{json.dumps(compact, ensure_ascii=False)}\n\n"
        f"{REPORT_ANALYSIS_RULE}"
        f"按这个 JSON 结构输出：\n{REPORT_ANALYSIS_SCHEMA}"
    )
    try:
        result = chat_json(
            settings,
            system=REPORT_ANALYSIS_SYSTEM,
            user=prompt,
            max_tokens=settings.llm_report_max_tokens or REPORT_MAX_TOKENS,
            timeout=settings.llm_report_timeout_seconds,
        )
    except LLMTruncated as exc:
        if len(batch) <= 1:
            if notes is not None:
                notes.append(f"报告分析单篇批次输出被截断（{exc}），已跳过")
            return {}
        mid = len(batch) // 2
        if notes is not None:
            notes.append(f"报告分析 {len(batch)} 篇批次输出被截断，拆成 {mid}+{len(batch) - mid} 重试")
        left = _analyze_report_batch(settings, topic=topic, batch=batch[:mid], notes=notes, directions=directions)
        right = _analyze_report_batch(settings, topic=topic, batch=batch[mid:], notes=notes, directions=directions)
        return {**left, **right}
    except LLMError as exc:
        if notes is not None:
            notes.append(f"报告分析 {len(batch)} 篇批次调用失败：{exc}")
        return {}
    items = result.get("items") if isinstance(result, dict) else None
    if not isinstance(items, list):
        if notes is not None:
            notes.append(f"报告分析 {len(batch)} 篇批次返回结构不符合预期（缺少 items 数组），已跳过")
        return {}
    known = {str(row.get("record_id")) for row in batch}
    analysis: dict[str, dict[str, str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        record_id = str(item.get("record_id") or "")
        if record_id not in known:
            continue
        fields = {
            key: str(item.get(key) or "").strip()
            for key in ("abstract_zh", "impact_zh", "impact_en", "method_category")
        }
        if allowed and fields["method_category"]:
            fields["method_category"] = _canonical_direction(fields["method_category"], allowed)
        analysis[record_id] = {key: value for key, value in fields.items() if value}
    return analysis


REPORT_SYSTEM = (
    "你是资深科研综述作者。你只能依据给定论文摘要写作，严禁编造原文没有的方法、数据、样本量或结论。"
    "中文栏位只写中文，英文栏位只写英文。只输出 JSON。"
)

REPORT_SCHEMA = """{
  "title_zh": "报告标题，中文",
  "title_en": "Report title in English",
  "executive_summary_zh": "执行摘要，300-500 字",
  "executive_summary_en": "Executive summary, 150-250 words",
  "sections_zh": {
    "research_question": "研究问题，80-200 字",
    "methods": "提出的方法，80-200 字",
    "samples": "样本与数据，80-200 字",
    "results": "主要结果，80-200 字",
    "limitations": "局限，80-200 字",
    "business_impact": "应用与业务影响，80-200 字"
  },
  "sections_en": {
    "research_question": "80-150 words",
    "methods": "80-150 words",
    "samples": "80-150 words",
    "results": "80-150 words",
    "limitations": "80-150 words",
    "business_impact": "80-150 words"
  },
  "subthemes": [
    {
      "title_zh": "方向名称，4-12 字",
      "title_en": "Approach family name",
      "summary_zh": "该方向的综合评述，80-150 字",
      "summary_en": "Synthesis of this approach family, 60-120 words",
      "paper_ids": ["输入中出现的 record_id"]
    }
  ]
}"""

#: Synthesis is the one JSON that must describe every paper at once: an
#: executive summary, six bilingual sections and 3–6 subthemes. On a reasoning
#: model the chain of thought comes out of this same allowance — a real run
#: spent 4 981 reasoning tokens on 20 papers and 7 687 on 5, so 8 000 left no
#: room for the JSON. 16 000 covers reasoning plus the full answer and is
#: accepted by the configured provider; truncation escalates the budget up to
#: REPORT_SYNTHESIS_TOKEN_CAP instead of shrinking the paper set.
REPORT_SYNTHESIS_MAX_TOKENS = 16000
#: Provider-verified ceiling for the escalation retries (16k/32k/64k all
#: answered 200 OK). Beyond this the caller falls back to deterministic
#: method-family grouping rather than burning more reasoning tokens.
REPORT_SYNTHESIS_TOKEN_CAP = 64000
REPORT_SYNTHESIS_TIMEOUT_SECONDS = 180.0

REPORT_SYNTHESIS_RULES = (
    "写作规则：\n"
    "- 输出 3-6 个 subthemes，按方法或观点聚类，不要按来源或年份聚类。\n"
    "- 每篇论文的 record_id 必须且只能出现在一个 subtheme 的 paper_ids 中；不得使用输入之外的 id。\n"
    "- 所有六个 sections 键都必须有实质内容，不能只写一句话或留空。\n"
    "- 摘要没有提供的信息（如具体样本量、百分比）不得编造；不确定时用定性表述。\n"
    "- 中文栏位不要整句英文，英文栏位不要中文。\n"
    "- 只写研究内容本身。禁止出现检索流程、来源数量、时间窗口、去重、纳入篇数、"
    "全文覆盖率等元信息（例如“本报告依据 N 篇论文摘要”“本次检索…”）；"
    "这类句子必须改写为对研究内容、方法或结论的陈述。\n\n"
)


REPORT_PAYLOAD_KEYS = ("subthemes", "executive_summary_zh", "sections_zh", "title_zh")


def _coerce_report_payload(result: Any) -> dict[str, Any] | None:
    """Unwrap the containers models occasionally add around the report.

    Observed in practice: the same prompt returns a plain object most runs and
    an array containing that object on others; a double-encoded JSON string is
    also possible. Anything without a recognizable report key is rejected, so
    an unrelated object cannot silently become a generic report.
    """
    if isinstance(result, dict):
        if any(key in result for key in REPORT_PAYLOAD_KEYS):
            return result
        for key in ("report", "result", "data"):
            nested = result.get(key)
            if isinstance(nested, dict) and any(key in nested for key in REPORT_PAYLOAD_KEYS):
                return nested
        return None
    if isinstance(result, list):
        for item in result:
            payload = _coerce_report_payload(item)
            if payload is not None:
                return payload
        return None
    if isinstance(result, str):
        try:
            return _coerce_report_payload(json.loads(result))
        except json.JSONDecodeError:
            return None
    return None


def synthesize_report(
    settings: Settings,
    *,
    topic: dict[str, Any],
    papers: list[dict[str, Any]],
    directions: list[str] | None = None,
    notes: list[str] | None = None,
    correction: str = "",
    max_tokens: int | None = None,
) -> dict[str, Any] | None:
    """Write the integrated report from the paper set in one JSON call.

    On truncation the *budget* is raised and the paper set is kept intact.
    Reasoning models truncate because of the thinking, not the input size — a
    real run spent more reasoning tokens on 5 papers than on 20 — so halving
    the papers cannot fix it and only pushed the dropped papers into the
    report's "other" bucket. When the budget cap is still not enough, return
    ``{}``: the caller then keeps every card and groups the papers by their
    method family instead of emitting no report at all. ``None`` is reserved
    for "the model gave us nothing usable" (network error, malformed JSON),
    where skipping the report is the honest outcome.

    ``correction`` carries a targeted instruction for the retry that follows a
    contract violation (for example the model narrating the retrieval process
    instead of the literature) without changing the paper set.
    """
    if not settings.llm_enabled or not papers:
        return None
    budget = max_tokens or settings.llm_report_synthesis_max_tokens or REPORT_SYNTHESIS_MAX_TOKENS
    compact = [
        {
            "record_id": row.get("record_id"),
            "title": row.get("title"),
            "abstract": (row.get("abstract") or "")[:700],
            "abstract_zh": (row.get("abstract_zh") or "")[:400],
            "method_category": row.get("research_direction") or "",
        }
        for row in papers
    ]
    planned = [str(item).strip() for item in (directions or []) if str(item).strip()]
    direction_rule = ""
    if planned:
        direction_rule = "规划阶段预期的研究方向（可参考，不强制每篇都归入）：\n" + "\n".join(
            f"- {item}" for item in planned
        ) + "\n\n"
    correction_rule = f"{correction}\n\n" if correction else ""
    prompt = (
        f"研究主题：{topic.get('topic_name', '')}\n"
        f"用户关注点：{topic.get('primary_question') or '（无）'}\n"
        f"{direction_rule}"
        f"以下是 {len(compact)} 篇论文：\n"
        f"{json.dumps(compact, ensure_ascii=False)}\n\n"
        f"{correction_rule}"
        f"{REPORT_SYNTHESIS_RULES}"
        f"按这个 JSON 结构输出：\n{REPORT_SCHEMA}"
    )
    try:
        result = chat_json(
            settings,
            system=REPORT_SYSTEM,
            user=prompt,
            max_tokens=budget,
            timeout=settings.llm_report_timeout_seconds or REPORT_SYNTHESIS_TIMEOUT_SECONDS,
        )
    except LLMTruncated as exc:
        if budget >= REPORT_SYNTHESIS_TOKEN_CAP:
            if notes is not None:
                notes.append(
                    f"综合写作在 max_tokens={budget} 下仍被截断（{exc}），"
                    "改用按方法家族的确定性分组，全部文献保留在报告中"
                )
            return {}
        raised = min(REPORT_SYNTHESIS_TOKEN_CAP, budget * 2)
        if notes is not None:
            notes.append(
                f"综合写作输出被截断（{exc}），预算 {budget} 提升至 {raised} 重试，"
                f"论文集保持 {len(papers)} 篇不变"
            )
        return synthesize_report(
            settings,
            topic=topic,
            papers=papers,
            directions=directions,
            notes=notes,
            correction=correction,
            max_tokens=raised,
        )
    except LLMError as exc:
        if notes is not None:
            notes.append(f"综合写作失败：{exc}")
        return None
    payload = _coerce_report_payload(result)
    if payload is None:
        if notes is not None:
            notes.append("综合写作返回结构不符合预期（缺少 subthemes / 摘要 / 小节字段）")
        return None
    if payload is not result and notes is not None:
        notes.append("综合写作返回被数组或字符串包裹，已解包使用")
    return payload
