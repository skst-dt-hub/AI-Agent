from typing import Any

from .common import cosine_similarity, invoke_titan_embedding, load_s3_json, normalize_bool


MOVE_TIMING_ORDER = {
    "즉시": 0,
    "3개월내": 1,
    "6개월내": 2,
    "1년내": 3,
}


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


def get_int(record: dict[str, Any], key: str) -> int | None:
    value = record.get(key)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def matches_hard_conditions(record: dict[str, Any], hard_conditions: dict[str, Any]) -> bool:
    career_min = hard_conditions.get("경력연수_최소")
    if career_min is not None:
        career_years = get_int(record, "경력연수")
        if career_years is None or career_years < int(career_min):
            return False

    team_leading_required = normalize_bool(hard_conditions.get("팀리딩필요"))
    if team_leading_required is not None:
        if normalize_bool(record.get("팀리딩여부")) is not team_leading_required:
            return False

    dispatch_required = normalize_bool(hard_conditions.get("파견가능"))
    if dispatch_required is not None:
        if normalize_bool(record.get("파견가능여부")) is not dispatch_required:
            return False

    overseas_required = normalize_bool(hard_conditions.get("해외근무가능"))
    if overseas_required is not None:
        if normalize_bool(record.get("해외근무가능")) is not overseas_required:
            return False

    required_timing = normalize_move_timing(hard_conditions.get("이동가능시점"))
    if required_timing is not None:
        candidate_timing = normalize_move_timing(record.get("이동가능시점"))
        if candidate_timing is None:
            return False
        if MOVE_TIMING_ORDER[candidate_timing] > MOVE_TIMING_ORDER[required_timing]:
            return False

    return True


def build_soft_query(original_query: str, soft_conditions: dict[str, Any]) -> str:
    job_keywords = soft_conditions.get("직무키워드") or []
    capability_keywords = soft_conditions.get("역량키워드") or []
    parts = [original_query]
    parts.extend(str(item) for item in job_keywords)
    parts.extend(str(item) for item in capability_keywords)
    return " ".join(part.strip() for part in parts if str(part).strip())


def retrieve_candidates(requirements: dict[str, Any], top_k: int = 10) -> dict[str, Any]:
    hard_conditions = requirements.get("hard_conditions") or {}
    soft_conditions = requirements.get("soft_conditions") or {}
    original_query = str(requirements.get("original_query", "")).strip()

    structured_records = load_s3_json("structured_data.json")
    embeddings = load_s3_json("embeddings.json")

    if not isinstance(structured_records, list):
        raise ValueError("structured_data.json은 list 형태여야 합니다.")
    if not isinstance(embeddings, dict):
        raise ValueError("embeddings.json은 dict 형태여야 합니다.")

    filtered = [
        record
        for record in structured_records
        if matches_hard_conditions(record, hard_conditions)
    ]

    soft_query = build_soft_query(original_query, soft_conditions) or original_query
    query_embedding = invoke_titan_embedding(soft_query)

    all_scored = []
    for record in structured_records:
        person_id = str(record.get("사번", "")).strip()
        embedding = embeddings.get(person_id)
        if not embedding:
            continue
        similarity = cosine_similarity(query_embedding, embedding)
        all_scored.append((similarity, record))

    filtered_ids = {str(record.get("사번", "")).strip() for record in filtered}
    scored = [
        (similarity, record)
        for similarity, record in all_scored
        if str(record.get("사번", "")).strip() in filtered_ids
    ]

    scored.sort(key=lambda item: item[0], reverse=True)

    candidates = []
    for similarity, record in scored[:top_k]:
        candidates.append(
            {
                "사번": record.get("사번"),
                "성명": record.get("성명"),
                "소속조직": record.get("소속조직"),
                "경력연수": record.get("경력연수"),
                "팀리딩여부": record.get("팀리딩여부"),
                "이동가능시점": record.get("이동가능시점"),
                "파견가능여부": record.get("파견가능여부"),
                "해외근무가능": record.get("해외근무가능"),
                "유사도": round(float(similarity), 4),
            }
        )

    return {
        "candidates": candidates,
        "total_before_filter": len(structured_records),
        "total_after_filter": len(filtered),
        "original_query": original_query,
        "hard_conditions": hard_conditions,
        "soft_conditions": soft_conditions,
        "all_similarity_scores": {
            str(record.get("사번", "")).strip(): round(float(similarity), 6)
            for similarity, record in all_scored
            if str(record.get("사번", "")).strip()
        },
    }
