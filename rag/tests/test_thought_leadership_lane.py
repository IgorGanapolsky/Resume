"""Tests for the optional thought leadership lane."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "thought_leadership_lane.py"
    )
    spec = importlib.util.spec_from_file_location(
        "thought_leadership_lane_test_mod", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_main_uses_fallback_post_when_llm_is_unauthorized(tmp_path, monkeypatch):
    mod = _load_module()

    queue_json = tmp_path / "linkedin" / "linkedin_post_queue.json"
    queue_json.parent.mkdir(parents=True, exist_ok=True)
    queue_json.write_text(json.dumps({"queue": []}), encoding="utf-8")

    tracker_csv = (
        tmp_path / "applications" / "job_applications" / "application_tracker.csv"
    )
    tracker_csv.parent.mkdir(parents=True, exist_ok=True)
    tracker_csv.write_text(
        "Company,Role,Status,Submission Verified At\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "QUEUE_JSON", queue_json)
    monkeypatch.setattr(mod, "METRICS_PROM", tmp_path / "missing.prom")
    monkeypatch.setattr(mod, "TRACKER_CSV", tracker_csv)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _raise_unauthorized(*args, **kwargs):
        raise RuntimeError("HTTP Error 401: Unauthorized")

    monkeypatch.setattr(mod.urllib.request, "urlopen", _raise_unauthorized)

    rc = mod.main()
    assert rc == 0

    payload = json.loads(queue_json.read_text(encoding="utf-8"))
    assert len(payload["queue"]) == 1
    assert payload["queue"][0]["content"] == mod.FALLBACK_POST
