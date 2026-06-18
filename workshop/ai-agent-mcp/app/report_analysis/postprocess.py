from __future__ import annotations

import html
import json
import os
import re
from dataclasses import asdict
from datetime import datetime
from typing import Any

import boto3

from .config import AWS_REGION, MODEL_ID
from .models import InternalResult


DEFAULT_KEYWORD_ALIASES: dict[str, list[str]] = {
    "몰리브덴": ["몰리브덴", "molybdenum", "MoF6"],
}

DEPARTMENT_COLORS = {
    "경영지원": "#3b82f6",
    "연구": "#8b5cf6",
    "R&D": "#8b5cf6",
    "생산": "#10b981",
    "품질": "#f59e0b",
    "영업": "#ef4444",
    "마케팅": "#ec4899",
    "전략": "#6366f1",
}


def expand_keywords(keyword: str) -> list[str]:
    keyword = keyword.strip()
    aliases = DEFAULT_KEYWORD_ALIASES.copy()
    try:
        aliases.update(json.loads(os.getenv("KEYWORD_ALIASES_JSON", "{}")))
    except json.JSONDecodeError:
        pass

    expanded = aliases.get(keyword, [keyword])
    seen = set()
    ordered = []
    for term in [keyword, *expanded]:
        normalized = term.strip()
        key = normalized.lower()
        if normalized and key not in seen:
            ordered.append(normalized)
            seen.add(key)
    return ordered


def filter_and_rank_results(
    keyword: str,
    results: list[InternalResult],
    limit: int | None = None,
    use_llm_reranker: bool | None = None,
) -> tuple[list[InternalResult], list[InternalResult]]:
    aliases = expand_keywords(keyword)
    judged = []
    for item in results:
        metadata = dict(item.metadata)
        relevance_score, confidence, reason = judge_relevance(
            keyword,
            aliases,
            item.content,
            item.score,
            use_llm_reranker=use_llm_reranker,
        )
        metadata.update(
            {
                "_keyword": keyword,
                "_keyword_aliases": aliases,
                "_text_match": has_direct_match(item.content, aliases),
                "_confidence": confidence,
                "_relevance_score": relevance_score,
                "_relevance_reason": reason,
            }
        )
        judged.append(
            InternalResult(
                content=item.content,
                source=item.source,
                score=item.score,
                metadata=metadata,
            )
        )

    judged.sort(
        key=lambda item: (
            {"High": 3, "Medium": 2, "Low": 1}.get(item.metadata.get("_confidence"), 0),
            item.metadata.get("_relevance_score", 0.0),
            item.score or 0.0,
        ),
        reverse=True,
    )
    filtered = [item for item in judged if item.metadata.get("_confidence") in {"High", "Medium"}]
    possible = [item for item in judged if item.metadata.get("_confidence") == "Low"]
    return filtered[:limit] if limit else filtered, possible


def judge_relevance(
    keyword: str,
    aliases: list[str],
    content: str,
    kb_score: float | None,
    use_llm_reranker: bool | None = None,
) -> tuple[float, str, str]:
    text_match = has_direct_match(content, aliases)
    score = float(kb_score or 0.0)
    if text_match:
        base_score = max(0.72, min(0.98, score + 0.45))
        confidence = "High" if base_score >= 0.82 else "Medium"
        reason = "keyword_or_alias_found_in_chunk"
    elif score >= 0.55 and use_llm_reranker:
        base_score = min(0.7, score)
        confidence = "Medium"
        reason = "kb_score_without_text_match_pending_llm"
    else:
        base_score = min(0.49, score)
        confidence = "Low"
        reason = "no_keyword_or_alias_match"

    if use_llm_reranker is None:
        use_llm_reranker = os.getenv("ENABLE_LLM_RERANKER", "false").lower() == "true"
    if use_llm_reranker:
        llm_result = _llm_rerank(keyword, content)
        if llm_result:
            return llm_result
    return round(base_score, 4), confidence, reason


def has_direct_match(content: str, terms: list[str]) -> bool:
    haystack = content.lower()
    for term in terms:
        if not term:
            continue
        needle = term.lower()
        if re.fullmatch(r"[A-Za-z0-9]+", term):
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(needle)}(?![A-Za-z0-9])", haystack):
                return True
        elif needle in haystack:
            return True
    return False


