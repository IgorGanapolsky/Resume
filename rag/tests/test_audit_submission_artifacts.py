"""Tests for tracker submission artifact auditing and normalization."""

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
        / "audit_submission_artifacts.py"
    )
    spec = importlib.util.spec_from_file_location(
        "audit_submission_artifacts_test_mod", script_path
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
        "Location",
        "Salary Range",
        "Status",
        "Date Applied",
        "Follow Up Date",
        "Response",
        "Interview Stage",
        "Days To Response",
        "Response Type",
        "Cover Letter Used",
        "What Worked",
        "Tags",
        "Notes",
        "Career Page URL",
        "Submitted Resume Path",
        "Submission Evidence Path",
        "Submission Verified At",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_run_audit_normalizes_unverified_applied_rows(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "Owner.com",
                "Role": "Software Engineer Mobile",
                "Location": "Remote",
                "Salary Range": "",
                "Status": "Applied",
                "Date Applied": "2026-02-02",
                "Follow Up Date": "2026-02-09",
                "Response": "",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "remote;mobile",
                "Notes": "React Native focus; pending review and submission",
                "Career Page URL": "https://jobs.example.com/owner/mobile",
                "Submitted Resume Path": "",
                "Submission Evidence Path": "",
                "Submission Verified At": "",
            }
        ],
    )

    rc = mod.run_audit(
        tracker_csv=tracker,
        report_path=report,
        write=True,
        fail_on_missing=False,
        normalize_unverified_applied=True,
    )
    assert rc == 0

    with tracker.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    row = rows[0]
    assert row["Status"] == "Draft"
    assert row["Date Applied"] == ""
    assert row["Follow Up Date"] == ""
    assert row["Submitted Resume Path"] == ""
    assert row["Submission Evidence Path"] == ""
    assert row["Submission Verified At"] == ""
    assert "Tracker normalized on" in row["Notes"]

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["applied_total_before"] == 1
    assert payload["applied_total_after"] == 0
    assert payload["normalized_count"] == 1
    assert payload["missing_count"] == 0


def test_run_audit_backfills_verified_artifacts_and_keeps_applied(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    company_slug = "anthropic"
    role = "Software Engineer"
    role_slug = mod._slug(role)
    resume_dir = tmp_path / "applications" / company_slug / "tailored_resumes"
    submission_dir = tmp_path / "applications" / company_slug / "submissions"
    resume_dir.mkdir(parents=True, exist_ok=True)
    submission_dir.mkdir(parents=True, exist_ok=True)
    resume_path = resume_dir / f"2026-02-18_{company_slug}_{role_slug}.docx"
    evidence_path = submission_dir / "2026-02-18_anthropic_submission.png"
    resume_path.write_bytes(b"docx")
    evidence_path.write_bytes(b"png")

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "Anthropic",
                "Role": role,
                "Location": "Remote",
                "Salary Range": "",
                "Status": "Applied",
                "Date Applied": "2026-02-18",
                "Follow Up Date": "2026-02-25",
                "Response": "",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "ai;infra",
                "Notes": (
                    "Confirmation screenshot: "
                    "applications/anthropic/submissions/2026-02-18_anthropic_submission.png"
                ),
                "Career Page URL": "https://job-boards.greenhouse.io/anthropic/jobs/123",
                "Submitted Resume Path": "",
                "Submission Evidence Path": "",
                "Submission Verified At": "",
            }
        ],
    )

    rc = mod.run_audit(
        tracker_csv=tracker,
        report_path=report,
        write=True,
        fail_on_missing=False,
        normalize_unverified_applied=True,
    )
    assert rc == 0

    with tracker.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    row = rows[0]
    assert row["Status"] == "Applied"
    assert row["Submitted Resume Path"] == str(resume_path.relative_to(tmp_path))
    assert row["Submission Evidence Path"] == str(evidence_path.relative_to(tmp_path))
    assert row["Submission Verified At"]

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["applied_total_before"] == 1
    assert payload["applied_total_after"] == 1
    assert payload["backfilled_count"] == 1
    assert payload["normalized_count"] == 0
    assert payload["missing_count"] == 0


def test_run_audit_keeps_human_verified_claims_for_manual_follow_up(
    tmp_path, monkeypatch
):
    mod = _load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "Anthropic",
                "Role": "Software Engineer",
                "Location": "Remote",
                "Salary Range": "",
                "Status": "Applied",
                "Date Applied": "2026-02-18",
                "Follow Up Date": "2026-02-25",
                "Response": "",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "ai;infra",
                "Notes": (
                    "Submitted 2026-02-18 via Greenhouse. "
                    "Confirmation screenshot: applications/anthropic/submissions/2026-02-18_missing.png"
                ),
                "Career Page URL": "https://job-boards.greenhouse.io/anthropic/jobs/123",
                "Submitted Resume Path": "applications/anthropic/tailored_resumes/2026-02-18_anthropic_software-engineer.docx",
                "Submission Evidence Path": "applications/anthropic/submissions/2026-02-18_missing.png",
                "Submission Verified At": "",
            }
        ],
    )

    rc = mod.run_audit(
        tracker_csv=tracker,
        report_path=report,
        write=True,
        fail_on_missing=False,
        normalize_unverified_applied=True,
    )
    assert rc == 0

    with tracker.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    row = rows[0]
    assert row["Status"] == "Applied"
    assert row["Date Applied"] == "2026-02-18"
    assert "Tracker normalized on" not in row["Notes"]

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["normalized_count"] == 0
    assert payload["missing_count"] == 1
