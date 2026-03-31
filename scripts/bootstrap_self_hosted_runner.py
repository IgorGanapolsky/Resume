#!/usr/bin/env python3
"""Bootstrap a macOS self-hosted GitHub Actions runner for Resume automation.

This script downloads the latest GitHub Actions runner for macOS arm64, registers
it to the target repository, and installs it as a LaunchAgent so the runner stays
available across logins.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


DEFAULT_REPO = "IgorGanapolsky/Resume"
DEFAULT_RUNNER_NAME = "resume-ci-mac"
DEFAULT_LABELS = "self-hosted,macOS,resume-ci"
DEFAULT_WORK_DIR = "_work"
DEFAULT_BASE_DIR = (
    Path.home() / "Library" / "Application Support" / "resume-ci" / "github-runner"
)
DEFAULT_PLIST = (
    Path.home() / "Library" / "LaunchAgents" / "com.resume.github-runner.plist"
)
DEFAULT_LOG_DIR = Path.home() / "Library" / "Logs" / "resume-ci"
RUNNER_API = "https://api.github.com/repos/actions/runner/releases/latest"


def _run(
    command: Iterable[str],
    *,
    cwd: Optional[Path] = None,
    check: bool = True,
    capture_output: bool = False,
    text: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=str(cwd) if cwd is not None else None,
        check=check,
        capture_output=capture_output,
        text=text,
    )


def _gh_api_json(endpoint: str, *, method: str = "GET") -> Dict[str, Any]:
    proc = _run(
        ["gh", "api", "-X", method, endpoint],
        capture_output=True,
    )
    return json.loads(proc.stdout)


def _safe_extract_tarball(tar: tarfile.TarFile, destination: Path) -> None:
    dest_root = destination.resolve()
    members = tar.getmembers()
    for member in members:
        target = (destination / member.name).resolve()
        if dest_root not in target.parents and target != dest_root:
            raise RuntimeError(f"Unsafe path in tarball: {member.name}")
    tar.extractall(destination, members)


def _ensure_runner_archive(base_dir: Path) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    latest = _gh_api_json(RUNNER_API)
    assets = latest.get("assets", [])
    target_asset = None
    for asset in assets:
        name = str(asset.get("name", ""))
        if name.startswith("actions-runner-osx-arm64-") and name.endswith(".tar.gz"):
            target_asset = asset
            break
    if target_asset is None:
        raise RuntimeError(
            "Could not find macOS arm64 runner archive in latest release"
        )

    version = str(latest.get("tag_name", "")).lstrip("v")
    sentinel = base_dir / ".runner_version"
    archive_name = str(target_asset["name"])
    archive_path = base_dir / archive_name
    extracted_marker = base_dir / "run.sh"
    if (
        extracted_marker.exists()
        and sentinel.exists()
        and sentinel.read_text().strip() == version
    ):
        return base_dir

    url = str(target_asset["browser_download_url"])
    with urllib.request.urlopen(url) as response, archive_path.open("wb") as out:
        shutil.copyfileobj(response, out)

    with tarfile.open(archive_path, "r:gz") as tar:
        _safe_extract_tarball(tar, base_dir)

    archive_path.unlink(missing_ok=True)
    sentinel.write_text(version, encoding="utf-8")
    return base_dir


def _ensure_configured(
    runner_dir: Path,
    *,
    repo: str,
    runner_name: str,
    labels: str,
    work_dir: str,
    replace: bool,
) -> None:
    marker = runner_dir / ".runner"
    if marker.exists() and not replace:
        return

    proc = _run(
        ["gh", "api", f"repos/{repo}/actions/runners/registration-token", "-X", "POST"],
        capture_output=True,
    )
    token = json.loads(proc.stdout)["token"]
    command = [
        str(runner_dir / "config.sh"),
        "--unattended",
        "--url",
        f"https://github.com/{repo}",
        "--token",
        token,
        "--name",
        runner_name,
        "--labels",
        labels,
        "--work",
        work_dir,
        "--replace",
    ]
    _run(command, cwd=runner_dir, check=True)


def _write_launch_agent(plist_path: Path, runner_dir: Path) -> None:
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": "com.resume.github-runner",
        "ProgramArguments": [str(runner_dir / "run.sh")],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(runner_dir),
        "StandardOutPath": str(DEFAULT_LOG_DIR / "github-runner.out.log"),
        "StandardErrorPath": str(DEFAULT_LOG_DIR / "github-runner.err.log"),
    }
    with plist_path.open("wb") as fh:
        plistlib.dump(payload, fh)


def _load_launch_agent(plist_path: Path) -> None:
    uid = os.getuid()
    label = "com.resume.github-runner"
    _run(["launchctl", "bootout", f"gui/{uid}", label], check=False)
    _run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)])
    _run(["launchctl", "enable", f"gui/{uid}/{label}"], check=False)
    _run(["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"], check=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repository slug.")
    parser.add_argument(
        "--runner-name",
        default=DEFAULT_RUNNER_NAME,
        help="Self-hosted runner name visible in GitHub.",
    )
    parser.add_argument(
        "--labels",
        default=DEFAULT_LABELS,
        help="Comma-separated runner labels.",
    )
    parser.add_argument(
        "--base-dir",
        default=str(DEFAULT_BASE_DIR),
        help="Directory where the runner will be installed.",
    )
    parser.add_argument(
        "--plist",
        default=str(DEFAULT_PLIST),
        help="LaunchAgent plist path.",
    )
    parser.add_argument(
        "--work-dir",
        default=DEFAULT_WORK_DIR,
        help="Runner work directory relative to base-dir.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Force runner reconfiguration even if it already exists.",
    )
    parser.add_argument(
        "--skip-launch",
        action="store_true",
        help="Configure the runner without loading the LaunchAgent.",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    if platform.system().lower() != "darwin":
        print("ERROR: this bootstrapper is only supported on macOS.", file=sys.stderr)
        return 2

    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    runner_dir = Path(args.base_dir).expanduser().resolve()
    plist_path = Path(args.plist).expanduser().resolve()

    try:
        runner_dir = _ensure_runner_archive(runner_dir)
        _ensure_configured(
            runner_dir,
            repo=args.repo,
            runner_name=args.runner_name,
            labels=args.labels,
            work_dir=args.work_dir,
            replace=args.replace,
        )
        _write_launch_agent(plist_path, runner_dir)
        if not args.skip_launch:
            _load_launch_agent(plist_path)
        print(f"Runner installed at: {runner_dir}")
        print(f"LaunchAgent plist: {plist_path}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
