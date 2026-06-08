import math
from typing import Any

from hr_pipeline.candidate_retrieval import MOVE_TIMING_ORDER, normalize_move_timing
from hr_pipeline.common import load_s3_json, normalize_bool


DIFFICULTY_SCORE = {"상": 3, "중": 2, "하": 1}
REPLACEABILITY_SCORE = {"하": 3, "중": 2, "상": 1}
LEADERSHIP_KEYWORDS = {"리더십", "팀리딩", "조직관리", "조직 관리", "관리역량", "매니징"}
SCORE_REASON = (
    "직무적합도는 전체 구성원 유사도 분포를 Z-score 정규화해 50점 스케일로 환산하고, "
    "경험깊이는 경력요건충족도와 주요업무깊이를 합산했습니다. "
    "리더십과 이동/배치 점수는 질의에 해당 조건이 있을 때만 적용했습니다."
)


def to_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value: Any, default: int = 0) -> int:
    return int(to_float(value, default))


def round_score(value: float) -> float:
    return round(float(value), 1)


def normal_cdf(z_score: float) -> float:
    return 0.5 * (1 + math.erf(z_score / math.sqrt(2)))


def percentile(value: float, distribution: list[float]) -> float:
    values = [to_float(item) for item in distribution]
    n = len(values)
    if n == 0:
        return 0.0
    if n == 1:
        return 1.0

    lower_count = sum(1 for item in values if item < value)
    equal_count = sum(1 for item in values if item == value)
    return (lower_count + 0.5 * equal_count) / n


def calc_job_fit_score(similarity: float, all_similarity_scores: list[float]) -> dict[str, Any]:
    scores = [to_float(score) for score in all_similarity_scores]
    mean_value = sum(scores) / len(scores) if scores else 0.0
    variance = sum((score - mean_value) ** 2 for score in scores) / len(scores) if scores else 0.0
    std_value = math.sqrt(variance)

    if not scores or std_value == 0:
        z_score = 0.0
        percentile_value = 50.0
    else:
        z_score = (similarity - mean_value) / std_value
        percentile_value = normal_cdf(z_score) * 100

    converted_score = percentile_value * 0.5
    return {
        "항목": "직무적합도",
        "획득점수": round_score(converted_score),
        "만점": 50,
        "산출과정": {
            "코사인유사도": round(float(similarity), 4),
            "전체평균": round(mean_value, 4),
            "표준편차": round(std_value, 4),
            "Z-score": round(z_score, 3),
            "백분위": round(percentile_value, 1),
            "환산식": "백분위 * 0.5",
        },
    }


def get_career_requirement(hard_conditions: dict[str, Any]) -> int | None:
    value = hard_conditions.get("경력연수_최소")
    if value is None:
        return None
    parsed = to_int(value)
    return parsed if parsed > 0 else None


def calc_career_score(candidate: dict[str, Any], all_structured_data: list[dict[str, Any]], hard_conditions: dict[str, Any]) -> dict[str, Any]:
    career_years = to_float(candidate.get("경력연수"))
    required_years = get_career_requirement(hard_conditions)

    if required_years:
        ratio = min(career_years / required_years, 1.0)
        score = ratio * 10
        process = {
            "계산방식": "요구연수 충족도",
            "경력연수": career_years,
            "요구연수": required_years,
            "충족비율": round(ratio, 3),
        }
    else:
        score = 7.0
        process = {
            "계산방식": "경력 조건 없음 - 중립 기본점",
            "경력연수": career_years,
            "기본점": score,
        }

    return {
        "획득점수": round_score(score),
        "만점": 10,
        "산출과정": process,
    }


def task_depth_raw_score(record: dict[str, Any]) -> tuple[float, dict[str, str]]:
    total = 0.0
    details = {}
    for index in (1, 2, 3):
        weight = to_float(record.get(f"주요업무{index}_비중"))
        difficulty_text = str(record.get(f"주요업무{index}_난이도", "")).strip()
        replaceability_text = str(record.get(f"주요업무{index}_대체가능성", "")).strip()
        difficulty = DIFFICULTY_SCORE.get(difficulty_text, 0)
        replaceability = REPLACEABILITY_SCORE.get(replaceability_text, 0)
        raw = weight * difficulty * replaceability
        total += raw
        details[f"업무{index}"] = f"{weight:g} * {difficulty} * {replaceability} = {raw:g}"
    return total, details


