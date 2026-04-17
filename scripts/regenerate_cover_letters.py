#!/usr/bin/env python3
"""Regenerate cover letters for tracker rows still in Draft/ReadyToSubmit.

Reuses scripts/ralph_loop_ci.py's classifier and the new humanized
build_cover_letter(). Pulls the job description from the captured JD
file when one exists so the JD-anchor quoting logic has something to
chew on.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "applications" / "job_applications" / "application_tracker.csv"
APPLICATIONS = ROOT / "applications"

_QUEUED_STATUSES = {"Draft", "ReadyToSubmit"}


def _load_ralph():
    spec = importlib.util.spec_from_file_location(
        "ralph_loop_ci", ROOT / "scripts" / "ralph_loop_ci.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ralph_loop_ci"] = mod
    spec.loader.exec_module(mod)
    return mod


def _find_latest_jd(company_slug: str, role_slug: str) -> Optional[Path]:
    jobs_dir = APPLICATIONS / company_slug / "jobs"
    if not jobs_dir.exists():
        return None
    matches = sorted(jobs_dir.glob(f"*_{company_slug}_{role_slug}_*.md"), reverse=True)
    if matches:
        return matches[0]
    # Fallback: any JD file with the role slug prefix if slug truncation differs.
    prefix_matches = sorted(jobs_dir.glob(f"*_{company_slug}_*.md"), reverse=True)
    for m in prefix_matches:
        if role_slug[:40] in m.name:
            return m
    return None


_HTML_BLOCK_RE = re.compile(r"```html\n(.*?)\n```", re.DOTALL)


def _extract_description(jd_path: Path) -> str:
    text = jd_path.read_text(encoding="utf-8", errors="replace")
    block = _HTML_BLOCK_RE.search(text)
    source = block.group(1) if block else text
    no_tags = re.sub(r"<[^>]+>", " ", source)
    collapsed = re.sub(r"\s+", " ", no_tags).strip()
    return collapsed


def _compose_job(
    row: Dict[str, str], ralph, company_slug: str, role_slug: str
) -> Dict[str, str]:
    jd_path = _find_latest_jd(company_slug, role_slug)
    description = _extract_description(jd_path) if jd_path else ""
    return {
        "company": row.get("Company", ""),
        "title": row.get("Role", ""),
        "location": row.get("Location", ""),
        "tags": row.get("Tags", ""),
        "url": row.get("Career Page URL", ""),
        "description": description,
    }


def _cover_stem(today_prefix: str, company_slug: str, role_slug: str) -> str:
    return f"{today_prefix}_{company_slug}_{role_slug}"


def _existing_cover_path(
    company_slug: str, role_slug: str
) -> Optional[Tuple[Path, str]]:
    cl_dir = APPLICATIONS / company_slug / "cover_letters"
    if not cl_dir.exists():
        return None
    matches = sorted(cl_dir.glob(f"*_{company_slug}_{role_slug}.md"), reverse=True)
    if matches:
        return matches[0], matches[0].stem
    # Role slug may have been truncated to 64 chars; try the first 40.
    prefix_matches = sorted(cl_dir.glob(f"*_{company_slug}_*.md"), reverse=True)
    for m in prefix_matches:
        if role_slug[:40] in m.name:
            return m, m.stem
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview companies/roles without writing files.",
    )
    ap.add_argument(
        "--report",
        type=Path,
        default=APPLICATIONS
        / "job_applications"
        / "cover_letter_regeneration_report.json",
    )
    ap.add_argument(
        "--sample-out",
        type=Path,
        default=None,
        help="Optional JSON path to dump the first 5 regenerated letters for review.",
    )
    args = ap.parse_args()

    ralph = _load_ralph()

    with TRACKER.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    regenerated: List[Dict[str, str]] = []
    skipped_no_cover: List[Dict[str, str]] = []
    samples: List[Dict[str, str]] = []

    for row in rows:
        status = (row.get("Status") or "").strip()
        if status not in _QUEUED_STATUSES:
            continue
        company = (row.get("Company") or "").strip()
        role = (row.get("Role") or "").strip()
        if not company or not role:
            continue
        company_slug = ralph._slug(company)
        role_slug = ralph._slug(role)[:64]
        existing = _existing_cover_path(company_slug, role_slug)
        if existing is None:
            skipped_no_cover.append({"company": company, "role": role})
            continue
        cover_path, stem = existing

        job = _compose_job(row, ralph, company_slug, role_slug)
        profile = ralph.classify_role(job)
        letter = ralph.build_cover_letter(job, profile)

        if not args.dry_run:
            cover_path.write_text(letter, encoding="utf-8")
        regenerated.append(
            {
                "company": company,
                "role": role,
                "path": str(cover_path.relative_to(ROOT)),
                "chars": len(letter),
                "track": profile.track,
            }
        )
        if len(samples) < 5:
            samples.append(
                {
                    "company": company,
                    "role": role,
                    "path": str(cover_path.relative_to(ROOT)),
                    "content": letter,
                }
            )

    report = {
        "regenerated": len(regenerated),
        "skipped_missing_cover_file": len(skipped_no_cover),
        "dry_run": args.dry_run,
        "entries": regenerated,
        "skipped": skipped_no_cover,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if args.sample_out:
        args.sample_out.parent.mkdir(parents=True, exist_ok=True)
        args.sample_out.write_text(json.dumps(samples, indent=2), encoding="utf-8")

    print(
        f"Regenerated: {len(regenerated)} | "
        f"Skipped (no existing cover): {len(skipped_no_cover)} | "
        f"Dry-run: {args.dry_run}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
