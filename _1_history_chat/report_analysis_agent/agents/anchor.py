from __future__ import annotations

from config import MODEL_ID
from models import AnalysisRun, StepResult


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


def build_markdown_report(run: AnalysisRun) -> tuple[StepResult, str]:
    prompt = _build_prompt(run)

    try:
        from strands import Agent

        agent = Agent(
            model=MODEL_ID,
            system_prompt="""당신은 내부 자료와 외부 동향을 종합해 보고용 Markdown 리포트를 작성하는 분석가입니다.
근거가 부족한 내용은 추정하지 말고 확인 필요로 표시하세요.
내부 문서 근거와 외부 웹 근거를 명확히 구분하세요.""",
            callback_handler=None,
        )
        report = _result_text(agent(prompt))
        if report:
            return StepResult(status="ok", message="Markdown 리포트 생성 완료"), report
    except Exception as exc:
        fallback = _fallback_report(run, f"{type(exc).__name__}: {exc}")
        return StepResult(status="error", error=f"Anchor Agent 실행 실패: {type(exc).__name__}: {exc}"), fallback

    return StepResult(status="error", error="Anchor Agent가 빈 응답을 반환했습니다."), _fallback_report(run)


def _build_prompt(run: AnalysisRun) -> str:
    internal = "\n\n".join(
        f"- 출처: {item.source}\n- score: {item.score}\n- 내용: {item.content}"
        for item in run.internal_results
    ) or "(내부 검색 결과 없음)"

    external = "\n\n".join(
        f"- 제목: {item.title}\n- 날짜: {item.date}\n- URL: {item.url}\n- 요약: {item.summary}"
        for item in run.external_results
    ) or f"(외부 검색 결과 없음. 상태: {run.ranger.status}, 오류: {run.ranger.error})"

    return f"""키워드: {run.keyword}

[내부 문서 검색 결과]
{internal}

[외부 웹 검색 결과]
{external}

아래 형식으로 Markdown 리포트를 작성하세요.

## 내부 현황 (문서 기반)

## 외부 동향 (웹 검색 기반)

## 종합 결론

## 참고 출처
"""


def _fallback_report(run: AnalysisRun, reason: str = "") -> str:
    lines = [
        f"# {run.keyword} 분석 리포트",
        "",
        "## 내부 현황 (KB 기반)",
    ]
    if run.internal_results:
        for item in run.internal_results[:10]:
            score = f" / score: {item.score:.4f}" if item.score is not None else ""
            lines.extend([f"- 출처: {item.source}{score}", f"  - {item.content[:500]}"])
    else:
        lines.append("- 내부 검색 결과가 없습니다.")

    lines.extend(["", "## 외부 동향 (웹 검색 기반)"])
    if run.external_results:
        for item in run.external_results:
            lines.append(f"- {item.title} ({item.date}) - {item.summary} {item.url}")
    else:
        detail = run.ranger.error or run.ranger.message or "외부 검색 결과가 없습니다."
        lines.append(f"- {detail}")

    lines.extend(
        [
            "",
            "## 종합 결론",
            "- 자동 분석 리포트 생성에 실패했거나 제한되어, 수집 결과 중심의 fallback 리포트를 생성했습니다.",
            "",
            "## 참고 출처",
            "- 내부 출처와 외부 URL은 Excel 파일의 Internal/External 시트에서 확인하세요.",
        ]
    )
    if reason:
        lines.extend(["", f"> 생성 제한 사유: {reason}"])
    return "\n".join(lines)
