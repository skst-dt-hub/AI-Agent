#!/usr/bin/env python3
"""Generate a sample HR dataset and upload it to S3 for the hr_search agent.

Produces, under s3://{S3_BUCKET}/{S3_DATA_PREFIX}/:
  - structured_data.json : list of employee records (hard-filter + scoring fields)
  - text_data.json       : list of {사번, 성명, 텍스트} for embeddings + explanation
  - embeddings.json      : {사번: [float, ...]} via Amazon Titan text embeddings v2

Schema matches app/hr_search/{candidate_retrieval,scoring,explanation}.py.

Usage:
    export S3_BUCKET=ai-agent-mcp-data-137738454056-us-east-1
    export AWS_REGION=us-east-1
    python scripts/seed_hr_data.py
"""

from __future__ import annotations

import json
import os

import boto3

REGION = os.getenv("AWS_REGION", "us-east-1")
BUCKET = os.environ["S3_BUCKET"]
PREFIX = os.getenv("S3_DATA_PREFIX", "data").strip("/")
EMBED_MODEL = os.getenv("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")

s3 = boto3.client("s3", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)


# (사번, 성명, 소속조직, 현재직무명, 경력연수, 팀리딩, 팀리딩인원, 팀리딩기간_월,
#  이동가능시점, 파견가능, 해외근무, 업무[(비중,난이도,대체가능성), x3], 텍스트)
EMPLOYEES = [
    ("2005001", "김민준", "재무팀", "자금운영", 20, True, 8, 60, "6개월 내", True, True,
     [(0.5, "상", "하"), (0.3, "중", "중"), (0.2, "중", "중")],
     "현재직무: 자금운영. 자금 운영계획 수립 및 Cash Flow 관리, 금융기관 대응과 자금조달을 총괄. "
     "재무팀 8명을 5년간 리딩하며 환리스크 헤지와 단기 유동성 관리를 주도. 해외 자회사 자금통합(IHB) 경험."),
    ("2006002", "배유정", "자금팀", "자금관리", 14, True, 5, 36, "3개월 내", True, False,
     [(0.45, "상", "하"), (0.35, "중", "중"), (0.2, "하", "상")],
     "현재직무: 자금관리. Cash Flow 예측과 자금수지 관리, 차입금 포트폴리오 운영. "
     "팀리딩 경험 3년(5명). 금융기관 협상 및 신용평가 대응 경험 보유."),
    ("2007003", "이서연", "경영기획팀", "전략기획", 12, True, 6, 48, "1년 내", False, False,
     [(0.4, "상", "하"), (0.3, "상", "중"), (0.3, "중", "중")],
     "현재직무: 전략기획. 중장기 전략 수립, 경영진 보고, 비용 절감 프로젝트 리딩. "
     "기획팀 6명 리딩 4년. 신사업 타당성 분석 및 KPI 체계 설계."),
    ("2008004", "정도현", "생산관리팀", "생산운영", 16, True, 12, 72, "즉시", True, False,
     [(0.5, "상", "하"), (0.3, "중", "하"), (0.2, "중", "중")],
     "현재직무: 생산운영. 생산계획/공정관리/설비 가동률 개선을 담당. 현장 12명 리딩 6년. "
     "스마트팩토리 도입과 원가절감 활동 주도. 즉시 이동 가능."),
    ("2009005", "한지우", "영업관리팀", "영업관리", 11, False, 0, 0, "즉시", True, True,
     [(0.5, "중", "중"), (0.3, "중", "중"), (0.2, "하", "상")],
     "현재직무: 영업관리. 국내외 영업채널 관리, 매출/실적 분석, 거래선 대응. "
     "해외근무 가능하며 즉시 이동 가능. 영업기획 및 가격정책 수립 경험."),
    ("2010006", "오세훈", "해외영업팀", "해외영업", 9, True, 4, 24, "3개월 내", True, True,
     [(0.45, "중", "중"), (0.35, "상", "중"), (0.2, "중", "중")],
     "현재직무: 해외영업. 동남아/북미 거래선 개발과 수출 계약 협상. 4명 리딩 2년. "
     "영어 비즈니스 협상 가능, 장기 해외 파견 경험 보유."),
    ("2011007", "신예린", "회계팀", "재무회계", 13, False, 0, 0, "6개월 내", False, False,
     [(0.5, "중", "하"), (0.3, "중", "중"), (0.2, "하", "상")],
     "현재직무: 재무회계. 결산/연결재무제표/외부감사 대응. IFRS 적용 및 세무 신고 경험. "
     "팀리딩 경험은 없으나 회계 실무 깊이가 높음."),
    ("2012008", "장하늘", "구매팀", "구매조달", 10, True, 5, 30, "1년 내", True, True,
     [(0.45, "중", "중"), (0.35, "중", "중"), (0.2, "중", "하")],
     "현재직무: 구매조달. 원자재 소싱과 공급사 관리, 단가 협상. 5명 리딩 2.5년. "
     "해외 공급망 관리와 글로벌 소싱 경험 보유."),
    ("2013009", "문서윤", "재무팀", "자금운영", 7, False, 0, 0, "즉시", False, False,
     [(0.5, "중", "중"), (0.3, "하", "상"), (0.2, "하", "상")],
     "현재직무: 자금운영. 일일 자금 마감, 예금/대출 실행 지원, 자금일보 작성. "
     "주니어 실무자로 자금운영 기초 경험. 리더십 경험 없음."),
    ("2014010", "윤재호", "경영지원팀", "경영관리", 18, True, 10, 84, "6개월 내", True, True,
     [(0.4, "상", "하"), (0.35, "상", "중"), (0.25, "중", "중")],
     "현재직무: 경영관리. 예산/성과관리, 경영진 보고, 조직 운영 총괄. 10명 리딩 7년. "
     "비용 절감과 전략 실행, 해외법인 관리 경험. 폭넓은 관리 역량 보유."),
    ("2015011", "강민서", "영업기획팀", "영업기획", 8, True, 3, 18, "3개월 내", True, False,
     [(0.45, "중", "중"), (0.35, "중", "중"), (0.2, "하", "상")],
     "현재직무: 영업기획. 영업전략 수립, 실적 분석, 채널 정책 설계. 3명 리딩 1.5년. "
     "데이터 기반 영업관리와 CRM 운영 경험."),
    ("2016012", "조은우", "생산기술팀", "생산기술", 15, True, 7, 54, "1년 내", True, False,
     [(0.5, "상", "하"), (0.3, "상", "중"), (0.2, "중", "중")],
     "현재직무: 생산기술. 공정 개선, 품질/수율 향상, 설비 투자 검토. 7명 리딩 4.5년. "
     "생산운영 전반과 원가 개선 프로젝트 다수 수행."),
]


