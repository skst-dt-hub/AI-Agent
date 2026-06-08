from __future__ import annotations

import importlib
import json
import os
from typing import Any

from config import MODEL_ID
from models import ExternalResult, SearchLog, SearchPlan, StepResult


SEARCH_TOOL_CANDIDATES = (
    ("strands_tools", "web_search"),
    ("strands_tools.tavily", "tavily_search"),
    ("strands_tools.exa", "exa_search"),
)


WEB_SEARCH_SYSTEM_PROMPT = """역할:
당신은 내부 문서 분석을 보조하기 위해 외부 공개 자료를 검색하는 조사 Agent다.
사용자의 키워드와 직접 관련된 최신 외부 동향만 수집한다.

목표:
- 내부 문서 검색 결과를 자동으로 해석하거나 확장하지 않는다.
- 사용자가 입력한 키워드 자체에 대한 외부 동향만 확인한다.
- 검색 결과는 리포트에 자동 반영되지 않으며, 사용자가 검토할 후보 자료로만 제공된다.

검색 원칙:
- 별도 뉴스 API는 사용하지 않는다.
- strands-agents-tools에서 현재 환경에 사용 가능한 웹 검색 도구만 사용한다.
- 검색 도구, API 키, 네트워크 제약으로 실패하면 전체 분석을 중단하지 않는다.
- 실패 시 "외부 검색 불가" 상태와 오류 메시지를 구조화해서 반환한다.
- 키워드를 산업/정책/공급망/시장 전체로 임의 확장하지 않는다.
- 키워드가 특정 소재, 제품, 회사, 기술이면 그 대상 자체가 제목/본문/요약에 직접 등장하는 결과를 우선한다.
- 동명이인, 스포츠, 엔터테인먼트, 일반 시장 보고서, SEO성 광고 페이지처럼 직접 관련성이 낮은 결과는 제외한다.
- 키워드와 직접 관련성이 불확실하면 결과에 포함하지 말고 제외 사유를 남긴다.

검색 범위:
- 최근 뉴스, 공식 발표, 기업 공시, 산업 분석, 정부/기관 자료를 우선한다.
- 블로그, 광고성 시장 리포트, 출처 불명 페이지는 후순위로 둔다.
- 날짜가 확인되지 않는 자료는 최신 동향으로 확정하지 않는다.

출력 원칙:
- 결과는 사용자가 검토할 후보 자료다.
- 과장하거나 종합 결론을 만들지 않는다.
- 검색 결과 원문에 없는 해석을 추가하지 않는다.
- 각 결과마다 제목, 날짜, URL, 출처, 요약, 관련성 판단을 포함한다.
- 반드시 JSON만 출력한다."""


def _result_text(result: object) -> str:
    message = getattr(result, "message", None)
    if isinstance(message, dict):
        parts = [
            block["text"]
            for block in message.get("content", [])
            if isinstance(block, dict) and block.get("text")
        ]
        if parts:
            return "\n".join(parts).strip()
    return str(result).strip()


def _load_search_tool() -> tuple[str, object]:
    if not (os.environ.get("TAVILY_API_KEY") or os.environ.get("EXA_API_KEY")):
        raise RuntimeError("TAVILY_API_KEY 또는 EXA_API_KEY가 없어 자동 웹 검색을 비활성화했습니다.")

    for module_name, tool_name in SEARCH_TOOL_CANDIDATES:
        if "tavily" in module_name and not os.environ.get("TAVILY_API_KEY"):
            continue
        if "exa" in module_name and not os.environ.get("EXA_API_KEY"):
            continue
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        tool = getattr(module, tool_name, None)
        if tool is not None:
            return tool_name, tool
    raise RuntimeError("strands_tools에서 사용 가능한 웹 검색 도구를 찾지 못했습니다.")


def search_latest_trends(keyword: str, max_items: int = 5) -> tuple[StepResult, list[ExternalResult], str, list[SearchLog]]:
    search_keyword = keyword.strip()
    if not search_keyword:
        return StepResult(status="skipped", message="외부 검색 키워드가 없습니다."), [], "", []

    step, results, raw_text, log = _search_external_with_prompt(search_keyword, max_items=max_items)
    return step, results, raw_text, [log]


def search_external_trends(keyword: str, max_items: int = 8) -> tuple[StepResult, list[ExternalResult], str]:
    step, results, raw_text, _log = _search_external_with_prompt(keyword, max_items=max_items)
    return step, results, raw_text


def search_external_plan(plan: SearchPlan, max_items: int = 5) -> tuple[StepResult, list[ExternalResult], str, list[SearchLog]]:
    if not plan.needs_external_search or not plan.topics:
        return StepResult(status="skipped", message=plan.reason or "외부 검색 계획이 없습니다."), [], "", []

    topic_text = "; ".join(topic.query or topic.topic for topic in plan.topics)
    step, results, raw_text, log = _search_external_with_prompt(topic_text, max_items=max_items)
    return step, results, raw_text, [log]


