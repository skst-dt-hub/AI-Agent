from __future__ import annotations

import boto3

from .config import AWS_REGION, KB_NUMBER_OF_RESULTS, KB_SEARCH_TYPE, KNOWLEDGE_BASE_ID, is_kb_configured
from .models import InternalResult, StepResult
from .postprocess import expand_keywords, filter_and_rank_results


def retrieve_internal_documents(
    keyword: str,
    number_of_results: int | None = None,
    search_type: str | None = None,
    expand_related_terms: bool = True,
    use_llm_reranker: bool | None = None,
) -> tuple[StepResult, list[InternalResult]]:
    if not is_kb_configured():
        return (
            StepResult(
                status="skipped",
                message="Knowledge Base settings are missing; internal document search was skipped.",
            ),
            [],
        )

    requested_results = number_of_results or KB_NUMBER_OF_RESULTS
    retrieval_results = max(20, requested_results * 4)
    search_type = (search_type or KB_SEARCH_TYPE).upper()
    queries = expand_keywords(keyword) if expand_related_terms else [keyword]

    try:
        responses = [_retrieve(query, retrieval_results, search_type) for query in queries]
    except Exception as exc:
        if search_type == "HYBRID":
            try:
                responses = [_retrieve(query, retrieval_results, "SEMANTIC") for query in queries]
                search_type = "SEMANTIC"
            except Exception as fallback_exc:
                return StepResult(status="error", error=f"{type(fallback_exc).__name__}: {fallback_exc}"), []
        else:
            return StepResult(status="error", error=f"{type(exc).__name__}: {exc}"), []

    candidates: list[InternalResult] = []
    seen = set()
    for query, response in zip(queries, responses):
        for item in response.get("retrievalResults", []):
            content = item.get("content", {}).get("text", "")
            location = item.get("location", {})
            source = (
                location.get("s3Location", {}).get("uri")
                or location.get("webLocation", {}).get("url")
                or str(location)
            )
            dedupe_key = (source, content[:240])
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            candidates.append(
                InternalResult(
                    content=content,
                    source=source,
                    score=item.get("score"),
                    metadata={**item.get("metadata", {}), "_query": query, "_search_type": search_type},
                )
            )

    filtered, possible = filter_and_rank_results(
        keyword,
        candidates,
        limit=requested_results,
        use_llm_reranker=use_llm_reranker,
    )
    for item in filtered:
        item.metadata["_filtered_out_count"] = len(possible)
        item.metadata["_candidate_count"] = len(candidates)

    return (
        StepResult(
            status="ok",
            message=(
                f"internal document search completed "
                f"({search_type}, queries={len(queries)}, candidates={len(candidates)}, "
                f"returned={len(filtered)}, filtered_out={len(possible)})"
            ),
        ),
        filtered,
    )


def _retrieve(keyword: str, number_of_results: int, search_type: str) -> dict:
    vector_config = {"numberOfResults": number_of_results}
    if search_type in {"HYBRID", "SEMANTIC"}:
        vector_config["overrideSearchType"] = search_type

    client = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)
    return client.retrieve(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        retrievalQuery={"text": keyword},
        retrievalConfiguration={"vectorSearchConfiguration": vector_config},
    )
