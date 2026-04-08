"""Regression tests for the self-hosted local submit workflow."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ralph-local-submit.yml"


def test_self_hosted_workflow_targets_local_submit_lane():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "runs-on: [self-hosted, macOS, resume-ci]" in text  # nosec B101
    assert "ANCHOR_BROWSER_API_KEY: ${{ secrets.ANCHOR_BROWSER_API_KEY }}" in text  # nosec B101
    assert (
        "ANCHOR_BROWSER_PROXY_ACTIVE: ${{ vars.ANCHOR_BROWSER_PROXY_ACTIVE }}" in text
    )  # nosec B101
    assert "python3 scripts/run_local_submit_lane.py" in text  # nosec B101
    assert "python3 -m venv .venv" in text  # nosec B101
    assert "python -m pip install numpy" in text  # nosec B101
    assert "default: auto" in text  # nosec B101
    assert 'default: "chromium"' in text  # nosec B101
    assert 'default: "false"' in text  # nosec B101
    assert "Ralph Local Submit" in text  # nosec B101
    assert 'if [ -z "$BROWSER_BACKEND" ]; then BROWSER_BACKEND=auto; fi' in text  # nosec B101
    assert '--browser-backend "$BROWSER_BACKEND"' in text  # nosec B101
    assert '--browser-channel "${CI_SUBMIT_BROWSER_CHANNEL:-chromium}"' in text  # nosec B101
    assert "continue-on-error: true" in text  # nosec B101
    assert "applications/job_applications/*report*.json" in text  # nosec B101
    assert "actions/setup-python" not in text  # nosec B101
    assert "\npip install numpy" not in text  # nosec B101
