# AI Agent PoC

AWS Bedrock과 Strands Agents SDK 기반의 AI Agent PoC 프로젝트 모음입니다.

## 프로젝트 구조

```
AI-Agent/
├── _1_history_chat/            # 보고서 히스토리 분석 에이전트
│   ├── history_tree/           # PoC v1 - KB 검색 + History Tree 생성
│   └── report_analysis_agent/ # PoC v2 - 멀티에이전트 보고서 분석
├── _2_hr_search_agent/         # HR 인재 검색 에이전트
└── _Setting/                   # 공통 환경 설정 가이드
```

---

## _1_history_chat

엑셀 보고 파일을 기반으로 특정 키워드의 보고 히스토리를 분석하고 정리하는 에이전트입니다.

### history_tree (PoC v1)

Bedrock Knowledge Base에서 키워드로 관련 보고 내용을 검색하고, 날짜순 History Tree로 출력합니다.

- **Search Agent** → Knowledge Base 시맨틱 검색
- **Analyst Agent** → 신규 등장 / 반복 / 소멸 분류
- **Writer Agent** → History Tree 형태로 출력
- Streamlit 채팅 UI 제공

### report_analysis_agent (PoC v2)

멀티에이전트 파이프라인으로 내부 문서 검색, 외부 웹 검색, 종합 리포트 생성을 수행합니다.

| Agent | 역할 |
|---|---|
| Scout | Bedrock Knowledge Base RAG 검색으로 내부 문서 근거 수집 |
| Ranger | 외부 웹 검색으로 최신 동향 수집 (Tavily / Exa) |
| Anchor | Scout + Ranger 결과를 종합해 Markdown 리포트 작성 |

결과는 Markdown과 Excel 파일로 저장됩니다.

---

## _2_hr_search_agent

HR 엑셀 데이터를 전처리하고, 자연어 질의로 인재를 검색·추천하는 에이전트입니다.

**파이프라인**

```
HR Excel → 전처리 → 임베딩 생성 → S3 업로드
              ↓
     자연어 질의 입력
              ↓
  Query Understanding (Claude)
              ↓
  Candidate Retrieval (하드 필터 + 코사인 유사도)
              ↓
  Scoring Agent (직무적합도 / 경험깊이 / 리더십 / 이동배치)
              ↓
  Explanation Agent (추천 근거 생성)
```

Strands GraphBuilder 기반 노드 파이프라인으로 구성되며, Streamlit UI와 CLI 실행을 모두 지원합니다.

---

## 공통 환경

- Python 3.10 이상
- AWS 계정 (Bedrock 접근 권한 필요)
- AWS CLI 설치 및 자격증명 설정

설치 및 설정 방법은 [`_Setting/SETUP.md`](_Setting/SETUP.md)를 참고하세요.

---

## 기술 스택

| 항목 | 내용 |
|---|---|
| LLM | AWS Bedrock (Claude Haiku 4.5) |
| Embedding | Amazon Titan Text Embeddings V2 |
| Agent Framework | Strands Agents SDK |
| RAG | AWS Bedrock Knowledge Base |
| Storage | Amazon S3 |
| UI | Streamlit |
