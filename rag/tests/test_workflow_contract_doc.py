"""Tests for the repo-level agent workflow contract."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_MD = ROOT / "workflow.md"


def test_workflow_contract_exists_and_has_required_sections():
    text = WORKFLOW_MD.read_text(encoding="utf-8")

    required_sections = [
        "# Resume Agent Workflow",
        "## Scope",
        "## Prohibited Changes",
        "## Setup",
        "## Live Submit Auth",
        "## Test Commands",
        "## Proof of Work",
        "## Task Intake",
        "## Done Criteria",
        "## PR Requirements",
    ]
    for section in required_sections:
        assert section in text


def test_workflow_contract_references_real_commands_and_templates():
    text = WORKFLOW_MD.read_text(encoding="utf-8")

    required_commands = {
        "python3 scripts/check_calendar_guardrails.py": ROOT
        / "scripts"
        / "check_calendar_guardrails.py",
        "python3 scripts/scrub_job_captures.py --dry-run": ROOT
        / "scripts"
        / "scrub_job_captures.py",
        "python3 -m pytest rag/tests -v": ROOT / "rag" / "tests",
        "python3 rag/cli.py build": ROOT / "rag" / "cli.py",
        "python3 scripts/sync_quarantined_issues.py --report applications/job_applications/quarantine_issue_sync_report.json": ROOT
        / "scripts"
        / "sync_quarantined_issues.py",
        "python3 scripts/capture_submit_auth.py": ROOT
        / "scripts"
        / "capture_submit_auth.py",
    }
    for command, path in required_commands.items():
        assert command in text
        assert path.exists()

    assert (ROOT / ".github" / "ISSUE_TEMPLATE" / "agent-ready.yml").exists()
    assert (
        ROOT / ".github" / "ISSUE_TEMPLATE" / "quarantined-application.yml"
    ).exists()
