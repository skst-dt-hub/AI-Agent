# HR Search Agent Flow

이 문서는 HR 인재 검색 PoC 프로그램의 전체 흐름과 작동 원리를 정리한다.

## 1. 전체 구조

프로그램은 엑셀 HR 데이터를 전처리하고, 구성원별 비정형 텍스트를 임베딩한 뒤, 사용자의 자연어 질의를 Strands Graph로 처리해 추천 후보와 근거를 생성한다.

```text
HR Excel
  -> hr_excel_parser.py
  -> structured_data.json
  -> text_data.json
  -> scripts/build_embeddings.py
  -> embeddings.json
  -> S3 업로드
  -> main.py
  -> Strands Graph
  -> result.json
```

현재 실행 구조는 AWS Lambda가 아니라 **Python 함수 + Strands GraphBuilder** 방식이다.

## 2. 데이터 전처리

입력 파일:

```text
data/HR_Data_Sample_260515.xlsx
```

실행:

```powershell
python hr_excel_parser.py ./data/HR_Data_Sample_260515.xlsx
```

생성 파일:

```text
data/structured_data.json
data/text_data.json
```

`structured_data.json`은 정형 필터링에 사용된다.

예:

```json
{
  "사번": "2005001",
  "성명": "김민준",
  "소속조직": "재무팀",
  "경력연수": 20,
  "팀리딩여부": true,
  "이동가능시점": "6개월 내",
  "파견가능여부": true,
  "해외근무가능": true
}
```

`text_data.json`은 임베딩 검색과 추천 근거 생성에 사용된다.

예:

```json
{
  "사번": "2005001",
  "성명": "김민준",
  "텍스트": "현재직무: 자금운영 / 자금 운영계획 수립 및 Cash Flow 관리..."
}
```

## 3. 임베딩 생성 및 S3 적재

임베딩 생성:

```powershell
python scripts/build_embeddings.py --region us-east-1
```

사용 모델:

```text
amazon.titan-embed-text-v2:0
```

생성 파일:

```text
data/embeddings.json
```

형태:

```json
{
  "2005001": [0.123, 0.456],
  "2006002": [0.789, 0.012]
}
```

S3 업로드까지 한 번에 수행:

```powershell
python scripts/build_embeddings.py --region us-east-1 --upload
```

이미 `embeddings.json`이 있는 경우 재생성 없이 업로드:

```powershell
python scripts/build_embeddings.py --region us-east-1 --reuse-existing --upload
```

기본 S3 위치:

```text
s3://sk-hr-poc-bucket/data/structured_data.json
s3://sk-hr-poc-bucket/data/text_data.json
s3://sk-hr-poc-bucket/data/embeddings.json
```

설정 파일:

```text
config.py
```

기본값:

```python
AWS_REGION = "us-east-1"
S3_BUCKET = "sk-hr-poc-bucket"
S3_DATA_PREFIX = "data"
BEDROCK_EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
BEDROCK_CLAUDE_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
```

환경변수로 덮어쓸 수 있다.

## 4. 실행 진입점

메인 실행 파일:

```text
main.py
```

질의를 인자로 전달:

```powershell
python main.py "자금운영 경험 10년 이상이고 리더십 있는 사람 찾아줘"
```

질의를 실행 후 입력:

```powershell
python main.py
```

출력:

```text
콘솔 출력
result.json 저장
```

디버깅용 순차 실행:

```powershell
python local_test.py "자금운영 경험 10년 이상이고 리더십 있는 사람 찾아줘"
```

`main.py`는 Strands GraphBuilder를 사용한다.

`local_test.py`는 Strands 없이 Python 함수를 순서대로 직접 호출한다.

## 5. Strands Graph 구조

Graph 노드:

```text
query_understanding_agent
  -> candidate_retrieval_agent
  -> explanation_agent
```

구현 파일:

```text
main.py
strands_function_nodes.py
```

`main.py`는 GraphBuilder로 노드와 엣지를 정의한다.

`strands_function_nodes.py`는 일반 Python 함수를 Strands Graph 노드가 실행할 수 있는 형태로 감싸는 얇은 어댑터다.

