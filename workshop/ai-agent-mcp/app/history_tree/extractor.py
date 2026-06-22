from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import unquote

import boto3

from app.report_analysis.config import AWS_REGION, MODEL_ID

from .schemas import HistoryTreeResponse, SearchPlan, TimelineItem, ValidatedChunk


DEPARTMENT_KEYWORDS = {
    "경영지원": "경영지원본부",
    "영업": "영업본부",
    "생산": "생산본부",
    "품질": "품질부문",
    "R&BD": "R&BD",
    "연구": "R&BD",
    "개발": "R&BD",
    "구매": "구매실",
}


def compose_timeline(
    plan: SearchPlan,
    validated_chunks: list[ValidatedChunk],
    diagnostics: dict[str, Any],
    search_type: str,
) -> HistoryTreeResponse:
    items = [extract_item(plan, item) for item in validated_chunks]
    items.sort(key=lambda item: (item.date or "9999-99-99", -item.relevance_score))
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
    content = normalize_space(chunk.content)
    matched_terms = validated.validation.matched_terms or [plan.primary_keyword, *plan.aliases]
    focused_content = build_relevant_excerpt(content, matched_terms)

    llm = extract_with_llm(
        plan=plan,
        focused_content=focused_content,
        full_content=content,
        metadata=chunk.metadata,
        source=chunk.source,
    )
    title = llm.get("title") or fallback_title(focused_content, chunk.source, matched_terms)
    summary = llm.get("summary") or summarize(focused_content)
    details = llm.get("details") if isinstance(llm.get("details"), list) else fallback_details(focused_content)
    tags = llm.get("tags") if isinstance(llm.get("tags"), list) else fallback_tags(focused_content)
    department = extract_department(
        content=content,
        metadata=chunk.metadata,
        source=chunk.source,
        llm_department=str(llm.get("department") or ""),
    )
    date = normalize_date(str(llm.get("date") or "")) or extract_date(content, chunk.metadata, chunk.source)

    return TimelineItem(
        date=str(date or ""),
        title=str(title)[:120],
        department=str(department or "기타"),
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
            "focused_excerpt": focused_content[:700],
        },
    )


def normalize_space(text: str) -> str:
    return " ".join(str(text or "").split())


def build_relevant_excerpt(content: str, terms: list[str], window: int = 700) -> str:
    if not content:
        return ""
    lowered = content.lower()
    positions = []
    for term in terms:
        term = str(term or "").strip()
        if not term:
            continue
        index = lowered.find(term.lower())
        if index >= 0:
            positions.append(index)

    if not positions:
        return content[: min(len(content), window * 2)]

    center = min(positions)
    start = max(0, center - window)
    end = min(len(content), center + window)
    excerpt = content[start:end].strip()

    marker_positions = [
        excerpt.rfind("■", 0, min(len(excerpt), window)),
        excerpt.rfind("□", 0, min(len(excerpt), window)),
        excerpt.rfind("▶", 0, min(len(excerpt), window)),
    ]
    marker = max(marker_positions)
    if marker > 0:
        excerpt = excerpt[marker:].strip()
    return excerpt


