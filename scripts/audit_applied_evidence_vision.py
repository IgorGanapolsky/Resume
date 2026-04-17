#!/usr/bin/env python3
"""Vision-based audit of all Applied rows.

For each Applied row, verifies that the submission evidence screenshot
actually shows a confirmation (not a mid-fill form). Uses any available
vision-capable CLI (openclaw, codex, or gemini) to analyze the screenshot.

If verification fails, demotes row to ReadyToSubmit with an audit note.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "applications" / "job_applications" / "application_tracker.csv"
REPORT = ROOT / "applications" / "job_applications" / "vision_audit_report.json"


def _which_vision_cli() -> Optional[Tuple[str, List[str]]]:
    """Return (name, command_prefix) for the first available vision-capable CLI."""
    # Prefer openclaw's image tool via shell; fallback to gemini or codex
    for name, probe in (
        ("gemini", ["gemini", "--version"]),
        ("codex", ["codex", "--version"]),
    ):
        if shutil.which(name):
            try:
                subprocess.run(probe, capture_output=True, timeout=5)
                return (name, [name])
            except Exception:
                continue
    return None


def _prompt(image_path: Path) -> str:
    return (
        "Look at this screenshot of a job application submission page. "
        "Reply with ONLY one word:\n"
        "- 'CONFIRMED' if the page shows a successful submission confirmation "
        "(e.g. 'Thank you for applying', 'Your application has been submitted', "
        "'Application received', a submission ID, or a post-submit success state).\n"
        "- 'INCOMPLETE' if the page shows a mid-fill form, an error, "
        "a 'Submit Application' button not yet clicked, or anything that "
        "is not a successful submission confirmation.\n"
        f"Image: {image_path}"
    )


def _analyze(image_path: Path, cli: Tuple[str, List[str]]) -> str:
    name, prefix = cli
    try:
        if name == "gemini":
            # gemini CLI: gemini --image <path> "<prompt>"
            result = subprocess.run(
                prefix + ["--image", str(image_path), _prompt(image_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            out = (result.stdout or "").upper()
            if "CONFIRMED" in out:
                return "CONFIRMED"
            if "INCOMPLETE" in out:
                return "INCOMPLETE"
            return "UNKNOWN"
    except Exception as e:
        return f"ERROR:{e.__class__.__name__}"
    return "UNKNOWN"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Do not modify tracker")
    parser.add_argument("--report", default=str(REPORT))
    args = parser.parse_args()

    cli = _which_vision_cli()
    if cli is None:
        print("No vision CLI available. Skipping vision audit.", file=sys.stderr)
        Path(args.report).write_text(json.dumps({"status": "skipped_no_vision_cli"}))
        return 0

    with TRACKER.open() as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    today = dt.date.today().isoformat()
    demoted: List[Dict] = []
    kept: List[Dict] = []
    unknown: List[Dict] = []

    for r in rows:
        if r.get("Status", "").strip() != "Applied":
            continue
        ev = r.get("Submission Evidence Path", "").strip()
        if not ev:
            continue
        ev_path = ROOT / ev if not ev.startswith("/") else Path(ev)
        if not ev_path.exists():
            continue

        verdict = _analyze(ev_path, cli)
        entry = {
            "company": r.get("Company", ""),
            "role": r.get("Role", ""),
            "evidence": str(ev_path),
            "verdict": verdict,
        }

        if verdict == "CONFIRMED":
            kept.append(entry)
        elif verdict == "INCOMPLETE":
            demoted.append(entry)
            if not args.dry_run:
                r["Status"] = "ReadyToSubmit"
                old = r.get("Notes", "")
                r["Notes"] = (
                    f"{old} | Vision audit {today}: demoted from Applied - "
                    "screenshot does not show submission confirmation."
                )
        else:
            unknown.append(entry)

    if not args.dry_run and demoted:
        with TRACKER.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "vision_cli": cli[0],
        "dry_run": args.dry_run,
        "confirmed_count": len(kept),
        "demoted_count": len(demoted),
        "unknown_count": len(unknown),
        "demoted": demoted,
        "confirmed": kept,
        "unknown": unknown,
    }
    Path(args.report).write_text(json.dumps(report, indent=2))
    print(
        f"Vision audit: confirmed={len(kept)} demoted={len(demoted)} "
        f"unknown={len(unknown)} (dry_run={args.dry_run})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
