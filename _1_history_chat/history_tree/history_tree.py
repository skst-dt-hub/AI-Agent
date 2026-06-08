import boto3
from strands import Agent
from strands.tools import tool

KNOWLEDGE_BASE_ID = "PKHCULLVMP"
REGION = "us-east-2"

bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)


def result_text(result) -> str:
    """Extract only assistant text from a Strands AgentResult."""
    message = getattr(result, "message", None)
    if isinstance(message, dict):
        parts = []
        for block in message.get("content", []):
            if isinstance(block, dict) and block.get("text"):
                parts.append(block["text"])
        if parts:
            return "\n".join(parts).strip()

    return str(result).strip()


# ── Tool: Knowledge Base 검색 ──────────────────────────────────────────────────
@tool
def search_knowledge_base(keyword: str) -> str:
    """Knowledge Base에서 키워드로 관련 보고 내용을 검색합니다."""
    response = bedrock_agent_runtime.retrieve(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        retrievalQuery={"text": keyword},
        retrievalConfiguration={
            "vectorSearchConfiguration": {"numberOfResults": 20}
        },
    )

    results = []
    for item in response["retrievalResults"]:
        content = item["content"]["text"]
        source = item.get("location", {}).get("s3Location", {}).get("uri", "출처 미확인")
        score = item.get("score", 0)
        results.append(f"[출처: {source} / 관련도: {score:.2f}]\n{content}")

    return "\n\n---\n\n".join(results) if results else "관련 내용 없음"


# ── Agent 정의 ─────────────────────────────────────────────────────────────────
search_agent = Agent(
    model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    system_prompt="""당신은 보고 문서 검색 전문가입니다.
Knowledge Base에서 키워드 관련 항목을 검색하고,
날짜, 현안 제목, 주요 내용을 추출하여 날짜 역순으로 정리해주세요.""",
    tools=[search_knowledge_base],
    callback_handler=None,
)

analyst_agent = Agent(
    model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    system_prompt="""당신은 보고 히스토리 분석 전문가입니다.
검색된 항목들을 분석하여 각 항목을 다음으로 분류하세요:
- 신규 등장: 처음 나타난 이슈
- 반복: 이전 보고에서도 등장한 이슈
- 소멸: 이후 보고에서 사라진 이슈
변화 흐름과 패턴도 파악해주세요.""",
    tools=[],
    callback_handler=None,
)

writer_agent = Agent(
    model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    system_prompt="""당신은 보고 History Tree 작성 전문가입니다.
분석된 내용을 아래 형식으로 출력하세요:

[키워드: {keyword}] 보고 History Tree

─────────────────────────────
날짜
─────────────────────────────
현안:
요약:
변화: 신규 등장 / 반복 / 소멸
근거: 파일명 > 항목번호
""",
    tools=[],
    callback_handler=None,
)


# ── 메인 파이프라인 ────────────────────────────────────────────────────────────
def run_history_tree(keyword: str):
    print(f"\n🔍 [{keyword}] 검색 중...\n")

    # 1. Search
    search_result = search_agent(f"'{keyword}' 관련 보고 내용을 검색해서 날짜, 현안, 내용을 정리해줘")
    search_text = result_text(search_result)

    # 2. Analyst
    analyst_result = analyst_agent(
        f"다음 검색 결과를 분석해서 신규등장/반복/소멸로 분류해줘:\n\n{search_text}"
    )
    analyst_text = result_text(analyst_result)

    # 3. Writer
    writer_result = writer_agent(
        f"키워드: {keyword}\n\n분석 결과:\n{analyst_text}\n\nHistory Tree 형식으로 출력해줘"
    )
    writer_text = result_text(writer_result)

    print("\n" + writer_text)


# ── 실행 ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    keyword = input("검색할 키워드를 입력하세요: ")
    run_history_tree(keyword)
