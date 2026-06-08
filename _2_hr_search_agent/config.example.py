import os


AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

BEDROCK_EMBEDDING_MODEL_ID = os.getenv(
    "BEDROCK_EMBEDDING_MODEL_ID",
    "amazon.titan-embed-text-v2:0",
)
BEDROCK_CLAUDE_MODEL_ID = os.getenv(
    "BEDROCK_CLAUDE_MODEL_ID",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
)

S3_BUCKET = os.getenv("S3_BUCKET", "your-s3-bucket-name")
S3_DATA_PREFIX = os.getenv("S3_DATA_PREFIX", "data")
