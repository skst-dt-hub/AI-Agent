from __future__ import annotations

import json
from datetime import date

from .config import MODEL_ID
from .models import InternalResult, SearchPlan, SearchTopic, StepResult


MAX_PLANNER_EXCERPTS = 5
EXCERPT_LENGTH = 300


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


def plan_external_search(
    question: str,
    internal_results: list[InternalResult],
    intensity: str,
) -> tuple[StepResult, SearchPlan, str]:
    excerpts = _build_excerpts(internal_results)
    if not excerpts:
        plan = SearchPlan(
            needs_external_search=False,
            reason="내부 검색 결과가 없어 외부 검색 계획을 만들지 않았습니다.",
            intensity=intensity,
        )
        return StepResult(status="skipped", message=plan.reason), plan, ""

    prompt = _build_prompt(question, excerpts, intensity)
    try:
        from strands import Agent

        agent = Agent(
            model=MODEL_ID,
            system_prompt=_system_prompt(),
            callback_handler=None,
        )
        raw_text = _result_text(agent(prompt))
        plan = _parse_plan(raw_text, intensity)
        return StepResult(status="ok", message=f"외부 검색 계획 생성 완료: {len(plan.topics)}개 주제"), plan, raw_text
    except Exception as exc:
        plan = SearchPlan(
            needs_external_search=False,
            reason=f"외부 검색 계획 생성 실패: {type(exc).__name__}: {exc}",
            intensity=intensity,
        )
        return StepResult(status="error", error=plan.reason), plan, ""


def _build_excerpts(internal_results: list[InternalResult]) -> list[str]:
    excerpts = []
    for item in internal_results[:MAX_PLANNER_EXCERPTS]:
        text = " ".join(item.content.split())[:EXCERPT_LENGTH]
        if text:
            excerpts.append(text)
    return excerpts


def _system_prompt() -> str:
    return """당신은 내부 문서 검색 결과를 바탕으로 외부 웹 검색 계획을 세우는 Planner입니다.
내부 문서의 출처, 파일명, 경로, metadata는 제공되지 않습니다.
제공된 내부 발췌문에서 공통 주제와 이슈만 추출해 외부 검색 query를 만드세요.
내부 전용 표현, 사내 조직명, 사람 이름, 계약 세부 조건, 파일명처럼 민감하거나 불필요한 정보는 검색어에 포함하지 마세요.
검색어는 웹 검색에 적합하도록 간결하게 작성하세요.
반드시 JSON만 출력하세요."""


def _build_prompt(question: str, excerpts: list[str], intensity: str) -> str:
    if intensity == "가볍게":
        intensity_rule = """가볍게 규칙:
- 내부 발췌문에서 가장 중심적인 키워드 또는 주제 1개만 선택하세요.
- 검색 주제는 최대 1개입니다.
- query는 넓고 짧게 작성하세요.
- 예: molybdenum, WF6, semiconductor supply chain"""
    else:
        intensity_rule = """표준 규칙:
- 내부 발췌문 상위 결과에서 공통적으로 반복되는 내용이나 이슈를 찾으세요.
- 검색 주제는 최대 3개입니다.
- query는 핵심 키워드와 이슈 유형을 결합하세요.
- 예: molybdenum patent, WF6 semiconductor specialty gas price trend, semiconductor materials supply chain risk"""

    excerpt_lines = "\n".join(f"{idx + 1}. {text}" for idx, text in enumerate(excerpts))
    return f"""현재 날짜: {date.today().isoformat()}
사용자 질문: {question}
검색 강도: {intensity}

{intensity_rule}

내부 검색 결과 발췌문:
{excerpt_lines}

아래 JSON 형식만 출력하세요.
{{
  "needs_external_search": true,
  "reason": "외부 검색이 필요한 이유 또는 불필요한 이유",
  "topics": [
    {{
      "topic": "검색 주제명",
      "query": "실제 웹 검색 query",
      "why_needed": "이 검색이 필요한 이유",
      "expected_use": "답변/리포트에서 사용할 위치"
    }}
  ]
}}

외부 검색이 필요 없으면 needs_external_search는 false, topics는 빈 배열로 출력하세요."""


def _parse_plan(raw_text: str, intensity: str) -> SearchPlan:
    payload = _extract_json(raw_text)
    topics = []
    max_topics = 1 if intensity == "가볍게" else 3
    for item in payload.get("topics", [])[:max_topics]:
        if not isinstance(item, dict):
            continue
        query = str(item.get("query", "")).strip()
        topic = str(item.get("topic", "")).strip()
        if not query or not topic:
            continue
        topics.append(
            SearchTopic(
                topic=topic,
                query=query,
                why_needed=str(item.get("why_needed", "")).strip(),
                expected_use=str(item.get("expected_use", "")).strip(),
            )
        )

    needs_external_search = bool(payload.get("needs_external_search", False)) and bool(topics)
    return SearchPlan(
        needs_external_search=needs_external_search,
        reason=str(payload.get("reason", "")).strip(),
        intensity=intensity,
        topics=topics,
    )


def _extract_json(raw_text: str) -> dict:
    text = raw_text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Planner 응답에서 JSON 객체를 찾지 못했습니다.")
    return json.loads(text[start : end + 1])
