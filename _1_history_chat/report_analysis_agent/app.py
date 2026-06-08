from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import json
import re

import streamlit as st

import config

from agents.anchor import build_markdown_report
from agents.ranger import parse_manual_external_notes, search_latest_trends
from agents.scout import retrieve_internal_documents
from models import AnalysisRun, SearchPlan, StepResult
from services.kb_loader import delete_s3_files, list_ingestion_jobs, list_s3_files, upload_many_and_sync
from services.report_forge import save_outputs
from services.report_store import list_report_runs

KST = ZoneInfo("Asia/Seoul")


EVIDENCE_CARD_LIMIT = 5
SEARCH_INTENSITY_DESCRIPTIONS = {
    "가볍게": "핵심 키워드 중심으로 빠르게 확인",
    "표준": "핵심 키워드와 내부 자료의 이슈 유형을 결합",
}


def main() -> None:
    st.set_page_config(page_title="Report Analysis Agent", page_icon="RA", layout="wide")
    _init_state()
    settings = render_sidebar()

    st.title("Report Analysis Agent")
    st.caption("자료 상태를 확인하고, 내부 근거를 검토한 뒤, 필요한 경우 외부 자료를 보강해 리포트를 생성합니다.")

    page = st.segmented_control(
        "화면 선택",
        ["자료실", "Agent", "결과 보관함"],
        default="Agent",
        label_visibility="collapsed",
        key="main_page",
    )

    if page == "자료실":
        render_library_tab()
    elif page == "Agent":
        render_agent_tab(settings)
    else:
        render_archive_tab()


def _init_state() -> None:
    st.session_state.setdefault("last_sync", None)
    st.session_state.setdefault("last_run", None)
    st.session_state.setdefault("search_run", None)
    st.session_state.setdefault("messages", [])


def render_sidebar() -> dict:
    with st.sidebar:
        st.header("설정")

        st.subheader("시스템 상태")
        st.caption(f"Model: `{config.MODEL_ID}`")
        st.caption(f"Region: `{config.AWS_REGION}`")
        st.caption(f"KB: `{'정상' if config.is_kb_configured() else '미설정'}`")
        st.caption(f"S3: `{config.S3_BUCKET or '미설정'}`")
        st.caption(f"Tavily: `{'사용 가능' if config.TAVILY_API_KEY else '미설정'}`")

        st.divider()
        st.subheader("내부 검색")
        result_count = st.slider(
            "검색 결과 수",
            min_value=1,
            max_value=10,
            value=4,
            step=1,
            help="Bedrock Knowledge Base에서 가져올 내부 문서 조각 수입니다. 화면에는 상위 근거만 카드로 표시하고 전체 결과는 접어서 확인합니다.",
        )

        st.divider()
        st.subheader("외부 검색")
        use_web_search = st.checkbox(
            "Strands 웹 검색 사용",
            value=False,
            help="TAVILY_API_KEY가 설정된 경우 Tavily 기반 Strands 검색 도구를 사용합니다.",
        )
        web_result_count = st.slider(
            "검색 결과 수",
            min_value=1,
            max_value=10,
            value=3,
            step=1,
            disabled=not use_web_search,
            help="웹 검색을 실행할 때 가져올 결과 후보 수입니다.",
        )

        with st.expander("수동 외부 자료", expanded=False):
            manual_external = st.text_area(
                "뉴스/URL/시장 메모",
                placeholder="외부 기사 요약, URL, 시장 동향 메모 등을 붙여넣으면 내부 검색 결과와 함께 종합합니다.",
                height=120,
                label_visibility="collapsed",
            )

        st.divider()
        st.subheader("추천 질문")
        st.caption("아직 추천 질문이 없습니다.")
        st.caption("문서 요약/추천 질문 생성 기능은 다음 단계에서 추가 예정입니다.")

    return {
        "result_count": result_count,
        "use_web_search": use_web_search,
        "max_news": web_result_count,
        "manual_external": manual_external,
    }


