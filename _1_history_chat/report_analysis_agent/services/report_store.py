from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import OUTPUT_DIR


def list_report_runs() -> list[dict[str, Any]]:
    output_dir = Path(OUTPUT_DIR)
    if not output_dir.exists():
        return []

    runs = []
    for metadata_path in output_dir.glob("*/*/run_metadata.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = {}

        run_dir = metadata_path.parent
        runs.append(
            {
                "label": f"{metadata.get('started_at', run_dir.name)} | {metadata.get('keyword', run_dir.parent.name)}",
                "keyword": metadata.get("keyword", run_dir.parent.name),
                "started_at": metadata.get("started_at", run_dir.name),
                "run_dir": run_dir,
                "markdown_path": run_dir / "report.md",
                "excel_path": run_dir / "report.xlsx",
                "metadata": metadata,
            }
        )

    return sorted(runs, key=lambda item: item["started_at"], reverse=True)
