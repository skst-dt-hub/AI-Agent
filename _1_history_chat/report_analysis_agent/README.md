# Report Analysis Agent

Strands Agents SDK와 AWS Bedrock Knowledge Base를 기반으로 내부 문서 검색, 외부 웹 검색, 종합 리포트 생성, 결과 파일 저장을 수행하는 내부 보고서 분석 에이전트 프로젝트입니다.

## 현재 단계

초기 Streamlit 앱과 분석 파이프라인 구현이 들어간 상태입니다.

아래 문서를 기준으로 기능 범위와 남은 결정을 관리합니다.

- `PROJECT_PROMPT.md`: 개발 프롬프트 및 구현 요구사항
- `DECISIONS.md`: 아직 결정해야 하는 사항
- `config.example.py`: 환경 설정 예시

## 구조

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
├── scripts/
│   └── check_strands_web_search.py
├── templates/
├── output/
├── PROJECT_PROMPT.md
├── DECISIONS.md
└── requirements.txt
```

## 기본 방향

- 기존 `history_tree` 코드는 참고만 하고, 새 프로젝트는 독립 폴더에서 관리합니다.
- 기존 Bedrock Knowledge Base는 삭제되었으므로 고정 KB ID를 사용하지 않습니다.
- KB ID, Data Source ID, S3 Bucket 등은 `config.py`에서 관리합니다.
- 외부 뉴스 API는 사용하지 않고, Strands tools에서 사용 가능한 웹 검색 도구만 사용합니다.
- 초기 파일 생성 범위는 Markdown + Excel입니다.
- `문서 업로드&Sync` 탭은 여러 파일을 S3에 업로드한 뒤 KB Sync를 한 번 실행합니다.
- `Agent` 탭은 채팅형 입력, 내부 문서 검색 결과, 외부 자료, 종합 답변, 다운로드를 한 화면에서 제공합니다.

## 실행

먼저 `.env.example`을 참고해서 `report_analysis_agent/.env` 파일을 만듭니다.

```text
AWS_REGION=us-east-2
MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0

S3_BUCKET=your-bucket-name
S3_PREFIX=report-analysis-agent/uploads/

KNOWLEDGE_BASE_ID=your-kb-id
DATA_SOURCE_ID=your-data-source-id

OUTPUT_DIR=output

TAVILY_API_KEY=tvly-...
EXA_API_KEY=
```

```powershell
# _1_history_chat 폴더에서 실행
python -m streamlit run report_analysis_agent\app.py --server.port 8501
```

## Strands 웹 검색 도구 확인

```powershell
python report_analysis_agent\scripts\check_strands_web_search.py
```

실제 검색 호출까지 확인하려면:

```powershell
$env:RUN_STRANDS_SEARCH_TEST="1"
python report_analysis_agent\scripts\check_strands_web_search.py
Remove-Item Env:RUN_STRANDS_SEARCH_TEST
```
