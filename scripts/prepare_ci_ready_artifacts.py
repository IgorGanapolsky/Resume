#!/usr/bin/env python3
"""Prepare CI-submit artifacts for adapter-backed technical roles.

This step fills missing/low-fit resume and cover-letter artifacts for rows that
can realistically be submitted by CI (supported adapter + technical role +
sufficient remote likelihood). It does not submit applications.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
TRACKER_CSV = ROOT / "applications" / "job_applications" / "application_tracker.csv"
DEFAULT_REPORT = (
    ROOT / "applications" / "job_applications" / "ci_prepare_artifacts_report.json"
)

FDE_REQUIRED_TOKENS = (
    "forward-deployed ai/software engineer",
    "forward-deployed competencies",
    "customer-facing delivery",
    "integration engineering",
)


def _load_script_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def _today_iso() -> str:
    return dt.date.today().isoformat()


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


def _is_technical(role: str, ci_mod: Any) -> bool:
    return bool(ci_mod.TECH_ROLE_RE.search(role)) and not (
        ci_mod.NON_TECH_ROLE_RE.search(role) and not ci_mod.TECH_ROLE_RE.search(role)
    )


def _needs_fde_refresh(html_text: str) -> bool:
    hay = (html_text or "").lower()
    return any(token not in hay for token in FDE_REQUIRED_TOKENS)


def _ensure_python_signal(html_text: str) -> str:
    if "python" in html_text.lower():
        return html_text
    insert = (
        "\n<p><strong>Python</strong>: Production API/integration services and "
        "automation workflows.</p>\n"
    )
    marker = "</body>"
    idx = html_text.lower().rfind(marker)
    if idx == -1:
        return html_text + insert
    return html_text[:idx] + insert + html_text[idx:]


def _prepare_row(
    *,
    row: Dict[str, str],
    ci_mod: Any,
    ralph_mod: Any,
    adapters: Sequence[Any],
    fit_threshold: int,
    remote_min_score: int,
) -> Dict[str, Any]:
    company = str(row.get("Company", "")).strip()
    role = str(row.get("Role", "")).strip()
    url = str(row.get("Career Page URL", "")).strip()
    status = str(row.get("Status", "")).strip()
    result: Dict[str, Any] = {
        "company": company,
        "role": role,
        "url": url,
        "status_before": status,
        "prepared": False,
        "candidate": False,
        "assessment_before": {},
        "assessment_after": {},
        "artifact_updates": [],
        "skip_reason": "",
    }

    if not (ci_mod._is_draft_status(status) or ci_mod._is_ready_status(status)):
        result["skip_reason"] = "status_not_queueable"
        return result

    if not company or not role or not url:
        result["skip_reason"] = "missing_company_role_or_url"
        return result

    adapter = ci_mod._find_adapter(url, adapters)
    if adapter is None:
        result["skip_reason"] = "unsupported_site_for_ci_submit"
        return result

    if not _is_technical(role, ci_mod):
        result["skip_reason"] = "non_technical_role"
        return result

    job_path = ci_mod._resolve_job_capture(company, role)
    job_text = ci_mod._read_text(job_path)
    _, remote_score, _ = ci_mod._infer_remote_profile(row, job_text=job_text)
    if remote_score < remote_min_score:
        result["skip_reason"] = (
            f"remote_likelihood_below_threshold:{remote_score}<{remote_min_score}"
        )
        return result

    result["candidate"] = True
    before = ci_mod._assess_queue_gate(
        row,
        fit_threshold=fit_threshold,
        remote_min_score=remote_min_score,
        adapters=adapters,
    )
    result["assessment_before"] = {
        "eligible": before.eligible,
        "score": before.score,
        "reasons": before.reasons,
        "submission_lane": before.submission_lane,
        "remote_score": before.remote_score,
    }
    if before.eligible:
        result["skip_reason"] = "already_gate_eligible"
        return result

    company_slug = ci_mod._slug(company)
    role_slug = ci_mod._slug(role)[:64]
    today = _today_iso()

    resumes_dir = ROOT / "applications" / company_slug / "tailored_resumes"
    covers_dir = ROOT / "applications" / company_slug / "cover_letters"
    resumes_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)

    resume_html_path = ci_mod._resolve_resume_html(company, role)
    if resume_html_path is None:
        resume_html_path = resumes_dir / f"{today}_{company_slug}_{role_slug}.html"
    cover_path = ci_mod._resolve_cover_letter(company, role)
    if cover_path is None:
        cover_path = covers_dir / f"{today}_{company_slug}_{role_slug}.md"

    track, signals = ci_mod._role_track_and_signals(
        role,
        str(row.get("Tags", "")).strip(),
        str(row.get("Notes", "")).strip(),
        job_text,
    )
    python_required = "python" in signals

    base_resume = Path(ralph_mod.BASE_RESUME)
    if not base_resume.exists():
        raise RuntimeError(f"Base resume missing: {base_resume}")
    base_html = base_resume.read_text(encoding="utf-8")
    profile = ralph_mod.RoleProfile(
        track="fde" if track == "fde" else "general",
        score=0,
        signals=signals,
        is_relevant=True,
    )
    rendered_html = ralph_mod.tailor_resume_html(base_html, profile)
    if python_required:
        rendered_html = _ensure_python_signal(rendered_html)

    existing_html = ""
    if resume_html_path.exists():
        existing_html = resume_html_path.read_text(encoding="utf-8", errors="replace")

    should_write_html = (not resume_html_path.exists()) or (
        track == "fde" and _needs_fde_refresh(existing_html)
    )
    if python_required and "python" not in existing_html.lower():
        should_write_html = True
    if should_write_html:
        resume_html_path.write_text(rendered_html, encoding="utf-8")
        result["artifact_updates"].append(f"resume_html:{resume_html_path}")

    resume_docx = ci_mod._resolve_resume(company, role)
    if resume_docx is None or resume_docx.suffix.lower() not in {".docx", ".pdf"}:
        created = ci_mod._create_docx_from_html(resume_html_path)
        if created is not None:
            result["artifact_updates"].append(f"resume_docx:{created}")

    if not cover_path.exists():
        job_payload = {
            "company": company,
            "title": role,
            "location": str(row.get("Location", "")).strip(),
            "salary": str(row.get("Salary Range", "")).strip(),
            "job_type": "",
            "source": "tracker",
            "tags": str(row.get("Tags", "")).strip(),
            "description": job_text,
            "url": url,
        }
        cover_path.write_text(
            ralph_mod.build_cover_letter(job_payload, profile),
            encoding="utf-8",
        )
        result["artifact_updates"].append(f"cover_letter:{cover_path}")

    after = ci_mod._assess_queue_gate(
        row,
        fit_threshold=fit_threshold,
        remote_min_score=remote_min_score,
        adapters=adapters,
    )
    row["Remote Policy"] = after.remote_policy
    row["Remote Likelihood Score"] = str(after.remote_score)
    row["Remote Evidence"] = ";".join(after.remote_evidence)
    row["Submission Lane"] = after.submission_lane

    result["assessment_after"] = {
        "eligible": after.eligible,
        "score": after.score,
        "reasons": after.reasons,
        "submission_lane": after.submission_lane,
        "remote_score": after.remote_score,
    }
    result["prepared"] = bool(result["artifact_updates"])
    if not result["prepared"]:
        result["skip_reason"] = "no_artifact_changes_required"
    return result


def run_prepare(
    *,
    tracker_csv: Path,
    report_path: Path,
    max_jobs: int,
    fit_threshold: int,
    remote_min_score: int,
) -> int:
    ci_mod = _load_script_module("ci_submit_pipeline_prepare_mod", ROOT / "scripts" / "ci_submit_pipeline.py")
    ralph_mod = _load_script_module("ralph_loop_prepare_mod", ROOT / "scripts" / "ralph_loop_ci.py")

    fields, rows = _read_tracker(tracker_csv)
    fields = ci_mod._ensure_tracker_fields(fields, rows, ci_mod.TRACKER_REMOTE_FIELDS)
    adapters = [ci_mod.AshbyAdapter(), ci_mod.GreenhouseAdapter(), ci_mod.LeverAdapter()]

    processed = 0
    failures = 0
    artifact_updates = 0
    became_eligible = 0
    details: List[Dict[str, Any]] = []

    for row in rows:
        if processed >= max(0, max_jobs):
            break
        status = str(row.get("Status", "")).strip()
        if not (ci_mod._is_draft_status(status) or ci_mod._is_ready_status(status)):
            continue

        try:
            row_result = _prepare_row(
                row=row,
                ci_mod=ci_mod,
                ralph_mod=ralph_mod,
                adapters=adapters,
                fit_threshold=fit_threshold,
                remote_min_score=remote_min_score,
            )
        except Exception as exc:
            row_result = {
                "company": str(row.get("Company", "")).strip(),
                "role": str(row.get("Role", "")).strip(),
                "url": str(row.get("Career Page URL", "")).strip(),
                "prepared": False,
                "candidate": True,
                "skip_reason": "",
                "error": str(exc),
            }
            failures += 1

        details.append(row_result)
        processed += 1
        artifact_updates += len(row_result.get("artifact_updates", []))
        assessment_after = row_result.get("assessment_after") or {}
        if bool(assessment_after.get("eligible")):
            became_eligible += 1

    _write_tracker(tracker_csv, fields, rows)

    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "tracker_csv": str(tracker_csv),
        "max_jobs": max_jobs,
        "fit_threshold": fit_threshold,
        "remote_min_score": remote_min_score,
        "processed_rows": processed,
        "artifact_updates": artifact_updates,
        "became_gate_eligible": became_eligible,
        "failures": failures,
        "results": details,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

    print(
        "Prepare CI artifacts complete: "
        f"processed={processed} updates={artifact_updates} eligible={became_eligible} failures={failures}"
    )
    print(f"Report: {report_path}")
    return 1 if failures > 0 else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracker", default=str(TRACKER_CSV))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=15,
        help="Max queued tracker rows (Draft/ReadyToSubmit) to inspect.",
    )
    parser.add_argument(
        "--fit-threshold",
        type=int,
        default=70,
        help="Target fit threshold for queue gating.",
    )
    parser.add_argument(
        "--remote-min-score",
        type=int,
        default=50,
        help="Target remote-likelihood threshold for queue gating.",
    )
    args = parser.parse_args()

    return run_prepare(
        tracker_csv=Path(args.tracker),
        report_path=Path(args.report),
        max_jobs=max(0, args.max_jobs),
        fit_threshold=max(0, min(100, args.fit_threshold)),
        remote_min_score=max(0, min(100, args.remote_min_score)),
    )


if __name__ == "__main__":
    raise SystemExit(main())
