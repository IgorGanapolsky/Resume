"""Regression tests for the self-hosted local submit workflow."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ralph-local-submit.yml"


def test_self_hosted_workflow_targets_local_submit_lane():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "runs-on: [self-hosted, macOS, resume-ci]" in text
    assert "python3 scripts/run_local_submit_lane.py" in text
    assert "Ralph Local Submit" in text
    assert "applications/job_applications/*report*.json" in text
    assert "actions/setup-python" not in text
