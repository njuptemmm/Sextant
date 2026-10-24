"""Optional LLM enrichment over an OpenAI-compatible chat-completions API.

The whole application must work without an LLM. Every function here degrades
to a deterministic fallback or an empty result instead of raising.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from sextant.config import Settings


class LLMError(RuntimeError):
    pass


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
                settings.llm_timeout_seconds,
            )
            content = (
                response.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            return _extract_json(content)
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


def plan_queries(settings: Settings, topic: dict[str, Any]) -> dict[str, Any] | None:
    """Ask the model for retrieval variants. Returns None when unavailable."""
    if not settings.llm_enabled:
        return None
    prompt = (
        f"研究主题：{topic.get('topic_name', '')}\n"
        f"用户给出的关键词：{', '.join(topic.get('keywords') or []) or '（无）'}\n"
        f"想回答的问题：{topic.get('primary_question') or '（无）'}\n\n"
        f"按这个 JSON 结构输出：\n{PLAN_SCHEMA}"
    )
    try:
        result = chat_json(settings, system=PLAN_SYSTEM, user=prompt, max_tokens=900)
    except LLMError:
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
ENRICH_MAX_TOKENS = 3200


def enrich_records(
    settings: Settings,
    *,
    topic: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Translate and classify the top records. Returns ``{record_id: fields}``.

    Records are enriched in independent batches; a failing batch contributes
    nothing while the remaining batches still succeed.
    """
    if not settings.llm_enabled or not records:
        return {}
    limit = max(1, min(settings.llm_max_candidates, len(records)))
    selected = records[:limit]
    enriched: dict[str, dict[str, Any]] = {}
    for start in range(0, len(selected), ENRICH_BATCH_SIZE):
        enriched.update(
            _enrich_batch(settings, topic=topic, batch=selected[start : start + ENRICH_BATCH_SIZE])
        )
    return enriched


def _enrich_batch(
    settings: Settings,
    *,
    topic: dict[str, Any],
    batch: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not batch:
        return {}
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
    prompt = (
        f"研究主题：{topic.get('topic_name', '')}\n"
        f"用户关注点：{topic.get('primary_question') or '（无）'}\n"
        f"请处理以下 {len(compact)} 篇论文：\n"
        f"{json.dumps(compact, ensure_ascii=False)}\n\n"
        f"按这个 JSON 结构输出，items 数量必须与输入一致：\n{ENRICH_SCHEMA}"
    )
    try:
        result = chat_json(settings, system=ENRICH_SYSTEM, user=prompt, max_tokens=ENRICH_MAX_TOKENS)
    except LLMError:
        return {}
    items = result.get("items") if isinstance(result, dict) else None
    if not isinstance(items, list):
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
        enriched[record_id] = {key: value for key, value in fields.items() if value}
    return enriched
