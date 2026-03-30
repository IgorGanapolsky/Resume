"""Regression tests for Ralph Loop workflow gating."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ralph-loop.yml"


def test_live_submit_requires_profile_and_answers_but_not_auth():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert (
        "GitHub-hosted Ralph Loop runs are dry-run only. Use python3 "
        "scripts/run_local_submit_lane.py for live submissions."
    ) in text
    assert (
        "if ! python3 scripts/ci_submit_pipeline.py \\" in text
        and "--validate-secrets-only \\" in text
    )
    assert (
        '[ -z "$CI_SUBMIT_PROFILE_JSON" ] || [ -z "$CI_SUBMIT_AUTH_JSON" ] || '
        '[ -z "$CI_SUBMIT_ANSWERS_JSON" ]' not in text
    )
    assert (
        "CI submit auth secret absent; proceeding without browser storage state."
        in text
    )
