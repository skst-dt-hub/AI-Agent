"""Check which Strands web search tools are available in this environment."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


TOOL_CANDIDATES = [
    ("strands_tools", "web_search"),
    ("strands_tools.tavily", "tavily_search"),
    ("strands_tools.exa", "exa_search"),
]


def describe_tool(name: str, value: Any) -> str:
    module = getattr(value, "__module__", "(unknown module)")
    qualname = getattr(value, "__qualname__", getattr(value, "__name__", type(value).__name__))
    return f"{name}: available ({module}.{qualname})"


def main() -> None:
    print("Checking strands_tools imports...")
    available = []
    for module_name, name in TOOL_CANDIDATES:
        try:
            tools_module = importlib.import_module(module_name)
        except Exception as exc:
            print(f"{module_name}.{name}: import failed ({type(exc).__name__}: {exc})")
            continue
        tool = getattr(tools_module, name, None)
        if tool is None:
            print(f"{module_name}.{name}: not found")
            continue
        print(describe_tool(f"{module_name}.{name}", tool))
        available.append((module_name, name))

    print()
    if not available:
        print("No known web search tool was found in strands_tools.")
        return

    if os.environ.get("RUN_STRANDS_SEARCH_TEST") != "1":
        print("Import check complete.")
        print("To run a live search test, set RUN_STRANDS_SEARCH_TEST=1 and rerun this script.")
        return

    print("Running live search test...")
    from strands import Agent

    selected_module_name, selected_name = available[0]
    selected_module = importlib.import_module(selected_module_name)
    selected_tool = getattr(selected_module, selected_name)
    agent = Agent(
        tools=[selected_tool],
        callback_handler=None,
    )
    result = agent(f"Use {selected_name} to search the web for today's AWS Bedrock Knowledge Bases news. Summarize in 3 bullets.")
    print(result)


if __name__ == "__main__":
    main()
