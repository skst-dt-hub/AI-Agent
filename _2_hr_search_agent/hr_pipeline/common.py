import json
import math
import os
import re
from typing import Any

import boto3


AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET = os.getenv("S3_BUCKET", "sk-hr-poc-bucket")
S3_DATA_PREFIX = os.getenv("S3_DATA_PREFIX", "data")
BEDROCK_EMBEDDING_MODEL_ID = os.getenv(
    "BEDROCK_EMBEDDING_MODEL_ID",
    "amazon.titan-embed-text-v2:0",
)
BEDROCK_CLAUDE_MODEL_ID = os.getenv(
    "BEDROCK_CLAUDE_MODEL_ID",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
)

_s3_client = None
_bedrock_runtime = None
_json_cache: dict[str, Any] = {}


def get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=AWS_REGION)
    return _s3_client


def get_bedrock_runtime():
    global _bedrock_runtime
    if _bedrock_runtime is None:
        _bedrock_runtime = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    return _bedrock_runtime


def s3_key(filename: str) -> str:
    return f"{S3_DATA_PREFIX.strip('/')}/{filename}"


def load_s3_json(filename: str) -> Any:
    key = s3_key(filename)
    if key in _json_cache:
        return _json_cache[key]

    response = get_s3_client().get_object(Bucket=S3_BUCKET, Key=key)
    data = json.loads(response["Body"].read().decode("utf-8"))
    _json_cache[key] = data
    return data


def invoke_titan_embedding(text: str) -> list[float]:
    body = {
        "inputText": text,
        "dimensions": 1024,
        "normalize": True,
    }
    response = get_bedrock_runtime().invoke_model(
        modelId=BEDROCK_EMBEDDING_MODEL_ID,
        body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(response["body"].read())
    embedding = payload.get("embedding")
    if not isinstance(embedding, list):
        raise ValueError(f"Titan embedding 응답 형식이 예상과 다릅니다: {payload}")
    return embedding


def invoke_claude_json(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> dict[str, Any]:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": 0,
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": user_prompt}],
            }
        ],
    }
    response = get_bedrock_runtime().invoke_model(
        modelId=BEDROCK_CLAUDE_MODEL_ID,
        body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(response["body"].read())
    content = payload.get("content", [])
    text_parts = [part.get("text", "") for part in content if part.get("type") == "text"]
    return extract_json("\n".join(text_parts))


def extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return json.loads(stripped)

    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if not match:
        raise ValueError(f"Claude 응답에서 JSON 객체를 찾지 못했습니다: {text}")
    return json.loads(match.group(0))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError(f"벡터 차원이 다릅니다: {len(left)} != {len(right)}")
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def normalize_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().upper()
    if text in {"Y", "YES", "TRUE", "1", "필요", "가능"}:
        return True
    if text in {"N", "NO", "FALSE", "0", "불필요", "불가능"}:
        return False
    return None

