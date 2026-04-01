"""Tests for candidate prioritization in scripts/prepare_ci_ready_artifacts.py."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "prepare_ci_ready_artifacts.py"
    )
    spec = importlib.util.spec_from_file_location(
        "prepare_ci_ready_artifacts_test_mod", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def _write_tracker(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "Company",
        "Role",
        "Status",
        "Career Page URL",
        "Submission Lane",
        "Remote Policy",
        "Remote Likelihood Score",
        "Remote Evidence",
        "Notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_run_prepare_spends_budget_on_candidates_not_early_unsupported_rows(
    tmp_path, monkeypatch
):
    mod = _load_module()

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    rows = []
    for idx in range(15):
        rows.append(
            {
                "Company": f"Unsupported {idx}",
                "Role": "Software Engineer",
                "Status": "Draft",
                "Career Page URL": f"https://remoteok.com/remote-jobs/{idx}",
                "Submission Lane": "manual",
                "Remote Policy": "remote",
                "Remote Likelihood Score": "90",
                "Remote Evidence": "remote_feed_source",
                "Notes": "",
            }
        )
    rows.append(
        {
            "Company": "Inferact",
            "Role": "Member of Technical Staff",
            "Status": "Draft",
            "Career Page URL": "https://jobs.ashbyhq.com/inferact/abc123",
            "Submission Lane": "ci_auto:ashby",
            "Remote Policy": "remote",
            "Remote Likelihood Score": "85",
            "Remote Evidence": "remote_keyword",
            "Notes": "",
        }
    )
    _write_tracker(tracker, rows)

    class FakeCiModule:
        TRACKER_REMOTE_FIELDS = (
            "Remote Policy",
            "Remote Likelihood Score",
            "Remote Evidence",
            "Submission Lane",
        )

        @staticmethod
        def _ensure_tracker_fields(fields, _rows, _extras):
            return list(fields)

        @staticmethod
        def _is_draft_status(status):
            return status == "Draft"

        @staticmethod
        def _is_ready_status(status):
            return status == "ReadyToSubmit"

        class AshbyAdapter:
            pass

        class GreenhouseAdapter:
            pass

        class LeverAdapter:
            pass

        class OracleAdapter:
            pass

    class FakeRalphModule:
        BASE_RESUME = str(tmp_path / "base_resume.html")

    def fake_load_script_module(name, _path):
        if name == "ci_submit_pipeline_prepare_mod":
            return FakeCiModule
        if name == "ralph_loop_prepare_mod":
            return FakeRalphModule
        raise AssertionError(f"unexpected module request: {name}")

    def fake_prepare_row(**kwargs):
        row = kwargs["row"]
        if row["Company"] == "Inferact":
            return {
                "company": row["Company"],
                "role": row["Role"],
                "prepared": True,
                "candidate": True,
                "assessment_before": {"eligible": False},
                "assessment_after": {"eligible": True},
                "artifact_updates": ["resume_docx:/tmp/inferact.docx"],
                "skip_reason": "",
            }
        return {
            "company": row["Company"],
            "role": row["Role"],
            "prepared": False,
            "candidate": False,
            "assessment_before": {},
            "assessment_after": {},
            "artifact_updates": [],
            "skip_reason": "unsupported_site_for_ci_submit",
        }

    monkeypatch.setattr(mod, "_load_script_module", fake_load_script_module)
    monkeypatch.setattr(mod, "_prepare_row", fake_prepare_row)

    rc = mod.run_prepare(
        tracker_csv=tracker,
        report_path=report,
        max_jobs=1,
        fit_threshold=70,
        remote_min_score=50,
    )
    assert rc == 0

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["processed_rows"] == 1
    assert payload["inspected_rows"] == 16
    assert payload["artifact_updates"] == 1
    assert payload["became_gate_eligible"] == 1
    assert payload["results"][-1]["company"] == "Inferact"
