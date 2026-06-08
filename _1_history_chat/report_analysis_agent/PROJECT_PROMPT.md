# Report Analysis Agent 개발 프롬프트

## 프로젝트 개요

Report Analysis Agent는 Strands Agents SDK와 AWS Bedrock Knowledge Base 기반의 멀티에이전트 내부 문서 분석 시스템이다.

기존 `history_tree` PoC의 구조인 "KB RAG 검색 -> 분석 -> 출력" 흐름을 확장하여, 내부 문서 검색, 외부 웹 검색, 종합 Markdown 리포트 생성, 결과 파일 저장까지 처리하는 Streamlit 애플리케이션으로 개발한다.

## 전제 및 환경

- 런타임: Python, Windows 환경
- 실행 UI: Streamlit
- AWS Region: `us-east-2`
- Bedrock 모델 ID: `us.anthropic.claude-haiku-4-5-20251001-v1:0`
- Bedrock 모델 ID는 inference profile 형식의 `us.` 접두사를 포함해야 한다.
- 기존 Bedrock Knowledge Base는 삭제되었으므로 고정 KB ID를 사용하지 않는다.
- 새 KB는 AWS Console에서 생성한 뒤, 앱의 `config.py`에 값을 입력해 사용한다.
- 사내망 환경이므로 pip 설치 시 필요하면 `--trusted-host` 옵션을 사용한다.
- 외부 뉴스 API는 추가하지 않는다.
- 별도 뉴스 API 대신 Strands tools 패키지에서 사용 가능한 웹 검색 도구를 사용한다.

## 설정값 관리

아래 값은 `.env`에서 관리하고, `config.py`에서 로드한다.

- `AWS_REGION`
- `MODEL_ID`
- `S3_BUCKET`
- `S3_PREFIX`
- `KNOWLEDGE_BASE_ID`
- `DATA_SOURCE_ID`
- `OUTPUT_DIR`
- `TAVILY_API_KEY`
- `EXA_API_KEY`

`KNOWLEDGE_BASE_ID` 또는 `DATA_SOURCE_ID`가 비어 있으면 앱은 "KB 미설정" 상태를 표시하고, 업로드/Sync/분석 실행 기능을 비활성화한다.

## Data Source ID 의미

Bedrock Knowledge Base는 KB 자체와 원천 데이터 연결을 분리해서 관리한다.

```text
Knowledge Base
└── Data Source
    └── S3 bucket / prefix
```

`KNOWLEDGE_BASE_ID`는 검색 대상 KB의 ID이고, `DATA_SOURCE_ID`는 해당 KB에 연결된 S3 data source의 ID이다.

S3에 파일을 업로드한 뒤 KB Sync를 실행하려면 `knowledgeBaseId`와 `dataSourceId`가 모두 필요하다.

## 파일 구조

```text
report_analysis_agent/
├── app.py
├── config.py
├── config.example.py
├── agents/
│   ├── scout.py
│   ├── ranger.py
│   └── anchor.py
├── services/
│   ├── kb_loader.py
│   └── report_forge.py
├── templates/
│   └── report_template.xlsx
├── output/
├── requirements.txt
└── README.md
```

## Streamlit Tab 구조

### Tab 1. 문서 업로드&Sync

- 현재 KB 설정 상태 표시
- 여러 파일 업로드
- S3 일괄 업로드
- KB Sync 실행
- ingestion job 상태 폴링
- 진행 상황과 오류 메시지 표시

진행률은 ingestion job의 `status`를 기준으로 표시한다. `statistics` 필드가 있는 경우에만 보조 수치로 문서 스캔/색인 개수를 표시한다.

### Tab 2. Agent

- KB 설정 상태 확인
- 채팅형 질문 입력
- 내부 문서 검색 결과 표시
- 외부 웹 검색 또는 수동 외부 자료 표시
- 종합 답변 표시
- Markdown/Excel 다운로드
- Scout -> Ranger -> Anchor 순서로 실행
- 각 단계 결과를 `st.session_state`에 저장
- 외부 웹 검색 실패 시 전체 분석을 중단하지 않고 Anchor에 실패 상태를 전달

