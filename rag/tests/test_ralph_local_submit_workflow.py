"""Regression tests for the self-hosted local submit workflow."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ralph-local-submit.yml"


def test_self_hosted_workflow_targets_local_submit_lane():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "runs-on: [self-hosted, macOS, resume-ci]" in text
    assert "python3 scripts/run_local_submit_lane.py" in text
    assert "python3 -m venv .venv" in text
    assert "python -m pip install numpy" in text
    assert 'default: "chromium"' in text
    assert 'default: "true"' in text
    assert "Ralph Local Submit" in text
    assert '--browser-channel "${CI_SUBMIT_BROWSER_CHANNEL:-chromium}"' in text
    assert "continue-on-error: true" in text
    assert "add-paths:" in text
    assert "rag/data/applications.jsonl" not in text
    assert "applications/job_applications/*report*.json" in text
    assert "actions/setup-python" not in text
    assert "\npip install numpy" not in text