def calc_task_depth_score(candidate: dict[str, Any], all_structured_data: list[dict[str, Any]]) -> dict[str, Any]:
    raw_score, details = task_depth_raw_score(candidate)
    distribution = [task_depth_raw_score(record)[0] for record in all_structured_data]
    percentile_value = percentile(raw_score, distribution)
    score = percentile_value * 10

    return {
        "획득점수": round_score(score),
        "만점": 10,
        "산출과정": {
            **details,
            "개인합산": round(raw_score, 1),
            "백분위": round(percentile_value * 100, 1),
        },
    }


def calc_experience_score(candidate: dict[str, Any], all_structured_data: list[dict[str, Any]], hard_conditions: dict[str, Any]) -> dict[str, Any]:
    career_result = calc_career_score(candidate, all_structured_data, hard_conditions)
    task_result = calc_task_depth_score(candidate, all_structured_data)
    total = career_result["획득점수"] + task_result["획득점수"]

    return {
        "항목": "경험깊이",
        "획득점수": round_score(total),
        "만점": 20,
        "산출과정": {
            "경력요건충족도": career_result,
            "주요업무깊이": task_result,
        },
    }


def leadership_requested(requirements: dict[str, Any]) -> bool:
    hard_conditions = requirements.get("hard_conditions") or {}
    soft_conditions = requirements.get("soft_conditions") or {}
    if hard_conditions.get("팀리딩필요") is not None:
        return True

    keywords = []
    keywords.extend(soft_conditions.get("역량키워드") or [])
    keywords.extend(soft_conditions.get("직무키워드") or [])
    keyword_text = " ".join(str(item) for item in keywords)
    return any(keyword in keyword_text for keyword in LEADERSHIP_KEYWORDS)


def calc_leadership_score(candidate: dict[str, Any], all_structured_data: list[dict[str, Any]]) -> dict[str, Any]:
    if not normalize_bool(candidate.get("팀리딩여부")):
        return {
            "항목": "리더십",
            "획득점수": 0.0,
            "만점": 15,
            "적용": True,
            "산출과정": {"팀리딩여부": False},
        }

    member_count = to_float(candidate.get("팀리딩인원"))
    period_months = to_float(candidate.get("팀리딩기간_월"))
    member_distribution = [to_float(record.get("팀리딩인원")) for record in all_structured_data]
    period_distribution = [to_float(record.get("팀리딩기간_월")) for record in all_structured_data]
    member_percentile = percentile(member_count, member_distribution)
    period_percentile = percentile(period_months, period_distribution)

    base_score = 7
    member_score = member_percentile * 4
    period_score = period_percentile * 4
    total = base_score + member_score + period_score

    return {
        "항목": "리더십",
        "획득점수": round_score(total),
        "만점": 15,
        "적용": True,
        "산출과정": {
            "팀리딩여부": True,
            "팀리딩경험보유": "7점",
            "팀리딩인원": member_count,
            "팀리딩인원_백분위": round(member_percentile * 100, 1),
            "팀리딩인원점수": round_score(member_score),
            "팀리딩기간_월": period_months,
            "팀리딩기간_백분위": round(period_percentile * 100, 1),
            "팀리딩기간점수": round_score(period_score),
        },
    }


def mobility_requested(requirements: dict[str, Any]) -> bool:
    hard_conditions = requirements.get("hard_conditions") or {}
    return any(
        hard_conditions.get(key) is not None
        for key in ("이동가능시점", "파견가능", "해외근무가능")
    )


def calc_move_timing_score(candidate_timing: Any, required_timing: Any) -> tuple[float, dict[str, Any]]:
    required = normalize_move_timing(required_timing)
    candidate = normalize_move_timing(candidate_timing)
    if required is None:
        return 0.0, {"적용": False}
    if candidate is None:
        return 0.0, {"요구": required, "후보": None, "점수": 0}

    required_rank = MOVE_TIMING_ORDER[required]
    candidate_rank = MOVE_TIMING_ORDER[candidate]
    if candidate_rank > required_rank:
        score = 0.0
    else:
        score_map = {0: 4.0, 1: 3.0, 2: 2.0, 3: 1.0}
        score = score_map.get(candidate_rank, 0.0)
    return score, {"요구": required, "후보": candidate, "점수": score}


def calc_boolean_condition_score(candidate_value: Any, required_value: Any, max_score: float) -> tuple[float, dict[str, Any]]:
    required = normalize_bool(required_value)
    if required is None:
        return 0.0, {"적용": False}

    candidate = normalize_bool(candidate_value)
    score = max_score if candidate is required else 0.0
    return score, {"요구": required, "후보": candidate, "점수": score}