def to_structured(emp) -> dict:
    (sabun, name, org, role, years, lead, lead_n, lead_m, move, dispatch, overseas, tasks, _text) = emp
    rec = {
        "사번": sabun, "성명": name, "소속조직": org, "현재직무명": role,
        "경력연수": years, "팀리딩여부": lead, "팀리딩인원": lead_n, "팀리딩기간_월": lead_m,
        "이동가능시점": move, "파견가능여부": dispatch, "해외근무가능": overseas,
    }
    for i, (w, diff, repl) in enumerate(tasks, start=1):
        rec[f"주요업무{i}_비중"] = w
        rec[f"주요업무{i}_난이도"] = diff
        rec[f"주요업무{i}_대체가능성"] = repl
    return rec


def to_text(emp) -> dict:
    return {"사번": emp[0], "성명": emp[1], "텍스트": emp[-1]}


def embed(text: str) -> list[float]:
    body = {"inputText": text, "dimensions": 1024, "normalize": True}
    resp = bedrock.invoke_model(
        modelId=EMBED_MODEL,
        body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]


def put_json(name: str, data) -> None:
    key = f"{PREFIX}/{name}"
    s3.put_object(
        Bucket=BUCKET, Key=key,
        Body=json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"uploaded s3://{BUCKET}/{key}")


def main() -> None:
    structured = [to_structured(e) for e in EMPLOYEES]
    texts = [to_text(e) for e in EMPLOYEES]
    print(f"Embedding {len(texts)} profiles with {EMBED_MODEL} ...")
    embeddings = {t["사번"]: embed(t["텍스트"]) for t in texts}

    put_json("structured_data.json", structured)
    put_json("text_data.json", texts)
    put_json("embeddings.json", embeddings)
    print(f"\nDone. {len(structured)} employees, embedding dim = {len(next(iter(embeddings.values())))}")


if __name__ == "__main__":
    main()
