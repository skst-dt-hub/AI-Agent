import html as html_lib
import json
import os
import re
from datetime import datetime
from pathlib import Path

import boto3
from dotenv import load_dotenv
from strands import Agent
from strands.tools import tool

load_dotenv(Path(__file__).resolve().parent / ".env")

KNOWLEDGE_BASE_ID = os.getenv("KNOWLEDGE_BASE_ID", "")
REGION = os.getenv("AWS_REGION", "us-east-2")
MODEL_ID = os.getenv("MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "")
MIN_SCORE = float(os.getenv("MIN_SCORE", "0") or "0")
KB_VALIDATION_ENABLED = os.getenv("KB_VALIDATION_ENABLED", "true").lower() == "true"
KB_VALIDATION_FAIL_OPEN = os.getenv("KB_VALIDATION_FAIL_OPEN", "false").lower() == "true"

bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)
bedrock_runtime = boto3.client("bedrock-runtime", region_name=REGION)
s3_client = boto3.client("s3", region_name=REGION)


def _extract_json_array(text: str) -> list:
    """Extract the first JSON array from an LLM response."""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end <= start:
        raise ValueError("검증 Agent 응답에 JSON 배열이 없습니다.")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, list):
        raise ValueError("검증 Agent 응답이 JSON 배열이 아닙니다.")
    return payload


def _validate_retrieval_results(keyword: str, items: list) -> list:
    """Keep only chunks that directly answer or substantively discuss the query."""
    if not KB_VALIDATION_ENABLED or not items:
        return items

    candidates = []
    for index, item in enumerate(items):
        location = item.get("location", {})
        source = (
            location.get("s3Location", {}).get("uri")
            or location.get("webLocation", {}).get("url")
            or str(location)
        )
        candidates.append(
            {
                "id": index,
                "source": source,
                "kb_score": item.get("score"),
                "content": item.get("content", {}).get("text", "")[:3000],
            }
        )

    prompt = f"""사용자 질의와 KB 검색 청크의 직접 관련성을 엄격하게 검증하세요.

사용자 질의: {keyword}

판정 기준:
- direct: 청크가 질의 대상 자체를 명시적으로 다루며, 답변 근거로 사용할 구체적 내용이 있음
- partial: 같은 산업/상위 개념/유사 단어만 다루거나 배경 정보 수준임
- irrelevant: 동명이인, 다른 대상, 단순 키워드 일치, 질의 답변에 도움이 되지 않음
- KB 점수는 참고하지 말고 질의와 청크 내용만 판정
- 애매하면 partial 또는 irrelevant로 판정

후보:
{json.dumps(candidates, ensure_ascii=False)}

각 후보를 빠짐없이 다음 JSON 배열로만 반환하세요.
[{{"id": 0, "verdict": "direct|partial|irrelevant", "reason": "짧은 판정 근거"}}]
"""

    try:
        response = bedrock_runtime.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"temperature": 0, "maxTokens": 2000},
        )
        text = response["output"]["message"]["content"][0]["text"]
        decisions = {
            int(decision["id"]): decision
            for decision in _extract_json_array(text)
            if isinstance(decision, dict) and "id" in decision
        }
    except Exception as exc:
        print(f"[WARN] KB 검증 Agent 실패: {type(exc).__name__}: {exc}")
        return items if KB_VALIDATION_FAIL_OPEN else []

    validated = []
    for index, item in enumerate(items):
        decision = decisions.get(index, {})
        verdict = str(decision.get("verdict", "irrelevant")).lower()
        reason = str(decision.get("reason", "검증 결과 없음"))
        score = item.get("score", 0)
        source = item.get("location", {}).get("s3Location", {}).get("uri", "")
        print(f"[VALIDATE] {verdict} | score={score:.3f} | {source.split('/')[-1]} | {reason}")
        if verdict == "direct":
            item["_validation"] = {"verdict": verdict, "reason": reason}
            validated.append(item)
    return validated


# ── URI 파싱 헬퍼 ──────────────────────────────────────────────────────────────
def parse_org(uri: str) -> str:
    """s3://bucket/조직명/파일명 → 조직명"""
    parts = uri.split("/")
    return parts[3] if len(parts) > 3 else "미확인"


def parse_date(uri: str) -> str:
    """파일명의 (yymmdd) 접두사 → YYYY-MM-DD. 파싱 실패 시 빈 문자열."""
    filename = uri.split("/")[-1]
    match = re.search(r'\((\d{6})\)', filename)
    if match:
        try:
            return datetime.strptime(match.group(1), "%y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            return ""
    return ""


def parse_title(uri: str) -> str:
    """파일명에서 (yymmdd) 접두사와 확장자를 제거한 제목."""
    filename = uri.split("/")[-1]
    title = re.sub(r'^\(\d{6}\)\s*', '', filename)
    title = re.sub(r'\.[^.]+$', '', title)
    return title.strip() or filename


# ── S3 조직별 파일 수 집계 ─────────────────────────────────────────────────────
def list_files_by_division() -> dict:
    """S3 버킷에서 조직별 파일 수 반환: {division: count}"""
    counts: dict[str, int] = {}
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET_NAME):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            parts = key.split("/")
            if len(parts) >= 2:
                org = parts[0]
                counts[org] = counts.get(org, 0) + 1
    return counts