def _search_external_with_prompt(keyword: str, max_items: int) -> tuple[StepResult, list[ExternalResult], str, SearchLog]:
    try:
        from strands import Agent

        tool_name, search_tool = _load_search_tool()
    except Exception as exc:
        error = f"웹 검색 도구 로드 실패: {type(exc).__name__}: {exc}"
        return (
            StepResult(status="error", error=error),
            [],
            "",
            SearchLog(topic="외부 동향", query=keyword, status="error", error=error),
        )

    prompt = _build_web_search_prompt(keyword, max_items, tool_name)
    try:
        agent = Agent(
            model=MODEL_ID,
            system_prompt=WEB_SEARCH_SYSTEM_PROMPT,
            tools=[search_tool],
            callback_handler=None,
        )
        raw_text = _result_text(agent(prompt))
    except Exception as exc:
        error = f"웹 검색 실행 실패: {type(exc).__name__}: {exc}"
        return (
            StepResult(status="error", error=error),
            [],
            "",
            SearchLog(topic="외부 동향", query=keyword, status="error", error=error),
        )

    payload = _extract_json_payload(raw_text)
    if isinstance(payload, dict) and payload.get("status") == "external_search_unavailable":
        error = str(payload.get("error") or "외부 검색 불가")
        query_used = str(payload.get("query_used") or keyword)
        return (
            StepResult(status="error", error=error),
            [],
            raw_text,
            SearchLog(topic="외부 동향", query=query_used, status="error", error=error),
        )

    results = _parse_external_results_from_payload(payload)
    excluded = _parse_excluded_results_from_payload(payload)
    query_used = _query_used(payload, keyword)
    excluded_note = _format_excluded_note(excluded)
    message = f"{tool_name} 기반 외부 동향 수집 완료: {len(results)}건"
    if excluded_note:
        message = f"{message}; {excluded_note}"
    if not results:
        message = f"{tool_name} 기반 외부 검색은 실행됐지만 구조화된 결과가 없습니다."
        if excluded_note:
            message = f"{message}; {excluded_note}"
    return (
        StepResult(status="ok", message=message),
        results,
        raw_text,
        SearchLog(topic="외부 동향", query=query_used, status="ok", message=message, result_count=len(results)),
    )


def _build_web_search_prompt(keyword: str, max_items: int, tool_name: str) -> str:
    return f"""사용자 키워드: {keyword}
사용 가능한 웹 검색 도구: {tool_name}
수집할 최대 결과 수: {max_items}

작업:
1. 웹 검색 도구를 사용해 사용자 키워드와 직접 관련된 최신 외부 동향을 수집하세요.
2. 키워드의 의미를 공급망, 정책, 산업 전체로 임의 확장하지 마세요.
3. 검색 결과가 사용자 키워드와 직접 관련이 없으면 결과에 넣지 말고 excluded_results에 제외 사유를 남기세요.
4. 검색 도구/API 키/네트워크 문제로 실패하면 실패 사유를 구조화해서 반환하세요.
5. 결과는 리포트가 아니라 사용자가 검토할 후보 자료입니다. 종합 결론을 만들지 마세요.

성공 시 JSON 형식:
{{
  "status": "ok",
  "query_used": "실제로 사용한 검색어",
  "results": [
    {{
      "title": "제목",
      "date": "날짜 또는 확인 불가",
      "url": "URL",
      "source": "매체/사이트 또는 확인 불가",
      "summary": "2~3문장 요약",
      "relevance": "direct 또는 partial",
      "relevance_reason": "키워드와 관련 있다고 판단한 이유"
    }}
  ],
  "excluded_results": [
    {{
      "title": "제외한 결과 제목",
      "reason": "제외 사유"
    }}
  ]
}}

실패 시 JSON 형식:
{{
  "status": "external_search_unavailable",
  "query_used": "시도한 검색어",
  "error": "실패 사유"
}}

JSON 외의 설명은 출력하지 마세요."""


def _extract_json_payload(raw_text: str) -> Any:
    text = raw_text.strip()
    if not text:
        return None

    start_obj = text.find("{")
    end_obj = text.rfind("}")
    if start_obj != -1 and end_obj > start_obj:
        try:
            return json.loads(text[start_obj : end_obj + 1])
        except json.JSONDecodeError:
            pass

    start_arr = text.find("[")
    end_arr = text.rfind("]")
    if start_arr != -1 and end_arr > start_arr:
        try:
            return json.loads(text[start_arr : end_arr + 1])
        except json.JSONDecodeError:
            pass

    return None


def _query_used(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        return str(payload.get("query_used") or fallback)
    return fallback


def _parse_external_results_from_payload(payload: Any) -> list[ExternalResult]:
    if isinstance(payload, dict):
        items = payload.get("results") or payload.get("content") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        summary = str(item.get("summary") or item.get("content") or item.get("snippet") or "").strip()
        url = str(item.get("url") or item.get("link") or "").strip()
        date = str(item.get("date") or item.get("published_date") or item.get("publishedAt") or "").strip()
        source = str(item.get("source") or item.get("publisher") or item.get("site_name") or "").strip()
        relevance = str(item.get("relevance") or "").strip()
        relevance_reason = str(item.get("relevance_reason") or "").strip()
        if not title and not summary:
            continue
        results.append(
            ExternalResult(
                title=title or "외부 검색 결과",
                summary=summary,
                url=url,
                date=date,
                source=source,
                relevance=relevance,
                relevance_reason=relevance_reason,
            )
        )
    return results


def _parse_excluded_results_from_payload(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        return []
    items = payload.get("excluded_results") or []
    excluded = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if title or reason:
            excluded.append({"title": title, "reason": reason})
    return excluded


def _format_excluded_note(excluded: list[dict[str, str]]) -> str:
    if not excluded:
        return ""
    samples = []
    for item in excluded[:3]:
        title = item.get("title") or "제목 없음"
        reason = item.get("reason") or "사유 없음"
        samples.append(f"{title}({reason})")
    suffix = "" if len(excluded) <= 3 else f" 외 {len(excluded) - 3}건"
    return f"제외 {len(excluded)}건: " + "; ".join(samples) + suffix


def parse_manual_external_notes(text: str) -> list[ExternalResult]:
    text = text.strip()
    if not text:
        return []

    return [
        ExternalResult(
            title="수동 입력 외부 자료",
            summary=text,
            source="manual",
            relevance="manual",
            relevance_reason="사용자가 직접 입력한 외부 자료",
        )
    ]
