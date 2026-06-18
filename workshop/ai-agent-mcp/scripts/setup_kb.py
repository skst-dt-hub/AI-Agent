#!/usr/bin/env python3
"""Create a Bedrock Knowledge Base (OpenSearch Serverless vector store) for the
history_tree / report_analysis agents, upload sample report docs, ingest, and
print the KNOWLEDGE_BASE_ID + DATA_SOURCE_ID to inject into the runtime.

Idempotent-ish: tolerates already-existing AOSS policies/collection.

Env:
    AWS_REGION (default us-east-1)
    S3_BUCKET  (required)  - reused; KB docs go under KB_PREFIX
"""

from __future__ import annotations

import json
import os
import time

import boto3
from botocore.exceptions import ClientError
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth

REGION = os.getenv("AWS_REGION", "us-east-1")
ACCOUNT = boto3.client("sts").get_caller_identity()["Account"]
BUCKET = os.environ["S3_BUCKET"]
KB_PREFIX = "kb-docs/"

COLLECTION = "ai-agent-kb"
INDEX = "bedrock-knowledge-base-index"
KB_ROLE = "AiAgentKbExecutionRole"
EMBED_ARN = f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0"
VECTOR_FIELD = "bedrock-knowledge-base-default-vector"
TEXT_FIELD = "AMAZON_BEDROCK_TEXT_CHUNK"
META_FIELD = "AMAZON_BEDROCK_METADATA"
DIM = 1024

session = boto3.Session(region_name=REGION)
iam = session.client("iam")
aoss = session.client("opensearchserverless")
s3 = session.client("s3")
bedrock_agent = session.client("bedrock-agent")

caller_arn = boto3.client("sts").get_caller_identity()["Arn"]
# normalize assumed-role session arn -> role arn for AOSS data access principal
caller_role_arn = caller_arn
if ":assumed-role/" in caller_arn:
    parts = caller_arn.split(":assumed-role/")[1].split("/")[0]
    caller_role_arn = f"arn:aws:iam::{ACCOUNT}:role/{parts}"


def log(msg):
    print(msg, flush=True)


