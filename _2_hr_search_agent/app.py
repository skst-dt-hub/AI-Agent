import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from hr_pipeline.candidate_retrieval import retrieve_candidates
from hr_pipeline.explanation import explain_candidates
from hr_pipeline.query_understanding import understand_query
from hr_pipeline.scoring import score_candidates


RESULT_PATH = Path("result.json")
EXAMPLE_QUERIES = [
    "자금운영 경험 10년 이상이고 리더십 있는 사람 찾아줘",
    "Cash Flow 관리 경험이 있고 금융기관 대응을 해본 사람 중 팀리딩 경험 있는 후보 추천해줘",
    "해외근무 가능하고 영업관리 경험이 있는 사람 추천해줘",
    "생산운영 경험이 10년 이상이고 즉시 이동 가능한 사람 알려줘",
    "전략 수립, 비용 절감, 경영진 보고 경험이 있는 사람 추천해줘",
]


def run_search(query: str) -> dict:
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


def save_latest_result(result: dict) -> None:
    RESULT_PATH.write_text(
        json.dumps(result["explanation"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def render_candidate(candidate: dict) -> None:
    st.markdown(f"### {candidate.get('순위')}. {candidate.get('성명')} · {candidate.get('소속조직')}")
    cols = st.columns(4)
    cols[0].metric("최종Score", candidate.get("최종Score", "-"))
    cols[1].metric("사번", candidate.get("사번", "-"))
    cols[2].metric("강점 수", len(candidate.get("강점") or []))
    cols[3].metric("약점 수", len(candidate.get("약점") or []))

    st.write(candidate.get("추천근거", ""))

    strength_col, weakness_col = st.columns(2)
    with strength_col:
        st.markdown("**강점**")
        strengths = candidate.get("강점") or []
        if strengths:
            for item in strengths:
                st.markdown(f"- {item}")
        else:
            st.caption("표시할 강점이 없습니다.")

    with weakness_col:
        st.markdown("**약점**")
        weaknesses = candidate.get("약점") or []
        if weaknesses:
            for item in weaknesses:
                st.markdown(f"- {item}")
        else:
            st.caption("표시할 약점이 없습니다.")


def render_score_details(scored_candidate: dict) -> None:
    details = scored_candidate.get("점수상세") or {}
    score_cols = st.columns(4)
    for idx, key in enumerate(["직무적합도", "경험깊이", "리더십", "이동배치"]):
        item = details.get(key)
        if item:
            score_cols[idx].metric(key, f"{item.get('획득점수', 0)} / {item.get('만점', 0)}")
        else:
            score_cols[idx].metric(key, "미적용")

    with st.expander("점수 산출 상세"):
        st.json(
            {
                "최종Score": scored_candidate.get("최종Score"),
                "획득점수": scored_candidate.get("획득점수"),
                "적용가능점수": scored_candidate.get("적용가능점수"),
                "점수상세": details,
                "Score산출근거": scored_candidate.get("Score산출근거"),
            }
        )


def render_result(result: dict) -> None:
    explanation = result["explanation"]
    retrieval = result["retrieval"]
    scoring = result["scoring"]
    understood = result["query_understanding"]

    st.subheader("요약")
    st.write(explanation.get("summary", ""))

    before = retrieval.get("total_before_filter", 0)
    after = retrieval.get("total_after_filter", 0)
    top_count = len(scoring.get("scored_candidates") or [])
    metric_cols = st.columns(3)
    metric_cols[0].metric("전체 인원", before)
    metric_cols[1].metric("필터 후", after)
    metric_cols[2].metric("추천 후보", top_count)

    table = explanation.get("comparison_table") or []
    if table:
        st.subheader("후보 비교")
        st.dataframe(pd.DataFrame(table), use_container_width=True, hide_index=True)

    st.subheader("추천 후보")
    ranked_candidates = explanation.get("ranked_candidates") or []
    scored_by_id = {
        str(candidate.get("사번", "")): candidate
        for candidate in scoring.get("scored_candidates") or []
    }
    if ranked_candidates:
        for idx, candidate in enumerate(ranked_candidates):
            if idx:
                st.divider()
            render_candidate(candidate)
            scored_candidate = scored_by_id.get(str(candidate.get("사번", "")))
            if scored_candidate:
                render_score_details(scored_candidate)
    else:
        st.info("추천 후보가 없습니다.")

    with st.expander("질의 해석 보기"):
        st.json(understood)

    with st.expander("검색 결과 원문 보기"):
        st.json(retrieval)

    with st.expander("점수 산출 결과 보기"):
        st.json(scoring)

    with st.expander("최종 응답 JSON 보기"):
        st.json(explanation)


def init_state() -> None:
    if "history" not in st.session_state:
        st.session_state.history = []
    if "query_text" not in st.session_state:
        st.session_state.query_text = ""


def set_example_query(query: str) -> None:
    st.session_state.query_text = query


def main() -> None:
    st.set_page_config(
        page_title="HR Search Agent",
        page_icon="",
        layout="wide",
    )
    init_state()

    st.title("HR Search Agent")
    st.caption("자연어로 조건을 입력하면 정형 필터와 임베딩 검색을 결합해 후보를 추천합니다.")

    with st.sidebar:
        st.header("질의 예시")
        for query in EXAMPLE_QUERIES:
            st.button(query, key=f"example-{query}", on_click=set_example_query, args=(query,))

        st.divider()
        if st.button("대화 기록 지우기"):
            st.session_state.history = []

    with st.form("query-form", clear_on_submit=False):
        query = st.text_area(
            "질의",
            key="query_text",
            height=110,
            placeholder="예: 자금운영 경험 10년 이상이고 리더십 있는 사람 찾아줘",
        )
        submitted = st.form_submit_button("검색")

    if submitted:
        query = query.strip()
        if not query:
            st.warning("질의를 입력하세요.")
        else:
            with st.spinner("후보를 검색하고 추천 근거를 생성하는 중입니다..."):
                try:
                    result = run_search(query)
                    save_latest_result(result)
                    st.session_state.history.insert(
                        0,
                        {
                            "query": query,
                            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "result": result,
                        },
                    )
                except Exception as exc:
                    st.error(f"처리 중 오류가 발생했습니다: {exc}")

    if not st.session_state.history:
        st.info("질의를 입력하거나 왼쪽 예시를 선택해 검색을 시작하세요.")
        return

    latest = st.session_state.history[0]
    st.markdown(f"## 최근 질의")
    st.write(latest["query"])
    st.caption(latest["created_at"])
    render_result(latest["result"])

    if len(st.session_state.history) > 1:
        st.divider()
        st.subheader("이전 질의")
        for item in st.session_state.history[1:]:
            with st.expander(f"{item['created_at']} · {item['query']}"):
                render_result(item["result"])


if __name__ == "__main__":
    main()
