#!/usr/bin/env python3
"""Autonomous supervisor for Resume CI job-search workflows.

This orchestrates discovery, quality gates, indexing, and optional submission
lanes with dependency-aware parallel execution.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = (
    ROOT / "applications" / "job_applications" / "autonomous_supervisor_report.json"
)
DEFAULT_LOGS_DIR = (
    ROOT / "applications" / "job_applications" / "autonomous_supervisor_logs"
)
DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:14b"
DEFAULT_OLLAMA_TIMEOUT_S = 120
DEFAULT_OLLAMA_DELEGATE_LANES = {
    "discover",
    "prepare_submit_artifacts",
    "queue_gate",
    "submit_dry_run",
    "submit_execute",
}


@dataclass(frozen=True)
class Lane:
    name: str
    command: List[str]
    depends_on: List[str] = field(default_factory=list)


@dataclass
class LaneResult:
    name: str
    command: List[str]
    returncode: int
    started_at: float
    ended_at: float
    stdout: str
    stderr: str
    skipped: bool = False
    skip_reason: str = ""

    @property
    def duration_s(self) -> float:
        return max(0.0, self.ended_at - self.started_at)

    def to_json(
        self,
        *,
        include_full_output: bool = False,
        stdout_preview_chars: int = 600,
        stderr_preview_chars: int = 400,
        stdout_log_path: str = "",
        stderr_log_path: str = "",
    ) -> Dict[str, object]:
        status = (
            "skipped"
            if self.skipped
            else ("succeeded" if self.returncode == 0 else "failed")
        )
        payload: Dict[str, object] = {
            "name": self.name,
            "status": status,
            "command_text": " ".join(self.command),
            "command": self.command,
            "returncode": self.returncode,
            "started_at_iso": dt.datetime.fromtimestamp(
                self.started_at, dt.timezone.utc
            ).isoformat(),
            "ended_at_iso": dt.datetime.fromtimestamp(
                self.ended_at, dt.timezone.utc
            ).isoformat(),
            "started_at_epoch": self.started_at,
            "ended_at_epoch": self.ended_at,
            "duration_s": round(self.duration_s, 3),
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "stdout_preview": self.stdout[: max(0, stdout_preview_chars)],
            "stderr_preview": self.stderr[: max(0, stderr_preview_chars)],
            "stdout_chars": len(self.stdout),
            "stderr_chars": len(self.stderr),
            "stdout_truncated": len(self.stdout) > max(0, stdout_preview_chars),
            "stderr_truncated": len(self.stderr) > max(0, stderr_preview_chars),
            "stdout_log_path": stdout_log_path,
            "stderr_log_path": stderr_log_path,
        }
        if include_full_output:
            payload["stdout"] = self.stdout
            payload["stderr"] = self.stderr
        return payload


Runner = Callable[[Lane], LaneResult]


def _run_lane_subprocess(lane: Lane) -> LaneResult:
    started = time.time()
    proc = subprocess.run(
        lane.command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    ended = time.time()
    return LaneResult(
        name=lane.name,
        command=lane.command,
        returncode=proc.returncode,
        started_at=started,
        ended_at=ended,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def _parse_csv_set(raw: str) -> Set[str]:
    return {item.strip() for item in (raw or "").split(",") if item.strip()}


def _is_truthy(raw: str) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _build_ollama_prompt(lane: Lane) -> str:
    command_text = " ".join(lane.command)
    dep_text = ", ".join(lane.depends_on) if lane.depends_on else "none"
    return (
        "You are a workflow subagent for Resume CI automation.\n"
        f"Lane: {lane.name}\n"
        f"Dependencies: {dep_text}\n"
        f"Command: {command_text}\n"
        "Task: Briefly validate this lane plan in 2-4 bullet points with risk checks, "
        "then return one line starting with READY:. Keep under 120 words."
    )


def _invoke_ollama_subagent(
    *, model: str, prompt: str, timeout_s: int
) -> Tuple[int, str, str]:
    proc = subprocess.run(
        ["ollama", "run", model, prompt],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=max(1, timeout_s),
    )
    return proc.returncode, proc.stdout, proc.stderr


def _ollama_model_ready(model: str, timeout_s: int = 8) -> Tuple[bool, str]:
    try:
        version = subprocess.run(
            ["ollama", "--version"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            timeout=max(1, timeout_s),
        )
    except FileNotFoundError:
        return False, "ollama_not_installed"
    except Exception as exc:
        return False, f"ollama_version_check_error:{exc}"

    if version.returncode != 0:
        return False, "ollama_version_check_failed"

    try:
        listing = subprocess.run(
            ["ollama", "list"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            timeout=max(1, timeout_s),
        )
    except Exception as exc:
        return False, f"ollama_list_error:{exc}"

    if listing.returncode != 0:
        return False, "ollama_list_failed"

    target = model.strip()
    lines = [line.strip() for line in listing.stdout.splitlines() if line.strip()]
    models = []
    for line in lines:
        head = line.split()[0]
        if head.lower() == "name":
            continue
        models.append(head)
    if target in models:
        return True, "ollama_model_ready"
    return False, f"ollama_model_missing:{target}"


def resolve_agent_runtime(
    *,
    requested_runtime: str,
    ollama_model: str,
    ollama_timeout_s: int,
) -> Tuple[str, str]:
    req = (requested_runtime or "auto").strip().lower()
    if req not in {"auto", "local", "ollama"}:
        raise ValueError(f"Unsupported agent runtime: {requested_runtime}")

    forced = os.getenv("AUTONOMOUS_AGENT_RUNTIME", "").strip().lower()
    if forced in {"local", "ollama"}:
        if forced == "local":
            return "local", "forced_by_env"
        ready, reason = _ollama_model_ready(ollama_model, timeout_s=ollama_timeout_s)
        if ready:
            return "ollama", "forced_by_env"
        return "local", f"forced_ollama_unavailable:{reason}"

    if req == "local":
        return "local", "requested_local"

    if req == "ollama":
        return "ollama", "requested_ollama"

    if _is_truthy(os.getenv("AUTONOMOUS_DISABLE_OLLAMA", "")):
        return "local", "ollama_disabled_by_env"

    ready, reason = _ollama_model_ready(ollama_model, timeout_s=ollama_timeout_s)
    if ready:
        return "ollama", "auto_detected_ollama_ready"
    return "local", f"auto_fallback_to_local:{reason}"


def _run_lane_with_ollama_assist(
    lane: Lane,
    *,
    model: str,
    timeout_s: int,
    strict: bool,
) -> LaneResult:
    started = time.time()
    prompt = _build_ollama_prompt(lane)
    try:
        ollama_rc, ollama_stdout, ollama_stderr = _invoke_ollama_subagent(
            model=model,
            prompt=prompt,
            timeout_s=timeout_s,
        )
    except Exception as exc:
        ollama_rc = 1
        ollama_stdout = ""
        ollama_stderr = str(exc)

    if ollama_rc != 0 and strict:
        ended = time.time()
        return LaneResult(
            name=lane.name,
            command=lane.command,
            returncode=1,
            started_at=started,
            ended_at=ended,
            stdout="",
            stderr=(
                "ollama_subagent_failed_strict_mode\n"
                f"model={model}\n"
                f"error={ollama_stderr.strip()}"
            ),
        )

    local_result = _run_lane_subprocess(lane)
    assist_header = f"[ollama model={model} rc={ollama_rc}] " + (
        "fallback_to_local" if ollama_rc != 0 else "assisted"
    )
    assist_body = (
        ollama_stdout.strip() if ollama_stdout.strip() else ollama_stderr.strip()
    )
    combined_stdout = f"{assist_header}\n{assist_body}\n\n{local_result.stdout}".strip()
    combined_stderr = local_result.stderr
    if ollama_rc != 0 and ollama_stderr.strip():
        combined_stderr = f"{local_result.stderr}\n{ollama_stderr}".strip()

    return LaneResult(
        name=local_result.name,
        command=local_result.command,
        returncode=local_result.returncode,
        started_at=started,
        ended_at=time.time(),
        stdout=combined_stdout,
        stderr=combined_stderr,
    )


def build_lane_runner(
    *,
    agent_runtime: str,
    ollama_model: str,
    ollama_delegate_lanes: Sequence[str],
    ollama_timeout_s: int,
    ollama_strict: bool,
) -> Runner:
    runtime = (agent_runtime or "local").strip().lower()
    delegated = set(ollama_delegate_lanes)
    if runtime not in {"local", "ollama"}:
        raise ValueError(f"Unsupported agent runtime: {agent_runtime}")

    if runtime == "local":
        return _run_lane_subprocess

    def _runner(lane: Lane) -> LaneResult:
        if lane.name not in delegated:
            return _run_lane_subprocess(lane)
        return _run_lane_with_ollama_assist(
            lane,
            model=ollama_model,
            timeout_s=ollama_timeout_s,
            strict=ollama_strict,
        )

    return _runner


def build_lane_plan(
    *,
    max_new_jobs: int,
    fit_threshold: int,
    remote_min_score: int,
    max_submit_jobs: int,
    execute_submissions: bool,
) -> List[Lane]:
    lanes = [
        Lane(
            name="discover",
            command=[
                "python3",
                "scripts/ralph_loop_ci.py",
                "--max-new-jobs",
                str(max_new_jobs),
            ],
        ),
        Lane(
            name="prepare_submit_artifacts",
            command=[
                "python3",
                "scripts/prepare_ci_ready_artifacts.py",
                "--fit-threshold",
                str(fit_threshold),
                "--remote-min-score",
                str(remote_min_score),
                "--max-jobs",
                str(max_submit_jobs * 3),
                "--report",
                "applications/job_applications/ci_prepare_artifacts_report.json",
            ],
            depends_on=["discover"],
        ),
        Lane(
            name="queue_gate",
            command=[
                "python3",
                "scripts/ci_submit_pipeline.py",
                "--queue-only",
                "--fit-threshold",
                str(fit_threshold),
                "--remote-min-score",
                str(remote_min_score),
                "--report",
                "applications/job_applications/ci_ready_queue_report.json",
            ],
            depends_on=["discover", "prepare_submit_artifacts"],
        ),
        Lane(
            name="rag_build",
            command=["python3", "rag/cli.py", "build"],
            depends_on=["queue_gate"],
        ),
        Lane(
            name="scrub_job_captures",
            command=["python3", "scripts/scrub_job_captures.py"],
            depends_on=["queue_gate"],
        ),
        Lane(
            name="rag_status",
            command=["python3", "rag/cli.py", "status"],
            depends_on=["rag_build"],
        ),
    ]
    if execute_submissions:
        lanes.append(
            Lane(
                name="submit_execute",
                command=[
                    "python3",
                    "scripts/ci_submit_pipeline.py",
                    "--execute",
                    "--max-jobs",
                    str(max_submit_jobs),
                    "--fit-threshold",
                    str(fit_threshold),
                    "--remote-min-score",
                    str(remote_min_score),
                    "--report",
                    "applications/job_applications/ci_submit_execute_report.json",
                    "--fail-on-error",
                ],
                depends_on=["queue_gate"],
            )
        )
    else:
        lanes.append(
            Lane(
                name="submit_dry_run",
                command=[
                    "python3",
                    "scripts/ci_submit_pipeline.py",
                    "--max-jobs",
                    str(max_submit_jobs),
                    "--fit-threshold",
                    str(fit_threshold),
                    "--remote-min-score",
                    str(remote_min_score),
                    "--report",
                    "applications/job_applications/ci_submit_dry_run_report.json",
                ],
                depends_on=["queue_gate"],
            )
        )
    return lanes


def _ready_lanes(
    pending: Dict[str, Lane],
    completed: Dict[str, LaneResult],
    running: Dict[str, Future],
) -> List[Lane]:
    ready: List[Lane] = []
    for name, lane in pending.items():
        if name in running:
            continue
        if all(
            dep in completed and completed[dep].returncode == 0
            for dep in lane.depends_on
        ):
            ready.append(lane)
    return ready


def _mark_skipped_dependents(
    *,
    pending: Dict[str, Lane],
    completed: Dict[str, LaneResult],
) -> None:
    changed = True
    while changed:
        changed = False
        for name, lane in list(pending.items()):
            if name in completed:
                continue
            failed_dep = next(
                (
                    d
                    for d in lane.depends_on
                    if d in completed and completed[d].returncode != 0
                ),
                None,
            )
            if failed_dep is None:
                continue
            now = time.time()
            completed[name] = LaneResult(
                name=name,
                command=lane.command,
                returncode=1,
                started_at=now,
                ended_at=now,
                stdout="",
                stderr="",
                skipped=True,
                skip_reason=f"dependency_failed:{failed_dep}",
            )
            pending.pop(name, None)
            changed = True


def _lane_log_paths(base_dir: Path, lane_name: str) -> Tuple[Path, Path]:
    safe = lane_name.replace("/", "_")
    return base_dir / f"{safe}.stdout.log", base_dir / f"{safe}.stderr.log"


def _write_lane_logs(
    results: Sequence[LaneResult], logs_dir: Path
) -> Dict[str, Tuple[str, str]]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Tuple[str, str]] = {}
    for result in results:
        stdout_path, stderr_path = _lane_log_paths(logs_dir, result.name)
        stdout_path.write_text(result.stdout, encoding="utf-8")
        stderr_path.write_text(result.stderr, encoding="utf-8")
        out[result.name] = (str(stdout_path), str(stderr_path))
    return out


def run_supervisor(
    *,
    lanes: List[Lane],
    max_parallel: int,
    fail_fast: bool,
    report_path: Path,
    agent_runtime: str = "auto",
    ollama_model: str = DEFAULT_OLLAMA_MODEL,
    ollama_delegate_lanes: Optional[Sequence[str]] = None,
    ollama_timeout_s: int = DEFAULT_OLLAMA_TIMEOUT_S,
    ollama_strict: bool = False,
    logs_dir: Path = DEFAULT_LOGS_DIR,
    include_full_output: bool = False,
    stdout_preview_chars: int = 600,
    stderr_preview_chars: int = 400,
    runner: Optional[Runner] = None,
) -> int:
    lane_by_name = {lane.name: lane for lane in lanes}
    if len(lane_by_name) != len(lanes):
        raise ValueError("Duplicate lane names are not allowed")
    for lane in lanes:
        missing = [dep for dep in lane.depends_on if dep not in lane_by_name]
        if missing:
            raise ValueError(f"Lane {lane.name!r} has missing dependencies: {missing}")

    started = time.time()
    pending: Dict[str, Lane] = dict(lane_by_name)
    completed: Dict[str, LaneResult] = {}
    running: Dict[str, Future] = {}
    lock = threading.Lock()
    resolved_runtime, runtime_resolution_reason = resolve_agent_runtime(
        requested_runtime=agent_runtime,
        ollama_model=ollama_model,
        ollama_timeout_s=ollama_timeout_s,
    )
    runner_fn = runner or build_lane_runner(
        agent_runtime=resolved_runtime,
        ollama_model=ollama_model,
        ollama_delegate_lanes=(
            list(ollama_delegate_lanes)
            if ollama_delegate_lanes is not None
            else sorted(DEFAULT_OLLAMA_DELEGATE_LANES)
        ),
        ollama_timeout_s=ollama_timeout_s,
        ollama_strict=ollama_strict,
    )

    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        while pending or running:
            _mark_skipped_dependents(
                pending=pending,
                completed=completed,
            )

            if fail_fast and any(
                (not r.skipped and r.returncode != 0) for r in completed.values()
            ):
                # Mark everything else skipped and stop scheduling.
                for name, lane in list(pending.items()):
                    now = time.time()
                    completed[name] = LaneResult(
                        name=name,
                        command=lane.command,
                        returncode=1,
                        started_at=now,
                        ended_at=now,
                        stdout="",
                        stderr="",
                        skipped=True,
                        skip_reason="fail_fast",
                    )
                    pending.pop(name, None)
                break

            for lane in _ready_lanes(pending, completed, running):
                if lane.name in running:
                    continue

                def _submit(current_lane: Lane = lane) -> LaneResult:
                    return runner_fn(current_lane)

                running[lane.name] = pool.submit(_submit)
                pending.pop(lane.name, None)

            if not running:
                # Remaining pending lanes are blocked; mark and exit.
                for name, lane in list(pending.items()):
                    now = time.time()
                    completed[name] = LaneResult(
                        name=name,
                        command=lane.command,
                        returncode=1,
                        started_at=now,
                        ended_at=now,
                        stdout="",
                        stderr="",
                        skipped=True,
                        skip_reason="blocked_by_dependencies",
                    )
                    pending.pop(name, None)
                break

            done, _ = wait(list(running.values()), timeout=0.1)
            for fut in done:
                lane_name = next(
                    name for name, value in list(running.items()) if value is fut
                )
                with lock:
                    completed[lane_name] = fut.result()
                    running.pop(lane_name, None)

    ended = time.time()
    ordered_results = [completed[lane.name] for lane in lanes if lane.name in completed]
    failures = [r for r in ordered_results if not r.skipped and r.returncode != 0]
    skipped = [r for r in ordered_results if r.skipped]
    success_count = len(
        [r for r in ordered_results if not r.skipped and r.returncode == 0]
    )
    overall_rc = 1 if failures else 0
    lane_log_map = _write_lane_logs(ordered_results, logs_dir=logs_dir)

    report = {
        "generated_at_iso": dt.datetime.fromtimestamp(
            ended, dt.timezone.utc
        ).isoformat(),
        "generated_at_epoch": ended,
        "duration_s": round(max(0.0, ended - started), 3),
        "max_parallel": max_parallel,
        "fail_fast": fail_fast,
        "requested_agent_runtime": agent_runtime,
        "resolved_agent_runtime": resolved_runtime,
        "runtime_resolution_reason": runtime_resolution_reason,
        "agent_runtime": resolved_runtime,
        "ollama_model": ollama_model if resolved_runtime == "ollama" else "",
        "ollama_delegate_lanes": (
            sorted(set(ollama_delegate_lanes))
            if ollama_delegate_lanes is not None and resolved_runtime == "ollama"
            else []
        ),
        "ollama_timeout_s": ollama_timeout_s if resolved_runtime == "ollama" else 0,
        "ollama_strict": bool(ollama_strict) if resolved_runtime == "ollama" else False,
        "overall_returncode": overall_rc,
        "summary": {
            "total_lanes": len(lanes),
            "succeeded": success_count,
            "failed": len(failures),
            "skipped": len(skipped),
        },
        "logs_dir": str(logs_dir),
        "lanes": [
            result.to_json(
                include_full_output=include_full_output,
                stdout_preview_chars=stdout_preview_chars,
                stderr_preview_chars=stderr_preview_chars,
                stdout_log_path=lane_log_map.get(result.name, ("", ""))[0],
                stderr_log_path=lane_log_map.get(result.name, ("", ""))[1],
            )
            for result in ordered_results
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        "Autonomous supervisor complete: "
        f"succeeded={success_count} failed={len(failures)} skipped={len(skipped)} "
        f"report={report_path}"
    )
    return overall_rc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-new-jobs", type=int, default=10)
    parser.add_argument("--fit-threshold", type=int, default=70)
    parser.add_argument("--remote-min-score", type=int, default=50)
    parser.add_argument("--max-submit-jobs", type=int, default=5)
    parser.add_argument("--max-parallel", type=int, default=3)
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument(
        "--logs-dir",
        default=str(DEFAULT_LOGS_DIR),
        help="Directory for per-lane stdout/stderr logs.",
    )
    parser.add_argument(
        "--include-full-output",
        action="store_true",
        help="Include full stdout/stderr in report JSON (default keeps previews only).",
    )
    parser.add_argument(
        "--stdout-preview-chars",
        type=int,
        default=600,
        help="Max stdout preview characters per lane in report JSON.",
    )
    parser.add_argument(
        "--stderr-preview-chars",
        type=int,
        default=400,
        help="Max stderr preview characters per lane in report JSON.",
    )
    parser.add_argument(
        "--agent-runtime",
        choices=["auto", "local", "ollama"],
        default="auto",
        help=(
            "Execution backend: auto prefers Ollama when model is available and "
            "falls back to local. local runs all lanes directly. ollama delegates "
            "selected lanes to Ollama-backed subagent assist before deterministic "
            "local execution."
        ),
    )
    parser.add_argument(
        "--ollama-model",
        default=DEFAULT_OLLAMA_MODEL,
        help="Ollama model used when --agent-runtime ollama.",
    )
    parser.add_argument(
        "--ollama-delegate-lanes",
        default=",".join(sorted(DEFAULT_OLLAMA_DELEGATE_LANES)),
        help="Comma-separated lane names to delegate when --agent-runtime ollama.",
    )
    parser.add_argument(
        "--ollama-timeout-s",
        type=int,
        default=DEFAULT_OLLAMA_TIMEOUT_S,
        help="Timeout for each Ollama subagent call in seconds.",
    )
    parser.add_argument(
        "--ollama-strict",
        action="store_true",
        help=(
            "Fail delegated lane if Ollama assist call fails. "
            "Default behavior is fallback to local execution."
        ),
    )
    parser.add_argument(
        "--execute-submissions",
        action="store_true",
        help="Run real submission lane after queue gating (requires CI secrets).",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop scheduling additional lanes after first failure.",
    )
    args = parser.parse_args()

    lanes = build_lane_plan(
        max_new_jobs=args.max_new_jobs,
        fit_threshold=args.fit_threshold,
        remote_min_score=max(0, min(100, args.remote_min_score)),
        max_submit_jobs=args.max_submit_jobs,
        execute_submissions=args.execute_submissions,
    )
    return run_supervisor(
        lanes=lanes,
        max_parallel=max(1, args.max_parallel),
        fail_fast=args.fail_fast,
        report_path=Path(args.report),
        agent_runtime=args.agent_runtime,
        ollama_model=args.ollama_model,
        ollama_delegate_lanes=sorted(_parse_csv_set(args.ollama_delegate_lanes)),
        ollama_timeout_s=max(1, args.ollama_timeout_s),
        ollama_strict=args.ollama_strict,
        logs_dir=Path(args.logs_dir),
        include_full_output=args.include_full_output,
        stdout_preview_chars=max(0, args.stdout_preview_chars),
        stderr_preview_chars=max(0, args.stderr_preview_chars),
    )


if __name__ == "__main__":
    raise SystemExit(main())
