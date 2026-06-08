# 보고 History Tree PoC - 셋업 가이드

## 개요

엑셀 보고 파일에서 키워드로 관련 내용을 검색하고, 날짜 순으로 정리된 History Tree를 자동 생성하는 AI Agent입니다.

- **Search Agent** → Knowledge Base 시맨틱 검색
- **Analyst Agent** → 신규 등장 / 반복 / 소멸 분류
- **Writer Agent** → History Tree 형태로 출력

---

## 사전 준비

- Python 3.10 이상
- AWS 계정 (Bedrock 접근 가능)
- 보고 엑셀 파일 (.xlsx)

---

## Step 1. AWS CLI 설치

아래 링크에서 인스톨러 다운로드 후 실행 (다음 다음 하면 설치 완료)

https://awscli.amazonaws.com/AWSCLIV2.msi

설치 후 **새 PowerShell 창** 열고 확인:

```powershell
aws --version
```

---

## Step 2. AWS 자격증명 설정

IAM 콘솔에서 Access Key 발급 후:

```powershell
aws configure
```

- AWS Access Key ID: 발급받은 키 입력
- AWS Secret Access Key: 발급받은 시크릿 입력
- Default region name: `us-east-2`
- Default output format: 그냥 엔터

---

## Step 3. Bedrock 모델 액세스 신청

1. AWS 콘솔 → Amazon Bedrock → Model catalog
2. `Claude Haiku 4.5` 검색 → 클릭
3. use case 폼 작성 및 제출
4. **15분 대기** 후 사용 가능

---

## Step 4. Python 패키지 설치

```powershell
pip install -r requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org
```

ex. pip install strands-agents strands-agents-tools boto3 --trusted-host pypi.org --trusted-host files.pythonhosted.org

> 사내망 환경이 아닌 경우 `--trusted-host` 옵션 없이 실행해도 됩니다.

---

## Step 5. S3 버킷 생성 및 파일 업로드

```powershell
# 버킷 생성
aws s3 mb s3://버킷이름 --region us-east-2

# 단일 파일 업로드
aws s3 cp "파일경로\파일명.xlsx" s3://버킷이름/

# 폴더 전체 업로드
aws s3 cp ".\data" s3://버킷이름/ --recursive

# 업로드 확인
aws s3 ls s3://버킷이름/
```

---

## Step 6. Bedrock Knowledge Base 생성

1. AWS 콘솔 → Amazon Bedrock → Knowledge Bases → **Create**
2. **Unstructured** 선택
3. Data source: S3 선택 → 위에서 만든 버킷 연결
4. Embedding model: `Titan Text Embeddings V2` 선택
5. Vector store: **새로운 벡터 저장소 빠른 생성 (권장)** 선택
6. Create → 5~10분 대기
7. 생성 완료 후 **Sync** 버튼 클릭 → 동기화 완료 확인
8. Knowledge Base ID 복사해두기 (예: `PKHCULLVMP`)

---

## Step 7. 코드 설정

`history_tree.py` 상단 두 줄 본인 값으로 수정:

```python
KNOWLEDGE_BASE_ID = "여기에_Knowledge_Base_ID"
REGION = "us-east-2"
```

---

## Step 8. 실행

```powershell
python history_tree.py
```

키워드 입력 예시:
```
검색할 키워드를 입력하세요: 몰리브덴
```

---

## 파일 구조

```
📁 프로젝트 폴더
├── history_tree.py     # 메인 실행 파일
└── 📁 data             # 보고 엑셀 파일 폴더
    ├── 보고자료_2026.xlsx
    └── ...
└── 📁 _Setting
    ├── AWSCLIV2.msi        # AWS CLI 설치 파일
    ├── requirements.txt    # 패키지 목록
    └── SETUP.md            # 이 파일
```

---

## 문제 해결

**`aws: command not found`**
→ PowerShell 새 창 열고 다시 시도

**`invalid peer certificate`**
→ pip 설치 시 `--trusted-host` 옵션 추가 (사내망 SSL 문제)

**`ResourceNotFoundException: Model use case details have not been submitted`**
→ Bedrock 콘솔에서 use case 폼 제출 후 15분 대기

**`ValidationException: Invocation of model ID ... isn't supported`**
→ 모델 ID가 `us.anthropic.claude-haiku-4-5-20251001-v1:0` 형태인지 확인

**`S3 동기화 실패 (301 에러)`**
→ S3 버킷 리전과 Knowledge Base 리전이 동일한지 확인