def _llm_rerank(keyword: str, content: str) -> tuple[float, str, str] | None:
    prompt = (
        "Judge whether this report chunk is directly about the keyword. "
        "Return strict JSON only: "
        '{"relevance_score":0.0,"confidence":"High|Medium|Low","reason":"short"}\n\n'
        f"Keyword: {keyword}\n"
        f"Chunk:\n{content[:4000]}"
    )
    try:
        client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        response = client.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 300,
                    "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}],
                }
            ),
        )
        payload = json.loads(response["body"].read())
        text = payload["content"][0]["text"]
        parsed = json.loads(text[text.find("{") : text.rfind("}") + 1])
        confidence = parsed.get("confidence", "Low")
        if confidence not in {"High", "Medium", "Low"}:
            confidence = "Low"
        return (
            round(float(parsed.get("relevance_score", 0.0)), 4),
            confidence,
            f"llm_reranker:{parsed.get('reason', '')}",
        )
    except Exception:
        return None


def build_structured_output(
    keyword: str,
    results: list[InternalResult],
    possible_results: list[InternalResult] | None = None,
    search_type: str = "HYBRID",
    output_format: str = "structured",
) -> dict[str, Any]:
    possible_results = possible_results or []
    items = [extract_timeline_item(item) for item in results]
    items.sort(key=lambda item: item["date"] or "9999-99-99")
    inferred_filtered_count = max(
        [int(item.metadata.get("_filtered_out_count", 0) or 0) for item in results] or [0]
    )
    filtered_out_count = len(possible_results) if possible_results else inferred_filtered_count
    payload = {
        "results": items,
        "metadata": {
            "keyword": keyword,
            "total_count": len(items),
            "search_type": search_type,
            "filtered_out_count": filtered_out_count,
            "possible_count": len(possible_results),
        },
    }
    if output_format == "timeline_html":
        payload["timeline_html"] = render_timeline_html(payload)
    return payload


def extract_timeline_item(result: InternalResult) -> dict[str, Any]:
    content = " ".join(result.content.split())
    date = extract_date(content, result.metadata)
    title = extract_title(content, result.source)
    department = extract_department(content, result.metadata)
    summary = summarize(content)
    return {
        "date": date,
        "title": title,
        "department": department,
        "department_color": department_color(department),
        "tags": extract_tags(content, result.metadata),
        "summary": summary,
        "details": extract_details(content),
        "source": result.source,
        "source_url": result.source if result.source.startswith(("http://", "https://", "s3://")) else "",
        "relevance_score": result.metadata.get("_relevance_score", result.score),
        "confidence": result.metadata.get("_confidence", "Low"),
        "raw_score": result.score,
        "metadata": result.metadata,
    }


def extract_date(content: str, metadata: dict[str, Any]) -> str:
    for key in ("date", "report_date", "created_at", "last_modified"):
        value = metadata.get(key)
        if value:
            parsed = normalize_date(str(value))
            if parsed:
                return parsed
    match = re.search(r"(20\d{2})[.\-/년 ]\s*(\d{1,2})[.\-/월 ]\s*(\d{1,2})", content)
    if match:
        return normalize_date("-".join(match.groups()))
    return ""