각 노드는 실제 로직을 직접 갖지 않고 `hr_pipeline`의 Python 함수를 호출한다.

```text
query_understanding_agent -> hr_pipeline.query_understanding.understand_query
candidate_retrieval_agent -> hr_pipeline.candidate_retrieval.retrieve_candidates
explanation_agent         -> hr_pipeline.explanation.explain_candidates
```

## 6. Step 1: Query Understanding

구현 파일:

```text
hr_pipeline/query_understanding.py
```

역할:

사용자 자연어 질의를 Claude Haiku 4.5에 전달해 하드 조건과 소프트 조건으로 분해한다.

입력:

```json
{
  "query": "자금운영 경험 10년 이상이고 리더십 있는 사람 찾아줘"
}
```

출력:

```json
{
  "hard_conditions": {
    "경력연수_최소": 10,
    "팀리딩필요": true,
    "이동가능시점": null,
    "파견가능": null,
    "해외근무가능": null
  },
  "soft_conditions": {
    "직무키워드": ["자금운영"],
    "역량키워드": ["리더십"]
  },
  "original_query": "자금운영 경험 10년 이상이고 리더십 있는 사람 찾아줘"
}
```

하드 조건은 정형 데이터로 필터링 가능한 조건이다.

소프트 조건은 임베딩 유사도 검색에 사용할 키워드다.

## 7. Step 2: Candidate Retrieval

구현 파일:

```text
hr_pipeline/candidate_retrieval.py
```

역할:

1. S3에서 `structured_data.json`과 `embeddings.json`을 로드한다.
2. 하드 조건으로 후보를 1차 필터링한다.
3. 사용자 질의와 소프트 조건을 합쳐 Titan Embeddings로 질의 벡터를 만든다.
4. 후보별 사전 생성 임베딩과 코사인 유사도를 계산한다.
5. 상위 10명을 반환한다.

S3 JSON은 실행 컨텍스트 내 `_json_cache`에 캐싱된다.

필터링 예:

```text
경력연수_최소: 10
팀리딩필요: true
이동가능시점: 6개월내
```

이동가능시점은 다음 순서로 비교한다.

```text
즉시 < 3개월내 < 6개월내 < 1년내
```

출력 예:

```json
{
  "candidates": [
    {
      "사번": "2005001",
      "성명": "김민준",
      "소속조직": "재무팀",
      "경력연수": 20,
      "팀리딩여부": true,
      "유사도": 0.3642
    }
  ],
  "total_before_filter": 30,
  "total_after_filter": 15,
  "original_query": "..."
}
```

## 8. Step 3: Explanation

구현 파일:

```text
hr_pipeline/explanation.py
```

역할:

1. S3에서 `text_data.json`을 로드한다.
2. 후보 사번 기준으로 상세 텍스트를 붙인다.
3. Claude Haiku 4.5에 원본 질의, 후보 정형 정보, 상세 텍스트를 전달한다.
4. 추천 근거, 강점, 약점, 비교표, 요약을 생성한다.

출력 예:

```json
{
  "ranked_candidates": [
    {
      "순위": 1,
      "사번": "2005001",
      "성명": "김민준",
      "소속조직": "재무팀",
      "유사도": 0.3642,
      "추천근거": "자금운영 20년 경력으로 요구 조건 충족...",
      "강점": ["자금운영 직무 경험 20년", "팀리딩 경험"],
      "약점": ["즉시 이동 불가"]
    }
  ],
  "comparison_table": [
    {
      "성명": "김민준",
      "유사도": 0.3642,
      "경력연수": 20,
      "팀리딩": "예",
      "이동가능시점": "6개월 내"
    }
  ],
  "summary": "..."
}
```

## 9. 핵심 모듈 역할

