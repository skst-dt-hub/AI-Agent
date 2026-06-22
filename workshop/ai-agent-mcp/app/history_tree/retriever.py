from __future__ import annotations

import boto3

from app.report_analysis.config import AWS_REGION, KNOWLEDGE_BASE_ID, is_kb_configured

from .schemas import RawChunk, SearchPlan


def retrieve_evidence(
    plan: SearchPlan,
    number_of_results: int,
    search_type: str = "HYBRID",
) -> tuple[str, list[RawChunk]]:
    if not is_kb_configured():
        return "Knowledge Base settings are missing; retrieval skipped.", []

    effective_search_type = (search_type or "HYBRID").upper()
    retrieval_count = max(20, number_of_results * 4)

    try:
        return (
            effective_search_type,
            _retrieve_for_queries(plan.search_queries, retrieval_count, effective_search_type),
        )
    except Exception:
        if effective_search_type == "HYBRID":
            fallback = "SEMANTIC"
            return fallback, _retrieve_for_queries(plan.search_queries, retrieval_count, fallback)
        raise


def _retrieve_for_queries(queries: list[str], number_of_results: int, search_type: str) -> list[RawChunk]:
    chunks: list[RawChunk] = []
    for query in queries:
        response = _retrieve(query, number_of_results, search_type)
        for item in response.get("retrievalResults", []):
            location = item.get("location", {})
            source = (
                location.get("s3Location", {}).get("uri")
                or location.get("webLocation", {}).get("url")
                or str(location)
            )
            chunks.append(
                RawChunk(
                    content=item.get("content", {}).get("text", ""),
                    source=source,
                    kb_score=item.get("score"),
                    metadata={**item.get("metadata", {}), "_query": query, "_search_type": search_type},
                    query=query,
                )
            )
    return chunks


def _retrieve(query: str, number_of_results: int, search_type: str) -> dict:
    vector_config = {"numberOfResults": number_of_results}
    if search_type in {"HYBRID", "SEMANTIC"}:
        vector_config["overrideSearchType"] = search_type

    client = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)
    return client.retrieve(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": vector_config},
    )
