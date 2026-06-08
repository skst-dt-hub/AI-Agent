import json
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

from strands.agent.agent_result import AgentResult
from strands.telemetry.metrics import EventLoopMetrics


PipelineFunction = Callable[[Any], dict[str, Any]]


def content_blocks_to_text(prompt: Any) -> str:
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, list):
        parts = []
        for item in prompt:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            elif hasattr(item, "get"):
                parts.append(str(item.get("text", "")))
            elif hasattr(item, "text"):
                parts.append(str(item.text))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(prompt or "")


def extract_last_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    candidates = []

    for match in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            candidates.append(obj)

    if not candidates:
        raise ValueError(f"입력에서 JSON 객체를 찾지 못했습니다: {text}")

    for obj in reversed(candidates):
        if "original_query" in obj or "candidates" in obj or "hard_conditions" in obj:
            return obj
    return candidates[-1]


def make_agent_result(payload: dict[str, Any]) -> AgentResult:
    return AgentResult(
        stop_reason="end_turn",
        message={
            "role": "assistant",
            "content": [
                {
                    "text": json.dumps(payload, ensure_ascii=False),
                }
            ],
        },
        metrics=EventLoopMetrics(),
        state={},
    )


class FunctionGraphNode:
    """Strands GraphBuilder가 실행할 수 있도록 Python 함수를 AgentBase 형태로 맞춘 노드."""

    def __init__(
        self,
        name: str,
        function: PipelineFunction,
        input_builder: Callable[[Any], Any],
    ) -> None:
        self.name = name
        self.function = function
        self.input_builder = input_builder

    def __call__(self, prompt=None, **kwargs: Any) -> AgentResult:
        function_input = self.input_builder(prompt)
        payload = self.function(function_input)
        return make_agent_result(payload)

    async def invoke_async(self, prompt=None, **kwargs: Any) -> AgentResult:
        return self(prompt, **kwargs)

    async def stream_async(self, prompt=None, **kwargs: Any) -> AsyncIterator[dict[str, AgentResult]]:
        yield {"result": await self.invoke_async(prompt, **kwargs)}


def query_input_builder(prompt: Any) -> str:
    return content_blocks_to_text(prompt).strip()


def previous_json_input_builder(prompt: Any) -> dict[str, Any]:
    return extract_last_json_object(content_blocks_to_text(prompt))
