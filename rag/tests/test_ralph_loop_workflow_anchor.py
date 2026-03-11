from pathlib import Path


def test_ralph_loop_workflow_exposes_anchor_browser_env():
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ralph-loop.yml"
    ).read_text(encoding="utf-8")

    assert "ANCHOR_BROWSER_API_KEY: ${{ secrets.ANCHOR_BROWSER_API_KEY }}" in workflow
    assert (
        "ANCHOR_BROWSER_PROFILE_NAME: ${{ vars.ANCHOR_BROWSER_PROFILE_NAME }}"
        in workflow
    )
    assert (
        "ANCHOR_BROWSER_PROXY_ACTIVE: ${{ vars.ANCHOR_BROWSER_PROXY_ACTIVE }}"
        in workflow
    )
    assert (
        "ANCHOR_BROWSER_EXTRA_STEALTH_ACTIVE: ${{ vars.ANCHOR_BROWSER_EXTRA_STEALTH_ACTIVE }}"
        in workflow
    )
    assert '[ -z "$CI_SUBMIT_AUTH_JSON" ]' not in workflow
