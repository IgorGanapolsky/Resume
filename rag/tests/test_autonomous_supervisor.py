"""Tests for autonomous supervisor lane orchestration."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path


def _load_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "autonomous_supervisor.py"
    )
    spec = importlib.util.spec_from_file_location(
        "autonomous_supervisor_test_mod", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_build_lane_plan_contains_queue_gate_and_parallel_lanes():
    mod = _load_module()
    lanes = mod.build_lane_plan(
        max_new_jobs=9,
        fit_threshold=72,
        max_submit_jobs=4,
        execute_submissions=False,
    )
    names = [lane.name for lane in lanes]
    assert "discover" in names
    assert "queue_gate" in names
    assert "rag_build" in names
    assert "scrub_job_captures" in names
    assert "submit_dry_run" in names

    lane_map = {lane.name: lane for lane in lanes}
    assert lane_map["queue_gate"].depends_on == ["discover"]
    assert lane_map["rag_build"].depends_on == ["queue_gate"]
    assert lane_map["scrub_job_captures"].depends_on == ["queue_gate"]
    assert lane_map["submit_dry_run"].depends_on == ["queue_gate"]
    assert "--fit-threshold" in lane_map["queue_gate"].command


def test_run_supervisor_skips_dependents_after_failure(tmp_path):
    mod = _load_module()
    lanes = [
        mod.Lane(name="discover", command=["discover"]),
        mod.Lane(name="queue_gate", command=["queue"], depends_on=["discover"]),
        mod.Lane(name="rag_build", command=["rag"], depends_on=["queue_gate"]),
    ]

    def fake_runner(lane):
        now = time.time()
        rc = 1 if lane.name == "discover" else 0
        return mod.LaneResult(
            name=lane.name,
            command=lane.command,
            returncode=rc,
            started_at=now,
            ended_at=now,
            stdout="",
            stderr="",
        )

    report = tmp_path / "report.json"
    rc = mod.run_supervisor(
        lanes=lanes,
        max_parallel=2,
        fail_fast=False,
        report_path=report,
        runner=fake_runner,
    )
    assert rc == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    lane_map = {lane["name"]: lane for lane in payload["lanes"]}
    assert lane_map["discover"]["returncode"] == 1
    assert lane_map["queue_gate"]["skipped"] is True
    assert lane_map["rag_build"]["skipped"] is True


def test_run_supervisor_fail_fast_marks_pending_skipped(tmp_path):
    mod = _load_module()
    lanes = [
        mod.Lane(name="discover", command=["discover"]),
        mod.Lane(name="queue_gate", command=["queue"], depends_on=["discover"]),
        mod.Lane(name="scrub", command=["scrub"], depends_on=["queue_gate"]),
    ]

    def fake_runner(lane):
        now = time.time()
        rc = 1 if lane.name == "queue_gate" else 0
        return mod.LaneResult(
            name=lane.name,
            command=lane.command,
            returncode=rc,
            started_at=now,
            ended_at=now,
            stdout="",
            stderr="",
        )

    report = tmp_path / "report_fail_fast.json"
    rc = mod.run_supervisor(
        lanes=lanes,
        max_parallel=2,
        fail_fast=True,
        report_path=report,
        runner=fake_runner,
    )
    assert rc == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    lane_map = {lane["name"]: lane for lane in payload["lanes"]}
    assert lane_map["discover"]["returncode"] == 0
    assert lane_map["queue_gate"]["returncode"] == 1
    assert lane_map["scrub"]["skipped"] is True
    assert lane_map["discover"]["status"] == "succeeded"
    assert lane_map["queue_gate"]["status"] == "failed"
    assert lane_map["scrub"]["status"] == "skipped"
    assert "stdout_preview" in lane_map["discover"]
    assert "stderr_preview" in lane_map["discover"]
    assert Path(lane_map["discover"]["stdout_log_path"]).exists()
    assert Path(lane_map["discover"]["stderr_log_path"]).exists()


def test_build_lane_runner_ollama_delegates_selected_lanes():
    mod = _load_module()
    calls = {"assist": 0, "local": 0}

    def fake_assist(lane, *, model, timeout_s, strict):
        calls["assist"] += 1
        now = time.time()
        return mod.LaneResult(
            name=lane.name,
            command=lane.command,
            returncode=0,
            started_at=now,
            ended_at=now,
            stdout=f"assisted:{lane.name}:{model}:{timeout_s}:{strict}",
            stderr="",
        )

    def fake_local(lane):
        calls["local"] += 1
        now = time.time()
        return mod.LaneResult(
            name=lane.name,
            command=lane.command,
            returncode=0,
            started_at=now,
            ended_at=now,
            stdout=f"local:{lane.name}",
            stderr="",
        )

    orig_assist = mod._run_lane_with_ollama_assist
    orig_local = mod._run_lane_subprocess
    mod._run_lane_with_ollama_assist = fake_assist
    mod._run_lane_subprocess = fake_local
    try:
        runner = mod.build_lane_runner(
            agent_runtime="ollama",
            ollama_model="qwen-test",
            ollama_delegate_lanes=["discover"],
            ollama_timeout_s=12,
            ollama_strict=True,
        )
        delegated = runner(mod.Lane(name="discover", command=["discover"]))
        local_only = runner(mod.Lane(name="rag_build", command=["rag"]))
    finally:
        mod._run_lane_with_ollama_assist = orig_assist
        mod._run_lane_subprocess = orig_local

    assert calls["assist"] == 1
    assert calls["local"] == 1
    assert "assisted:discover" in delegated.stdout
    assert "local:rag_build" in local_only.stdout


def test_run_lane_with_ollama_assist_strict_failure_blocks_lane():
    mod = _load_module()
    lane = mod.Lane(name="discover", command=["discover"])

    def fake_invoke(*, model, prompt, timeout_s):
        return (1, "", "ollama unavailable")

    def forbidden_local(_lane):
        raise AssertionError("local subprocess should not run in strict failure mode")

    orig_invoke = mod._invoke_ollama_subagent
    orig_local = mod._run_lane_subprocess
    mod._invoke_ollama_subagent = fake_invoke
    mod._run_lane_subprocess = forbidden_local
    try:
        result = mod._run_lane_with_ollama_assist(
            lane,
            model="qwen-test",
            timeout_s=30,
            strict=True,
        )
    finally:
        mod._invoke_ollama_subagent = orig_invoke
        mod._run_lane_subprocess = orig_local

    assert result.returncode == 1
    assert "ollama_subagent_failed_strict_mode" in result.stderr


def test_run_lane_with_ollama_assist_fallback_to_local_when_not_strict():
    mod = _load_module()
    lane = mod.Lane(name="discover", command=["discover"])

    def fake_invoke(*, model, prompt, timeout_s):
        return (1, "", "ollama unavailable")

    def fake_local(run_lane):
        now = time.time()
        return mod.LaneResult(
            name=run_lane.name,
            command=run_lane.command,
            returncode=0,
            started_at=now,
            ended_at=now,
            stdout="local execution ok",
            stderr="",
        )

    orig_invoke = mod._invoke_ollama_subagent
    orig_local = mod._run_lane_subprocess
    mod._invoke_ollama_subagent = fake_invoke
    mod._run_lane_subprocess = fake_local
    try:
        result = mod._run_lane_with_ollama_assist(
            lane,
            model="qwen-test",
            timeout_s=30,
            strict=False,
        )
    finally:
        mod._invoke_ollama_subagent = orig_invoke
        mod._run_lane_subprocess = orig_local

    assert result.returncode == 0
    assert "fallback_to_local" in result.stdout
    assert "local execution ok" in result.stdout


def test_resolve_agent_runtime_auto_prefers_ollama_when_ready():
    mod = _load_module()
    orig = mod._ollama_model_ready
    mod._ollama_model_ready = lambda model, timeout_s=8: (True, "ollama_model_ready")
    try:
        resolved, reason = mod.resolve_agent_runtime(
            requested_runtime="auto",
            ollama_model="qwen2.5-coder:14b",
            ollama_timeout_s=20,
        )
    finally:
        mod._ollama_model_ready = orig
    assert resolved == "ollama"
    assert reason == "auto_detected_ollama_ready"


def test_resolve_agent_runtime_auto_falls_back_to_local_when_unavailable():
    mod = _load_module()
    orig = mod._ollama_model_ready
    mod._ollama_model_ready = lambda model, timeout_s=8: (
        False,
        "ollama_not_installed",
    )
    try:
        resolved, reason = mod.resolve_agent_runtime(
            requested_runtime="auto",
            ollama_model="qwen2.5-coder:14b",
            ollama_timeout_s=20,
        )
    finally:
        mod._ollama_model_ready = orig
    assert resolved == "local"
    assert reason.startswith("auto_fallback_to_local:")


def test_resolve_agent_runtime_forced_env_override():
    mod = _load_module()
    orig = mod._ollama_model_ready
    os.environ["AUTONOMOUS_AGENT_RUNTIME"] = "local"
    mod._ollama_model_ready = lambda model, timeout_s=8: (True, "ollama_model_ready")
    try:
        resolved, reason = mod.resolve_agent_runtime(
            requested_runtime="auto",
            ollama_model="qwen2.5-coder:14b",
            ollama_timeout_s=20,
        )
    finally:
        os.environ.pop("AUTONOMOUS_AGENT_RUNTIME", None)
        mod._ollama_model_ready = orig
    assert resolved == "local"
    assert reason == "forced_by_env"
