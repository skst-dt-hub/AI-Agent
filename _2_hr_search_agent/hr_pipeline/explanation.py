from typing import Any

from hr_pipeline.common import invoke_claude_json, load_s3_json


SYSTEM_PROMPT = """
당신은 HR 인재 추천 결과를 간결하게 설명하는 인사 분석가입니다.
반드시 JSON 객체만 반환하세요. 설명, 코드블록, 마크다운은 쓰지 마세요.

주의사항:
- 후보 정보에 없는 사실을 만들지 마세요.
- 약점은 후보 상세 텍스트 또는 정형 항목에서 근거가 있을 때만 쓰세요.
- 개인정보를 불필요하게 반복하지 말고 추천 판단에 필요한 근거만 쓰세요.

반환 스키마:
{
  "추천근거": "string",
  "강점": ["string"],
  "약점": ["string"]
}
""".strip()


def map_text_by_person_id(text_records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for record in text_records:
        person_id = str(record.get("사번", "")).strip()
        if person_id:
            result[person_id] = record
    return result


def enrich_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text_records = load_s3_json("text_data.json")
    if not isinstance(text_records, list):
        raise ValueError("text_data.json은 list 형태여야 합니다.")

    text_by_id = map_text_by_person_id(text_records)
    enriched = []
    for candidate in candidates:
        person_id = str(candidate.get("사번", "")).strip()
        text_record = text_by_id.get(person_id, {})
        enriched.append(
            {
                **candidate,
                "상세텍스트": text_record.get("텍스트", ""),
            }
        )
    return enriched


def score_label(candidate: dict[str, Any], key: str) -> str:
    item = (candidate.get("점수상세") or {}).get(key)
    if not item:
        return "미적용"
    return f"{item.get('획득점수', 0)} / {item.get('만점', 0)}"


def team_leading_label(candidate: dict[str, Any]) -> str:
    if not candidate.get("팀리딩여부"):
        return "N"
    member_count = candidate.get("팀리딩인원", "")
    period = candidate.get("팀리딩기간_월", "")
    return f"Y ({member_count}명/{period}개월)"


def explain_one_candidate(original_query: str, candidate: dict[str, Any]) -> dict[str, Any]:
    compact_payload = {
        "original_query": original_query,
        "candidate": {
            "성명": candidate.get("성명"),
            "소속조직": candidate.get("소속조직"),
            "최종Score": candidate.get("최종Score"),
            "경력연수": candidate.get("경력연수"),
            "현재직무명": candidate.get("현재직무명"),
            "팀리딩여부": candidate.get("팀리딩여부"),
            "팀리딩인원": candidate.get("팀리딩인원"),
            "팀리딩기간_월": candidate.get("팀리딩기간_월"),
            "이동가능시점": candidate.get("이동가능시점"),
            "점수상세": candidate.get("점수상세"),
            "상세텍스트": candidate.get("상세텍스트", ""),
        },
    }
    prompt = (
        "다음 후보에 대해 추천근거, 강점, 약점을 생성하세요. "
        "점수는 입력된 산출 결과를 근거로 설명하세요.\n"
        f"{compact_payload}"
    )
    result = invoke_claude_json(SYSTEM_PROMPT, prompt, max_tokens=1200)
    return {
        "추천근거": str(result.get("추천근거", "")).strip(),
        "강점": result.get("강점") if isinstance(result.get("강점"), list) else [],
        "약점": result.get("약점") if isinstance(result.get("약점"), list) else [],
    }


def build_comparison_table(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table = []
    for candidate in candidates:
        table.append(
            {
                "성명": candidate.get("성명"),
                "최종Score": candidate.get("최종Score"),
                "직무적합도": score_label(candidate, "직무적합도"),
                "경험깊이": score_label(candidate, "경험깊이"),
                "리더십": score_label(candidate, "리더십"),
                "이동배치": score_label(candidate, "이동배치"),
                "경력연수": candidate.get("경력연수"),
                "팀리딩": team_leading_label(candidate),
                "이동가능시점": candidate.get("이동가능시점"),
            }
        )
    return table


def build_summary(scoring_result: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "조건에 맞는 추천 후보가 없습니다."
    top = candidates[0]
    return (
        f"총 {scoring_result.get('total_before_filter', 0)}명 중 "
        f"{scoring_result.get('total_after_filter', 0)}명이 정형 조건을 통과했고, "
        f"최종Score 기준 상위 {len(candidates)}명을 분석했습니다. "
        f"가장 적합한 후보는 {top.get('성명')}이며 최종Score는 {top.get('최종Score')}점입니다."
    )


def explain_candidates(scoring_result: dict[str, Any]) -> dict[str, Any]:
    candidates = scoring_result.get("scored_candidates") or scoring_result.get("candidates") or []
    if not isinstance(candidates, list):
        raise ValueError("scored_candidates는 list 형태여야 합니다.")

    original_query = str(scoring_result.get("original_query", "")).strip()
    enriched_candidates = enrich_candidates(candidates[:5])
    ranked_candidates = []

    for candidate in enriched_candidates:
        explanation = explain_one_candidate(original_query, candidate)
        ranked_candidates.append(
            {
                "순위": candidate.get("순위"),
                "사번": candidate.get("사번"),
                "성명": candidate.get("성명"),
                "소속조직": candidate.get("소속조직"),
                "최종Score": candidate.get("최종Score"),
                "유사도": candidate.get("유사도"),
                "추천근거": explanation["추천근거"],
                "강점": explanation["강점"],
                "약점": explanation["약점"],
            }
        )

    return {
        "ranked_candidates": ranked_candidates,
        "comparison_table": build_comparison_table(candidates[:10]),
        "summary": build_summary(scoring_result, candidates[:10]),
        "original_query": original_query,
        "scored_candidates": candidates[:10],
        "score_policy": scoring_result.get("score_policy", {}),
    }
