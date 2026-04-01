#!/usr/bin/env python3
"""Run the live submit lane locally using a dedicated Chrome automation profile."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_PROFILE_JSON = (
    ROOT / "applications" / "job_applications" / "candidate_profile.json"
)
SUBMIT_ANSWERS_JSON = ROOT / "applications" / "job_applications" / "submit_answers.json"
DEFAULT_REPORT = (
    ROOT / "applications" / "job_applications" / "local_submit_lane_report.json"
)


def _load_json_file(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(payload, ensure_ascii=True)


def materialize_local_submit_env(
    base_env: Optional[Dict[str, str]] = None,
    *,
    auth_file: Optional[Path] = None,
    browser_channel: str = "",
    chrome_user_data_dir: str = "",
) -> Dict[str, str]:
    env = dict(base_env or os.environ)

    if not env.get("CI_SUBMIT_PROFILE_JSON", "").strip():
        if not CANDIDATE_PROFILE_JSON.exists():
            raise RuntimeError(
                f"Missing candidate profile file: {CANDIDATE_PROFILE_JSON}"
            )
        env["CI_SUBMIT_PROFILE_JSON"] = _load_json_file(CANDIDATE_PROFILE_JSON)

    if not env.get("CI_SUBMIT_ANSWERS_JSON", "").strip():
        if not SUBMIT_ANSWERS_JSON.exists():
            raise RuntimeError(f"Missing submit answers file: {SUBMIT_ANSWERS_JSON}")
        env["CI_SUBMIT_ANSWERS_JSON"] = _load_json_file(SUBMIT_ANSWERS_JSON)

    if auth_file is not None and not env.get("CI_SUBMIT_AUTH_JSON", "").strip():
        resolved = auth_file.expanduser().resolve()
        if not resolved.exists():
            raise RuntimeError(f"Auth file does not exist: {resolved}")
        env["CI_SUBMIT_AUTH_JSON"] = _load_json_file(resolved)

    if browser_channel.strip():
        env["CI_SUBMIT_BROWSER_CHANNEL"] = browser_channel.strip()

    if chrome_user_data_dir.strip():
        env["CI_SUBMIT_CHROME_USER_DATA_DIR"] = chrome_user_data_dir.strip()

    return env


def build_commands(args: argparse.Namespace) -> List[List[str]]:
    max_prepare_jobs = args.max_prepare_jobs or max(1, args.max_submit_jobs * 3)
    return [
        ["python3", "scripts/check_calendar_guardrails.py"],
        [
            "python3",
            "scripts/audit_submission_artifacts.py",
            "--write",
            "--normalize-unverified-applied",
            "--report",
            "applications/job_applications/tracker_integrity_report.local.json",
        ],
        [
            "python3",
            "scripts/prepare_ci_ready_artifacts.py",
            "--fit-threshold",
            str(args.fit_threshold),
            "--remote-min-score",
            str(args.remote_min_score),
            "--max-jobs",
            str(max_prepare_jobs),
            "--report",
            "applications/job_applications/ci_prepare_artifacts_report.local.json",
        ],
        [
            "python3",
            "scripts/ci_submit_pipeline.py",
            "--execute",
            "--use-local-chrome",
            "--max-jobs",
            str(args.max_submit_jobs),
            "--fit-threshold",
            str(args.fit_threshold),
            "--remote-min-score",
            str(args.remote_min_score),
            "--report",
            str(args.report),
            "--quarantine-blocked",
            "--fail-on-error",
            *(["--visible"] if not args.headless else []),
        ],
        [
            "python3",
            "scripts/audit_submission_artifacts.py",
            "--write",
            "--report",
            "applications/job_applications/submission_artifact_audit_report.local.json",
        ],
        ["python3", "rag/cli.py", "build"],
        ["python3", "rag/cli.py", "status"],
    ]


def run_command(command: Sequence[str], *, env: Dict[str, str]) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, check=True, cwd=ROOT, env=env)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-submit-jobs",
        type=int,
        default=3,
        help="Maximum queued jobs to attempt in this local run.",
    )
    parser.add_argument(
        "--max-prepare-jobs",
        type=int,
        default=0,
        help="Optional cap for artifact preparation. Defaults to 3x max-submit-jobs.",
    )
    parser.add_argument(
        "--fit-threshold",
        type=int,
        default=70,
        help="Minimum fit score required for auto-submit queue eligibility.",
    )
    parser.add_argument(
        "--remote-min-score",
        type=int,
        default=50,
        help="Minimum remote-likelihood score required for auto-submit queue eligibility.",
    )
    parser.add_argument(
        "--auth-file",
        default="",
        help="Optional local Playwright auth-state JSON to inject as CI_SUBMIT_AUTH_JSON.",
    )
    parser.add_argument(
        "--browser-channel",
        default="",
        help="Optional browser channel override passed to CI_SUBMIT_BROWSER_CHANNEL.",
    )
    parser.add_argument(
        "--chrome-user-data-dir",
        default="",
        help="Optional local Chrome user-data directory override.",
    )
    parser.add_argument(
        "--report",
        default=str(DEFAULT_REPORT),
        help="Write the submit execution report to this path.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run local Chrome headless. Default is visible mode for manual rescue.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        env = materialize_local_submit_env(
            auth_file=(
                Path(args.auth_file).expanduser() if args.auth_file.strip() else None
            ),
            browser_channel=args.browser_channel,
            chrome_user_data_dir=args.chrome_user_data_dir,
        )
        for command in build_commands(args):
            run_command(command, env=env)
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: command failed with exit code {exc.returncode}", file=sys.stderr)
        return exc.returncode or 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
