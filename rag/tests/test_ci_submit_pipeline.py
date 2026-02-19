"""Tests for queue gating and auto-promotion in scripts/ci_submit_pipeline.py."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "ci_submit_pipeline.py"
    )
    spec = importlib.util.spec_from_file_location(
        "ci_submit_pipeline_test_mod", script_path
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
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _seed_fde_artifacts(root: Path, role_slug: str, html_content: str) -> None:
    company_slug = "elevenlabs"
    resume_dir = root / "applications" / company_slug / "tailored_resumes"
    cover_dir = root / "applications" / company_slug / "cover_letters"
    jobs_dir = root / "applications" / company_slug / "jobs"
    resume_dir.mkdir(parents=True, exist_ok=True)
    cover_dir.mkdir(parents=True, exist_ok=True)
    jobs_dir.mkdir(parents=True, exist_ok=True)

    (resume_dir / f"2026-02-19_{company_slug}_{role_slug}.docx").write_bytes(b"docx")
    (resume_dir / f"2026-02-19_{company_slug}_{role_slug}.html").write_text(
        html_content, encoding="utf-8"
    )
    (cover_dir / f"2026-02-19_{company_slug}_{role_slug}.md").write_text(
        "Cover letter", encoding="utf-8"
    )
    (jobs_dir / f"2026-02-19_{company_slug}_{role_slug}_abcd1234.md").write_text(
        "Requirements: customer integrations, Python, APIs.",
        encoding="utf-8",
    )


def test_queue_only_promotes_high_fit_draft(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    role = "Forward Deployed Engineer - Software Engineer"
    role_slug = mod._slug(role)[:64]
    _seed_fde_artifacts(
        tmp_path,
        role_slug,
        (
            "Forward-Deployed AI/Software Engineer "
            "FORWARD-DEPLOYED COMPETENCIES "
            "customer-facing delivery "
            "integration engineering "
            "<strong>35%</strong>"
        ),
    )

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "ElevenLabs",
                "Role": role,
                "Location": "Remote",
                "Salary Range": "",
                "Status": "Draft",
                "Date Applied": "",
                "Follow Up Date": "",
                "Response": "",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "ai;integration",
                "Notes": "customer integrations",
                "Career Page URL": "https://jobs.ashbyhq.com/elevenlabs/abc123",
            }
        ],
    )

    rc = mod.run_pipeline(
        tracker_csv=tracker,
        report_path=report,
        dry_run=True,
        queue_only=True,
        max_jobs=5,
        fail_on_error=False,
        fit_threshold=70,
    )
    assert rc == 0

    with tracker.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Status"] == "ReadyToSubmit"

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["queue_promoted_count"] == 1
    assert payload["queue_demoted_count"] == 0


def test_queue_only_keeps_low_fit_draft(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    role = "Forward Deployed Engineer - Software Engineer"
    role_slug = mod._slug(role)[:64]
    _seed_fde_artifacts(
        tmp_path, role_slug, "Generic resume without required fit signals"
    )

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "ElevenLabs",
                "Role": role,
                "Location": "Remote",
                "Salary Range": "",
                "Status": "Draft",
                "Date Applied": "",
                "Follow Up Date": "",
                "Response": "",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "ai;integration",
                "Notes": "",
                "Career Page URL": "https://jobs.ashbyhq.com/elevenlabs/abc123",
            }
        ],
    )

    rc = mod.run_pipeline(
        tracker_csv=tracker,
        report_path=report,
        dry_run=True,
        queue_only=True,
        max_jobs=5,
        fail_on_error=False,
        fit_threshold=70,
    )
    assert rc == 0

    with tracker.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Status"] == "Draft"

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["queue_promoted_count"] == 0


def test_queue_only_demotes_ready_row_when_fit_drops(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    role = "Forward Deployed Engineer - Software Engineer"
    role_slug = mod._slug(role)[:64]
    _seed_fde_artifacts(tmp_path, role_slug, "Low-fit resume text")

    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "ElevenLabs",
                "Role": role,
                "Location": "Remote",
                "Salary Range": "",
                "Status": "ReadyToSubmit",
                "Date Applied": "",
                "Follow Up Date": "",
                "Response": "",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "ai;integration",
                "Notes": "",
                "Career Page URL": "https://jobs.ashbyhq.com/elevenlabs/abc123",
            }
        ],
    )

    rc = mod.run_pipeline(
        tracker_csv=tracker,
        report_path=report,
        dry_run=True,
        queue_only=True,
        max_jobs=5,
        fail_on_error=False,
        fit_threshold=70,
    )
    assert rc == 0

    with tracker.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Status"] == "Draft"

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["queue_demoted_count"] == 1
