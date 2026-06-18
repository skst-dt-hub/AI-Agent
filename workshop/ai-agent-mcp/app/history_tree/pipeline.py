from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.report_analysis.postprocess import build_structured_output
from app.report_analysis.scout import retrieve_internal_documents


def run_history_tree(
    keyword: str,
    number_of_results: int = 5,
    search_type: str = "HYBRID",
    output_format: str = "structured",
    use_llm_reranker: bool = False,
) -> str | dict[str, Any]:
    """Search report history and return a date-ordered timeline.

    The previous pipeline classified results as new/repeated/disappeared, but that
    classification is unreliable when retrieval misses related reports. This
    version focuses on high-precision search, structured extraction, and
    timeline rendering.
    """
    step, results = retrieve_internal_documents(
        keyword=keyword,
        number_of_results=number_of_results,
        search_type=search_type,
        expand_related_terms=True,
        use_llm_reranker=use_llm_reranker,
    )
    if output_format == "raw":
        return {
            "step": asdict(step),
            "results": [asdict(item) for item in results],
            "count": len(results),
        }

    structured = build_structured_output(
        keyword=keyword,
        results=results,
        search_type=search_type.upper(),
        output_format="timeline_html" if output_format == "timeline_html" else "structured",
    )
    structured["step"] = asdict(step)
    if output_format == "structured":
        return structured
    if output_format == "timeline_html":
        return structured["timeline_html"]
    return render_markdown(structured)


def render_markdown(payload: dict[str, Any]) -> str:
    keyword = payload["metadata"]["keyword"]
    lines = [f"# [{keyword}] 보고 타임라인", ""]
    for item in payload["results"]:
        tags = ", ".join(item.get("tags", [])) or "태그 없음"
        details = "\n".join(f"  - {detail}" for detail in item.get("details", []))
        lines.extend(
            [
                f"## {item.get('date') or '날짜 미분류'} - {item['title']}",
                f"- 부서: {item['department']}",
                f"- 신뢰도: {item['confidence']} ({item.get('relevance_score')})",
                f"- 태그: {tags}",
                f"- 요약: {item['summary']}",
                f"- 출처: {item['source']}",
            ]
        )
        if details:
            lines.append("- 상세:")
            lines.append(details)
        lines.append("")
    lines.append(
        f"검색 결과: {payload['metadata']['total_count']}건 "
        f"(필터링 제외: {payload['metadata']['filtered_out_count']}건)"
    )
    return "\n".join(lines).strip()
