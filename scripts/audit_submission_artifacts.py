#!/usr/bin/env python3
"""Audit/backfill submitted resume and evidence paths in tracker rows."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
from pathlib import Path
from typing import Dict, List, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
TRACKER_CSV = ROOT / "applications" / "job_applications" / "application_tracker.csv"
DEFAULT_REPORT = (
    ROOT / "applications" / "job_applications" / "submission_artifact_audit_report.json"
)
SUBMISSION_FIELDS = (
    "Submitted Resume Path",
    "Submission Evidence Path",
    "Submission Verified At",
)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _norm_key(text: str) -> str:
    return re.sub(r"[\s_]+", "", (text or "").strip().lower())


def _is_applied(status: str) -> bool:
    return _norm_key(status) == "applied"


def _read_tracker(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    return fields, rows


def _write_tracker(
    path: Path, fields: Sequence[str], rows: Sequence[Dict[str, str]]
) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def _ensure_fields(
    fields: Sequence[str], rows: Sequence[Dict[str, str]], extras: Sequence[str]
) -> List[str]:
    out = list(fields)
    missing = [name for name in extras if name not in out]
    if not missing:
        return out
    out.extend(missing)
    for row in rows:
        for name in missing:
            row.setdefault(name, "")
    return out


def _path_from_string(raw: str) -> Path | None:
    text = (raw or "").strip()
    if not text:
        return None
    p = Path(text)
    if p.is_absolute():
        return p if p.exists() else None
    candidate = ROOT / text
    return candidate if candidate.exists() else None


def _path_in_notes(notes: str, suffix: str) -> Path | None:
    for match in re.findall(r"(applications/[^\s,;]+)", notes or ""):
        p = _path_from_string(match)
        if p is None:
            continue
        if p.suffix.lower() == suffix.lower():
            return p
    return None


def _infer_resume_path(company: str, role: str) -> Path | None:
    company_slug = _slug(company)
    role_slug = _slug(role)
    base = ROOT / "applications" / company_slug / "tailored_resumes"
    if not base.exists():
        return None
    candidates = sorted(
        [*base.glob("*.docx"), *base.glob("*.pdf")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in candidates:
        if role_slug and role_slug in p.stem.lower():
            return p
    return candidates[0] if candidates else None


def _infer_evidence_path(company: str, date_applied: str, notes: str) -> Path | None:
    evidence_from_notes = _path_in_notes(notes, ".png") or _path_in_notes(notes, ".pdf")
    if evidence_from_notes is not None:
        return evidence_from_notes

    company_slug = _slug(company)
    sub_dir = ROOT / "applications" / company_slug / "submissions"
    if not sub_dir.exists():
        return None
    date_prefix = (date_applied or "").strip()
    patterns = []
    if date_prefix:
        patterns.extend([f"{date_prefix}_*.png", f"{date_prefix}_*.pdf"])
    patterns.extend(["*_confirmation*.png", "*_submitted*.png", "*.png", "*.pdf"])
    for pattern in patterns:
        files = sorted(sub_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            return files[0]
    return None


def run_audit(
    *,
    tracker_csv: Path,
    report_path: Path,
    write: bool,
    fail_on_missing: bool,
) -> int:
    fields, rows = _read_tracker(tracker_csv)
    fields = _ensure_fields(fields, rows, SUBMISSION_FIELDS)

    applied_total = 0
    missing_rows: List[Dict[str, object]] = []
    fixed_count = 0

    for idx, row in enumerate(rows):
        if not _is_applied(str(row.get("Status", ""))):
            continue
        applied_total += 1
        company = str(row.get("Company", "")).strip()
        role = str(row.get("Role", "")).strip()
        notes = str(row.get("Notes", "")).strip()
        date_applied = str(row.get("Date Applied", "")).strip()

        resume_raw = str(row.get("Submitted Resume Path", "")).strip()
        evidence_raw = str(row.get("Submission Evidence Path", "")).strip()
        verified_raw = str(row.get("Submission Verified At", "")).strip()

        resume_path = _path_from_string(resume_raw)
        evidence_path = _path_from_string(evidence_raw)

        row_fixed = False
        if write and resume_path is None:
            inferred_resume = _infer_resume_path(company, role)
            if inferred_resume is not None:
                row["Submitted Resume Path"] = str(inferred_resume.relative_to(ROOT))
                resume_path = inferred_resume
                row_fixed = True
        if write and evidence_path is None:
            inferred_evidence = _infer_evidence_path(company, date_applied, notes)
            if inferred_evidence is not None:
                row["Submission Evidence Path"] = str(inferred_evidence.relative_to(ROOT))
                evidence_path = inferred_evidence
                row_fixed = True
        if write and row_fixed and not verified_raw:
            row["Submission Verified At"] = dt.datetime.now(dt.timezone.utc).isoformat()

        missing: List[str] = []
        if resume_path is None:
            missing.append("submitted_resume_path")
        if evidence_path is None:
            missing.append("submission_evidence_path")
        if not str(row.get("Submission Verified At", "")).strip():
            missing.append("submission_verified_at")

        if row_fixed:
            fixed_count += 1
        if missing:
            missing_rows.append(
                {
                    "row_index": idx,
                    "company": company,
                    "role": role,
                    "missing": missing,
                }
            )

    if write:
        _write_tracker(tracker_csv, fields, rows)

    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "tracker_csv": str(tracker_csv),
        "applied_total": applied_total,
        "fixed_count": fixed_count,
        "missing_count": len(missing_rows),
        "missing_rows": missing_rows,
        "write": write,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

    print(
        f"Submission artifact audit: applied={applied_total} "
        f"fixed={fixed_count} missing={len(missing_rows)} report={report_path}"
    )
    if fail_on_missing and missing_rows:
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tracker", default=str(TRACKER_CSV), help="Tracker CSV path")
    ap.add_argument("--report", default=str(DEFAULT_REPORT), help="Audit JSON report path")
    ap.add_argument(
        "--write",
        action="store_true",
        help="Write inferred artifact paths back into the tracker.",
    )
    ap.add_argument(
        "--fail-on-missing",
        action="store_true",
        help="Return non-zero when any Applied row is still missing required fields.",
    )
    args = ap.parse_args()
    return run_audit(
        tracker_csv=Path(args.tracker),
        report_path=Path(args.report),
        write=args.write,
        fail_on_missing=args.fail_on_missing,
    )


if __name__ == "__main__":
    raise SystemExit(main())

