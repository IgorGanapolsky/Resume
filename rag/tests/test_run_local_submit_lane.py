"""Tests for scripts/run_local_submit_lane.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "run_local_submit_lane.py"
    )
    spec = importlib.util.spec_from_file_location(
        "run_local_submit_lane_test_mod", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_materialize_local_submit_env_uses_file_fallbacks(tmp_path, monkeypatch):
    mod = _load_module()
    profile = tmp_path / "candidate_profile.json"
    answers = tmp_path / "submit_answers.json"
    auth = tmp_path / "auth.json"
    profile.write_text(
        json.dumps(
            {
                "first_name": "Igor",
                "last_name": "Ganapolsky",
                "email": "iganapolsky@gmail.com",
                "phone": "(201) 639-1534",
            }
        ),
        encoding="utf-8",
    )
    answers.write_text(
        json.dumps(
            {
                "work_authorization_us": True,
                "require_sponsorship": False,
                "role_interest": "AI-heavy, integration-first role focused on production impact.",
                "eeo_default": "Prefer not to say",
            }
        ),
        encoding="utf-8",
    )
    auth.write_text(json.dumps({"ashby": {"storage_state": {}}}), encoding="utf-8")

    monkeypatch.setattr(mod, "CANDIDATE_PROFILE_JSON", profile)
    monkeypatch.setattr(mod, "SUBMIT_ANSWERS_JSON", answers)

    env = mod.materialize_local_submit_env({}, auth_file=auth)
    assert json.loads(env["CI_SUBMIT_PROFILE_JSON"])["email"] == "iganapolsky@gmail.com"
    assert (
        json.loads(env["CI_SUBMIT_ANSWERS_JSON"])["eeo_default"] == "Prefer not to say"
    )
    assert "CI_SUBMIT_AUTH_JSON" in env


def test_materialize_local_submit_env_applies_browser_overrides(tmp_path, monkeypatch):
    mod = _load_module()
    profile = tmp_path / "candidate_profile.json"
    answers = tmp_path / "submit_answers.json"
    profile.write_text(
        json.dumps(
            {
                "first_name": "Igor",
                "last_name": "Ganapolsky",
                "email": "iganapolsky@gmail.com",
                "phone": "(201) 639-1534",
            }
        ),
        encoding="utf-8",
    )
    answers.write_text(json.dumps({"work_authorization_us": True}), encoding="utf-8")

    monkeypatch.setattr(mod, "CANDIDATE_PROFILE_JSON", profile)
    monkeypatch.setattr(mod, "SUBMIT_ANSWERS_JSON", answers)

    env = mod.materialize_local_submit_env(
        {},
        browser_channel="chrome-beta",
        chrome_user_data_dir="~/tmp/resume-ci-profile",
    )

    assert env["CI_SUBMIT_BROWSER_CHANNEL"] == "chrome-beta"
    assert env["CI_SUBMIT_CHROME_USER_DATA_DIR"] == "~/tmp/resume-ci-profile"


def test_build_commands_prefers_visible_local_chrome():
    mod = _load_module()
    parser = mod.build_parser()
    args = parser.parse_args([])

    commands = mod.build_commands(args)
    submit_command = next(
        command
        for command in commands
        if command[:2] == ["python3", "scripts/ci_submit_pipeline.py"]
    )

    assert "--execute" in submit_command
    assert "--use-local-chrome" in submit_command
    assert "--visible" in submit_command
