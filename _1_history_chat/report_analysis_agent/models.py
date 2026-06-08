from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class InternalResult:
    content: str
    source: str = ""
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExternalResult:
    title: str
    summary: str
    url: str = ""
    date: str = ""
    source: str = ""
    relevance: str = ""
    relevance_reason: str = ""


@dataclass
class SearchTopic:
    topic: str
    query: str
    why_needed: str = ""
    expected_use: str = ""


@dataclass
class SearchPlan:
    needs_external_search: bool
    reason: str = ""
    intensity: str = "표준"
    topics: list[SearchTopic] = field(default_factory=list)


@dataclass
class SearchLog:
    topic: str
    query: str
    status: str
    message: str = ""
    result_count: int = 0
    error: str = ""


@dataclass
class StepResult:
    status: str
    message: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass
class AnalysisRun:
    keyword: str
    started_at: datetime
    internal_results: list[InternalResult] = field(default_factory=list)
    external_results: list[ExternalResult] = field(default_factory=list)
    search_plan: SearchPlan | None = None
    search_logs: list[SearchLog] = field(default_factory=list)
    scout: StepResult = field(default_factory=lambda: StepResult(status="pending"))
    ranger: StepResult = field(default_factory=lambda: StepResult(status="pending"))
    anchor: StepResult = field(default_factory=lambda: StepResult(status="pending"))
    markdown_report: str = ""
    output_dir: Path | None = None
    markdown_path: Path | None = None
    excel_path: Path | None = None


