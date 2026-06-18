from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

import boto3

from app.report_analysis.config import AWS_REGION, MODEL_ID

from .schemas import HistoryTreeResponse, SearchPlan, TimelineItem, ValidatedChunk


def compose_timeline(
    plan: SearchPlan,
    validated_chunks: list[ValidatedChunk],
    diagnostics: dict[str, Any],
    search_type: str,
) -> HistoryTreeResponse:
    items = [extract_item(plan, item) for item in validated_chunks]
    items.sort(key=lambda item: item.date or "9999-99-99")
    return HistoryTreeResponse(
        results=items,
        metadata={
            "keyword": plan.primary_keyword,
            "original_query": plan.original_query,
            "aliases": plan.aliases,
            "search_queries": plan.search_queries,
            "total_count": len(items),
            "search_type": search_type,
            "filtered_out_count": diagnostics.get("filtered_out_count", 0),
            "candidate_count": diagnostics.get("candidate_count", 0),
            "deduped_count": diagnostics.get("deduped_count", 0),
        },
        diagnostics=diagnostics,
    )


def extract_item(plan: SearchPlan, validated: ValidatedChunk) -> TimelineItem:
    chunk = validated.chunk
    content = " ".join(chunk.content.split())
    llm = extract_with_llm(plan, content)
    title = llm.get("title") or fallback_title(content, chunk.source)
    summary = llm.get("summary") or summarize(content)
    details = llm.get("details") if isinstance(llm.get("details"), list) else fallback_details(content)
    tags = llm.get("tags") if isinstance(llm.get("tags"), list) else fallback_tags(content)
    department = llm.get("department") or extract_department(content, chunk.metadata)
    date = llm.get("date") or extract_date(content, chunk.metadata)

    return TimelineItem(
        date=str(date or ""),
        title=str(title)[:120],
        department=str(department or "Unclassified"),
        tags=[str(tag)[:30] for tag in tags[:6]],
        summary=str(summary)[:400],
        details=[str(detail)[:220] for detail in details[:6]],
        source=chunk.source.rsplit("/", 1)[-1] if chunk.source else "",
        source_uri=chunk.source,
        confidence=validated.validation.confidence,
        relevance_score=validated.validation.relevance_score,
        raw_score=chunk.kb_score,
        metadata={
            **chunk.metadata,
            "matched_terms": validated.validation.matched_terms,
            "text_confidence": validated.validation.text_confidence,
            "llm_relevance_score": validated.validation.llm_relevance_score,
            "validation_reason": validated.validation.reason,
        },
    )


def extract_with_llm(plan: SearchPlan, content: str) -> dict[str, Any]:
    prompt = (
        "Extract timeline-card data from this internal report chunk. "
        "Return strict JSON only. Use Korean if the source is Korean.\n\n"
        f"Search keyword: {plan.primary_keyword}\n"
        f"Aliases: {plan.aliases}\n"
        f"Chunk:\n{content[:4500]}\n\n"
        "Schema:\n"
        "{"
        '"date":"YYYY-MM-DD or empty",'
        '"title":"large topic title",'
        '"department":"department or empty",'
        '"tags":["short tags"],'
        '"summary":"1-2 sentence summary",'
        '"details":["bullet detail"]'
        "}"
    )
    try:
        client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        response = client.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 900,
                    "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}],
                }
            ),
        )
        payload = json.loads(response["body"].read())
        text = payload["content"][0]["text"]
        return json.loads(text[text.find("{") : text.rfind("}") + 1])
    except Exception:
        return {}


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
    match = re.search(r"\((\d{2})(\d{2})(\d{2})\)", content)
    if match:
        yy, mm, dd = match.groups()
        return f"20{yy}-{mm}-{dd}"
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


def fallback_title(content: str, source: str) -> str:
    for candidate in re.split(r"[\n\r]| {2,}|[|]", content):
        cleaned = candidate.strip(" -:\t")
        if 8 <= len(cleaned) <= 90:
            return cleaned
    return source.rsplit("/", 1)[-1] if source else "Related report"


def extract_department(content: str, metadata: dict[str, Any]) -> str:
    for key in ("department", "dept", "org", "organization"):
        if metadata.get(key):
            return str(metadata[key])
    match = re.search(r"([가-힣A-Za-z& ]{2,20}(본부|팀|실|센터|그룹|부문))", content)
    return match.group(1).strip() if match else "Unclassified"


def summarize(content: str, max_len: int = 220) -> str:
    sentence = re.split(r"(?<=[.!?。])\s+|다\.\s*", content)[0].strip()
    if len(sentence) < 30:
        sentence = content[:max_len].strip()
    return sentence[:max_len].rstrip()


def fallback_details(content: str) -> list[str]:
    parts = [part.strip(" -\t") for part in re.split(r"[;\n\r]|(?<=다\.)\s+", content)]
    details = [part for part in parts if 12 <= len(part) <= 180]
    if not details and content:
        details = [content[:180].strip()]
    return details[:4]


def fallback_tags(content: str) -> list[str]:
    tags = []
    for token in ("IP", "patent", "R&BD", "TF", "investment", "quality", "process", "cost"):
        if token.lower() in content.lower():
            tags.append(token)
    return tags[:5]
