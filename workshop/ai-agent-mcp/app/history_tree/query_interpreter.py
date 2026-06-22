from __future__ import annotations

import json
import re
from typing import Any

import boto3

from app.report_analysis.config import AWS_REGION, MODEL_ID

from .schemas import SearchPlan


SYSTEM_PROMPT = """
You are the Query Interpreter Agent for an internal report-history search system.
Read the user's query and return strict JSON only.

Goals:
- Identify the user's intent.
- Pick one primary keyword or topic.
- The internal reports are mostly Korean business documents. Prefer Korean
  keywords, Korean synonyms, Korean abbreviations, and internal business terms.
- Suggest high-precision aliases and search queries that would actually appear
  in Korean reports.
- English aliases are allowed only as secondary terms when they are standard
  product/material names, chemical formulas, customer names, or common acronyms.
- Do not translate a Korean keyword into English unless that English term is
  likely to appear in the original documents.
- Avoid short ambiguous aliases unless they are part of a longer compound term.
- Keep the primary_keyword in the same language as the user's query when
  possible.
- Put Korean search queries before English search queries.

Schema:
{
  "intent": "history_search",
  "primary_keyword": "string",
  "aliases": ["string"],
  "search_queries": ["string"],
  "negative_terms": ["string"],
  "rationale": "short string"
}
""".strip()


def interpret_query(query: str) -> SearchPlan:
    query = str(query).strip()
    if not query:
        raise ValueError("keyword/query is required")

    parsed = _invoke_interpreter(query) or {}
    primary_keyword = str(parsed.get("primary_keyword") or query).strip()
    aliases = sanitize_terms(parsed.get("aliases") or [], primary_keyword)
    search_queries = sanitize_terms(parsed.get("search_queries") or [], primary_keyword)
    aliases = prioritize_korean_terms(aliases)
    search_queries = prioritize_korean_terms(merge_terms([primary_keyword, *search_queries, *aliases]))

    return SearchPlan(
        original_query=query,
        intent=str(parsed.get("intent") or "history_search"),
        primary_keyword=primary_keyword,
        aliases=aliases,
        search_queries=search_queries,
        negative_terms=sanitize_terms(parsed.get("negative_terms") or [], primary_keyword, allow_short=True),
        rationale=str(parsed.get("rationale") or ""),
    )


def sanitize_terms(values: Any, primary_keyword: str, allow_short: bool = False) -> list[str]:
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        term = str(value).strip()
        if not term or term == primary_keyword:
            continue
        if not allow_short and is_ambiguous_short_alias(term):
            continue
        if term.lower() not in {item.lower() for item in result}:
            result.append(term)
    return result[:8]


def prioritize_korean_terms(terms: list[str]) -> list[str]:
    return sorted(
        terms,
        key=lambda term: (
            0 if contains_korean(term) else 1,
            0 if not is_ascii_only(term) else 1,
            len(term),
        ),
    )


def contains_korean(term: str) -> bool:
    return bool(re.search(r"[가-힣]", term))


def is_ascii_only(term: str) -> bool:
    try:
        term.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def is_ambiguous_short_alias(term: str) -> bool:
    compact = re.sub(r"\s+", "", term)
    if re.fullmatch(r"[A-Za-z]{1,2}", compact):
        return True
    if re.fullmatch(r"[A-Za-z]{1,2}\d?", compact):
        return True
    return False


def merge_terms(terms: list[str]) -> list[str]:
    result = []
    seen = set()
    for term in terms:
        normalized = str(term).strip()
        key = normalized.lower()
        if normalized and key not in seen:
            result.append(normalized)
            seen.add(key)
    return result[:8]


def _invoke_interpreter(query: str) -> dict[str, Any] | None:
    prompt = f"User query: {query}"
    try:
        client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        response = client.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 700,
                    "temperature": 0,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": prompt}],
                }
            ),
        )
        payload = json.loads(response["body"].read())
        text = payload["content"][0]["text"]
        return json.loads(text[text.find("{") : text.rfind("}") + 1])
    except Exception:
        return None
