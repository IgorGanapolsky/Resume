#!/usr/bin/env python3
"""Generate Ralph Loop's machine-readable RLHF learning report."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_learning_helpers():
    try:
        from rag.learning import build_learning_report as build_report
        from rag.learning import load_arms as load_learning_arms
        from rag.learning import load_tracker_rows as load_rows

        return build_report, load_learning_arms, load_rows
    except ModuleNotFoundError as exc:
        if exc.name not in {"rag", "rag.learning"}:
            raise

    learning_path = _ROOT / "rag" / "learning.py"
    spec = importlib.util.spec_from_file_location("_resume_rag_learning", learning_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load learning helpers from {learning_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module.build_learning_report, module.load_arms, module.load_tracker_rows


build_learning_report, load_arms, load_tracker_rows = _load_learning_helpers()


ROOT = _ROOT
DEFAULT_TRACKER = ROOT / "applications" / "job_applications" / "application_tracker.csv"
DEFAULT_ARMS = ROOT / "rag" / "data" / "arms.json"
DEFAULT_REPORT = ROOT / "applications" / "job_applications" / "rag_learning_report.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracker", default=str(DEFAULT_TRACKER), help="Tracker CSV path")
    parser.add_argument("--arms", default=str(DEFAULT_ARMS), help="RLHF arms.json path")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="Output JSON report path")
    parser.add_argument("--top-k", type=int, default=10, help="How many top rows/arms to include")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    tracker_path = Path(args.tracker)
    arms_path = Path(args.arms)
    report_path = Path(args.report)

    tracker_rows = load_tracker_rows(tracker_path)
    arms = load_arms(arms_path)
    report = build_learning_report(
        tracker_rows,
        arms,
        top_k=max(1, int(args.top_k)),
        tracker_csv=str(tracker_path),
        arms_json=str(arms_path),
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    top_ready = report.get("ready_ranked", [])
    lead = top_ready[0] if top_ready else {}
    lead_text = (
        f"{lead.get('company', '')} / {lead.get('role', '')} "
        f"(score={lead.get('adjusted_score', '')})"
        if lead
        else "none"
    )
    print(
        "Generated learning report: "
        f"rows={report.get('total_tracker_rows', 0)} "
        f"arms={report.get('total_arms', 0)} "
        f"top_ready={lead_text} "
        f"report={report_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