## Agent 파이프라인

### 1. Scout Agent

역할: Bedrock Knowledge Base RAG 검색으로 내부 문서 근거를 수집한다.

구현:

- `boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)` 사용
- `retrieve()` 호출
- `knowledgeBaseId=KNOWLEDGE_BASE_ID`
- `numberOfResults=20`
- Strands `@tool`로 등록 가능한 검색 함수 제공

반환 데이터:

- 검색 내용 텍스트
- S3 URI 또는 출처 위치
- 관련도 score
- 원본 metadata

### 2. Ranger Agent

역할: Strands tools에서 사용 가능한 웹 검색 도구로 외부 동향을 수집한다.

구현 원칙:

- 별도 뉴스 API는 사용하지 않는다.
- `strands-agents-tools`에서 현재 환경에 사용 가능한 웹 검색 도구를 사용한다.
- 검색 도구, API 키, 네트워크 제약으로 실패하면 전체 분석을 중단하지 않는다.
- 실패 시 "외부 검색 불가" 상태와 오류 메시지를 구조화해서 반환한다.

출력:

- 최신 외부 동향 요약
- 가능하면 제목, 날짜, URL, 요약 포함
- 검색 실패 시 실패 사유 포함

### 3. Anchor Agent

역할: Scout 결과와 Ranger 결과를 종합해 Markdown 리포트를 작성한다.

출력 형식:

```markdown
## 내부 현황 (KB 기반)

## 외부 동향 (웹 검색 기반)

## 종합 결론

## 참고 출처
```

원칙:

- 근거가 부족하면 추정하지 않고 "확인 필요"로 표시한다.
- 내부 문서 근거와 외부 검색 근거를 구분한다.
- 외부 검색이 실패한 경우 해당 한계를 명시한다.

### 4. Report Forge Service

역할: Anchor 결과와 검색 원천 데이터를 파일로 저장한다.

Forge는 LLM Agent가 아니라 deterministic 파일 생성 서비스로 구현한다.

초기 구현 출력:

- Markdown `.md`
- Excel `.xlsx`

추후 선택 출력:

- PPT `.pptx`

원칙:

- `templates/` 원본 파일은 직접 수정하지 않는다.
- 템플릿이 있는 경우 복사본에 내용을 삽입해 `output/`에 저장한다.
- 템플릿이 없는 경우 기본 레이아웃으로 파일을 생성한다.

## Output 설계 초안

기본 저장 경로:

```text
output/{safe_keyword}/YYYYMMDD_HHMMSS/
├── report.md
├── report.xlsx
└── run_metadata.json
```

`run_metadata.json`에는 다음 정보를 저장한다.

- 실행 시각
- 키워드
- 모델 ID
- KB ID
- Data Source ID
- Scout 상태
- Ranger 상태
- Anchor 상태
- 생성 파일 경로
- 오류 메시지

Excel 기본 시트:

- `Summary`: 종합 결론 및 핵심 인사이트
- `Internal`: KB 검색 결과, 출처, score
- `External`: 웹 검색 결과, 제목, 날짜, URL, 요약
- `Report`: Markdown 섹션별 본문
- `RunLog`: 실행 시간, 설정값, 오류 정보

## 구현 주의사항

- 모든 Python 파일은 UTF-8로 작성한다.
- 기존 `history_tree`의 깨진 한글 문자열은 재사용하지 않는다.
- 사내망과 Windows 경로를 고려한다.
- `output/`은 로컬 결과 저장소로 사용하고, 자동 S3 업로드는 초기 범위에서 제외한다.
- 같은 키워드 재실행 시 덮어쓰지 않고 timestamp 폴더를 새로 만든다.
- 사용자에게 표시되는 오류는 원인과 다음 조치가 보이도록 정리한다.

## requirements.txt 초안

```text
streamlit
strands-agents
strands-agents-tools
boto3
openpyxl
```
