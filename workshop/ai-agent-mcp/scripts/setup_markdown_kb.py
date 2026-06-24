#!/usr/bin/env python3
"""Create an isolated Bedrock Knowledge Base for Markdown-preprocessed report docs.

This is intended for A/B testing against the original xlsx-ingested KB. It creates
separate S3 storage, OpenSearch Serverless collection/index, KB, data source, uploads
Markdown files, starts ingestion, and writes an env file with the new resource IDs.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

VECTOR_FIELD = "bedrock-knowledge-base-default-vector"
TEXT_FIELD = "AMAZON_BEDROCK_TEXT_CHUNK"
META_FIELD = "AMAZON_BEDROCK_METADATA"
DIM = 1024
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
INDEX = "bedrock-knowledge-base-md-index"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create isolated Markdown KB test resources.")
    parser.add_argument("--markdown-dir", required=True, help="Directory containing generated .md files.")
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE"), help="AWS CLI profile to use.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--bucket", help="S3 bucket to create/use. Defaults to ai-agent-mcp-md-test-{account}-{region}.")
    parser.add_argument("--prefix", default="kb-docs-md/", help="S3 prefix for Markdown KB docs.")
    parser.add_argument("--collection", default="ai-agent-md-kb", help="OpenSearch Serverless collection name.")
    parser.add_argument("--role-name", default="AiAgentMarkdownKbExecutionRole", help="Bedrock KB execution role name.")
    parser.add_argument("--kb-name", default="ai-agent-history-md-kb", help="Bedrock Knowledge Base name.")
    parser.add_argument("--data-source-name", default="s3-markdown-reports", help="Bedrock data source name.")
    parser.add_argument("--env-out", default=".env.markdown-kb", help="Output env file path.")
    args = parser.parse_args()

    markdown_dir = Path(args.markdown_dir).resolve()
    if not markdown_dir.is_dir():
        raise SystemExit(f"Markdown directory does not exist: {markdown_dir}")
    md_files = sorted(path for path in markdown_dir.rglob("*.md") if path.is_file())
    if not md_files:
        raise SystemExit(f"No .md files found: {markdown_dir}")

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    bucket = args.bucket or f"ai-agent-mcp-md-test-{account}-{args.region}"
    prefix = args.prefix.strip("/") + "/"

    clients = {
        "iam": session.client("iam"),
        "aoss": session.client("opensearchserverless"),
        "s3": session.client("s3"),
        "bedrock_agent": session.client("bedrock-agent"),
        "sts": session.client("sts"),
    }

    log(f"account={account}")
    log(f"region={args.region}")
    log(f"bucket={bucket}")
    log(f"prefix={prefix}")
    log(f"markdown_files={len(md_files)}")

    ensure_bucket(clients["s3"], bucket, args.region)
    role_arn = ensure_kb_role(
        iam=clients["iam"],
        role_name=args.role_name,
        bucket=bucket,
        region=args.region,
        account=account,
    )
    time.sleep(10)
    collection_arn, endpoint = ensure_aoss(
        session=session,
        aoss=clients["aoss"],
        sts=clients["sts"],
        collection=args.collection,
        role_arn=role_arn,
        region=args.region,
        account=account,
    )
    ensure_index(session=session, endpoint=endpoint, region=args.region)
    upload_markdown(clients["s3"], bucket, prefix, markdown_dir, md_files)
    time.sleep(20)
    kb_id, ds_id, ingestion_job_id = create_kb_and_ingest(
        bedrock_agent=clients["bedrock_agent"],
        kb_name=args.kb_name,
        data_source_name=args.data_source_name,
        role_arn=role_arn,
        collection_arn=collection_arn,
        bucket=bucket,
        prefix=prefix,
        region=args.region,
    )

    env_path = Path(args.env_out).resolve()
    env_lines = [
        f"AWS_REGION={args.region}",
        f"S3_BUCKET={bucket}",
        f"S3_PREFIX=data",
        f"KNOWLEDGE_BASE_ID={kb_id}",
        f"DATA_SOURCE_ID={ds_id}",
        f"KB_SEARCH_TYPE=HYBRID",
        f"KB_NUMBER_OF_RESULTS=20",
        f"MARKDOWN_KB_PREFIX={prefix}",
        f"MARKDOWN_KB_COLLECTION={args.collection}",
        f"MARKDOWN_KB_COLLECTION_ARN={collection_arn}",
        f"MARKDOWN_KB_ROLE_ARN={role_arn}",
        f"MARKDOWN_KB_INGESTION_JOB_ID={ingestion_job_id}",
    ]
    env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    log("\n================ MARKDOWN KB READY ================")
    for line in env_lines:
        log(line)
    log(f"ENV_FILE={env_path}")
    return 0


def log(message: str) -> None:
    print(message, flush=True)


def ensure_bucket(s3: Any, bucket: str, region: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
        log(f"bucket exists: s3://{bucket}")
        return
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code not in {"404", "NoSuchBucket", "NotFound"}:
            raise

    params: dict[str, Any] = {"Bucket": bucket}
    if region != "us-east-1":
        params["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**params)
    log(f"created bucket: s3://{bucket}")


def ensure_kb_role(iam: Any, role_name: str, bucket: str, region: str, account: str) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Bedrock KB execution role for Markdown A/B test",
        )["Role"]
        log(f"created role: {role['Arn']}")
    except iam.exceptions.EntityAlreadyExistsException:
        role = iam.get_role(RoleName=role_name)["Role"]
        log(f"role exists: {role['Arn']}")

    embed_arn = f"arn:aws:bedrock:{region}::foundation-model/{EMBED_MODEL_ID}"
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["bedrock:InvokeModel"], "Resource": [embed_arn]},
            {
                "Effect": "Allow",
                "Action": ["aoss:APIAccessAll"],
                "Resource": [f"arn:aws:aoss:{region}:{account}:collection/*"],
            },
            {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": [f"arn:aws:s3:::{bucket}"]},
            {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": [f"arn:aws:s3:::{bucket}/*"]},
        ],
    }
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="AiAgentMarkdownKbPolicy",
        PolicyDocument=json.dumps(policy),
    )
    return role["Arn"]


def ensure_aoss(
    session: boto3.Session,
    aoss: Any,
    sts: Any,
    collection: str,
    role_arn: str,
    region: str,
    account: str,
) -> tuple[str, str]:
    caller_arn = sts.get_caller_identity()["Arn"]
    caller_principal = caller_arn
    if ":assumed-role/" in caller_arn:
        role_name = caller_arn.split(":assumed-role/", 1)[1].split("/", 1)[0]
        caller_principal = f"arn:aws:iam::{account}:role/{role_name}"

    enc = {"Rules": [{"ResourceType": "collection", "Resource": [f"collection/{collection}"]}], "AWSOwnedKey": True}
    create_policy(aoss, f"{collection}-enc", "encryption", enc)

    net = [
        {
            "Rules": [
                {"ResourceType": "collection", "Resource": [f"collection/{collection}"]},
                {"ResourceType": "dashboard", "Resource": [f"collection/{collection}"]},
            ],
            "AllowFromPublic": True,
        }
    ]
    create_policy(aoss, f"{collection}-net", "network", net)

    access = [
        {
            "Rules": [
                {"ResourceType": "index", "Resource": [f"index/{collection}/*"], "Permission": ["aoss:*"]},
                {"ResourceType": "collection", "Resource": [f"collection/{collection}"], "Permission": ["aoss:*"]},
            ],
            "Principal": [role_arn, caller_principal],
        }
    ]
    create_policy(aoss, f"{collection}-acc", "data", access)

    try:
        aoss.create_collection(name=collection, type="VECTORSEARCH")
        log("creating collection ...")
    except ClientError as e:
        log(f"collection create: {e.response['Error']['Code']}")

    for _ in range(90):
        details = aoss.batch_get_collection(names=[collection]).get("collectionDetails", [])
        if details and details[0]["status"] == "ACTIVE":
            log(f"collection ACTIVE: {details[0]['arn']}")
            return details[0]["arn"], details[0]["collectionEndpoint"]
        time.sleep(5)
    raise TimeoutError("collection not active")


def create_policy(aoss: Any, name: str, policy_type: str, policy: Any) -> None:
    try:
        if policy_type == "data":
            aoss.create_access_policy(name=name, type=policy_type, policy=json.dumps(policy))
        else:
            aoss.create_security_policy(name=name, type=policy_type, policy=json.dumps(policy))
        log(f"created {policy_type} policy: {name}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ConflictException":
            log(f"{policy_type} policy exists: {name}")
        else:
            raise


def ensure_index(session: boto3.Session, endpoint: str, region: str) -> None:
    host = endpoint.replace("https://", "")
    auth = AWSV4SignerAuth(session.get_credentials(), region, "aoss")
    client = OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=60,
    )
    body = {
        "settings": {"index": {"knn": True}},
        "mappings": {
            "properties": {
                VECTOR_FIELD: {
                    "type": "knn_vector",
                    "dimension": DIM,
                    "method": {"name": "hnsw", "engine": "faiss", "space_type": "l2"},
                },
                TEXT_FIELD: {"type": "text"},
                META_FIELD: {"type": "text", "index": False},
            }
        },
    }
    for attempt in range(18):
        try:
            if client.indices.exists(index=INDEX):
                log(f"index exists: {INDEX}")
                return
            client.indices.create(index=INDEX, body=body)
            log(f"created index: {INDEX}")
            return
        except Exception as e:
            log(f"index attempt {attempt + 1}: {type(e).__name__}: {str(e)[:160]}")
            time.sleep(10)
    raise RuntimeError("failed to create vector index")


def upload_markdown(s3: Any, bucket: str, prefix: str, markdown_dir: Path, md_files: list[Path]) -> None:
    for path in md_files:
        relative = path.relative_to(markdown_dir).as_posix()
        key = prefix + relative
        s3.upload_file(
            str(path),
            bucket,
            key,
            ExtraArgs={"ContentType": "text/markdown; charset=utf-8"},
        )
    log(f"uploaded {len(md_files)} Markdown files to s3://{bucket}/{prefix}")


def create_kb_and_ingest(
    bedrock_agent: Any,
    kb_name: str,
    data_source_name: str,
    role_arn: str,
    collection_arn: str,
    bucket: str,
    prefix: str,
    region: str,
) -> tuple[str, str, str]:
    embed_arn = f"arn:aws:bedrock:{region}::foundation-model/{EMBED_MODEL_ID}"
    kb = bedrock_agent.create_knowledge_base(
        name=kb_name,
        roleArn=role_arn,
        knowledgeBaseConfiguration={
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {"embeddingModelArn": embed_arn},
        },
        storageConfiguration={
            "type": "OPENSEARCH_SERVERLESS",
            "opensearchServerlessConfiguration": {
                "collectionArn": collection_arn,
                "vectorIndexName": INDEX,
                "fieldMapping": {
                    "vectorField": VECTOR_FIELD,
                    "textField": TEXT_FIELD,
                    "metadataField": META_FIELD,
                },
            },
        },
    )["knowledgeBase"]
    kb_id = kb["knowledgeBaseId"]
    log(f"created KB: {kb_id}")

    ds = bedrock_agent.create_data_source(
        knowledgeBaseId=kb_id,
        name=data_source_name,
        dataSourceConfiguration={
            "type": "S3",
            "s3Configuration": {
                "bucketArn": f"arn:aws:s3:::{bucket}",
                "inclusionPrefixes": [prefix],
            },
        },
    )["dataSource"]
    ds_id = ds["dataSourceId"]
    log(f"created data source: {ds_id}")

    job = bedrock_agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)["ingestionJob"]
    job_id = job["ingestionJobId"]
    for _ in range(120):
        current = bedrock_agent.get_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id,
            ingestionJobId=job_id,
        )["ingestionJob"]
        status = current["status"]
        stats = current.get("statistics", {})
        log(f"ingestion {job_id}: {status} {stats}")
        if status in {"COMPLETE", "FAILED", "STOPPED"}:
            if status != "COMPLETE":
                log(json.dumps(current, ensure_ascii=False, default=str, indent=2))
            break
        time.sleep(10)
    return kb_id, ds_id, job_id


if __name__ == "__main__":
    raise SystemExit(main())

