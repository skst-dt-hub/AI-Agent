from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import boto3

from .config import AWS_REGION, DATA_SOURCE_ID, KNOWLEDGE_BASE_ID, S3_BUCKET, S3_PREFIX, is_kb_configured


ProgressCallback = Callable[[str, float | None], None]
TERMINAL_STATUSES = {"COMPLETE", "FAILED", "STOPPED"}


def upload_fileobj_to_s3(file_obj: BinaryIO, filename: str) -> str:
    if not S3_BUCKET:
        raise RuntimeError("S3_BUCKET 설정이 비어 있습니다.")

    safe_name = Path(filename).name
    key = f"{S3_PREFIX.rstrip('/')}/{safe_name}" if S3_PREFIX else safe_name
    s3 = boto3.client("s3", region_name=AWS_REGION)
    s3.upload_fileobj(file_obj, S3_BUCKET, key)
    return f"s3://{S3_BUCKET}/{key}"


def upload_files_to_s3(files: list[tuple[BinaryIO, str]], callback: ProgressCallback | None = None) -> list[str]:
    uploaded = []
    total = len(files)
    for index, (file_obj, filename) in enumerate(files, start=1):
        s3_uri = upload_fileobj_to_s3(file_obj, filename)
        uploaded.append(s3_uri)
        if callback:
            callback(f"S3 업로드 완료 ({index}/{total}): {s3_uri}", 0.05 + 0.45 * (index / total))
    return uploaded


def start_ingestion_job() -> str:
    if not is_kb_configured():
        raise RuntimeError("KNOWLEDGE_BASE_ID, DATA_SOURCE_ID, S3_BUCKET 설정이 필요합니다.")

    client = boto3.client("bedrock-agent", region_name=AWS_REGION)
    response = client.start_ingestion_job(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        dataSourceId=DATA_SOURCE_ID,
    )
    return response["ingestionJob"]["ingestionJobId"]


def poll_ingestion_job(
    ingestion_job_id: str,
    callback: ProgressCallback | None = None,
    interval_seconds: int = 3,
    timeout_seconds: int = 900,
) -> dict:
    client = boto3.client("bedrock-agent", region_name=AWS_REGION)
    started = time.monotonic()

    while True:
        response = client.get_ingestion_job(
            knowledgeBaseId=KNOWLEDGE_BASE_ID,
            dataSourceId=DATA_SOURCE_ID,
            ingestionJobId=ingestion_job_id,
        )
        job = response.get("ingestionJob", {})
        status = job.get("status", "UNKNOWN")
        progress = _estimate_progress(status, job.get("statistics", {}))

        if callback:
            callback(status, progress)

        if status in TERMINAL_STATUSES:
            return response

        if time.monotonic() - started > timeout_seconds:
            raise TimeoutError(f"KB Sync timeout: {timeout_seconds}s")

        time.sleep(interval_seconds)


def upload_many_and_sync(files: list[tuple[BinaryIO, str]], callback: ProgressCallback | None = None) -> dict:
    if not files:
        raise RuntimeError("업로드할 파일이 없습니다.")

    uploaded_uris = upload_files_to_s3(files, callback=callback)
    job_id = start_ingestion_job()
    if callback:
        callback(f"KB Sync 시작: {job_id}", 0.55)

    response = poll_ingestion_job(job_id, callback=callback)
    response["uploadedS3Uris"] = uploaded_uris
    return response


def list_s3_files(max_keys: int = 1000) -> list[dict]:
    if not S3_BUCKET:
        return []

    s3 = boto3.client("s3", region_name=AWS_REGION)
    paginator = s3.get_paginator("list_objects_v2")
    items = []
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_PREFIX or "", PaginationConfig={"MaxItems": max_keys}):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            items.append(
                {
                    "file_name": Path(key).name,
                    "size_kb": round(obj.get("Size", 0) / 1024, 1),
                    "last_modified": obj.get("LastModified"),
                    "s3_key": key,
                }
            )
    return sorted(items, key=lambda item: item["last_modified"] or "", reverse=True)


def delete_s3_files(keys: list[str]) -> None:
    if not keys:
        return
    if not S3_BUCKET:
        raise RuntimeError("S3_BUCKET 설정이 비어 있습니다.")

    s3 = boto3.client("s3", region_name=AWS_REGION)
    s3.delete_objects(
        Bucket=S3_BUCKET,
        Delete={"Objects": [{"Key": key} for key in keys]},
    )


def list_ingestion_jobs(max_results: int = 10) -> list[dict]:
    if not is_kb_configured():
        return []

    client = boto3.client("bedrock-agent", region_name=AWS_REGION)
    response = client.list_ingestion_jobs(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        dataSourceId=DATA_SOURCE_ID,
        maxResults=max_results,
    )
    jobs = response.get("ingestionJobSummaries", [])
    return sorted(jobs, key=lambda job: job.get("updatedAt") or job.get("startedAt") or "", reverse=True)


def _estimate_progress(status: str, statistics: dict) -> float | None:
    if status in {"COMPLETE", "FAILED", "STOPPED"}:
        return 1.0
    if status in {"STARTING", "IN_PROGRESS"}:
        scanned = statistics.get("numberOfDocumentsScanned") or 0
        indexed = statistics.get("numberOfNewDocumentsIndexed") or 0
        failed = statistics.get("numberOfDocumentsFailed") or 0
        total_known = scanned + failed
        if total_known > 0:
            return min(0.95, max(0.35, indexed / total_known))
        return 0.5
    return None