def render_library_tab() -> None:
    st.subheader("자료실")
    _render_config_status()

    st.markdown("### 파일 업로드")
    uploaded_files = st.file_uploader("KB에 추가할 파일", type=None, accept_multiple_files=True)
    disabled = not config.is_kb_configured() or not uploaded_files

    if st.button("S3 업로드 및 KB Sync", disabled=disabled, type="primary"):
        _run_upload_and_sync(uploaded_files)

    st.divider()
    st.markdown("### S3 파일 목록")
    _render_s3_files()

    st.divider()
    st.markdown("### KB Sync 이력")
    _render_ingestion_history()


def _format_s3_file_row(item: dict) -> dict:
    return {
        "file_name": item.get("file_name"),
        "size_kb": item.get("size_kb"),
        "last_modified": _format_kst(item.get("last_modified")),
        "s3_key": item.get("s3_key"),
    }


def _format_kst(value) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def _run_upload_and_sync(uploaded_files) -> None:
    progress = st.progress(0)
    status_box = st.empty()

    def callback(message: str, value: float | None) -> None:
        status_box.info(message)
        if value is not None:
            progress.progress(max(0.0, min(1.0, value)))

    try:
        with st.status("KB Sync 실행 중", expanded=True) as status:
            files = [(uploaded_file, uploaded_file.name) for uploaded_file in uploaded_files]
            result = upload_many_and_sync(files, callback=callback)
            job = result.get("ingestionJob", {})
            final_status = job.get("status", "UNKNOWN")
            st.write(f"최종 상태: `{final_status}`")
            st.write("업로드 위치:")
            for uri in result.get("uploadedS3Uris", []):
                st.write(f"- `{uri}`")
            if final_status == "COMPLETE":
                status.update(label="KB Sync 완료", state="complete")
                st.session_state.last_sync = datetime.now()
            else:
                status.update(label="KB Sync 종료", state="error")
    except Exception as exc:
        st.error(f"KB Sync 실패: {type(exc).__name__}: {exc}")


def _render_s3_files() -> None:
    if not config.S3_BUCKET:
        st.info("S3_BUCKET 설정이 필요합니다.")
        return

    try:
        files = list_s3_files()
    except Exception as exc:
        st.error(f"S3 파일 목록 조회 실패: {type(exc).__name__}: {exc}")
        return

    if not files:
        st.info("현재 prefix 아래에 파일이 없습니다.")
        return

    files_for_display = [_format_s3_file_row(item) for item in files]
    st.dataframe(files_for_display, use_container_width=True, hide_index=True)
    selected = st.multiselect(
        "삭제할 S3 key 선택",
        options=[item["s3_key"] for item in files],
        help="S3에서 파일을 삭제한 뒤에는 KB Sync를 다시 실행해야 검색 결과에서도 제거됩니다.",
    )
    if st.button("선택 파일 삭제", disabled=not selected):
        try:
            delete_s3_files(selected)
            st.warning("S3 파일을 삭제했습니다. 검색 결과에서 제거하려면 KB Sync를 다시 실행하세요.")
            st.rerun()
        except Exception as exc:
            st.error(f"S3 삭제 실패: {type(exc).__name__}: {exc}")


