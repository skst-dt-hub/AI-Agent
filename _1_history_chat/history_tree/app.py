import html as html_lib
import itertools
import re
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from history_tree import (
    KNOWLEDGE_BASE_ID, REGION, MODEL_ID,
    list_files_by_division, run_history_tree_structured,
)

BADGE_MAP = {
    "전략기획본부": "bp",
    "생산본부": "bt",
    "영업본부": "ba",
    "R&D본부": "bc",
    "경영지원본부": "bb",
}

TIMELINE_CSS = """
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
.wrap { font-family: sans-serif; }
.trow { display: flex; align-items: flex-start; border-bottom: 0.5px solid #ebebeb; }
.date-cell {
    width: 100px; flex-shrink: 0;
    padding: 14px 12px 14px 16px;
    text-align: right;
    border-right: 0.5px solid #ebebeb;
    position: relative;
}
.date-year { font-size: 11px; color: #aaa; }
.date-main { font-size: 13px; font-weight: 500; color: #222; }
.dot {
    position: absolute; right: -5px; top: 20px;
    width: 9px; height: 9px; border-radius: 50%;
    background: #fff; border: 1.5px solid #ccc;
}
.dot.lit { background: #7F77DD; border-color: #534AB7; }
.cards { flex: 1; padding: 10px 16px; display: flex; flex-direction: column; gap: 6px; }

details.card-wrap {
    border: 0.5px solid #e0e0e0; border-radius: 8px;
    background: #fff; overflow: hidden;
}
details.card-wrap summary {
    list-style: none; cursor: pointer;
    padding: 10px 14px;
    display: flex; align-items: flex-start; gap: 10px;
}
details.card-wrap summary::-webkit-details-marker { display: none; }
details.card-wrap summary:hover { background: #fafafa; }
.card-body { flex: 1; display: flex; flex-direction: column; gap: 4px; }
.card-summary-text { font-size: 12px; color: #555; line-height: 1.5; }
.card-chevron { font-size: 13px; color: #bbb; flex-shrink: 0; padding-top: 1px; }
details[open] .card-chevron { transform: rotate(90deg); }
.card-detail {
    padding: 10px 14px 14px;
    border-top: 0.5px solid #ebebeb;
    font-size: 12px; color: #444; line-height: 1.7;
    white-space: pre-wrap;
}
.card-source { margin-top: 10px; font-size: 11px; color: #aaa; }

.dept-badge {
    font-size: 11px; font-weight: 500;
    padding: 3px 9px; border-radius: 99px;
    white-space: nowrap; flex-shrink: 0; margin-top: 1px;
}
.bp { background: #EEEDFE; color: #3C3489; }
.bt { background: #E1F5EE; color: #085041; }
.ba { background: #FAEEDA; color: #633806; }
.bc { background: #FAECE7; color: #712B13; }
.bb { background: #E6F1FB; color: #0C447C; }
</style>
"""


def badge_class(division: str) -> str:
    return BADGE_MAP.get(division, "bb")


def format_detail_html(text: str) -> str:
    """bullet point 텍스트를 HTML <ul><li>로 변환."""
    lines = text.strip().splitlines()
    items = []
    for line in lines:
        stripped = re.sub(r'^[•\-\*]\s*', '', line.strip())
        if stripped:
            items.append(f'<li>{html_lib.escape(stripped)}</li>')
    if items:
        return f'<ul style="margin:0;padding-left:18px;">{"".join(items)}</ul>'
    return f'<p style="margin:0;">{html_lib.escape(text.strip())}</p>'


