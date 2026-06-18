from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SearchPlan:
    original_query: str
    intent: str
    primary_keyword: str
    aliases: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    negative_terms: list[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RawChunk:
    content: str
    source: str = ""
    kb_score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    query: str = ""


@dataclass
class ValidationResult:
    text_confidence: str
    confidence: str
    relevance_score: float
    matched_terms: list[str] = field(default_factory=list)
    llm_relevance_score: float | None = None
    llm_is_relevant: bool | None = None
    reason: str = ""


@dataclass
class ValidatedChunk:
    chunk: RawChunk
    validation: ValidationResult


@dataclass
class TimelineItem:
    date: str
    title: str
    department: str
    tags: list[str]
    summary: str
    details: list[str]
    source: str
    source_uri: str
    confidence: str
    relevance_score: float
    raw_score: float | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HistoryTreeResponse:
    results: list[TimelineItem]
    metadata: dict[str, Any]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [item.to_dict() for item in self.results],
            "metadata": self.metadata,
            "diagnostics": self.diagnostics,
        }