def _render_ingestion_history() -> None:
    if not config.is_kb_configured():
        st.info("KB 설정이 필요합니다.")
        return

    try:
        jobs = list_ingestion_jobs()
    except Exception as exc:
        st.error(f"Sync 이력 조회 실패: {type(exc).__name__}: {exc}")
        return

    if not jobs:
        st.info("조회된 Sync 이력이 없습니다.")
        return

    rows = []
    for job in jobs:
        stats = job.get("statistics", {})
        rows.append(
            {
                "status": job.get("status"),
                "started_at": _format_kst(job.get("startedAt")),
                "updated_at": _format_kst(job.get("updatedAt")),
                "scanned": stats.get("numberOfDocumentsScanned"),
                "indexed": stats.get("numberOfNewDocumentsIndexed"),
                "failed": stats.get("numberOfDocumentsFailed"),
                "job_id": job.get("ingestionJobId"),
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_agent_tab(settings: dict) -> None:
    st.subheader("Agent")
    _render_config_status(compact=True)

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    user_input = st.chat_input("분석할 질문이나 키워드를 입력하세요")
    if not user_input:
        _render_active_search_snapshot()
        _render_last_run_files()
        return

    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        if _is_report_request(user_input) and _get_active_search_run():
            run = _run_report_from_previous(user_input)
        else:
            run = _run_agent(user_input, settings)
        st.session_state.last_run = run
        st.session_state.messages.append({"role": "assistant", "content": run.markdown_report})


def _run_agent(user_input: str, settings: dict) -> AnalysisRun:
    run = AnalysisRun(keyword=user_input.strip(), started_at=datetime.now())
    progress_placeholder = st.empty()
    progress_state = {
        "internal": "대기 중",
        "external": "대기 중",
        "answer": "대기 중",
        "files": "대기 중",
        "details": [],
    }
    _render_progress_panel(progress_placeholder, progress_state)

    progress_state["internal"] = "진행 중"
    _render_progress_panel(progress_placeholder, progress_state)
    with st.status("내부 자료 검색", expanded=True) as status:
        run.scout, run.internal_results = retrieve_internal_documents(
            run.keyword,
            number_of_results=settings["result_count"],
            search_type=config.KB_SEARCH_TYPE,
        )
        _write_step_result(run.scout)
        _render_internal_results(run)
        internal_sources = {_source_name(item.source) for item in run.internal_results if item.source}
        progress_state["internal"] = f"완료: {len(run.internal_results)}건 / 문서 {len(internal_sources)}개"
        progress_state["details"].append(f"내부 검색: {_step_status_text(run.scout)}")
        _render_progress_panel(progress_placeholder, progress_state)
        status.update(
            label="내부 자료 검색 완료" if run.scout.status in {"ok", "skipped"} else "내부 자료 검색 실패",
            state="complete" if run.scout.status in {"ok", "skipped"} else "error",
        )

    progress_state["external"] = "진행 중"
    _render_progress_panel(progress_placeholder, progress_state)
    with st.status("외부 최신 동향 검색", expanded=True) as status:
        manual_results = parse_manual_external_notes(settings["manual_external"])
        if settings["use_web_search"]:
            trend_keyword = _extract_trend_keyword(run.keyword)
            st.write(f"외부 동향 수집 키워드: `{trend_keyword}`")
            with st.spinner("Strands 웹 검색 실행 중..."):
                run.ranger, web_results, raw_external, run.search_logs = search_latest_trends(
                    trend_keyword,
                    settings["max_news"],
                )
            run.external_results = manual_results + web_results
            _write_step_result(run.ranger)
            _render_search_logs(run)
            if raw_external and not web_results:
                with st.expander("파싱되지 않은 웹 검색 원문"):
                    st.text(raw_external)
        elif manual_results:
            run.ranger = StepResult(status="ok", message="수동 입력 외부 자료를 사용합니다.")
            run.external_results = manual_results
            _write_step_result(run.ranger)
        else:
            run.ranger = StepResult(status="skipped", message="외부 웹 검색과 수동 외부 자료 입력을 건너뜁니다.")
            _write_step_result(run.ranger)
        _render_external_results(run)
        progress_state["external"] = f"완료: {len(run.external_results)}건"
        progress_state["details"].append(f"외부 자료: {_step_status_text(run.ranger)}")
        if settings["use_web_search"]:
            progress_state["details"].append(f"외부 검색 목표 건수: {settings['max_news']}")
        progress_state["answer"] = "검토 대기"
        progress_state["files"] = "대기"
        _render_progress_panel(progress_placeholder, progress_state)
        status.update(
            label="외부 최신 동향 검색 완료",
            state="complete" if run.ranger.status in {"ok", "skipped"} else "error",
        )

    run.markdown_report = _build_review_prompt(run)
    try:
        run = save_outputs(run)
        _write_search_snapshot(run)
        st.session_state.search_run = run
        st.caption(f"검색 스냅샷 저장: {run.output_dir}")
    except Exception as exc:
        st.warning(f"검색 스냅샷 저장 실패: {type(exc).__name__}: {exc}")
    st.markdown("### 다음 단계")
    st.info(run.markdown_report)
    st.caption("현재 단계에서는 외부 검색 결과를 리포트에 자동 반영하지 않습니다. 내부/외부 결과를 검토한 뒤 반영할 내용을 지정해 리포트를 생성하는 흐름으로 분리했습니다.")

    return run


def _write_search_snapshot(run: AnalysisRun) -> None:
    if not run.output_dir:
        return

    output_dir = Path(run.output_dir)
    snapshot = {
        "keyword": run.keyword,
        "started_at": run.started_at.isoformat(timespec="seconds"),
        "internal_results": [
            {"no": index, **asdict(item)}
            for index, item in enumerate(run.internal_results, start=1)
        ],
        "external_results": [
            {"no": index, **asdict(item)}
            for index, item in enumerate(run.external_results, start=1)
        ],
        "search_logs": [asdict(item) for item in run.search_logs],
        "scout": asdict(run.scout),
        "ranger": asdict(run.ranger),
    }
    (output_dir / "search_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (output_dir / "search_snapshot.md").write_text(_build_search_snapshot_markdown(run), encoding="utf-8")


def _build_search_snapshot_markdown(run: AnalysisRun) -> str:
    lines = [
        f"# 검색 스냅샷: {run.keyword}",
        "",
        "## 내부 검색 결과",
    ]
    if run.internal_results:
        for index, item in enumerate(run.internal_results, start=1):
            lines.extend(
                [
                    f"### 내부 {index}. {_source_name(item.source)}",
                    f"- KB 원점수: {item.score if item.score is not None else 'N/A'}",
                    "```text",
                    item.content,
                    "```",
                    "",
                ]
            )
    else:
        lines.append("- 내부 검색 결과 없음")

    lines.extend(["", "## 외부 검색 결과"])
    if run.external_results:
        for index, item in enumerate(run.external_results, start=1):
            lines.extend(
                [
                    f"### 외부 {index}. {item.title}",
                    f"- 날짜: {item.date or '확인 불가'}",
                    f"- 출처: {item.source or '확인 불가'}",
                    f"- URL: {item.url or '없음'}",
                    "```text",
                    item.summary,
                    "```",
                    "",
                ]
            )
    else:
        lines.append("- 외부 검색 결과 없음")

    lines.extend(["", "## 검색 로그"])
    if run.search_logs:
        for log in run.search_logs:
            lines.append(f"- {log.topic} / {log.query} / {log.status} / {log.result_count}건 / {log.message or log.error}")
    else:
        lines.append("- 검색 로그 없음")
    return "\n".join(lines)


def _get_active_search_run() -> AnalysisRun | None:
    run = st.session_state.get("search_run") or st.session_state.get("last_run")
    if isinstance(run, AnalysisRun) and (run.internal_results or run.external_results):
        return run
    return None


def _is_report_request(text: str) -> bool:
    lowered = text.lower()
    triggers = ["리포트", "보고서", "작성", "반영", "포함", "정리해서", "만들어", "생성"]
    if any(trigger in lowered for trigger in triggers):
        return True
    return bool(
        re.search(
            r"(내부|외부)\s*(?:(?:근거|자료|문서|검색\s*결과)\s*)?(?:전체|전부|모두|[0-9])",
            text,
        )
    )


def _run_report_from_previous(instruction: str) -> AnalysisRun:
    base_run = _get_active_search_run()
    if base_run is None:
        run = AnalysisRun(keyword=instruction.strip(), started_at=datetime.now())
        run.markdown_report = "이전 검색 결과가 없어 리포트를 생성할 수 없습니다. 먼저 검색을 실행해 주세요."
        st.warning(run.markdown_report)
        return run

    selected_run = deepcopy(base_run)
    selected_run.started_at = datetime.now()
    selected_run.keyword = base_run.keyword
    internal_indices, internal_explicit = _parse_result_indices(
        instruction,
        "내부",
        len(base_run.internal_results),
    )
    external_indices, external_explicit = _parse_result_indices(
        instruction,
        "외부",
        len(base_run.external_results),
    )
    if internal_explicit != external_explicit:
        if not internal_explicit:
            internal_indices = []
        if not external_explicit:
            external_indices = []

    selected_run.internal_results = _select_by_indices(base_run.internal_results, internal_indices)
    selected_run.external_results = _select_by_indices(base_run.external_results, external_indices)

    st.write(_selection_summary(internal_indices, external_indices))
    with st.status("선택 근거 기반 리포트 생성", expanded=True) as status:
        selected_run.anchor, selected_run.markdown_report = build_markdown_report(selected_run)
        _write_step_result(selected_run.anchor)
        status.update(
            label="리포트 생성 완료" if selected_run.markdown_report else "리포트 생성 실패",
            state="complete" if selected_run.markdown_report else "error",
        )

    st.markdown("### 생성 리포트")
    st.markdown(selected_run.markdown_report)
    try:
        selected_run = save_outputs(selected_run)
        st.success(f"결과 저장 완료: {selected_run.output_dir}")
        _render_downloads(selected_run)
    except Exception as exc:
        st.error(f"결과 파일 저장 실패: {type(exc).__name__}: {exc}")
    return selected_run


def _parse_result_indices(text: str, label: str, max_count: int) -> tuple[list[int], bool]:
    if max_count <= 0:
        return [], False

    label_pattern = rf"{label}\s*(?:(?:근거|자료|문서|검색\s*결과)\s*)?"
    if re.search(rf"{label_pattern}\s*(제외|빼|빼고|없이)", text):
        return [], True
    if re.search(rf"{label_pattern}\s*(전체|전부|모두)", text):
        return list(range(1, max_count + 1)), True

    indices: list[int] = []
    pattern = rf"{label_pattern}\s*([0-9,\s~\-번과와]+)"
    match = re.search(pattern, text)
    if match:
        raw = match.group(1)
        for part in re.split(r"[,\s과와]+", raw):
            part = part.strip().replace("번", "")
            if not part:
                continue
            range_match = re.match(r"^(\d+)\s*[~-]\s*(\d+)$", part)
            if range_match:
                start, end = int(range_match.group(1)), int(range_match.group(2))
                if start > end:
                    start, end = end, start
                indices.extend(range(start, end + 1))
            elif part.isdigit():
                indices.append(int(part))

    if not indices:
        return list(range(1, max_count + 1)), False
    return sorted({index for index in indices if 1 <= index <= max_count}), True


def _select_by_indices(items: list, indices: list[int]) -> list:
    return [items[index - 1] for index in indices if 1 <= index <= len(items)]


def _selection_summary(internal_indices: list[int], external_indices: list[int]) -> str:
    internal_text = ", ".join(map(str, internal_indices)) if internal_indices else "없음"
    external_text = ", ".join(map(str, external_indices)) if external_indices else "없음"
    return f"선택된 근거: 내부 {internal_text} / 외부 {external_text}"
def _extract_trend_keyword(question: str) -> str:
    text = question.strip()
    removals = [
        "관련", "보고", "히스토리", "정리", "정리해줘", "알려줘", "분석", "분석해줘",
        "내용", "최신동향", "최신 동향", "동향", "대해서", "대한", "해줘", "해 주세요",
    ]
    for token in removals:
        text = text.replace(token, " ")
    words = [word.strip(" ,./:;()[]{}") for word in text.split() if word.strip(" ,./:;()[]{}")]
    return words[0] if words else question.strip()


def _build_review_prompt(run: AnalysisRun) -> str:
    internal_count = len(run.internal_results)
    external_count = len(run.external_results)
    return (
        f"내부 문서 검색 결과 {internal_count}건과 외부 최신 동향 {external_count}건을 확인했습니다.\n\n"
        "아직 리포트에는 자동 반영하지 않았습니다. "
        "내부 근거와 외부 자료를 검토한 뒤, 어떤 내용을 포함할지 말해 주세요.\n\n"
        "예: `내부 근거 1~3번 중심으로 작성하고, 외부 자료 2번은 참고만 해줘`"
    )
def _render_progress_panel(placeholder, state: dict) -> None:
    with placeholder.container():
        st.markdown("### 진행 상태")
        cols = st.columns(4)
        cols[0].metric("내부 검색", state["internal"])
        cols[1].metric("외부 자료", state["external"])
        cols[2].metric("답변 생성", state["answer"])
        cols[3].metric("파일 저장", state["files"])
        if state.get("details"):
            with st.expander("진행 상세"):
                for detail in state["details"]:
                    st.write(detail)

def _render_internal_results(run: AnalysisRun) -> None:
    if not run.internal_results:
        st.write("내부 검색 결과가 없습니다.")
        return

    st.markdown("### 내부 근거")
    st.caption("KB 점수는 검색 엔진의 정렬 참고값입니다. 답변 신뢰도나 사실성 점수가 아닙니다.")
    st.caption(f"상위 {min(len(run.internal_results), EVIDENCE_CARD_LIMIT)}건 표시 / 전체 {len(run.internal_results)}건")
    for index, item in enumerate(run.internal_results[:EVIDENCE_CARD_LIMIT], start=1):
        score = item.score if item.score is not None else 0
        score_label = _score_label(score)
        source_name = _source_name(item.source)
        contains_keyword = run.keyword.lower() in item.content.lower()

        with st.container(border=True):
            st.markdown(f"**근거 {index}. {source_name}**")
            cols = st.columns([1, 1, 2])
            cols[0].metric("검색 순위", index)
            cols[1].metric("KB 원점수", f"{score:.4f}" if item.score is not None else "N/A")
            cols[2].caption(f"질문 문구 직접 포함: {'예' if contains_keyword else '아니오'}")
            st.text(_excerpt(item.content, run.keyword))
            with st.expander("원문 chunk / 메타데이터"):
                st.caption(item.source)
                st.json(item.metadata)
                st.text(item.content)

    if len(run.internal_results) > EVIDENCE_CARD_LIMIT:
        with st.expander("내부 검색 결과 전체 보기"):
            rows = []
            for index, item in enumerate(run.internal_results, start=1):
                rows.append(
                    {
                        "no": index,
                        "score": item.score,
                        "source": _source_name(item.source),
                        "contains_keyword": run.keyword.lower() in item.content.lower(),
                        "excerpt": _excerpt(item.content, run.keyword, length=180),
                    }
                )
            st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_search_plan(plan: SearchPlan) -> None:
    st.markdown("### 외부 검색 계획")
    if not plan.needs_external_search:
        st.write(plan.reason or "외부 검색이 필요하지 않다고 판단했습니다.")
        return

    st.caption(f"검색 강도: {plan.intensity}")
    if plan.reason:
        st.write(plan.reason)
    for index, topic in enumerate(plan.topics, start=1):
        with st.container(border=True):
            st.markdown(f"**검색 주제 {index}. {topic.topic}**")
            st.write(f"검색어: `{topic.query}`")
            if topic.why_needed:
                st.caption(f"이유: {topic.why_needed}")
            if topic.expected_use:
                st.caption(f"사용 목적: {topic.expected_use}")

def _render_search_logs(run: AnalysisRun) -> None:
    if not run.search_logs:
        return

    with st.expander("웹 검색 실행 로그", expanded=True):
        rows = []
        for log in run.search_logs:
            rows.append(
                {
                    "topic": log.topic,
                    "query": log.query,
                    "status": log.status,
                    "result_count": log.result_count,
                    "message": log.message,
                    "error": log.error,
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)
def _render_external_results(run: AnalysisRun) -> None:
    st.markdown("### 외부 자료")
    if not run.external_results:
        st.write(run.ranger.message or run.ranger.error or "외부 자료가 없습니다.")
        return

    for item in run.external_results:
        with st.container(border=True):
            st.markdown(f"**{item.title}**")
            detail = " | ".join(part for part in [item.date, item.source, item.url] if part)
            if detail:
                st.caption(detail)
            relevance = " | ".join(part for part in [item.relevance, item.relevance_reason] if part)
            if relevance:
                st.caption(f"관련성: {relevance}")
            st.text(item.summary)


def render_archive_tab() -> None:
    st.subheader("결과 보관함")
    runs = list_report_runs()
    if not runs:
        st.info("저장된 결과가 없습니다.")
        return

    selected_label = st.selectbox("이전 결과 선택", options=[run["label"] for run in runs])
    selected = next(run for run in runs if run["label"] == selected_label)

    st.caption(f"폴더: {selected['run_dir']}")
    metadata = selected.get("metadata", {})
    if metadata:
        cols = st.columns(3)
        cols[0].metric("키워드", metadata.get("keyword", ""))
        cols[1].metric("내부 검색", metadata.get("scout", {}).get("status", ""))
        cols[2].metric("외부 검색", metadata.get("ranger", {}).get("status", ""))

    markdown_path = Path(selected["markdown_path"])
    if markdown_path.exists():
        st.markdown(markdown_path.read_text(encoding="utf-8"))

    _download_button("Markdown 다운로드", selected["markdown_path"], "text/markdown", namespace="archive")
    _download_button(
        "Excel 다운로드",
        selected["excel_path"],
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        namespace="archive",
    )


def _render_active_search_snapshot() -> None:
    run = st.session_state.get("search_run")
    if not isinstance(run, AnalysisRun):
        return
    if not run.internal_results and not run.external_results:
        return

    st.divider()
    st.markdown("### 최근 검색 결과")
    st.caption("아래 결과를 확인한 뒤 채팅창에 `내부 1~3번, 외부 2번 반영해서 리포트 작성해줘`처럼 입력하세요.")
    _render_internal_results(run)
    _render_search_logs(run)
    _render_external_results(run)
def _render_last_run_files() -> None:
    run: AnalysisRun | None = st.session_state.get("last_run")
    if run:
        st.divider()
        st.markdown("### 최근 결과 파일")
        _render_downloads(run)


def _render_downloads(run: AnalysisRun) -> None:
    cols = st.columns(2)
    with cols[0]:
        _download_button("Markdown 다운로드", run.markdown_path, "text/markdown", namespace="last_run")
    with cols[1]:
        _download_button(
            "Excel 다운로드",
            run.excel_path,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            namespace="last_run",
        )


def _render_config_status(compact: bool = False) -> None:
    values = {
        "Region": config.AWS_REGION,
        "Model": config.MODEL_ID,
        "S3 Bucket": config.S3_BUCKET or "(미설정)",
        "S3 Prefix": config.S3_PREFIX or "(없음)",
        "Knowledge Base ID": config.KNOWLEDGE_BASE_ID or "(미설정)",
        "Data Source ID": config.DATA_SOURCE_ID or "(미설정)",
    }
    if compact:
        st.caption(f"KB 설정 상태: {'정상' if config.is_kb_configured() else '미설정'}")
        return

    cols = st.columns(2)
    for index, (key, value) in enumerate(values.items()):
        cols[index % 2].markdown(f"**{key}**: `{value}`")


def _write_step_result(step) -> None:
    if step.message:
        st.write(step.message)
    if step.error:
        st.error(step.error)


def _step_status_text(step) -> str:
    detail = step.message or step.error
    return f"{step.status}: {detail}" if detail else step.status


def _download_button(label: str, path: Path | None, mime: str, namespace: str = "default") -> None:
    key = f"download::{namespace}::{label}::{Path(path).resolve() if path else 'missing'}"
    if not path or not Path(path).exists():
        st.button(label, disabled=True, use_container_width=True, key=key)
        return
    file_path = Path(path)
    st.download_button(
        label,
        data=file_path.read_bytes(),
        file_name=file_path.name,
        mime=mime,
        key=key,
        use_container_width=True,
    )


def _score_label(score: float) -> str:
    if score >= 0.75:
        return "높음"
    if score >= 0.55:
        return "보통"
    return "낮음"


def _source_name(source: str) -> str:
    if not source:
        return "출처 미상"
    return Path(source.replace("\\", "/")).name or source


def _excerpt(content: str, keyword: str, length: int = 450) -> str:
    content = " ".join(content.split())
    if not content:
        return ""
    lower_content = content.lower()
    lower_keyword = keyword.lower()
    index = lower_content.find(lower_keyword)
    if index == -1:
        return content[:length]
    start = max(0, index - length // 3)
    end = min(len(content), start + length)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(content) else ""
    return f"{prefix}{content[start:end]}{suffix}"


if __name__ == "__main__":
    main()






























