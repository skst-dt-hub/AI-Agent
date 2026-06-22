#!/usr/bin/env python3
"""Invoke the deployed AI-Agent MCP server on Bedrock AgentCore Runtime using
IAM (SigV4) authentication.

The AgentCore data plane endpoint for an MCP runtime is:

    https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{ENCODED_ARN}/invocations?qualifier=DEFAULT

ENCODED_ARN is the agent runtime ARN with ':' -> '%3A' and '/' -> '%2F'.

Auth options:
- IAM / SigV4 (default inbound auth): the caller signs every request with AWS
  credentials and must have bedrock-agentcore:InvokeAgentRuntime permission.
- OAuth / JWT (Bearer): pass {"authorization": "Bearer <token>"} headers instead
  (configure the runtime with an authorizer at create time).

This script demonstrates the IAM/SigV4 path: it wraps an httpx.Auth that
SigV4-signs each streamable-HTTP request to the "bedrock-agentcore" service.

Usage:
    export AGENT_ARN="arn:aws:bedrock-agentcore:us-east-1:...:runtime/ai_agent_mcp-xxxx"
    export AWS_REGION=us-east-1
    python scripts/invoke_remote.py                 # list tools
    python scripts/invoke_remote.py hr_understand_query '{"query":"..."}'
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from urllib.parse import quote

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session as BotoSession
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SERVICE = "bedrock-agentcore"


class SigV4HTTPXAuth(httpx.Auth):
    """httpx auth flow that SigV4-signs each request for AgentCore data plane."""

    requires_request_body = True

    def __init__(self, region: str):
        creds = BotoSession().get_credentials()
        if creds is None:
            raise RuntimeError("No AWS credentials found for SigV4 signing.")
        self._creds = creds
        self._signer = SigV4Auth(creds, SERVICE, region)

    def auth_flow(self, request: httpx.Request):
        body = request.content or b""
        aws_req = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=body,
            headers={
                # Only sign content-type; let SigV4 add the rest. Host is derived.
                "content-type": request.headers.get("content-type", "application/json"),
            },
        )
        self._signer.add_auth(aws_req)
        for key, value in aws_req.headers.items():
            request.headers[key] = value
        yield request


def build_url(agent_arn: str, region: str) -> str:
    encoded = quote(agent_arn, safe="")
    return (
        f"https://{SERVICE}.{region}.amazonaws.com/runtimes/{encoded}"
        f"/invocations?qualifier=DEFAULT"
    )


async def main() -> None:
    agent_arn = os.environ["AGENT_ARN"]
    region = os.getenv("AWS_REGION", "us-east-1")
    url = build_url(agent_arn, region)
    auth = SigV4HTTPXAuth(region)

    tool_name = sys.argv[1] if len(sys.argv) > 1 else None
    tool_args = parse_tool_args(sys.argv[2:]) if len(sys.argv) > 2 else {}

    print(f"Endpoint: {url}\nAuth: IAM SigV4 ({SERVICE})\n")

    async with streamablehttp_client(url, auth=auth, timeout=120, terminate_on_close=False) as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"Tools ({len(tools.tools)}):")
            for t in tools.tools:
                print(f"  - {t.name}")

            if tool_name:
                print(f"\nCalling {tool_name}({tool_args}) ...")
                result = await session.call_tool(tool_name, tool_args)
                for block in result.content:
                    text = getattr(block, "text", None)
                    print(text if text is not None else block)


def parse_tool_args(argv: list[str]) -> dict:
    raw = " ".join(argv).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        fallback = parse_powershell_object(raw)
        if fallback is not None:
            return fallback
        raise SystemExit(
            "Invalid JSON tool arguments.\n"
            f"Received: {raw}\n"
            "PowerShell example:\n"
            "$argsJson = @{ keyword = '몰리브덴'; output_format = 'structured' } | ConvertTo-Json -Compress\n"
            'python scripts/invoke_remote.py report_retrieve_internal "$argsJson"'
        ) from exc


def parse_powershell_object(raw: str) -> dict | None:
    """Accept PowerShell-stripped input like { keyword: 몰리브덴, output_format: structured }."""
    text = raw.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    body = text[1:-1].strip()
    if not body:
        return {}
    parsed = {}
    for part in body.split(","):
        if ":" not in part:
            return None
        key, value = part.split(":", 1)
        key = key.strip().strip("\"'")
        value = value.strip().strip("\"'")
        if not key:
            return None
        if value.lower() == "true":
            parsed[key] = True
        elif value.lower() == "false":
            parsed[key] = False
        elif value.isdigit():
            parsed[key] = int(value)
        else:
            parsed[key] = value
    return parsed


if __name__ == "__main__":
    asyncio.run(main())
