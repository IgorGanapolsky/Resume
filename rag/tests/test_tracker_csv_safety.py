"""Regression tests for tracker CSV read/write safety."""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path


def _load_module(module_name: str, relative_path: str):
    script_path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def _write_malformed_tracker(path: Path) -> None:
    path.write_text("Company,Role\nAnthropic,Engineer,EXTRA\n", encoding="utf-8")


def test_ci_submit_pipeline_sanitizes_rows_with_extra_columns(tmp_path):
    mod = _load_module("ci_submit_pipeline_csv_safety", "scripts/ci_submit_pipeline.py")
    tracker = tmp_path / "tracker.csv"
    _write_malformed_tracker(tracker)

    fields, rows = mod._read_tracker(tracker)
    assert fields == ["Company", "Role"]
    assert len(rows) == 1
    assert None not in rows[0]
    assert rows[0] == {"Company": "Anthropic", "Role": "Engineer"}

    mod._write_tracker(tracker, fields, rows)
    with tracker.open(newline="", encoding="utf-8") as f:
        written_rows = list(csv.DictReader(f))
    assert written_rows == [{"Company": "Anthropic", "Role": "Engineer"}]


def test_prepare_ci_ready_artifacts_sanitizes_rows_with_extra_columns(tmp_path):
    mod = _load_module(
        "prepare_ci_ready_artifacts_csv_safety",
        "scripts/prepare_ci_ready_artifacts.py",
    )
    tracker = tmp_path / "tracker.csv"
    _write_malformed_tracker(tracker)

    fields, rows = mod._read_tracker(tracker)
    assert fields == ["Company", "Role"]
    assert len(rows) == 1
    assert None not in rows[0]
    assert rows[0] == {"Company": "Anthropic", "Role": "Engineer"}

    mod._write_tracker(tracker, fields, rows)
    with tracker.open(newline="", encoding="utf-8") as f:
        written_rows = list(csv.DictReader(f))
    assert written_rows == [{"Company": "Anthropic", "Role": "Engineer"}]
