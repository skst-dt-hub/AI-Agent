from __future__ import annotations

import json
import re
from typing import Any

import boto3

from app.report_analysis.config import AWS_REGION, MODEL_ID

from .schemas import RawChunk, SearchPlan, ValidatedChunk, ValidationResult


def validate_evidence(
    plan: SearchPlan,
    chunks: list[RawChunk],
    limit: int,
    use_llm_reranker: bool = False,
) -> tuple[list[ValidatedChunk], dict[str, Any]]:
    unique_chunks = dedupe_chunks(chunks)
    validated = [
        ValidatedChunk(
            chunk=chunk,
            validation=judge_chunk(plan, chunk, use_llm_reranker=use_llm_reranker),
        )
        for chunk in unique_chunks
    ]
    accepted = [
        item
        for item in validated
        if item.validation.confidence in {"High", "Medium"}
    ]
    accepted.sort(
        key=lambda item: (
            {"High": 2, "Medium": 1}.get(item.validation.confidence, 0),
            item.validation.relevance_score,
            item.chunk.kb_score or 0.0,
        ),
        reverse=True,
    )
    rejected = [
        item
        for item in validated
        if item.validation.confidence not in {"High", "Medium"}
    ]
    rejected.sort(
        key=lambda item: (
            item.validation.relevance_score,
            item.chunk.kb_score or 0.0,
        ),
        reverse=True,
    )
    diagnostics = {
        "candidate_count": len(chunks),
        "deduped_count": len(unique_chunks),
        "filtered_out_count": len(unique_chunks) - len(accepted),
        "rejected_top": [debug_chunk(item) for item in rejected[:10]],
    }
    return accepted[:limit], diagnostics


def dedupe_chunks(chunks: list[RawChunk]) -> list[RawChunk]:
    seen = set()
    result = []
    for chunk in chunks:
        key = (chunk.source, " ".join(chunk.content.split())[:300])
        if key in seen:
            continue
        seen.add(key)
        result.append(chunk)
    return result


def debug_chunk(item: ValidatedChunk) -> dict[str, Any]:
    chunk = item.chunk
    validation = item.validation
    return {
        "source": chunk.source.rsplit("/", 1)[-1] if chunk.source else "",
        "source_uri": chunk.source,
        "query": chunk.query,
        "kb_score": chunk.kb_score,
        "matched_terms": validation.matched_terms,
        "text_confidence": validation.text_confidence,
        "confidence": validation.confidence,
        "relevance_score": validation.relevance_score,
        "llm_relevance_score": validation.llm_relevance_score,
        "llm_is_relevant": validation.llm_is_relevant,
        "reason": validation.reason,
        "excerpt": make_debug_excerpt(chunk.content, validation.matched_terms),
    }


def make_debug_excerpt(content: str, terms: list[str], max_len: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    if not text:
        return ""
    lowered = text.lower()
    positions = [
        lowered.find(str(term).lower())
        for term in terms
        if term and lowered.find(str(term).lower()) >= 0
    ]
    if not positions:
        return text[:max_len]
    center = min(positions)
    start = max(0, center - max_len // 2)
    end = min(len(text), start + max_len)
    return text[start:end]


def judge_chunk(
    plan: SearchPlan,
    chunk: RawChunk,
    use_llm_reranker: bool = False,
) -> ValidationResult:
    matched_primary = find_matches(chunk.content, [plan.primary_keyword])
    matched_alias = find_matches(chunk.content, plan.aliases)
    matched_terms = [*matched_primary, *matched_alias]

    if matched_primary:
        text_confidence = "High"
        base_score = 0.78
        reason = "primary_keyword_found"
    elif matched_alias:
        text_confidence = "Medium"
        base_score = 0.62
        reason = "alias_found"
    else:
        text_confidence = "Low"
        base_score = 0.2
        reason = "no_keyword_or_alias_found"

    kb_score = float(chunk.kb_score or 0.0)
    relevance_score = min(0.99, base_score + min(kb_score, 0.5) * 0.35)
    llm_result = None
    if use_llm_reranker:
        llm_result = judge_with_llm(plan, chunk)
        if llm_result:
            relevance_score = round((relevance_score * 0.35) + (llm_result["score"] * 0.65), 4)

    confidence = combine_confidence(text_confidence, relevance_score, llm_result)
    return ValidationResult(
        text_confidence=text_confidence,
        confidence=confidence,
        relevance_score=round(relevance_score, 4),
        matched_terms=matched_terms,
        llm_relevance_score=llm_result["score"] if llm_result else None,
        llm_is_relevant=llm_result["is_relevant"] if llm_result else None,
        reason=reason if not llm_result else f"{reason}; {llm_result['reason']}",
    )


def combine_confidence(
    text_confidence: str,
    relevance_score: float,
    llm_result: dict[str, Any] | None,
) -> str:
    if text_confidence == "Low":
        return "Low"
    if llm_result and not llm_result["is_relevant"]:
        return "Low"
    if text_confidence == "High" and relevance_score >= 0.72:
        return "High"
    if relevance_score >= 0.58:
        return "Medium"
    return "Low"


def find_matches(content: str, terms: list[str]) -> list[str]:
    haystack = content.lower()
    matches = []
    for term in terms:
        if not term:
            continue
        needle = term.lower().strip()
        if re.fullmatch(r"[A-Za-z0-9]+", needle):
            found = re.search(rf"(?<![A-Za-z0-9]){re.escape(needle)}(?![A-Za-z0-9])", haystack)
        else:
            found = needle in haystack
        if found:
            matches.append(term)
    return matches


def judge_with_llm(plan: SearchPlan, chunk: RawChunk) -> dict[str, Any] | None:
    prompt = (
        "Judge whether the report chunk is directly relevant to the search plan. "
        "Return strict JSON only.\n\n"
        f"Primary keyword: {plan.primary_keyword}\n"
        f"Aliases: {plan.aliases}\n"
        f"Chunk:\n{chunk.content[:4000]}\n\n"
        'Schema: {"is_relevant":true,"score":0.0,"reason":"short"}'
    )
    try:
        client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        response = client.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 350,
                    "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}],
                }
            ),
        )
        payload = json.loads(response["body"].read())
        text = payload["content"][0]["text"]
        parsed = json.loads(text[text.find("{") : text.rfind("}") + 1])
        return {
            "is_relevant": bool(parsed.get("is_relevant")),
            "score": max(0.0, min(1.0, float(parsed.get("score", 0.0)))),
            "reason": str(parsed.get("reason") or "llm_relevance_judged"),
        }
    except Exception:
        return None
