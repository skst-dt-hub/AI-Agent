import sys
from pathlib import Path

import streamlit as st
from strands import Agent

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from history_tree import REGION, KNOWLEDGE_BASE_ID, result_text, search_knowledge_base


MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

SYSTEM_PROMPT = f"""당신은 내부 보고 문서를 기반으로 대화형 분석을 돕는 History Tree 챗봇입니다.

역할:
- 사용자의 질문 의도를 파악하고 필요한 경우 Knowledge Base를 검색합니다.
- 이전 대화 내용을 참고하여 후속 질문에 답합니다.
- 보고 히스토리, 변화 흐름, 반복/소멸/신규 이슈를 명확히 정리합니다.
- 근거가 부족하면 추정하지 말고 추가 검색이나 확인이 필요하다고 말합니다.

환경:
- Knowledge Base ID: {KNOWLEDGE_BASE_ID}
- Knowledge Base Region: {REGION}

출력 원칙:
- 최종 답변만 출력합니다.
- Python dict, metadata, token usage 정보는 출력하지 않습니다.
- 표가 유용하면 Markdown 표를 사용합니다.
- 긴 답변은 제목과 짧은 bullet로 나누어 가독성을 우선합니다.
"""


def build_agent() -> Agent:
    return Agent(
        model=MODEL_ID,
        system_prompt=SYSTEM_PROMPT,
        tools=[search_knowledge_base],
        callback_handler=None,
    )


def init_session() -> None:
    if "agent" not in st.session_state:
        st.session_state.agent = build_agent()

    if "messages" not in st.session_state:
        st.session_state.messages = []


def conversation_context(max_turns: int = 8) -> str:
    recent_messages = st.session_state.messages[-max_turns:]
    lines = []
    for message in recent_messages:
        role = "사용자" if message["role"] == "user" else "Assistant"
        lines.append(f"{role}: {message['content']}")
    return "\n\n".join(lines)


def ask_agent(user_input: str) -> str:
    context = conversation_context()
    prompt = f"""아래는 현재 대화의 최근 흐름입니다. 후속 질문이면 이 맥락을 활용하세요.

[최근 대화]
{context if context else "(아직 이전 대화 없음)"}

[사용자 새 질문]
{user_input}
"""

    result = st.session_state.agent(prompt)
    return result_text(result)


def render_sidebar() -> None:
    with st.sidebar:
        st.header("설정")
        st.caption(f"KB: `{KNOWLEDGE_BASE_ID}`")
        st.caption(f"Region: `{REGION}`")
        st.caption(f"Model: `{MODEL_ID}`")

        if st.button("대화 초기화", use_container_width=True):
            st.session_state.messages = []
            st.session_state.agent = build_agent()
            st.rerun()

        st.divider()
        st.markdown(
            """
            **사용 예시**
            - 몰리브덴 관련 보고 히스토리 정리해줘
            - 방금 내용에서 4월 이슈만 표로 정리해줘
            - 신규 등장한 이슈와 반복 이슈를 비교해줘
            - 근거 문서 기준으로 다시 설명해줘
            """
        )


def main() -> None:
    st.set_page_config(
        page_title="History Tree Chat",
        page_icon="📌",
        layout="wide",
    )

    init_session()
    render_sidebar()

    st.title("History Tree Chat")
    st.caption("Knowledge Base 검색과 대화 맥락을 함께 사용하는 Strands 챗봇")

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    user_input = st.chat_input("질문을 입력하세요")
    if not user_input:
        return

    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Knowledge Base와 대화 맥락을 확인하는 중..."):
            try:
                answer = ask_agent(user_input)
            except Exception as exc:
                answer = f"오류가 발생했습니다.\n\n```text\n{exc}\n```"

        st.markdown(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
