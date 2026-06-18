from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
MODEL_ID = os.getenv("MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_PREFIX = os.getenv("S3_PREFIX", "report-analysis-agent/uploads/")

KNOWLEDGE_BASE_ID = os.getenv("KNOWLEDGE_BASE_ID", "")
DATA_SOURCE_ID = os.getenv("DATA_SOURCE_ID", "")

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(BASE_DIR / "output")))

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
EXA_API_KEY = os.getenv("EXA_API_KEY", "")

KB_SEARCH_TYPE = os.getenv("KB_SEARCH_TYPE", "HYBRID").upper()
KB_NUMBER_OF_RESULTS = int(os.getenv("KB_NUMBER_OF_RESULTS", "20"))


def is_kb_configured() -> bool:
    return bool(KNOWLEDGE_BASE_ID and DATA_SOURCE_ID and S3_BUCKET)
