"""Tests for quarantined application issue sync."""

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
        / "sync_quarantined_issues.py"
    )
    spec = importlib.util.spec_from_file_location(
        "sync_quarantined_issues_test_mod", script_path
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
        "Remote Policy",
        "Remote Likelihood Score",
        "Remote Evidence",
        "Submission Lane",
        "Submitted Resume Path",
        "Submission Evidence Path",
        "Submission Verified At",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_load_quarantined_applications_filters_tracker_and_collects_evidence(tmp_path):
    mod = _load_module()
    tracker = tmp_path / "application_tracker.csv"
    _write_tracker(
        tracker,
        [
            {
                "Company": "Baseten",
                "Role": "Senior Software Engineer - Infrastructure",
                "Location": "Remote",
                "Salary Range": "",
                "Status": "Quarantined",
                "Date Applied": "",
                "Follow Up Date": "",
                "Response": "Submit blocked",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "ai;infra",
                "Notes": (
                    "Auto-quarantined after submit blocker. Evidence: "
                    "applications/baseten/submissions/2026-02-18_baseten_application_submitted.png"
                ),
                "Career Page URL": "https://jobs.ashbyhq.com/baseten/abc123",
                "Remote Policy": "remote",
                "Remote Likelihood Score": "88",
                "Remote Evidence": "remote_keyword",
                "Submission Lane": "manual:quarantined",
                "Submitted Resume Path": "",
                "Submission Evidence Path": "",
                "Submission Verified At": "",
            },
            {
                "Company": "Owner.com",
                "Role": "Software Engineer Mobile",
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
                "Tags": "ai;mobile",
                "Notes": "",
                "Career Page URL": "https://jobs.lever.co/owner/abc123",
                "Remote Policy": "remote",
                "Remote Likelihood Score": "88",
                "Remote Evidence": "remote_keyword",
                "Submission Lane": "ci_auto:lever",
                "Submitted Resume Path": "",
                "Submission Evidence Path": "",
                "Submission Verified At": "",
            },
        ],
    )

    apps = mod.load_quarantined_applications(tracker)
    assert len(apps) == 1
    assert apps[0].company == "Baseten"
    assert apps[0].submission_lane == "manual:quarantined"
    assert apps[0].evidence_paths == [
        "applications/baseten/submissions/2026-02-18_baseten_application_submitted.png"
    ]


def test_build_sync_plan_handles_create_update_reopen_close_and_noop():
    mod = _load_module()
    app = mod.QuarantinedApplication(
        app_id="baseten__infra__1234567890",
        company="Baseten",
        role="Senior Software Engineer - Infrastructure",
        url="https://jobs.ashbyhq.com/baseten/abc123",
        notes="Auto-quarantined after submit blocker",
        response="Submit blocked",
        interview_stage="Initial",
        submission_lane="manual:quarantined",
        remote_policy="remote",
        remote_score="88",
        evidence_paths=["applications/baseten/submissions/confirm.png"],
    )
    title = mod.build_issue_title(app)
    body = mod.build_issue_body(app)

    create_plan = mod.build_sync_plan([app], {}, close_resolved=True)
    assert [item.action for item in create_plan] == ["create"]

    update_plan = mod.build_sync_plan(
        [app],
        {
            app.app_id: mod.ExistingIssue(
                number=12,
                title=title,
                body=body + "\nextra",
                state="OPEN",
                url="https://example.com/12",
                app_id=app.app_id,
            )
        },
        close_resolved=False,
    )
    assert update_plan[0].action == "update"
    assert update_plan[0].issue_number == 12

    reopen_plan = mod.build_sync_plan(
        [app],
        {
            app.app_id: mod.ExistingIssue(
                number=22,
                title=title,
                body=body,
                state="CLOSED",
                url="https://example.com/22",
                app_id=app.app_id,
            )
        },
        close_resolved=False,
    )
    assert reopen_plan[0].action == "reopen"

    noop_plan = mod.build_sync_plan(
        [app],
        {
            app.app_id: mod.ExistingIssue(
                number=24,
                title=title,
                body=body,
                state="OPEN",
                url="https://example.com/24",
                app_id=app.app_id,
            )
        },
        close_resolved=False,
    )
    assert noop_plan[0].action == "noop"

    close_plan = mod.build_sync_plan(
        [],
        {
            app.app_id: mod.ExistingIssue(
                number=25,
                title=title,
                body=body,
                state="OPEN",
                url="https://example.com/25",
                app_id=app.app_id,
            )
        },
        close_resolved=True,
    )
    assert close_plan[0].action == "close"


def test_run_sync_writes_json_report_without_applying(tmp_path):
    mod = _load_module()
    tracker = tmp_path / "application_tracker.csv"
    report = tmp_path / "report.json"
    _write_tracker(
        tracker,
        [
            {
                "Company": "Baseten",
                "Role": "Senior Software Engineer - Infrastructure",
                "Location": "Remote",
                "Salary Range": "",
                "Status": "Quarantined",
                "Date Applied": "",
                "Follow Up Date": "",
                "Response": "Submit blocked",
                "Interview Stage": "Initial",
                "Days To Response": "",
                "Response Type": "",
                "Cover Letter Used": "",
                "What Worked": "",
                "Tags": "ai;infra",
                "Notes": "Auto-quarantined after submit blocker",
                "Career Page URL": "https://jobs.ashbyhq.com/baseten/abc123",
                "Remote Policy": "remote",
                "Remote Likelihood Score": "88",
                "Remote Evidence": "remote_keyword",
                "Submission Lane": "manual:quarantined",
                "Submitted Resume Path": "",
                "Submission Evidence Path": "",
                "Submission Verified At": "",
            }
        ],
    )

    rc = mod.run_sync(
        tracker_csv=tracker,
        report_path=report,
        repo="IgorGanapolsky/Resume",
        apply=False,
        close_resolved=True,
        existing_issues={},
    )
    assert rc == 0

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["quarantined_count"] == 1
    assert payload["actions"][0]["action"] == "create"
    assert payload["quarantined_apps"][0]["company"] == "Baseten"
