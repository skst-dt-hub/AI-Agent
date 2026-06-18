"""AI-Agent MCP Server.

Exposes the Streamlit-free core logic of the three skst-dt-hub/AI-Agent PoCs as
MCP tools, served over streamable-HTTP so it can run on Amazon Bedrock AgentCore
Runtime.

AgentCore Runtime contract for MCP servers:
- Listen on 0.0.0.0:8000
- Serve MCP at the /mcp path
- Use stateless HTTP transport (no server-side session affinity)

Run locally:
    python -m server            # or: python server.py

The server is intentionally transport-agnostic in its tool definitions; only the
bottom run() block wires it to streamable-http for AgentCore.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

# ── History Tree agent ─────────────────────────────────────────────────────────
from app.history_tree.pipeline import run_history_tree

# ── Report Analysis agent ──────────────────────────────────────────────────────
from app.report_analysis import config as report_config
from app.report_analysis.anchor import build_markdown_report
from app.report_analysis.models import AnalysisRun, StepResult
from app.report_analysis.ranger import parse_manual_external_notes, search_latest_trends
from app.report_analysis.scout import retrieve_internal_documents
from app.report_analysis.kb_loader import (
    list_ingestion_jobs,
    list_s3_files,
    poll_ingestion_job,
    start_ingestion_job,
)
from app.report_analysis.postprocess import build_structured_output

# ── HR Search agent ────────────────────────────────────────────────────────────
from app.hr_search.candidate_retrieval import retrieve_candidates
from app.hr_search.explanation import explain_candidates
from app.hr_search.query_understanding import understand_query
from app.hr_search.scoring import score_candidates


# FastMCP defaults: host bound below, port 8000, mount path "/mcp".
# stateless_http=True is required by AgentCore Runtime.
mcp = FastMCP(
    name="ai-agent-mcp",
    host="0.0.0.0",
    stateless_http=True,
)


# ════════════════════════════════════════════════════════════════════════════════
# History Tree agent
# ════════════════════════════════════════════════════════════════════════════════
@mcp.tool()
def history_tree_report(
    keyword: str,
    number_of_results: int = 5,
    search_type: str = "HYBRID",
    output_format: str = "structured",
    use_llm_reranker: bool = False,
) -> str | dict[str, Any]:
    """Bedrock Knowledge Base에서 키워드 관련 보고 내용을 검색하고 날짜순
    History Tree(신규 등장/반복/소멸 분류 포함)로 정리합니다.

    내부 멀티에이전트 파이프라인(Search -> Analyst -> Writer)을 실행합니다.

    Args:
        keyword: 분석할 키워드 (예: "몰리브덴").

    Returns:
        History Tree 형식의 마크다운 텍스트.
    """
    return run_history_tree(
        keyword=keyword,
        number_of_results=number_of_results,
        search_type=search_type,
        output_format=output_format,
        use_llm_reranker=use_llm_reranker,
    )


# ════════════════════════════════════════════════════════════════════════════════
# Report Analysis agent
# ════════════════════════════════════════════════════════════════════════════════
@mcp.tool()
def report_retrieve_internal(
    keyword: str,
    number_of_results: int = 4,
    search_type: str = "HYBRID",
    output_format: str = "structured",
    use_llm_reranker: bool = False,
) -> dict[str, Any]:
    """Bedrock Knowledge Base(RAG)에서 내부 문서 근거를 검색합니다 (Scout).

    Args:
        keyword: 검색 키워드.
        number_of_results: 가져올 문서 조각 수 (기본 4).
        search_type: "HYBRID" 또는 "SEMANTIC".

    Returns:
        step(상태)와 results(내부 문서 근거 리스트)를 담은 dict.
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
    """외부 웹 검색으로 키워드 관련 최신 동향을 수집합니다 (Ranger).

    TAVILY_API_KEY 또는 EXA_API_KEY 환경변수와 strands-agents-tools 웹 검색
    도구가 필요합니다. 키가 없으면 step.status가 "error"로 반환됩니다.

    Args:
        keyword: 외부 동향 검색 키워드.
        max_items: 수집할 최대 결과 수 (기본 5).

    Returns:
        step, results, raw_text, logs를 담은 dict.
    """
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
    """내부 검색(Scout) + 외부 동향(Ranger) + 종합 리포트(Anchor)를 한 번에
    실행해 Markdown 리포트를 생성합니다.

    Args:
        keyword: 분석 키워드/질문.
        number_of_results: 내부 문서 검색 결과 수 (기본 4).
        use_web_search: True면 외부 웹 검색(Tavily/Exa) 사용.
        max_items: 외부 검색 최대 결과 수 (기본 5).
        manual_external: 수동으로 붙여넣을 외부 자료 텍스트(선택).

    Returns:
        markdown_report와 내부/외부 근거, 각 단계 상태를 담은 dict.
    """
    run = AnalysisRun(keyword=keyword.strip(), started_at=datetime.now())

    # Scout: internal documents
    run.scout, run.internal_results = retrieve_internal_documents(
        run.keyword,
        number_of_results=number_of_results,
        search_type=report_config.KB_SEARCH_TYPE,
    )

    # Ranger: external trends (manual + optional web search)
    manual_results = parse_manual_external_notes(manual_external)
    if use_web_search:
        run.ranger, web_results, _raw, run.search_logs = search_latest_trends(
            run.keyword, max_items
        )
        run.external_results = manual_results + web_results
    elif manual_results:
        run.ranger = StepResult(status="ok", message="수동 입력 외부 자료를 사용합니다.")
        run.external_results = manual_results
    else:
        run.ranger = StepResult(status="skipped", message="외부 자료 없이 내부 근거만 사용합니다.")

    # Anchor: synthesize markdown report
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
    """Report Analysis용 S3 prefix 아래의 파일 목록을 조회합니다."""
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
    """현재 S3에 있는 데이터로 Knowledge Base 재색인(ingestion job)을 시작하고
    완료될 때까지 대기합니다.

    Args:
        timeout_seconds: 최대 대기 시간(초). 기본 900.

    Returns:
        ingestion job 상태와 통계를 담은 dict.
    """
    if not report_config.is_kb_configured():
        return {
            "status": "skipped",
            "message": "KNOWLEDGE_BASE_ID, DATA_SOURCE_ID, S3_BUCKET 설정이 필요합니다.",
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
    """최근 Knowledge Base ingestion(Sync) 이력을 조회합니다."""
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


# ════════════════════════════════════════════════════════════════════════════════
# HR Search agent
# ════════════════════════════════════════════════════════════════════════════════
@mcp.tool()
def hr_understand_query(query: str) -> dict[str, Any]:
    """자연어 HR 질의를 하드 조건/소프트 조건으로 구조화합니다 (Step 1).

    Args:
        query: 인사담당자 자연어 질의.

    Returns:
        hard_conditions, soft_conditions, original_query를 담은 dict.
    """
    return understand_query(query)


@mcp.tool()
def hr_retrieve_candidates(requirements: dict[str, Any], top_k: int = 10) -> dict[str, Any]:
    """구조화된 조건으로 후보를 하드 필터링하고 임베딩 유사도로 정렬합니다 (Step 2).

    Args:
        requirements: hr_understand_query의 출력(hard/soft conditions 포함).
        top_k: 반환할 상위 후보 수 (기본 10).

    Returns:
        candidates와 필터 통계, 유사도 점수를 담은 dict.
    """
    return retrieve_candidates(requirements, top_k=top_k)


@mcp.tool()
def hr_score_candidates(retrieval_output: dict[str, Any]) -> dict[str, Any]:
    """후보를 직무적합도/경험깊이/리더십/이동배치 기준으로 채점·재정렬합니다 (Step 3).

    Args:
        retrieval_output: hr_retrieve_candidates의 출력.

    Returns:
        scored_candidates(최종Score 포함)와 score_policy를 담은 dict.
    """
    return score_candidates(retrieval_output)


@mcp.tool()
def hr_explain_candidates(scoring_result: dict[str, Any]) -> dict[str, Any]:
    """상위 후보의 추천근거/강점/약점/비교표/요약을 생성합니다 (Step 4).

    Args:
        scoring_result: hr_score_candidates의 출력.

    Returns:
        ranked_candidates, comparison_table, summary를 담은 dict.
    """
    return explain_candidates(scoring_result)


@mcp.tool()
def hr_search(query: str) -> dict[str, Any]:
    """HR 검색 전체 파이프라인을 한 번에 실행합니다.

    query understanding -> candidate retrieval -> scoring -> explanation 순으로
    실행해 최종 추천 결과를 반환합니다.

    Args:
        query: 인사담당자 자연어 질의.

    Returns:
        각 단계 결과(query_understanding, retrieval, scoring, explanation)를 담은 dict.
    """
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
    # AgentCore Runtime expects MCP over streamable-http at 0.0.0.0:8000/mcp.
    # PORT can be overridden locally; FastMCP reads its own settings, so we set it
    # via the FastMCP settings object below.
    port = int(os.getenv("PORT", "8000"))
    mcp.settings.port = port
    mcp.run(transport="streamable-http")
