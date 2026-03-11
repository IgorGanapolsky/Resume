"""Tests for scripts/capture_submit_auth.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "capture_submit_auth.py"
    )
    spec = importlib.util.spec_from_file_location(
        "capture_submit_auth_test_mod", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_parse_capture_target_accepts_supported_adapter():
    mod = _load_module()

    target = mod.parse_capture_target("ashby=https://jobs.ashbyhq.com/example/role")

    assert target.adapter == "ashby"
    assert target.url == "https://jobs.ashbyhq.com/example/role"


@pytest.mark.parametrize(
    "value",
    [
        "ashby",
        "unknown=https://example.com/job",
        "greenhouse=jobs.example.com/role",
    ],
)
def test_parse_capture_target_rejects_invalid_values(value):
    mod = _load_module()

    with pytest.raises(Exception):
        mod.parse_capture_target(value)


def test_merge_auth_payload_preserves_valid_entries_and_overrides_updates():
    mod = _load_module()

    merged = mod.merge_auth_payload(
        {
            "ashby": {"storage_state": {"cookies": [{"name": "a"}], "origins": []}},
            "bogus": {"storage_state": {"cookies": [], "origins": []}},
            "lever": "bad-shape",
        },
        {
            "greenhouse": {"cookies": [{"name": "g"}], "origins": []},
            "ashby": {"cookies": [{"name": "new"}], "origins": []},
        },
    )

    assert sorted(merged) == ["ashby", "greenhouse"]
    assert merged["ashby"]["storage_state"]["cookies"][0]["name"] == "new"
    assert merged["greenhouse"]["storage_state"]["cookies"][0]["name"] == "g"


def test_load_auth_payload_normalizes_existing_file(tmp_path):
    mod = _load_module()
    payload_path = tmp_path / "auth.json"
    payload_path.write_text(
        json.dumps(
            {
                "greenhouse": {"storage_state": {"cookies": [], "origins": []}},
                "invalid": {"storage_state": {"cookies": [], "origins": []}},
            }
        ),
        encoding="utf-8",
    )

    payload = mod.load_auth_payload(payload_path)

    assert payload == {"greenhouse": {"storage_state": {"cookies": [], "origins": []}}}


def test_load_auth_payload_returns_empty_for_missing_file(tmp_path):
    mod = _load_module()

    assert mod.load_auth_payload(tmp_path / "missing.json") == {}


def test_load_auth_payload_rejects_non_object_json(tmp_path):
    mod = _load_module()
    payload_path = tmp_path / "auth.json"
    payload_path.write_text(json.dumps(["bad"]), encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        mod.load_auth_payload(payload_path)


def test_resolve_repo_prefers_explicit_repo():
    mod = _load_module()

    assert mod.resolve_repo("IgorGanapolsky/Resume") == "IgorGanapolsky/Resume"


def test_resolve_repo_uses_gh_cli(monkeypatch):
    mod = _load_module()

    class _Proc:
        stdout = "IgorGanapolsky/Resume\n"

    monkeypatch.setattr(mod.subprocess, "run", lambda *args, **kwargs: _Proc())

    assert mod.resolve_repo("") == "IgorGanapolsky/Resume"


def test_main_writes_output_and_syncs_secret(tmp_path, monkeypatch):
    mod = _load_module()
    output_path = tmp_path / "captured.json"
    secret_calls = []

    monkeypatch.setattr(
        mod,
        "capture_storage_state",
        lambda target, headless: {"cookies": [{"name": target.adapter}], "origins": []},
    )
    monkeypatch.setattr(mod, "resolve_repo", lambda repo: "IgorGanapolsky/Resume")
    monkeypatch.setattr(
        mod,
        "set_secret",
        lambda repo, name, value: secret_calls.append((repo, name, value)),
    )

    rc = mod.main(
        [
            "--capture",
            "ashby=https://jobs.ashbyhq.com/example/role",
            "--output",
            str(output_path),
            "--sync-secret",
        ]
    )

    assert rc == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["ashby"]["storage_state"]["cookies"][0]["name"] == "ashby"
    assert len(secret_calls) == 1
    assert secret_calls[0][0] == "IgorGanapolsky/Resume"
    assert secret_calls[0][1] == "CI_SUBMIT_AUTH_JSON"


def test_main_requires_output_or_sync_secret():
    mod = _load_module()

    with pytest.raises(SystemExit):
        mod.main(["--capture", "ashby=https://jobs.ashbyhq.com/example/role"])
