from __future__ import annotations

import boto3

from config import AWS_REGION, KB_NUMBER_OF_RESULTS, KB_SEARCH_TYPE, KNOWLEDGE_BASE_ID, is_kb_configured
from models import InternalResult, StepResult


def retrieve_internal_documents(
    keyword: str,
    number_of_results: int | None = None,
    search_type: str | None = None,
) -> tuple[StepResult, list[InternalResult]]:
    if not is_kb_configured():
        return (
            StepResult(
                status="skipped",
                message="KB_ID, DATA_SOURCE_ID, S3_BUCKET 설정이 없어 내부 문서 검색을 건너뜁니다.",
            ),
            [],
        )

    number_of_results = number_of_results or KB_NUMBER_OF_RESULTS
    search_type = (search_type or KB_SEARCH_TYPE).upper()

    try:
        response = _retrieve(keyword, number_of_results, search_type)
    except Exception as exc:
        if search_type == "HYBRID":
            try:
                response = _retrieve(keyword, number_of_results, "SEMANTIC")
                search_type = "SEMANTIC"
            except Exception as fallback_exc:
                return StepResult(status="error", error=f"{type(fallback_exc).__name__}: {fallback_exc}"), []
        else:
            return StepResult(status="error", error=f"{type(exc).__name__}: {exc}"), []

    results = []
    for item in response.get("retrievalResults", []):
        content = item.get("content", {}).get("text", "")
        location = item.get("location", {})
        source = (
            location.get("s3Location", {}).get("uri")
            or location.get("webLocation", {}).get("url")
            or str(location)
        )
        results.append(
            InternalResult(
                content=content,
                source=source,
                score=item.get("score"),
                metadata={**item.get("metadata", {}), "_query": keyword, "_search_type": search_type},
            )
        )

    return (
        StepResult(status="ok", message=f"내부 문서 {len(results)}건 검색 완료 ({search_type}, top {number_of_results})"),
        results,
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