```text
app.py
  Streamlit 기반 사용자 UI
  여러 번 질의하고 이전 결과를 확인할 수 있음

hr_excel_parser.py
  엑셀을 structured_data.json, text_data.json으로 변환

scripts/build_embeddings.py
  text_data.json을 Titan Embeddings로 변환
  선택적으로 S3 업로드 수행

config.py
  AWS region, S3 bucket, Bedrock model ID 기본값 관리

hr_pipeline/common.py
  S3 로드, 캐시, Bedrock 호출, JSON 추출, 코사인 유사도 계산

hr_pipeline/query_understanding.py
  사용자 질의를 하드 조건과 소프트 조건으로 구조화

hr_pipeline/candidate_retrieval.py
  하드 필터링과 임베딩 유사도 검색

hr_pipeline/explanation.py
  최종 추천 근거와 비교표 생성

strands_function_nodes.py
  Python 함수를 Strands Graph 노드 형태로 감싸는 어댑터

main.py
  Strands GraphBuilder 기반 실행 진입점

local_test.py
  Strands 없이 순차 실행하는 디버깅용 진입점
```

## 10. 유사도 계산 방식

현재 유사도는 Titan Embeddings 벡터 간 **코사인 유사도**로 계산한다.

계산 흐름:

```text
1. text_data.json의 구성원별 텍스트를 Titan Embeddings로 변환
2. embeddings.json에 사번별 벡터 저장
3. 사용자 질의 입력
4. Claude가 질의를 hard_conditions와 soft_conditions로 분해
5. original_query + 직무키워드 + 역량키워드를 하나의 검색 문장으로 결합
6. 검색 문장을 Titan Embeddings로 변환
7. 후보자의 사전 생성 벡터와 검색 문장 벡터의 코사인 유사도 계산
8. 유사도 내림차순으로 정렬
```

구현 위치:

```text
hr_pipeline/candidate_retrieval.py
  build_soft_query()
  retrieve_candidates()

hr_pipeline/common.py
  invoke_titan_embedding()
  cosine_similarity()
```

Titan 호출 설정:

```python
{
  "inputText": text,
  "dimensions": 1024,
  "normalize": True
}
```

코사인 유사도 공식:

```text
similarity = dot(query_vector, candidate_vector)
             / (norm(query_vector) * norm(candidate_vector))
```

현재 Titan 호출에서 `normalize=True`를 사용하므로 벡터가 정규화되어 반환된다. 그래도 코드에서는 안전하게 norm을 다시 계산해 코사인 유사도를 구한다.

### 유사도가 낮아 보이는 이유

현재 결과에서 0.3대 유사도가 나오는 것은 반드시 나쁜 결과라는 뜻은 아니다.

이유:

- 검색 문장은 짧다.
- 후보자 텍스트는 여러 업무, 경력, 강점, 커리어 희망이 합쳐진 긴 문서다.
- 임베딩은 단어 일치율이 아니라 의미 공간의 방향 유사도를 본다.
- 현재 후보자는 hard filter를 먼저 통과한 뒤 유사도 정렬되므로, 유사도는 최종 점수가 아니라 soft matching 순위에 가깝다.
- Titan Embeddings의 raw cosine score는 0.8, 0.9처럼 높게 나오지 않는 경우가 많다. 특히 짧은 질의와 긴 프로필 문서 비교에서는 더 그렇다.

따라서 현재 UI의 `유사도`는 절대 점수라기보다 **동일 질의 내 후보 간 상대 순위**로 해석하는 것이 맞다.

예:

```text
김민준 0.3642
배유정 0.3618
이서연 0.2106
```

이 경우 김민준과 배유정은 거의 비슷하게 관련성이 높고, 이서연은 앞의 두 명보다 의미적으로 떨어진다고 해석한다.

### 개선 가능성

유사도를 사용자가 보기 좋게 만들려면 다음 중 하나를 적용할 수 있다.

```text
1. UI에는 raw similarity 대신 0~100점으로 변환한 검색점수 표시
2. 직무/역량/경력/리더십 등 항목별 점수로 분해
3. hard condition 충족 점수 + soft similarity를 합산한 최종 적합도 생성
4. 후보자 텍스트를 하나의 긴 문서가 아니라 직무, 과제, 강점 단위로 chunking 후 max similarity 사용
5. Scoring Agent를 추가해 근거 기반 적합도를 재평가
```