def normalize_date(value: str) -> str:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y %m %d"):
        try:
            return datetime.strptime(value[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    match = re.search(r"(20\d{2}).?(\d{1,2}).?(\d{1,2})", value)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return ""


def extract_title(content: str, source: str) -> str:
    candidates = re.split(r"[\n\r]| {2,}|[|]", content)
    for candidate in candidates:
        cleaned = candidate.strip(" -:\t")
        if 8 <= len(cleaned) <= 80:
            return cleaned
    source_name = source.rsplit("/", 1)[-1] if source else "관련 보고"
    return source_name[:80]


def extract_department(content: str, metadata: dict[str, Any]) -> str:
    for key in ("department", "dept", "org", "organization"):
        if metadata.get(key):
            return str(metadata[key])
    match = re.search(r"([가-힣A-Za-z& ]{2,20}(본부|팀|실|센터|그룹))", content)
    return match.group(1).strip() if match else "미분류"


def department_color(department: str) -> str:
    for key, color in DEPARTMENT_COLORS.items():
        if key in department:
            return color
    return "#64748b"


def extract_tags(content: str, metadata: dict[str, Any]) -> list[str]:
    tags = []
    for key in ("tags", "keywords"):
        value = metadata.get(key)
        if isinstance(value, list):
            tags.extend(str(item) for item in value)
        elif isinstance(value, str):
            tags.extend(part.strip() for part in re.split(r"[,;/]", value) if part.strip())
    for token in ("IP", "특허", "R&BD", "TF", "투자", "품질", "공정", "원가", "개발"):
        if token.lower() in content.lower() and token not in tags:
            tags.append(token)
    return tags[:5]


def summarize(content: str, max_len: int = 160) -> str:
    sentence = re.split(r"(?<=[.!?。])\s+|다\.\s*", content)[0].strip()
    if len(sentence) < 30:
        sentence = content[:max_len].strip()
    return sentence[:max_len].rstrip()


def extract_details(content: str) -> list[str]:
    parts = [part.strip(" -\t") for part in re.split(r"[;\n\r]|(?<=다\.)\s+", content)]
    details = [part for part in parts if 12 <= len(part) <= 180]
    if not details and content:
        details = [content[:180].strip()]
    return details[:4]


def render_timeline_html(payload: dict[str, Any]) -> str:
    cards = []
    for item in payload["results"]:
        tags = "".join(f"<span class='tag'>{html.escape(tag)}</span>" for tag in item["tags"])
        details = "".join(f"<li>{html.escape(detail)}</li>" for detail in item.get("details", []))
        source = html.escape(item.get("source", ""))
        source_url = item.get("source_url") or ""
        source_html = (
            f"<a href='{html.escape(source_url)}' target='_blank' rel='noreferrer'>{source}</a>"
            if source_url.startswith(("http://", "https://"))
            else f"<span>{source}</span>"
        )
        cards.append(
            f"""
            <article class="timeline-card" style="--dept:{html.escape(item['department_color'])}">
              <div class="marker"></div>
              <div class="card-main">
                <div class="meta">
                  <time>{html.escape(item.get('date') or '날짜 미분류')}</time>
                  <span class="dept">{html.escape(item['department'])}</span>
                  <span class="confidence">{html.escape(item['confidence'])}</span>
                </div>
                <h3>{html.escape(item['title'])}</h3>
                <p>{html.escape(item['summary'])}</p>
                <div class="tags">{tags}</div>
                <details>
                  <summary>상세 내용</summary>
                  <ul>{details}</ul>
                </details>
                <div class="source">출처: {source_html}</div>
              </div>
            </article>
            """
        )
    return f"""
<section class="report-timeline">
  <style>
    .report-timeline {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; color:#0f172a; }}
    .timeline-card {{ position:relative; display:grid; grid-template-columns:18px 1fr; gap:14px; margin:0 0 14px; }}
    .timeline-card:before {{ content:""; position:absolute; left:8px; top:20px; bottom:-16px; width:2px; background:#e2e8f0; }}
    .marker {{ z-index:1; width:16px; height:16px; margin-top:18px; border-radius:50%; background:var(--dept); box-shadow:0 0 0 4px #fff; }}
    .card-main {{ border:1px solid #e2e8f0; border-left:4px solid var(--dept); border-radius:8px; padding:14px 16px; background:#fff; }}
    .meta {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; font-size:12px; color:#64748b; }}
    .dept,.confidence,.tag {{ border-radius:999px; padding:2px 8px; background:#f1f5f9; }}
    h3 {{ margin:8px 0 6px; font-size:17px; line-height:1.35; }}
    p {{ margin:0 0 10px; line-height:1.55; }}
    .tags {{ display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px; }}
    details {{ margin-top:8px; }}
    summary {{ cursor:pointer; font-weight:600; }}
    li {{ margin:4px 0; }}
    .source {{ margin-top:10px; font-size:12px; color:#64748b; word-break:break-all; }}
  </style>
  {''.join(cards)}
</section>
"""


def serialize_internal_results(results: list[InternalResult]) -> list[dict[str, Any]]:
    return [asdict(item) for item in results]
