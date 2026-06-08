# 결정해야 하는 사항

## 1. Bedrock Knowledge Base 생성 방식

결정됨.

- AWS Console에서 KB와 S3 Data Source를 직접 생성한 뒤, `config.py`에 ID를 입력한다.
- 앱 또는 스크립트에서 KB를 자동 생성하지 않는다.

필요한 값:

- `KNOWLEDGE_BASE_ID`
- `DATA_SOURCE_ID`
- `S3_BUCKET`
- `S3_PREFIX`

## 2. Strands 웹 검색 도구

일부 확인됨.

확인할 것:

- 현재 사내망에서 `strands-agents-tools`의 웹 검색 도구가 실제 검색까지 동작하는지
- 별도 API key가 필요한지
- 실패 시 반환할 오류 형식

확인된 import 방식:

- `strands_tools.tavily.tavily_search`
- `strands_tools.exa.exa_search`

아직 실제 live search는 실행하지 않았다.

결정 방향:

- 별도 뉴스 API는 사용하지 않는다.
- Strands tools에서 사용 가능한 검색 도구만 사용한다.
- 실패해도 내부 KB 기반 분석은 계속 진행한다.

## 3. Output 파일 범위

결정됨.

초기 구현 범위:

- Markdown + Excel
- PPT는 템플릿 또는 레이아웃 요구가 정해진 뒤 추가

## 4. PPT 템플릿 필요 여부

아직 결정 필요.

확인할 것:

- 회사 표준 보고서 템플릿이 있는지
- 슬라이드 수가 고정인지 가변인지
- 제목/요약/내부 현황/외부 동향/결론/출처를 각각 어느 placeholder에 넣을지

템플릿이 없으면:

- 기본 4~5장 구성으로 자동 생성
- 디자인 품질은 제한적

## 5. Excel 사용 목적

아직 결정 필요.

선택지:

- 보고서 요약용
- 검색 원천 데이터 보관용
- 둘 다

권장:

- 둘 다.
- `Summary`, `Internal`, `External`, `Report`, `RunLog` 시트로 구분.

## 6. 출처 표시 수준

아직 결정 필요.

권장:

- 최종 Markdown에는 주요 출처만 표시.
- Excel에는 내부 S3 URI, score, 외부 URL, 날짜를 최대한 보존.

## 7. 결과 보관 정책

아직 결정 필요.

권장:

- 같은 키워드라도 timestamp 폴더를 새로 만들어 덮어쓰지 않는다.
- 오래된 output 삭제는 수동 또는 별도 관리 기능으로 둔다.

## 8. 프로젝트 표시 이름

현재 폴더명:

```text
report_analysis_agent
```

앱 화면 표시 이름은 아직 결정 필요.

후보:

- Report Analysis Agent
- Intelligence Report Agent
- Internal Report Insight Agent
- 보고서 분석 에이전트
