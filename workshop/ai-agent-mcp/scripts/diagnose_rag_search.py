from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.history_tree.query_interpreter import interpret_query
from app.history_tree.retriever import retrieve_evidence
from app.history_tree.validator import dedupe_chunks, judge_chunk
from app.report_analysis.models import InternalResult
from app.report_analysis.postprocess import expand_keywords, filter_and_rank_results
from app.report_analysis.scout import _retrieve as retrieve_report_chunk


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dump step-by-step Bedrock KB RAG diagnostics without changing server logic."
    )
    parser.add_argument("keyword", help="Keyword or query to diagnose.")
    parser.add_argument(
        "--mode",
        choices=("history-tree", "report-internal"),
        default="history-tree",
        help="Pipeline to diagnose.",
    )
    parser.add_argument("--results", type=int, default=5, help="Requested final result count.")
    parser.add_argument("--search-type", default="HYBRID", help="Bedrock KB search type.")
    parser.add_argument("--llm-reranker", action="store_true", help="Enable existing LLM reranker.")
    parser.add_argument("--excerpt-chars", type=int, default=500, help="Excerpt length per candidate.")
    parser.add_argument("--output", help="Optional JSON output path.")
    args = parser.parse_args()

    if args.mode == "history-tree":
        payload = diagnose_history_tree(
            keyword=args.keyword,
            results=args.results,
            search_type=args.search_type,
            use_llm_reranker=args.llm_reranker,
            excerpt_chars=args.excerpt_chars,
        )
    else:
        payload = diagnose_report_internal(
            keyword=args.keyword,
            results=args.results,
            search_type=args.search_type,
            use_llm_reranker=args.llm_reranker,
            excerpt_chars=args.excerpt_chars,
        )

    output_path = Path(args.output) if args.output else default_output_path(args.keyword, args.mode)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(payload, output_path)
    return 0


def diagnose_history_tree(
    keyword: str,
    results: int,
    search_type: str,
    use_llm_reranker: bool,
    excerpt_chars: int,
) -> dict[str, Any]:
    plan = interpret_query(keyword)
    effective_search_type, raw_chunks = retrieve_evidence(
        plan=plan,
        number_of_results=results,
        search_type=search_type,
    )
    unique_chunks = dedupe_chunks(raw_chunks)
    judged = [
        {
            "index": index + 1,
            "source": chunk.source,
            "query": chunk.query,
            "kb_score": chunk.kb_score,
            "metadata": chunk.metadata,
            "validation": asdict(judge_chunk(plan, chunk, use_llm_reranker=use_llm_reranker)),
            "excerpt": make_excerpt(chunk.content, excerpt_chars),
        }
        for index, chunk in enumerate(unique_chunks)
    ]
    accepted = [
        item
        for item in judged
        if item["validation"]["confidence"] in {"High", "Medium"}
    ]
    rejected = [
        item
        for item in judged
        if item["validation"]["confidence"] not in {"High", "Medium"}
    ]
    return {
        "mode": "history-tree",
        "keyword": keyword,
        "search_type_requested": search_type,
        "search_type_effective": effective_search_type,
        "query_plan": plan.to_dict(),
        "counts": {
            "raw_chunks": len(raw_chunks),
            "deduped_chunks": len(unique_chunks),
            "accepted_chunks": len(accepted),
            "rejected_chunks": len(rejected),
        },
        "accepted": sort_history_items(accepted),
        "rejected": sort_history_items(rejected),
    }


def diagnose_report_internal(
    keyword: str,
    results: int,
    search_type: str,
    use_llm_reranker: bool,
    excerpt_chars: int,
) -> dict[str, Any]:
    queries = expand_keywords(keyword)
    retrieval_count = max(20, results * 4)
    raw_candidates: list[InternalResult] = []
    seen = set()

    for query in queries:
        response = retrieve_report_chunk(query, retrieval_count, search_type.upper())
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
            raw_candidates.append(
                InternalResult(
                    content=content,
                    source=source,
                    score=item.get("score"),
                    metadata={**item.get("metadata", {}), "_query": query, "_search_type": search_type.upper()},
                )
            )

    filtered, possible = filter_and_rank_results(
        keyword,
        raw_candidates,
        limit=results,
        use_llm_reranker=use_llm_reranker,
    )
    return {
        "mode": "report-internal",
        "keyword": keyword,
        "search_type_requested": search_type,
        "search_type_effective": search_type.upper(),
        "queries": queries,
        "counts": {
            "raw_candidates": len(raw_candidates),
            "accepted_chunks": len(filtered),
            "rejected_or_possible_chunks": len(possible),
        },
        "accepted": [serialize_internal_result(item, excerpt_chars) for item in filtered],
        "rejected_or_possible": [serialize_internal_result(item, excerpt_chars) for item in possible],
    }


def sort_history_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            {"High": 3, "Medium": 2, "Low": 1}.get(item["validation"]["confidence"], 0),
            item["validation"]["relevance_score"],
            item["kb_score"] or 0.0,
        ),
        reverse=True,
    )


def serialize_internal_result(item: InternalResult, excerpt_chars: int) -> dict[str, Any]:
    return {
        "source": item.source,
        "kb_score": item.score,
        "metadata": item.metadata,
        "excerpt": make_excerpt(item.content, excerpt_chars),
    }


def make_excerpt(content: str, limit: int) -> str:
    text = " ".join(str(content or "").split())
    return text[:limit]


def default_output_path(keyword: str, mode: str) -> Path:
    safe_keyword = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in keyword).strip("_")
    safe_keyword = safe_keyword[:40] or "query"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return PROJECT_ROOT / "output" / f"rag_diagnostic_{mode}_{safe_keyword}_{stamp}.json"


def print_summary(payload: dict[str, Any], output_path: Path) -> None:
    print(f"mode: {payload['mode']}")
    print(f"keyword: {payload['keyword']}")
    print(f"search_type: {payload['search_type_effective']}")
    print(f"counts: {json.dumps(payload['counts'], ensure_ascii=False)}")
    if "query_plan" in payload:
        print(f"search_queries: {payload['query_plan'].get('search_queries', [])}")
    if "queries" in payload:
        print(f"queries: {payload['queries']}")
    print(f"output: {output_path}")


if __name__ == "__main__":
    raise SystemExit(main())
