# Amazon Bedrock AgentCore Runtime requires linux/arm64 container images.
FROM --platform=linux/arm64 python:3.12-slim

WORKDIR /srv

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code.
COPY server.py .
COPY app ./app

# AgentCore Runtime contract: MCP server on 0.0.0.0:8000, path /mcp.
ENV PORT=8000
EXPOSE 8000

# AWS region used by boto3 / Strands / Bedrock clients (override at deploy time).
ENV AWS_REGION=us-east-1

CMD ["python", "server.py"]