def calc_mobility_score(candidate: dict[str, Any], hard_conditions: dict[str, Any]) -> dict[str, Any]:
    move_score, move_process = calc_move_timing_score(
        candidate.get("이동가능시점"),
        hard_conditions.get("이동가능시점"),
    )
    dispatch_score, dispatch_process = calc_boolean_condition_score(
        candidate.get("파견가능여부"),
        hard_conditions.get("파견가능"),
        3,
    )
    overseas_score, overseas_process = calc_boolean_condition_score(
        candidate.get("해외근무가능"),
        hard_conditions.get("해외근무가능"),
        3,
    )
    total = move_score + dispatch_score + overseas_score

    return {
        "항목": "이동배치",
        "획득점수": round_score(total),
        "만점": 10,
        "적용": True,
        "산출과정": {
            "이동가능시점": move_process,
            "파견가능여부": dispatch_process,
            "해외근무가능": overseas_process,
        },
    }


def merge_candidate_with_structured(candidate: dict[str, Any], structured_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    person_id = str(candidate.get("사번", "")).strip()
    structured = structured_by_id.get(person_id, {})
    return {**structured, **candidate}


def score_one_candidate(
    candidate: dict[str, Any],
    requirements: dict[str, Any],
    all_structured_data: list[dict[str, Any]],
    all_similarity_scores: dict[str, float],
) -> dict[str, Any]:
    person_id = str(candidate.get("사번", "")).strip()
    similarity = to_float(candidate.get("유사도", all_similarity_scores.get(person_id, 0.0)))
    all_similarity_values = list(all_similarity_scores.values())
    hard_conditions = requirements.get("hard_conditions") or {}

    job_fit = calc_job_fit_score(similarity, all_similarity_values)
    experience = calc_experience_score(candidate, all_structured_data, hard_conditions)
    score_details = {
        "직무적합도": job_fit,
        "경험깊이": experience,
    }

    acquired = job_fit["획득점수"] + experience["획득점수"]
    possible = job_fit["만점"] + experience["만점"]

    if leadership_requested(requirements):
        leadership = calc_leadership_score(candidate, all_structured_data)
        score_details["리더십"] = leadership
        acquired += leadership["획득점수"]
        possible += leadership["만점"]

    if mobility_requested(requirements):
        mobility = calc_mobility_score(candidate, hard_conditions)
        score_details["이동배치"] = mobility
        acquired += mobility["획득점수"]
        possible += mobility["만점"]

    final_score = (acquired / possible * 100) if possible else 0.0

    return {
        **candidate,
        "최종Score": round_score(final_score),
        "획득점수": round_score(acquired),
        "적용가능점수": round_score(possible),
        "점수상세": score_details,
        "Score산출근거": SCORE_REASON,
    }


def build_score_policy(requirements: dict[str, Any]) -> dict[str, int]:
    return {
        "직무적합도": 50,
        "경험깊이": 20,
        "리더십": 15 if leadership_requested(requirements) else 0,
        "이동배치": 10 if mobility_requested(requirements) else 0,
    }


def score_candidates(retrieval_output: dict[str, Any]) -> dict[str, Any]:
    all_structured_data = load_s3_json("structured_data.json")
    if not isinstance(all_structured_data, list):
        raise ValueError("structured_data.json은 list 형태여야 합니다.")

    structured_by_id = {
        str(record.get("사번", "")).strip(): record
        for record in all_structured_data
        if str(record.get("사번", "")).strip()
    }
    all_similarity_scores = {
        str(person_id): to_float(score)
        for person_id, score in (retrieval_output.get("all_similarity_scores") or {}).items()
    }

    requirements = {
        "hard_conditions": retrieval_output.get("hard_conditions") or {},
        "soft_conditions": retrieval_output.get("soft_conditions") or {},
        "original_query": retrieval_output.get("original_query", ""),
    }

    scored_candidates = []
    for candidate in retrieval_output.get("candidates") or []:
        merged = merge_candidate_with_structured(candidate, structured_by_id)
        scored_candidates.append(
            score_one_candidate(
                merged,
                requirements,
                all_structured_data,
                all_similarity_scores,
            )
        )

    scored_candidates.sort(key=lambda item: item.get("최종Score", 0), reverse=True)
    for index, candidate in enumerate(scored_candidates, start=1):
        candidate["순위"] = index

    return {
        "scored_candidates": scored_candidates,
        "retrieval_candidates": retrieval_output.get("candidates") or [],
        "total_before_filter": retrieval_output.get("total_before_filter", 0),
        "total_after_filter": retrieval_output.get("total_after_filter", 0),
        "original_query": retrieval_output.get("original_query", ""),
        "hard_conditions": retrieval_output.get("hard_conditions") or {},
        "soft_conditions": retrieval_output.get("soft_conditions") or {},
        "score_policy": build_score_policy(requirements),
    }
