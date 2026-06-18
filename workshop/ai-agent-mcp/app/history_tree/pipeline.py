from __future__ import annotations

from typing import Any

from .extractor import compose_timeline
from .query_interpreter import interpret_query
from .retriever import retrieve_evidence
from .validator import validate_evidence


def run_history_tree(
    keyword: str,
    number_of_results: int = 5,
    search_type: str = "HYBRID",
    output_format: str = "structured",
    use_llm_reranker: bool = False,
) -> dict[str, Any]:
    """Run the History Timeline v2 pipeline.

    Flow:
    Query Interpreter -> Evidence Retriever -> Relevance Judge -> Timeline Composer.
    UI rendering is intentionally left to Quick/App; this function returns
    structured data by default.
    """
    plan = interpret_query(keyword)
    effective_search_type, raw_chunks = retrieve_evidence(
        plan=plan,
        number_of_results=number_of_results,
        search_type=search_type,
    )
    validated_chunks, diagnostics = validate_evidence(
        plan=plan,
        chunks=raw_chunks,
        limit=number_of_results,
        use_llm_reranker=use_llm_reranker,
    )
    response = compose_timeline(
        plan=plan,
        validated_chunks=validated_chunks,
        diagnostics=diagnostics,
        search_type=effective_search_type,
    )
    payload = response.to_dict()
    payload["metadata"]["output_format"] = output_format

    if output_format == "raw":
        payload["raw_chunks"] = [
            {
                "content": chunk.content,
                "source": chunk.source,
                "score": chunk.kb_score,
                "metadata": chunk.metadata,
                "query": chunk.query,
            }
            for chunk in raw_chunks
        ]
    elif output_format == "debug":
        payload["debug"] = {
            "query_plan": plan.to_dict(),
            "retrieved_count": len(raw_chunks),
            "candidate_count": diagnostics.get("candidate_count", 0),
            "deduped_count": diagnostics.get("deduped_count", 0),
            "filtered_out_count": diagnostics.get("filtered_out_count", 0),
            "rejected_chunks_top10": diagnostics.get("rejected_top", []),
        }
    elif output_format == "timeline_html":
        payload["timeline_html"] = render_debug_timeline_html(payload)
    else:
        payload.pop("diagnostics", None)
    return payload


def render_debug_timeline_html(payload: dict[str, Any]) -> str:
    cards = []
    for item in payload.get("results", []):
        details = "".join(f"<li>{detail}</li>" for detail in item.get("details", []))
        tags = " ".join(f"<span>{tag}</span>" for tag in item.get("tags", []))
        cards.append(
            "<article>"
            f"<time>{item.get('date') or 'Unclassified'}</time>"
            f"<h3>{item.get('title', '')}</h3>"
            f"<p>{item.get('summary', '')}</p>"
            f"<div>{tags}</div>"
            f"<details><summary>Details</summary><ul>{details}</ul></details>"
            f"<small>{item.get('source', '')}</small>"
            "</article>"
        )
    return "<section class='report-timeline'>" + "".join(cards) + "</section>"
