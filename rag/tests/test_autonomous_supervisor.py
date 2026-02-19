"""Tests for autonomous supervisor lane orchestration."""

from __future__ import annotations

import importlib.util
import json
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
