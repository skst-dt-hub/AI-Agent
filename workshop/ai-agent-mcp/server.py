"""AI-Agent MCP Server for Amazon Bedrock AgentCore Runtime.

The server exposes report-history, report-analysis, KB-admin, and HR-search
tools over MCP streamable HTTP.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.history_tree.pipeline import run_history_tree
from app.report_analysis import config as report_config
from app.report_analysis.anchor import build_markdown_report
from app.report_analysis.kb_loader import (
    list_ingestion_jobs,
    list_s3_files,
    poll_ingestion_job,
    start_ingestion_job,
)
from app.report_analysis.models import AnalysisRun, StepResult
from app.report_analysis.postprocess import build_structured_output
from app.report_analysis.ranger import parse_manual_external_notes, search_latest_trends
from app.report_analysis.scout import retrieve_internal_documents

from app.hr_search.candidate_retrieval import retrieve_candidates
from app.hr_search.explanation import explain_candidates
from app.hr_search.query_understanding import understand_query
from app.hr_search.scoring import score_candidates


mcp = FastMCP(
    name="ai-agent-mcp",
    host="0.0.0.0",
    stateless_http=True,
)


@mcp.tool()
def history_tree_report(
    keyword: str,
    number_of_results: int = 5,
    search_type: str = "HYBRID",
    output_format: str = "structured",
    use_llm_reranker: bool = False,
) -> dict[str, Any]:
    """Search internal report history and return timeline-ready structured data.

    Pipeline:
    Query Interpreter -> Evidence Retriever -> Relevance Judge -> Timeline Composer.

    Use this tool for Quick/App timeline card UI. The recommended output_format
    is "structured". The response contains results[] with date, title,
    department, tags, summary, details, source, confidence, and relevance_score.

    This tool does not classify items as new/repeated/disappeared and does not
    return a markdown History Tree. Use output_format="debug" only for
    troubleshooting; it includes the top rejected chunks and rejection reasons.
    Do not use timeline_html unless the client can safely render HTML.
    """
    return run_history_tree(
        keyword=keyword,
        number_of_results=number_of_results,
        search_type=search_type,
        output_format=output_format,
        use_llm_reranker=use_llm_reranker,
    )


@mcp.tool()
def report_retrieve_internal(
    keyword: str,
    number_of_results: int = 4,
    search_type: str = "HYBRID",
    output_format: str = "structured",
    use_llm_reranker: bool = False,
) -> dict[str, Any]:
    """Retrieve internal report chunks from Bedrock Knowledge Base.

    This is a lower-level retrieval/debugging tool. For user-facing report
    timeline cards, prefer history_tree_report with output_format="structured".

    The tool retrieves candidate chunks, applies keyword/alias validation and
    relevance filtering, and can return raw chunks or structured timeline-like
    data. It does not run the full History Timeline v2 pipeline.
    """
    step, results = retrieve_internal_documents(
        keyword,
        number_of_results=number_of_results,
        search_type=search_type,
        use_llm_reranker=use_llm_reranker,
    )
    if output_format not in {"raw", "structured", "timeline_html"}:
        output_format = "structured"
    if output_format in {"structured", "timeline_html"}:
        structured = build_structured_output(
            keyword=keyword,
            results=results,
            search_type=search_type.upper(),
            output_format=output_format,
        )
        structured["step"] = asdict(step)
        return structured
    return {
        "step": asdict(step),
        "results": [asdict(item) for item in results],
        "count": len(results),
    }


@mcp.tool()
def report_search_external_trends(keyword: str, max_items: int = 5) -> dict[str, Any]:
    """Search external trend sources for the given keyword."""
    step, results, raw_text, logs = search_latest_trends(keyword, max_items=max_items)
    return {
        "step": asdict(step),
        "results": [asdict(item) for item in results],
        "raw_text": raw_text,
        "logs": [asdict(item) for item in logs],
        "count": len(results),
    }


@mcp.tool()
def report_generate(
    keyword: str,
    number_of_results: int = 4,
    use_web_search: bool = False,
    max_items: int = 5,
    manual_external: str = "",
) -> dict[str, Any]:
    """Generate a markdown report from internal evidence and optional external trends."""
    run = AnalysisRun(keyword=keyword.strip(), started_at=datetime.now())

    run.scout, run.internal_results = retrieve_internal_documents(
        run.keyword,
        number_of_results=number_of_results,
        search_type=report_config.KB_SEARCH_TYPE,
    )

    manual_results = parse_manual_external_notes(manual_external)
    if use_web_search:
        run.ranger, web_results, _raw, run.search_logs = search_latest_trends(
            run.keyword, max_items
        )
        run.external_results = manual_results + web_results
    elif manual_results:
        run.ranger = StepResult(status="ok", message="Using manually supplied external notes.")
        run.external_results = manual_results
    else:
        run.ranger = StepResult(status="skipped", message="No external evidence supplied.")

    run.anchor, run.markdown_report = build_markdown_report(run)

    return {
        "keyword": run.keyword,
        "markdown_report": run.markdown_report,
        "scout": asdict(run.scout),
        "ranger": asdict(run.ranger),
        "anchor": asdict(run.anchor),
        "internal_results": [asdict(item) for item in run.internal_results],
        "external_results": [asdict(item) for item in run.external_results],
        "search_logs": [asdict(item) for item in run.search_logs],
    }


@mcp.tool()
def report_kb_list_s3_files() -> dict[str, Any]:
    """List files under the configured report-analysis S3 prefix."""
    files = list_s3_files()
    return {
        "bucket": report_config.S3_BUCKET,
        "prefix": report_config.S3_PREFIX,
        "files": [
            {
                "file_name": item.get("file_name"),
                "size_kb": item.get("size_kb"),
                "s3_key": item.get("s3_key"),
                "last_modified": str(item.get("last_modified")),
            }
            for item in files
        ],
        "count": len(files),
    }


@mcp.tool()
def report_kb_sync(timeout_seconds: int = 900) -> dict[str, Any]:
    """Start a Knowledge Base ingestion job and wait for completion."""
    if not report_config.is_kb_configured():
        return {
            "status": "skipped",
            "message": "KNOWLEDGE_BASE_ID, DATA_SOURCE_ID, and S3_BUCKET are required.",
        }
    job_id = start_ingestion_job()
    response = poll_ingestion_job(job_id, timeout_seconds=timeout_seconds)
    job = response.get("ingestionJob", {})
    return {
        "ingestion_job_id": job_id,
        "status": job.get("status", "UNKNOWN"),
        "statistics": job.get("statistics", {}),
    }


@mcp.tool()
def report_kb_list_ingestion_jobs(max_results: int = 10) -> dict[str, Any]:
    """List recent Knowledge Base ingestion jobs."""
    jobs = list_ingestion_jobs(max_results=max_results)
    rows = []
    for job in jobs:
        stats = job.get("statistics", {})
        rows.append(
            {
                "status": job.get("status"),
                "started_at": str(job.get("startedAt")),
                "updated_at": str(job.get("updatedAt")),
                "scanned": stats.get("numberOfDocumentsScanned"),
                "indexed": stats.get("numberOfNewDocumentsIndexed"),
                "failed": stats.get("numberOfDocumentsFailed"),
                "job_id": job.get("ingestionJobId"),
            }
        )
    return {"jobs": rows, "count": len(rows)}


@mcp.tool()
def hr_understand_query(query: str) -> dict[str, Any]:
    """Parse a natural-language HR search request into hard and soft conditions."""
    return understand_query(query)


@mcp.tool()
def hr_retrieve_candidates(requirements: dict[str, Any], top_k: int = 10) -> dict[str, Any]:
    """Filter and retrieve HR candidates from structured data and embeddings."""
    return retrieve_candidates(requirements, top_k=top_k)


@mcp.tool()
def hr_score_candidates(retrieval_output: dict[str, Any]) -> dict[str, Any]:
    """Score retrieved HR candidates using job fit, experience, leadership, and mobility."""
    return score_candidates(retrieval_output)


@mcp.tool()
def hr_explain_candidates(scoring_result: dict[str, Any]) -> dict[str, Any]:
    """Generate concise recommendation reasons, strengths, and weaknesses."""
    return explain_candidates(scoring_result)


@mcp.tool()
def hr_search(query: str) -> dict[str, Any]:
    """Run the full HR search pipeline in one call."""
    understood = understand_query(query)
    retrieved = retrieve_candidates(understood)
    scored = score_candidates(retrieved)
    explained = explain_candidates(scored)
    return {
        "query_understanding": understood,
        "retrieval": retrieved,
        "scoring": scored,
        "explanation": explained,
    }


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    mcp.settings.port = port
    mcp.run(transport="streamable-http")