def extract_with_llm(
    plan: SearchPlan,
    focused_content: str,
    full_content: str,
    metadata: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    prompt = (
        "Extract timeline-card data from this internal report evidence. "
        "Use the focused excerpt for title, summary, details, and tags. "
        "Use source, metadata, and full context only to infer date or department. "
        "Ignore unrelated neighboring agenda items. Return strict JSON only. "
        "Use Korean if the source is Korean.\n\n"
        f"Search keyword: {plan.primary_keyword}\n"
        f"Aliases: {plan.aliases}\n"
        f"Source: {source}\n"
        f"Metadata: {json.dumps(metadata, ensure_ascii=False)[:1200]}\n"
        f"Focused excerpt:\n{focused_content[:3500]}\n\n"
        f"Full context head:\n{full_content[:1200]}\n\n"
        "Schema:\n"
        "{"
        '"date":"YYYY-MM-DD or empty",'
        '"title":"large topic title, not file name",'
        '"department":"department or empty",'
        '"tags":["short tags"],'
        '"summary":"1-2 sentence summary",'
        '"details":["bullet detail directly about the keyword"]'
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


def extract_date(content: str, metadata: dict[str, Any], source: str = "") -> str:
    for key in ("date", "report_date", "created_at", "last_modified"):
        value = metadata.get(key)
        if value:
            parsed = normalize_date(str(value))
            if parsed:
                return parsed

    search_text = f"{content} {unquote(source)}"
    for match in re.finditer(r"(20\d{2})[.\-/년_ ]\s*(\d{1,2})[.\-/월_ ]\s*(\d{1,2})", search_text):
        parsed = build_valid_date(match.group(1), match.group(2), match.group(3))
        if parsed:
            return parsed

    for match in re.finditer(r"(?<!\d)\(?(\d{2})(\d{2})(\d{2})\)?(?!\d)", search_text):
        parsed = build_valid_date(f"20{match.group(1)}", match.group(2), match.group(3))
        if parsed:
            return parsed
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
        parsed = build_valid_date(match.group(1), match.group(2), match.group(3))
        if parsed:
            return parsed
    return ""


def build_valid_date(year: str, month: str, day: str) -> str:
    try:
        parsed = datetime(int(year), int(month), int(day))
    except ValueError:
        return ""
    return parsed.strftime("%Y-%m-%d")


def fallback_title(content: str, source: str, terms: list[str]) -> str:
    heading = find_heading_near_terms(content, terms)
    if heading:
        return heading

    for candidate in split_candidate_phrases(content):
        cleaned = candidate.strip(" -:\t")
        if 8 <= len(cleaned) <= 90 and not looks_like_noise(cleaned):
            return cleaned
    return source.rsplit("/", 1)[-1] if source else "Related report"


def find_heading_near_terms(content: str, terms: list[str]) -> str:
    sentences = split_candidate_phrases(content)
    for sentence in sentences:
        if any(str(term).lower() in sentence.lower() for term in terms if term):
            heading_match = re.search(r"[■□▶]\s*([^■□▶]{4,80})", sentence)
            if heading_match:
                return heading_match.group(1).strip(" -:")
            return sentence[:90].strip(" -:")
    return ""


def split_candidate_phrases(content: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"[;\n\r]|(?=[■□▶])|(?<=다\.)\s+", content)
        if part.strip()
    ]


def looks_like_noise(text: str) -> bool:
    lower = text.lower()
    return lower.endswith((".xlsx", ".pptx", ".pdf")) or len(text) > 90


def extract_department(
    content: str,
    metadata: dict[str, Any],
    source: str = "",
    llm_department: str = "",
) -> str:
    source_department = extract_department_from_source(source)
    if source_department:
        return source_department

    for key in ("department", "dept", "org", "organization"):
        if metadata.get(key):
            mapped = normalize_department_name(str(metadata[key]))
            return mapped or str(metadata[key])

    mapped_llm_department = normalize_department_name(llm_department)
    if mapped_llm_department:
        return mapped_llm_department

    match = re.search(r"([가-힣A-Za-z& ]{2,20}(본부|팀|실|센터|그룹|부문))", content)
    if match:
        mapped = normalize_department_name(match.group(1))
        return mapped or match.group(1).strip()
    return "기타"


def extract_department_from_source(source: str) -> str:
    if not source:
        return ""
    normalized = unquote(source).replace("\\", "/")
    parts = [part.strip() for part in normalized.split("/") if part.strip()]
    if not parts:
        return ""

    file_name = parts[-1]
    folder_parts = parts[:-1]
    for candidate in [*reversed(folder_parts), file_name]:
        mapped = normalize_department_name(candidate)
        if mapped:
            return mapped
    return ""


def normalize_department_name(value: str) -> str:
    for keyword, department in DEPARTMENT_KEYWORDS.items():
        if keyword.lower() in value.lower():
            return department
    return ""


def summarize(content: str, max_len: int = 220) -> str:
    sentence = re.split(r"(?<=[.!?。])\s+|다\.\s*", content)[0].strip()
    if len(sentence) < 30:
        sentence = content[:max_len].strip()
    return sentence[:max_len].rstrip()


def fallback_details(content: str) -> list[str]:
    parts = split_candidate_phrases(content)
    details = [part.strip(" -\t") for part in parts if 12 <= len(part) <= 180]
    if not details and content:
        details = [content[:180].strip()]
    return details[:4]


def fallback_tags(content: str) -> list[str]:
    tags = []
    for token in ("IP", "특허", "R&BD", "TF", "투자", "품질", "공정", "원가", "개발"):
        if token.lower() in content.lower():
            tags.append(token)
    return tags[:5]
