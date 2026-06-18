from typing import Any

from .common import invoke_claude_json


SYSTEM_PROMPT = """
당신은 HR 인재 검색 질의를 구조화하는 분석기입니다.
반드시 JSON 객체만 반환하세요. 설명, 코드블록, 마크다운은 쓰지 마세요.

하드 조건은 정형 데이터로 필터링 가능한 조건입니다.
- 경력연수_최소: number 또는 null
- 팀리딩필요: boolean 또는 null
- 이동가능시점: "즉시", "3개월내", "6개월내", "1년내" 또는 null
- 파견가능: boolean 또는 null
- 해외근무가능: boolean 또는 null

소프트 조건은 텍스트 유사도 판단에 사용할 키워드입니다.
- 직무키워드: string array
- 역량키워드: string array

반환 스키마:
{
  "hard_conditions": {
    "경력연수_최소": null,
    "팀리딩필요": null,
    "이동가능시점": null,
    "파견가능": null,
    "해외근무가능": null
  },
  "soft_conditions": {
    "직무키워드": [],
    "역량키워드": []
  }
}
""".strip()


def normalize_move_timing(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace(" ", "")
    aliases = {
        "즉시": "즉시",
        "바로": "즉시",
        "3개월": "3개월내",
        "3개월내": "3개월내",
        "3개월이내": "3개월내",
        "6개월": "6개월내",
        "6개월내": "6개월내",
        "6개월이내": "6개월내",
        "1년": "1년내",
        "1년내": "1년내",
        "1년이내": "1년내",
    }
    return aliases.get(text)


def normalize_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "y", "yes", "필요", "가능"}:
        return True
    if text in {"false", "n", "no", "불필요", "불가능"}:
        return False
    return None


def normalize_keywords(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def normalize_result(result: dict[str, Any], original_query: str) -> dict[str, Any]:
    hard = result.get("hard_conditions") or {}
    soft = result.get("soft_conditions") or {}

    career_min = hard.get("경력연수_최소")
    try:
        career_min = int(career_min) if career_min is not None else None
    except (TypeError, ValueError):
        career_min = None

    return {
        "hard_conditions": {
            "경력연수_최소": career_min,
            "팀리딩필요": normalize_bool(hard.get("팀리딩필요")),
            "이동가능시점": normalize_move_timing(hard.get("이동가능시점")),
            "파견가능": normalize_bool(hard.get("파견가능")),
            "해외근무가능": normalize_bool(hard.get("해외근무가능")),
        },
        "soft_conditions": {
            "직무키워드": normalize_keywords(soft.get("직무키워드")),
            "역량키워드": normalize_keywords(soft.get("역량키워드")),
        },
        "original_query": original_query,
    }


def understand_query(query: str) -> dict[str, Any]:
    query = str(query).strip()
    if not query:
        raise ValueError("query 값이 필요합니다.")

    result = invoke_claude_json(SYSTEM_PROMPT, f"질의: {query}")
    return normalize_result(result, query)

