import argparse
import json
import sys
from pathlib import Path
from typing import Any

import boto3


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from config import AWS_REGION, BEDROCK_EMBEDDING_MODEL_ID, S3_BUCKET, S3_DATA_PREFIX


DATA_DIR = BASE_DIR / "data"
STRUCTURED_PATH = DATA_DIR / "structured_data.json"
TEXT_PATH = DATA_DIR / "text_data.json"
EMBEDDINGS_PATH = DATA_DIR / "embeddings.json"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_person_id(record: dict[str, Any]) -> str:
    person_id = str(record.get("사번", "")).strip()
    if not person_id:
        raise ValueError(f"사번이 없는 text_data 레코드가 있습니다: {record}")
    return person_id


def get_text(record: dict[str, Any]) -> str:
    text = str(record.get("텍스트", "")).strip()
    if not text:
        raise ValueError(f"텍스트가 없는 text_data 레코드가 있습니다: {record}")
    return text


def embed_text(bedrock_runtime, text: str) -> list[float]:
    body = {
        "inputText": text,
        "dimensions": 1024,
        "normalize": True,
    }
    response = bedrock_runtime.invoke_model(
        modelId=BEDROCK_EMBEDDING_MODEL_ID,
        body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(response["body"].read())
    embedding = payload.get("embedding")
    if not isinstance(embedding, list):
        raise ValueError(f"Bedrock embedding 응답 형식이 예상과 다릅니다: {payload}")
    return embedding


def build_embeddings(text_records: list[dict[str, Any]], region_name: str) -> dict[str, list[float]]:
    bedrock_runtime = boto3.client("bedrock-runtime", region_name=region_name)
    embeddings: dict[str, list[float]] = {}

    for index, record in enumerate(text_records, start=1):
        person_id = get_person_id(record)
        text = get_text(record)
        try:
            embeddings[person_id] = embed_text(bedrock_runtime, text)
        except Exception as exc:
            raise RuntimeError(f"{person_id} 임베딩 생성 실패") from exc
        print(f"[{index}/{len(text_records)}] {person_id} 임베딩 생성 완료")

    return embeddings


def ensure_bucket(s3_client, bucket: str, region_name: str, create_bucket: bool) -> None:
    try:
        s3_client.head_bucket(Bucket=bucket)
        return
    except Exception as exc:
        if not create_bucket:
            raise RuntimeError(
                f"S3 버킷을 찾을 수 없습니다: {bucket}. "
                "--create-bucket 옵션으로 생성하거나 기존 버킷명을 지정하세요."
            ) from exc

    print(f"S3 버킷 생성: {bucket}")
    if region_name == "us-east-1":
        s3_client.create_bucket(Bucket=bucket)
    else:
        s3_client.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": region_name},
        )
    s3_client.get_waiter("bucket_exists").wait(Bucket=bucket)


def upload_file(s3_client, local_path: Path, bucket: str, key: str) -> None:
    s3_client.upload_file(str(local_path), bucket, key)
    print(f"업로드 완료: s3://{bucket}/{key}")


def upload_outputs(region_name: str, bucket: str, prefix: str, create_bucket: bool) -> None:
    if not STRUCTURED_PATH.exists():
        raise FileNotFoundError(f"structured_data.json을 찾을 수 없습니다: {STRUCTURED_PATH}")
    if not TEXT_PATH.exists():
        raise FileNotFoundError(f"text_data.json을 찾을 수 없습니다: {TEXT_PATH}")
    if not EMBEDDINGS_PATH.exists():
        raise FileNotFoundError(f"embeddings.json을 찾을 수 없습니다: {EMBEDDINGS_PATH}")

    s3_client = boto3.client("s3", region_name=region_name)
    ensure_bucket(s3_client, bucket, region_name, create_bucket)

    normalized_prefix = prefix.strip("/")
    upload_file(s3_client, STRUCTURED_PATH, bucket, f"{normalized_prefix}/structured_data.json")
    upload_file(s3_client, TEXT_PATH, bucket, f"{normalized_prefix}/text_data.json")
    upload_file(s3_client, EMBEDDINGS_PATH, bucket, f"{normalized_prefix}/embeddings.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="text_data.json을 Titan Embeddings로 변환해 embeddings.json을 생성합니다."
    )
    parser.add_argument("--region", default=AWS_REGION, help="AWS region. 기본값: us-east-1")
    parser.add_argument(
        "--output",
        default=str(EMBEDDINGS_PATH),
        help="생성할 embeddings.json 경로",
    )
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="기존 embeddings.json이 있으면 Bedrock 재호출 없이 사용합니다.",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="생성 후 structured/text/embeddings JSON을 S3에 업로드합니다.",
    )
    parser.add_argument("--bucket", default=S3_BUCKET, help="S3 bucket name")
    parser.add_argument("--prefix", default=S3_DATA_PREFIX, help="S3 key prefix")
    parser.add_argument(
        "--create-bucket",
        action="store_true",
        help="S3 버킷이 없으면 생성합니다.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = Path(args.output)

    if not TEXT_PATH.exists():
        raise FileNotFoundError(f"text_data.json을 찾을 수 없습니다: {TEXT_PATH}")

    print(f"AWS region: {args.region}")
    print(f"Embedding model: {BEDROCK_EMBEDDING_MODEL_ID}")
    print(f"text_data 로드: {TEXT_PATH}")

    if args.reuse_existing and output_path.exists():
        print(f"기존 임베딩 사용: {output_path}")
    else:
        text_records = load_json(TEXT_PATH)
        if not isinstance(text_records, list):
            raise ValueError("text_data.json은 list 형태여야 합니다.")

        embeddings = build_embeddings(text_records, args.region)
        save_json(output_path, embeddings)
        print(f"임베딩 저장 완료: {output_path}")

    if args.upload:
        if output_path != EMBEDDINGS_PATH:
            raise ValueError(f"S3 업로드는 기본 경로만 지원합니다: {EMBEDDINGS_PATH}")
        upload_outputs(args.region, args.bucket, args.prefix, args.create_bucket)


if __name__ == "__main__":
    main()