def build_timeline_html(reports: list) -> str:
    sorted_reports = sorted(reports, key=lambda r: r["date"])
    rows = ""
    for date_str, group in itertools.groupby(sorted_reports, key=lambda r: r["date"]):
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        cards = ""
        for r in group:
            cls = badge_class(r["division"])
            summary = html_lib.escape(r.get("summary", "").strip())
            detail_html = format_detail_html(r.get("detail", ""))
            source = html_lib.escape(r.get("source", ""))
            cards += (
                f'<details class="card-wrap">'
                f'<summary>'
                f'<span class="dept-badge {cls}">{html_lib.escape(r["division"])}</span>'
                f'<div class="card-body">'
                f'<span class="card-summary-text">{summary}</span>'
                f'</div>'
                f'<span class="card-chevron">›</span>'
                f'</summary>'
                f'<div class="card-detail">{detail_html}'
                f'<div class="card-source">📄 {source}</div>'
                f'</div>'
                f'</details>'
            )
        rows += (
            f'<div class="trow">'
            f'<div class="date-cell">'
            f'<div class="date-year">{dt.strftime("%Y")}</div>'
            f'<div class="date-main">{dt.strftime("%m.%d")}</div>'
            f'<div class="dot lit"></div>'
            f'</div>'
            f'<div class="cards">{cards}</div>'
            f'</div>'
        )
    return TIMELINE_CSS + f'<div class="wrap">{rows}</div>'


def search(keyword: str) -> tuple:
    results = run_history_tree_structured(keyword)
    if not results:
        return "검색 결과가 없습니다.", []

    divs = list(dict.fromkeys(r["division"] for r in results))
    dates = sorted({r["date"] for r in results})
    summary = (
        f'**{len(results)}건** · {", ".join(divs[:3])}{"..." if len(divs) > 3 else ""}'
        f' · {dates[0]} ~ {dates[-1]}'
    )
    return summary, results


def get_division_status() -> str:
    """사이드바용 조직별 파일 수 한 줄 요약."""
    try:
        counts = list_files_by_division()
        if not counts:
            return "등록된 보고서가 없습니다."
        parts = [f"{org} {cnt}건" for org, cnt in sorted(counts.items())]
        return " · ".join(parts)
    except Exception:
        return "파일 현황을 불러올 수 없습니다."


def init_session() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "initialized" not in st.session_state:
        st.session_state.initialized = False
    if "division_status" not in st.session_state:
        st.session_state.division_status = None


def render_message(msg: dict) -> None:
    with st.chat_message(msg["role"]):
        if msg["type"] == "text":
            st.markdown(msg["content"])
        else:
            st.markdown(msg["summary"])
            st.html(msg["html"])


def render_sidebar() -> None:
    with st.sidebar:
        st.header("설정")
        st.caption(f"KB: `{KNOWLEDGE_BASE_ID}`")
        st.caption(f"Region: `{REGION}`")
        st.caption(f"Model: `{MODEL_ID}`")

        if st.button("대화 초기화", use_container_width=True):
            st.session_state.messages = []
            st.session_state.initialized = False
            st.rerun()

        st.divider()
        st.markdown(
            """
            **사용 예시**
            - 생산본부 보고 히스토리 보여줘
            - 5월 보고만 정리해줘
            - R&D 관련 보고 타임라인 보여줘
            """
        )


def main() -> None:
    st.set_page_config(page_title="History Tree", page_icon="📌", layout="wide")
    init_session()
    render_sidebar()
    st.title("History Tree")

    if not st.session_state.initialized:
        if st.session_state.division_status is None:
            st.session_state.division_status = get_division_status()
        st.session_state.messages.append({
            "role": "assistant",
            "type": "text",
            "content": f"현재 등록된 보고서 — {st.session_state.division_status}",
        })
        st.session_state.initialized = True

    for msg in st.session_state.messages:
        render_message(msg)

    user_input = st.chat_input("키워드를 입력하세요")
    if not user_input:
        return

    user_msg = {"role": "user", "type": "text", "content": user_input}
    st.session_state.messages.append(user_msg)
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("검색 중..."):
            summary, reports = search(user_input)
            html = build_timeline_html(reports)
        st.markdown(summary)
        st.html(html)

    st.session_state.messages.append({
        "role": "assistant",
        "type": "timeline",
        "summary": summary,
        "html": html,
    })


if __name__ == "__main__":
    main()