현재 PoC에서는 단순성과 투명성을 위해 raw cosine similarity를 그대로 노출한다.

## 10-1. Scoring Agent 점수 정책

Scoring Agent는 검색 후보를 최종Score 기준으로 재정렬한다.

```text
직무적합도: 항상 50점
경험깊이: 항상 20점
리더십: 질의에 리더십 조건이 있을 때만 15점
이동/배치: 질의에 이동/파견/해외 조건이 있을 때만 10점
```

최종Score는 적용된 항목 기준으로 정규화한다.

```text
최종Score = 획득점수 / 적용가능점수 * 100
```

경험깊이는 다음 두 항목으로 구성된다.

```text
경력요건충족도 10점
주요업무깊이 10점
```

경력요건충족도는 질의에 경력 조건이 있을 때와 없을 때 다르게 계산한다.

```text
경력 조건 있음 -> min(경력연수 / 요구연수, 1.0) * 10
경력 조건 없음 -> 7점
```

경력 조건이 없는 질의에서 전체 경력연수 percentile을 강하게 반영하면 사용자가 요구하지 않은 "연차"가 순위를 과도하게 좌우할 수 있으므로, 중립 기본점 7점을 부여한다.

## 11. 현재 파일 구조

```text
hr_search_agent/
  app.py
  config.py
  FLOW.md
  hr_excel_parser.py
  local_test.py
  main.py
  requirements.txt
  result.json
  result_local.json
  strands_function_nodes.py

  data/
    HR_Data_Sample_260515.xlsx
    structured_data.json
    text_data.json
    embeddings.json

  hr_pipeline/
    __init__.py
    common.py
    query_understanding.py
    candidate_retrieval.py
    explanation.py

  scripts/
    build_embeddings.py
```

파일 역할:

```text
app.py
  Streamlit UI 실행 진입점

main.py
  Strands GraphBuilder 기반 CLI 실행 진입점

local_test.py
  Strands 없이 함수들을 순차 실행하는 디버깅용 진입점

strands_function_nodes.py
  일반 Python 함수를 Strands Graph 노드로 감싸는 어댑터

hr_pipeline/
  실제 HR 검색 파이프라인 로직

scripts/build_embeddings.py
  임베딩 생성 및 선택적 S3 업로드

data/
  로컬 원천 데이터와 전처리/임베딩 산출물
```

## 12. 일반 실행 순서

엑셀 데이터가 바뀐 경우:

```powershell
python hr_excel_parser.py ./data/HR_Data_Sample_260515.xlsx
python scripts/build_embeddings.py --region us-east-1 --upload
python main.py
```

이미 S3 데이터가 준비된 경우:

```powershell
python main.py
```

질의를 명령어에 직접 넣는 경우:

```powershell
python main.py "해외근무 가능하고 영업관리 경험이 있는 사람 추천해줘"
```

UI 실행:

```powershell
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

브라우저:

```text
http://127.0.0.1:8501
```

## 13. 확장 포인트

Scoring Agent를 추가하려면 Graph에 노드를 하나 더 추가하면 된다.

현재:

```text
query_understanding_agent
  -> candidate_retrieval_agent
  -> explanation_agent
```

확장:

```text
query_understanding_agent
  -> candidate_retrieval_agent
  -> scoring_agent
  -> explanation_agent
```

이 경우 `hr_pipeline/scoring.py`를 추가하고, `main.py`에서 노드와 엣지만 조정하면 된다.

## 14. 주의사항

- 현재 데이터 크기에서는 S3 JSON 전체 로드와 메모리 코사인 유사도 계산으로 충분하다.
- 데이터가 수천 명 이상으로 커지면 OpenSearch Serverless, Aurora pgvector, FAISS 같은 벡터 인덱스 도입을 검토해야 한다.
- HR 데이터는 개인정보를 포함하므로 CloudWatch 로그, 콘솔 출력, S3 권한, IAM 정책을 최소화해야 한다.
- Claude가 생성하는 추천 근거는 후보 데이터에 기반하지만, 최종 인사 판단은 사람이 검토해야 한다.