# ── 1. KB execution role ────────────────────────────────────────────────────
def ensure_kb_role() -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": "bedrock.amazonaws.com"},
                       "Action": "sts:AssumeRole"}],
    }
    try:
        arn = iam.create_role(RoleName=KB_ROLE, AssumeRolePolicyDocument=json.dumps(trust),
                              Description="Bedrock KB execution role for ai-agent-mcp")["Role"]["Arn"]
        log(f"created KB role {arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        arn = iam.get_role(RoleName=KB_ROLE)["Role"]["Arn"]
        log(f"KB role exists {arn}")
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["bedrock:InvokeModel"], "Resource": [EMBED_ARN]},
            {"Effect": "Allow", "Action": ["aoss:APIAccessAll"],
             "Resource": [f"arn:aws:aoss:{REGION}:{ACCOUNT}:collection/*"]},
            {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": [f"arn:aws:s3:::{BUCKET}"]},
            {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": [f"arn:aws:s3:::{BUCKET}/*"]},
        ],
    }
    iam.put_role_policy(RoleName=KB_ROLE, PolicyName="AiAgentKbPolicy",
                        PolicyDocument=json.dumps(policy))
    return arn


# ── 2. AOSS policies + collection ───────────────────────────────────────────
def ensure_aoss(kb_role_arn: str) -> str:
    enc = {"Rules": [{"ResourceType": "collection", "Resource": [f"collection/{COLLECTION}"]}],
           "AWSOwnedKey": True}
    try:
        aoss.create_security_policy(name=f"{COLLECTION}-enc", type="encryption",
                                    policy=json.dumps(enc))
        log("created encryption policy")
    except ClientError as e:
        log(f"encryption policy: {e.response['Error']['Code']}")

    net = [{"Rules": [{"ResourceType": "collection", "Resource": [f"collection/{COLLECTION}"]},
                      {"ResourceType": "dashboard", "Resource": [f"collection/{COLLECTION}"]}],
            "AllowFromPublic": True}]
    try:
        aoss.create_security_policy(name=f"{COLLECTION}-net", type="network",
                                    policy=json.dumps(net))
        log("created network policy")
    except ClientError as e:
        log(f"network policy: {e.response['Error']['Code']}")

    access = [{"Rules": [
        {"ResourceType": "index", "Resource": [f"index/{COLLECTION}/*"],
         "Permission": ["aoss:*"]},
        {"ResourceType": "collection", "Resource": [f"collection/{COLLECTION}"],
         "Permission": ["aoss:*"]}],
        "Principal": [kb_role_arn, caller_role_arn]}]
    try:
        aoss.create_access_policy(name=f"{COLLECTION}-acc", type="data",
                                  policy=json.dumps(access))
        log("created data access policy")
    except ClientError as e:
        log(f"access policy: {e.response['Error']['Code']}")

    try:
        aoss.create_collection(name=COLLECTION, type="VECTORSEARCH")
        log("creating collection ...")
    except ClientError as e:
        log(f"collection: {e.response['Error']['Code']}")

    # wait active
    for _ in range(60):
        d = aoss.batch_get_collection(names=[COLLECTION])["collectionDetails"]
        if d and d[0]["status"] == "ACTIVE":
            log(f"collection ACTIVE: {d[0]['arn']}")
            return d[0]["arn"], d[0]["collectionEndpoint"]
        time.sleep(5)
    raise TimeoutError("collection not active")


# ── 3. vector index ─────────────────────────────────────────────────────────
def ensure_index(endpoint: str):
    host = endpoint.replace("https://", "")
    auth = AWSV4SignerAuth(session.get_credentials(), REGION, "aoss")
    client = OpenSearch(hosts=[{"host": host, "port": 443}], http_auth=auth,
                        use_ssl=True, verify_certs=True, connection_class=RequestsHttpConnection,
                        timeout=60)
    body = {
        "settings": {"index": {"knn": True}},
        "mappings": {"properties": {
            VECTOR_FIELD: {"type": "knn_vector", "dimension": DIM,
                           "method": {"name": "hnsw", "engine": "faiss", "space_type": "l2"}},
            TEXT_FIELD: {"type": "text"},
            META_FIELD: {"type": "text", "index": False},
        }},
    }
    # data-access policy can take a moment to propagate
    for attempt in range(12):
        try:
            if client.indices.exists(index=INDEX):
                log("index already exists")
                return
            client.indices.create(index=INDEX, body=body)
            log("created vector index")
            return
        except Exception as e:
            log(f"index attempt {attempt+1}: {type(e).__name__}: {str(e)[:120]}")
            time.sleep(10)
    raise RuntimeError("failed to create index")


# ── 4. real report docs ─────────────────────────────────────────────────────
import pathlib

LOCAL_DOCS_DIR = pathlib.Path(
    r"\\10.113.101.151\ai.dt추진단\DT\13. AI Agent\AWS PoC\data"
    r"\(26.04) AIDT 과제\(#1) 과거 보고 History Tree 생성\Input Data_전처리"
)
ALLOWED_EXTS = {".xlsx", ".pptx"}

CONTENT_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def upload_docs():
    files = [f for f in LOCAL_DOCS_DIR.rglob("*") if f.suffix in ALLOWED_EXTS and f.is_file()]
    log(f"uploading {len(files)} files from {LOCAL_DOCS_DIR}")
    for f in files:
        key = f"{KB_PREFIX}{f.name}"
        content_type = CONTENT_TYPES.get(f.suffix, "application/octet-stream")
        with open(f, "rb") as fh:
            s3.put_object(Bucket=BUCKET, Key=key, Body=fh, ContentType=content_type)
        log(f"uploaded s3://{BUCKET}/{key}")


# ── 5. KB + data source + ingestion ─────────────────────────────────────────
def create_kb(kb_role_arn: str, collection_arn: str) -> tuple[str, str]:
    kb = bedrock_agent.create_knowledge_base(
        name="ai-agent-history-kb",
        roleArn=kb_role_arn,
        knowledgeBaseConfiguration={
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {"embeddingModelArn": EMBED_ARN},
        },
        storageConfiguration={
            "type": "OPENSEARCH_SERVERLESS",
            "opensearchServerlessConfiguration": {
                "collectionArn": collection_arn,
                "vectorIndexName": INDEX,
                "fieldMapping": {"vectorField": VECTOR_FIELD, "textField": TEXT_FIELD,
                                 "metadataField": META_FIELD},
            },
        },
    )["knowledgeBase"]
    kb_id = kb["knowledgeBaseId"]
    log(f"created KB {kb_id}")

    ds = bedrock_agent.create_data_source(
        knowledgeBaseId=kb_id, name="s3-reports",
        dataSourceConfiguration={
            "type": "S3",
            "s3Configuration": {"bucketArn": f"arn:aws:s3:::{BUCKET}",
                                "inclusionPrefixes": [KB_PREFIX]},
        },
    )["dataSource"]
    ds_id = ds["dataSourceId"]
    log(f"created data source {ds_id}")

    job = bedrock_agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)["ingestionJob"]
    job_id = job["ingestionJobId"]
    for _ in range(60):
        st = bedrock_agent.get_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id,
                                             ingestionJobId=job_id)["ingestionJob"]["status"]
        log(f"ingestion: {st}")
        if st in ("COMPLETE", "FAILED"):
            break
        time.sleep(5)
    return kb_id, ds_id


def main():
    kb_role_arn = ensure_kb_role()
    time.sleep(8)  # role propagation
    collection_arn, endpoint = ensure_aoss(kb_role_arn)
    ensure_index(endpoint)
    upload_docs()
    time.sleep(20)  # let index settle before KB create
    kb_id, ds_id = create_kb(kb_role_arn, collection_arn)
    log("\n================ INJECT THESE ================")
    log(f"KNOWLEDGE_BASE_ID={kb_id}")
    log(f"DATA_SOURCE_ID={ds_id}")
    log(f"KB_ROLE_ARN={kb_role_arn}")
    log(f"COLLECTION_ARN={collection_arn}")


if __name__ == "__main__":
    main()