# ── LLM 요약 ──────────────────────────────────────────────────────────────────
def _summarize_with_llm(keyword: str, docs: list) -> list:
    """문서별 summary(1-2문장) + detail(bullet) 생성. 실패 시 원본 청크 첫 줄 사용."""
    doc_blocks = []
    for i, doc in enumerate(docs, 1):
        content = "\n".join(c["content"] for c in doc["_chunks"])
        doc_blocks.append(f"[{i}] 파일: {doc['source']}\n{content}")

    prompt = (
        f'"{keyword}" 관련 보고 문서들입니다. 각 문서에 대해 아래 JSON 배열 형식으로만 응답하세요.\n\n'
        '형식:\n'
        '[\n'
        '  {"index": 1, "summary": "이 문서가 해당 키워드와 관련하여 다루는 핵심 내용 1-2문장", '
        '"detail": "• 항목1\\n• 항목2\\n• 항목3 (3-5개)"}\n'
        ']\n\n'
        + "\n\n".join(doc_blocks)
    )

    try:
        response = bedrock_runtime.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
        )
        text = response["output"]["message"]["content"][0]["text"]
        match = re.search(r'\[.*?\]', text, re.DOTALL)
        if match:
            for s in json.loads(match.group()):
                idx = s.get("index", 0) - 1
                if 0 <= idx < len(docs):
                    docs[idx]["summary"] = s.get("summary", "")
                    docs[idx]["detail"] = s.get("detail", "")
    except Exception:
        for doc in docs:
            if doc["_chunks"]:
                raw = doc["_chunks"][0]["content"].replace("\n", " ").strip()
                doc["summary"] = raw[:150] + ("..." if len(raw) > 150 else "")

    return docs


# ── KB 구조화 검색 ─────────────────────────────────────────────────────────────
def run_history_tree_structured(keyword: str) -> list:
    """KB 검색 → LLM 요약 → [{date, division, title, source, summary, detail}] 반환."""
    response = bedrock_agent_runtime.retrieve(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        retrievalQuery={"text": keyword.strip()},
        retrievalConfiguration={
            "vectorSearchConfiguration": {"numberOfResults": 15}
        },
    )

    score_filtered = [
        item
        for item in response["retrievalResults"]
        if item.get("score", 0) >= MIN_SCORE
    ]
    validated_items = _validate_retrieval_results(keyword, score_filtered)

    chunks_by_uri: dict[str, list] = {}
    for item in validated_items:
        uri = item.get("location", {}).get("s3Location", {}).get("uri", "")
        score = item.get("score", 0)
        filename = uri.split("/")[-1] if uri else ""
        date_check = parse_date(uri) if uri else ""
        print(f"[DEBUG] score={score:.3f} | date={date_check or 'FAIL'} | {filename}")
        if not uri:
            continue
        chunks_by_uri.setdefault(uri, []).append({
            "content": item["content"]["text"],
            "score": score,
        })

    docs = []
    for uri, chunks in chunks_by_uri.items():
        date = parse_date(uri)
        if not date:
            print(f"[DEBUG] parse_date 실패로 제외: {uri.split('/')[-1]}")
            continue
        docs.append({
            "date": date,
            "division": parse_org(uri),
            "title": parse_title(uri),
            "source": uri.split("/")[-1],
            "summary": "",
            "detail": "",
            "_chunks": sorted(chunks, key=lambda c: c["score"], reverse=True),
        })

    if not docs:
        return []

    docs = _summarize_with_llm(keyword, docs)

    for doc in docs:
        doc.pop("_chunks", None)

    return docs


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

    score_filtered = [
        item
        for item in response["retrievalResults"]
        if item.get("score", 0) >= MIN_SCORE
    ]
    validated_items = _validate_retrieval_results(keyword, score_filtered)

    results = []
    for item in validated_items:
        content = item["content"]["text"]
        source = item.get("location", {}).get("s3Location", {}).get("uri", "출처 미확인")
        score = item.get("score", 0)
        reason = item.get("_validation", {}).get("reason", "")
        results.append(f"[출처: {source} / KB 점수: {score:.2f} / 검증: {reason}]\n{content}")

    return "\n\n---\n\n".join(results) if results else "관련 내용 없음"


# ── Agent 정의 ─────────────────────────────────────────────────────────────────
search_agent = Agent(
    model=MODEL_ID,
    system_prompt="""당신은 보고 문서 검색 전문가입니다.
Knowledge Base에서 키워드 관련 항목을 검색하고,
날짜, 현안 제목, 주요 내용을 추출하여 날짜 역순으로 정리해주세요.""",
    tools=[search_knowledge_base],
    callback_handler=None,
)

analyst_agent = Agent(
    model=MODEL_ID,
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
    model=MODEL_ID,
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

    search_result = search_agent(f"'{keyword}' 관련 보고 내용을 검색해서 날짜, 현안, 내용을 정리해줘")
    search_text = result_text(search_result)

    analyst_result = analyst_agent(
        f"다음 검색 결과를 분석해서 신규등장/반복/소멸로 분류해줘:\n\n{search_text}"
    )
    analyst_text = result_text(analyst_result)

    writer_result = writer_agent(
        f"키워드: {keyword}\n\n분석 결과:\n{analyst_text}\n\nHistory Tree 형식으로 출력해줘"
    )
    writer_text = result_text(writer_result)

    print("\n" + writer_text)


# ── 실행 ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    keyword = input("검색할 키워드를 입력하세요: ")
    run_history_tree(keyword)
