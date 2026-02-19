#!/usr/bin/env python3
"""CI submission pipeline with strict safety gates.

Design:
- Queue source: tracker rows with Status=ReadyToSubmit (or equivalent spelling).
- Secret-backed auth/profile: submit execution requires environment secrets.
- Site adapters: Ashby, Greenhouse, Lever (Playwright-based).
- Mandatory confirmation evidence: submission is counted only with screenshot.
- Tracker mutation rule: set Status=Applied only on verified success.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import traceback
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
TRACKER_CSV = ROOT / "applications" / "job_applications" / "application_tracker.csv"
DEFAULT_REPORT = ROOT / "applications" / "job_applications" / "ci_submit_report.json"
DEFAULT_READY_STATUS = "ReadyToSubmit"

READY_STATUS_KEYS = {
    "readytosubmit",
    "ready_to_submit",
    "ready to submit",
}
DRAFT_STATUS_KEYS = {
    "draft",
}

FDE_ROLE_RE = re.compile(
    r"(forward[- ]?deployed|customer engineer|solutions engineer|implementation engineer|"
    r"integration engineer)",
    re.IGNORECASE,
)
FDE_SIGNAL_RE = re.compile(
    r"(customer|stakeholder|executive|api|integration|end-to-end|ownership)",
    re.IGNORECASE,
)
PYTHON_RE = re.compile(r"\bpython\b", re.IGNORECASE)
VOICE_AUDIO_RE = re.compile(r"(voice|audio|speech|tts|asr|ivr)", re.IGNORECASE)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _norm_key(text: str) -> str:
    return re.sub(r"[\s_]+", "", (text or "").strip().lower())


def _today_iso() -> str:
    return dt.date.today().isoformat()


def _next_follow_up(days: int = 7) -> str:
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def _read_tracker(path: Path) -> tuple[List[str], List[Dict[str, str]]]:
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


@dataclass
class Profile:
    first_name: str
    last_name: str
    email: str
    phone: str
    location: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""


@dataclass
class AdapterAuth:
    storage_state: Optional[Dict[str, Any]] = None


@dataclass
class SubmitTask:
    row_index: int
    company: str
    role: str
    url: str
    resume_path: Path
    confirmation_path: Path


@dataclass
class SubmitResult:
    adapter: str
    verified: bool
    screenshot: Optional[Path]
    details: str


@dataclass
class QueueGateAssessment:
    eligible: bool
    score: int
    reasons: List[str]
    role_track: str
    signals: List[str]
    resume_path: Optional[Path]
    resume_html_path: Optional[Path]
    cover_path: Optional[Path]


class SiteAdapter:
    name = "base"

    def matches(self, url: str) -> bool:
        raise NotImplementedError

    def submit(
        self, task: SubmitTask, profile: Profile, auth: AdapterAuth
    ) -> SubmitResult:
        raise NotImplementedError


class PlaywrightFormAdapter(SiteAdapter):
    host_patterns: Sequence[re.Pattern[str]] = ()
    submit_button_patterns: Sequence[str] = ()
    success_text_patterns: Sequence[str] = ()

    def matches(self, url: str) -> bool:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        return any(p.search(host) for p in self.host_patterns)

    def submit(
        self, task: SubmitTask, profile: Profile, auth: AdapterAuth
    ) -> SubmitResult:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # type: ignore
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception as e:
            return SubmitResult(
                adapter=self.name,
                verified=False,
                screenshot=None,
                details=f"playwright_unavailable: {e}",
            )

        storage_state_arg: Optional[Any] = None
        if auth.storage_state is not None:
            storage_state_arg = auth.storage_state

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context_kwargs: Dict[str, Any] = {}
                if storage_state_arg is not None:
                    context_kwargs["storage_state"] = storage_state_arg
                context = browser.new_context(**context_kwargs)
                page = context.new_page()
                page.goto(task.url, wait_until="domcontentloaded", timeout=60000)

                # Best-effort generic fill by common labels/placeholders.
                self._fill_text(page, "First Name", profile.first_name)
                self._fill_text(page, "Last Name", profile.last_name)
                self._fill_text(
                    page, "Full Name", f"{profile.first_name} {profile.last_name}"
                )
                self._fill_text(
                    page, "Name", f"{profile.first_name} {profile.last_name}"
                )
                self._fill_text(page, "Email", profile.email)
                self._fill_text(page, "Phone", profile.phone)
                if profile.location:
                    self._fill_text(page, "Location", profile.location)
                    self._fill_text(page, "Current Location", profile.location)
                if profile.linkedin:
                    self._fill_text(page, "LinkedIn", profile.linkedin)
                if profile.github:
                    self._fill_text(page, "GitHub", profile.github)
                if profile.website:
                    self._fill_text(page, "Website", profile.website)

                # Resume upload is mandatory for this pipeline.
                file_input = page.locator("input[type='file']").first
                if file_input.count() < 1:
                    browser.close()
                    return SubmitResult(
                        adapter=self.name,
                        verified=False,
                        screenshot=None,
                        details="missing_file_input",
                    )
                file_input.set_input_files(str(task.resume_path))

                if not self._click_submit(page):
                    browser.close()
                    return SubmitResult(
                        adapter=self.name,
                        verified=False,
                        screenshot=None,
                        details="submit_button_not_found",
                    )

                confirmed = self._wait_for_confirmation(page)
                task.confirmation_path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(task.confirmation_path), full_page=True)

                browser.close()
                return SubmitResult(
                    adapter=self.name,
                    verified=confirmed and task.confirmation_path.exists(),
                    screenshot=task.confirmation_path
                    if task.confirmation_path.exists()
                    else None,
                    details="confirmed"
                    if confirmed
                    else "confirmation_text_not_detected",
                )
        except PlaywrightTimeoutError:
            return SubmitResult(
                adapter=self.name,
                verified=False,
                screenshot=None,
                details="timeout",
            )
        except Exception as e:
            return SubmitResult(
                adapter=self.name,
                verified=False,
                screenshot=None,
                details=f"exception: {e}",
            )

    def _fill_text(self, page: Any, key: str, value: str) -> None:
        if not value:
            return
        attempts = [
            lambda: page.get_by_label(key, exact=False).first.fill(value, timeout=1500),
            lambda: page.get_by_placeholder(key).first.fill(value, timeout=1500),
            lambda: page.locator(
                f"input[name*='{key.lower().replace(' ', '')}']"
            ).first.fill(value, timeout=1500),
        ]
        for fn in attempts:
            try:
                fn()
                return
            except Exception:
                continue

    def _click_submit(self, page: Any) -> bool:
        for pattern in self.submit_button_patterns:
            try:
                btn = page.get_by_role("button", name=re.compile(pattern, re.I)).first
                if btn.count() > 0:
                    btn.click(timeout=3000)
                    return True
            except Exception:
                continue
        try:
            submit_input = page.locator("input[type='submit']").first
            if submit_input.count() > 0:
                submit_input.click(timeout=3000)
                return True
        except Exception:
            pass
        return False

    def _wait_for_confirmation(self, page: Any) -> bool:
        try:
            page.wait_for_timeout(2000)
            text = page.inner_text("body")
        except Exception:
            return False
        normalized = (text or "").lower()
        return any(re.search(p, normalized, re.I) for p in self.success_text_patterns)


class AshbyAdapter(PlaywrightFormAdapter):
    name = "ashby"
    host_patterns = (re.compile(r"ashbyhq\.com"),)
    submit_button_patterns = (
        r"submit application",
        r"apply",
        r"submit",
    )
    success_text_patterns = (
        r"thank you for applying",
        r"application was successfully submitted",
        r"we'll be in touch",
    )


class GreenhouseAdapter(PlaywrightFormAdapter):
    name = "greenhouse"
    host_patterns = (re.compile(r"greenhouse\.io"),)
    submit_button_patterns = (r"submit application", r"apply", r"submit")
    success_text_patterns = (
        r"thank you for applying",
        r"application has been received",
        r"your application",
    )


class LeverAdapter(PlaywrightFormAdapter):
    name = "lever"
    host_patterns = (re.compile(r"lever\.co"),)
    submit_button_patterns = (r"submit application", r"apply", r"submit")
    success_text_patterns = (
        r"thank you",
        r"application has been submitted",
        r"we'll be in touch",
    )


def _resolve_resume(company: str, role: str) -> Optional[Path]:
    company_slug = _slug(company)
    role_slug = _slug(role)
    base = ROOT / "applications" / company_slug / "tailored_resumes"
    if not base.exists():
        return None

    docx = sorted(base.glob("*.docx"), key=lambda p: p.stat().st_mtime, reverse=True)
    pdf = sorted(base.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)

    for candidates in (docx, pdf):
        for p in candidates:
            stem = p.stem.lower()
            if role_slug and role_slug in stem:
                return p

    if docx:
        return docx[0]
    if pdf:
        return pdf[0]
    return None


def _select_best_artifact(paths: Sequence[Path], role_slug: str) -> Optional[Path]:
    if not paths:
        return None
    sorted_paths = sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)
    for p in sorted_paths:
        if role_slug and role_slug in p.stem.lower():
            return p
    return sorted_paths[0]


def _resolve_resume_html(company: str, role: str) -> Optional[Path]:
    company_slug = _slug(company)
    role_slug = _slug(role)
    base = ROOT / "applications" / company_slug / "tailored_resumes"
    if not base.exists():
        return None
    return _select_best_artifact(list(base.glob("*.html")), role_slug)


def _resolve_cover_letter(company: str, role: str) -> Optional[Path]:
    company_slug = _slug(company)
    role_slug = _slug(role)
    base = ROOT / "applications" / company_slug / "cover_letters"
    if not base.exists():
        return None
    candidates = list(base.glob("*.md")) + list(base.glob("*.txt"))
    return _select_best_artifact(candidates, role_slug)


def _resolve_job_capture(company: str, role: str) -> Optional[Path]:
    company_slug = _slug(company)
    role_slug = _slug(role)
    base = ROOT / "applications" / company_slug / "jobs"
    if not base.exists():
        return None
    return _select_best_artifact(list(base.glob("*.md")), role_slug)


def _read_text(path: Optional[Path]) -> str:
    if path is None or not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _role_track_and_signals(
    role: str, tags: str, notes: str, job_text: str
) -> tuple[str, List[str]]:
    hay = " ".join([role, tags, notes, job_text])
    signals: List[str] = []
    if FDE_ROLE_RE.search(hay):
        signals.append("fde-role")
    if FDE_SIGNAL_RE.search(hay):
        signals.append("customer-integration")
    if PYTHON_RE.search(hay):
        signals.append("python")
    if VOICE_AUDIO_RE.search(hay):
        signals.append("voice-audio")
    track = (
        "fde"
        if ("fde-role" in signals or "customer-integration" in signals)
        else "general"
    )
    return track, sorted(set(signals))


def _assess_queue_gate(row: Dict[str, str], fit_threshold: int) -> QueueGateAssessment:
    company = str(row.get("Company", "")).strip()
    role = str(row.get("Role", "")).strip()
    tags = str(row.get("Tags", "")).strip()
    notes = str(row.get("Notes", "")).strip()

    resume_path = _resolve_resume(company, role)
    resume_html_path = _resolve_resume_html(company, role)
    cover_path = _resolve_cover_letter(company, role)
    job_path = _resolve_job_capture(company, role)

    reasons: List[str] = []
    if resume_path is None:
        reasons.append("missing_resume_docx_or_pdf")
    if resume_html_path is None:
        reasons.append("missing_tailored_resume_html")
    if cover_path is None:
        reasons.append("missing_cover_letter")

    job_text = _read_text(job_path)
    resume_html_text = _read_text(resume_html_path).lower()
    track, signals = _role_track_and_signals(role, tags, notes, job_text)

    score = 0
    if track == "fde":
        score += 20
        if "forward-deployed ai/software engineer" in resume_html_text:
            score += 20
        else:
            reasons.append("missing_fde_headline")
        if "forward-deployed competencies" in resume_html_text:
            score += 15
        else:
            reasons.append("missing_fde_competencies_block")
        if "customer-facing delivery" in resume_html_text:
            score += 15
        else:
            reasons.append("missing_customer_facing_signal")
        if (
            "integration engineering" in resume_html_text
            or "api gateways" in resume_html_text
        ):
            score += 15
        else:
            reasons.append("missing_api_integration_signal")
        if (
            "<strong>35%</strong>" in resume_html_text
            or "<strong>40%</strong>" in resume_html_text
        ):
            score += 5
    else:
        if resume_html_text:
            score += 40
        if "summary" in resume_html_text:
            score += 15
        if "professional experience" in resume_html_text:
            score += 15

    python_required = "python" in signals
    if python_required and PYTHON_RE.search(resume_html_text):
        score += 10
    if python_required and not PYTHON_RE.search(resume_html_text):
        reasons.append("python_requested_not_explicit_in_resume")

    voice_required = "voice-audio" in signals
    if voice_required and VOICE_AUDIO_RE.search(resume_html_text):
        score += 5

    fit_ok = score >= fit_threshold
    if not fit_ok:
        reasons.append(f"fit_score_below_threshold:{score}<{fit_threshold}")

    eligible = fit_ok and all(
        reason
        not in {
            "missing_resume_docx_or_pdf",
            "missing_tailored_resume_html",
            "missing_cover_letter",
        }
        for reason in reasons
    )
    return QueueGateAssessment(
        eligible=eligible,
        score=score,
        reasons=sorted(set(reasons)),
        role_track=track,
        signals=signals,
        resume_path=resume_path,
        resume_html_path=resume_html_path,
        cover_path=cover_path,
    )


def _is_draft_status(status: str) -> bool:
    return _norm_key(status) in DRAFT_STATUS_KEYS


def _build_confirmation_path(company: str, role: str) -> Path:
    today = _today_iso()
    company_slug = _slug(company)
    role_slug = _slug(role)[:64]
    return (
        ROOT
        / "applications"
        / company_slug
        / "submissions"
        / f"{today}_{company_slug}_{role_slug}_ci_confirmation.png"
    )


def _load_profile_from_env(env_name: str) -> Optional[Profile]:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    required = ("first_name", "last_name", "email", "phone")
    if any(not str(payload.get(k, "")).strip() for k in required):
        return None

    return Profile(
        first_name=str(payload.get("first_name", "")).strip(),
        last_name=str(payload.get("last_name", "")).strip(),
        email=str(payload.get("email", "")).strip(),
        phone=str(payload.get("phone", "")).strip(),
        location=str(payload.get("location", "")).strip(),
        linkedin=str(payload.get("linkedin", "")).strip(),
        github=str(payload.get("github", "")).strip(),
        website=str(payload.get("website", "")).strip(),
    )


def _load_auth_by_adapter(env_name: str) -> Dict[str, AdapterAuth]:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}

    out: Dict[str, AdapterAuth] = {}
    for name, item in payload.items():
        if not isinstance(name, str) or not isinstance(item, dict):
            continue
        storage = item.get("storage_state")
        if isinstance(storage, dict):
            out[name] = AdapterAuth(storage_state=storage)
        else:
            out[name] = AdapterAuth(storage_state=None)
    return out


def _append_note(existing: str, note: str) -> str:
    base = (existing or "").strip()
    if note in base:
        return base
    if not base:
        return note
    return f"{base}\n{note}"


def _is_ready_status(status: str) -> bool:
    return _norm_key(status) in READY_STATUS_KEYS


def _find_adapter(url: str, adapters: Sequence[SiteAdapter]) -> Optional[SiteAdapter]:
    for adapter in adapters:
        if adapter.matches(url):
            return adapter
    return None


def _validate_row(row: Dict[str, str]) -> List[str]:
    errs = []
    if not str(row.get("Company", "")).strip():
        errs.append("missing_company")
    if not str(row.get("Role", "")).strip():
        errs.append("missing_role")
    if not str(row.get("Career Page URL", "")).strip():
        errs.append("missing_url")
    return errs


def run_pipeline(
    *,
    tracker_csv: Path,
    report_path: Path,
    dry_run: bool,
    queue_only: bool,
    max_jobs: int,
    fail_on_error: bool,
    fit_threshold: int = 70,
    require_secret_auth: bool = True,
    auto_promote_ready: bool = True,
    profile_env: str = "CI_SUBMIT_PROFILE_JSON",
    auth_env: str = "CI_SUBMIT_AUTH_JSON",
    adapters: Optional[Sequence[SiteAdapter]] = None,
) -> int:
    fields, rows = _read_tracker(tracker_csv)
    adapters = list(adapters or [AshbyAdapter(), GreenhouseAdapter(), LeverAdapter()])

    profile = _load_profile_from_env(profile_env)
    auth_map = _load_auth_by_adapter(auth_env)

    if not dry_run and not queue_only and require_secret_auth:
        if profile is None:
            print(f"ERROR: missing/invalid secret profile in ${profile_env}.")
            return 2
        if not auth_map:
            print(f"ERROR: missing/invalid secret auth map in ${auth_env}.")
            return 2
    elif profile is None:
        # Dry-run may proceed without secrets, but keep a sane placeholder profile.
        profile = Profile(
            first_name="Dry",
            last_name="Run",
            email="dry.run@example.com",
            phone="0000000000",
        )

    can_mutate_tracker = (not dry_run) or queue_only
    queue_promoted_count = 0
    queue_demoted_count = 0
    queue_audit: List[Dict[str, Any]] = []

    if auto_promote_ready:
        for idx, row in enumerate(rows):
            status_raw = str(row.get("Status", ""))
            if not (_is_draft_status(status_raw) or _is_ready_status(status_raw)):
                continue
            assessment = _assess_queue_gate(row, fit_threshold=fit_threshold)
            audit_item = {
                "row_index": idx,
                "company": str(row.get("Company", "")).strip(),
                "role": str(row.get("Role", "")).strip(),
                "status_before": status_raw.strip(),
                "role_track": assessment.role_track,
                "signals": assessment.signals,
                "fit_score": assessment.score,
                "eligible_for_ready": assessment.eligible,
                "reasons": assessment.reasons,
            }

            if _is_draft_status(status_raw) and assessment.eligible:
                queue_promoted_count += 1
                if can_mutate_tracker:
                    row["Status"] = DEFAULT_READY_STATUS
                    row["Notes"] = _append_note(
                        str(row.get("Notes", "")),
                        (
                            f"Queue gate passed on {_today_iso()} "
                            f"(fit={assessment.score}/{fit_threshold}, track={assessment.role_track})."
                        ),
                    )
            elif _is_ready_status(status_raw) and not assessment.eligible:
                queue_demoted_count += 1
                if can_mutate_tracker:
                    row["Status"] = "Draft"
                    row["Notes"] = _append_note(
                        str(row.get("Notes", "")),
                        (
                            f"Queue gate demoted on {_today_iso()} "
                            f"(fit={assessment.score}/{fit_threshold}; reasons={','.join(assessment.reasons)})."
                        ),
                    )
            queue_audit.append(audit_item)

    ready_indices = [
        i for i, row in enumerate(rows) if _is_ready_status(str(row.get("Status", "")))
    ][: max(0, max_jobs)]

    report: Dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dry_run": dry_run,
        "queue_only": queue_only,
        "max_jobs": max_jobs,
        "fit_threshold": fit_threshold,
        "tracker_csv": str(tracker_csv),
        "queue_promoted_count": queue_promoted_count,
        "queue_demoted_count": queue_demoted_count,
        "queue_audit": queue_audit,
        "ready_rows_total": len(ready_indices),
        "results": [],
    }

    applied_count = 0
    failed_count = 0

    if queue_only:
        report["applied_count"] = 0
        report["failed_count"] = 0
        report["changed"] = bool(
            can_mutate_tracker and (queue_promoted_count or queue_demoted_count)
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8"
        )
        if report["changed"]:
            _write_tracker(tracker_csv, fields, rows)
        print(
            "Queue gate processed: "
            f"promoted={queue_promoted_count} demoted={queue_demoted_count} "
            f"ready_now={len(ready_indices)}"
        )
        print(f"Report: {report_path}")
        if fail_on_error and queue_demoted_count > 0:
            return 1
        return 0

    for row_idx in ready_indices:
        row = rows[row_idx]
        company = str(row.get("Company", "")).strip()
        role = str(row.get("Role", "")).strip()
        url = str(row.get("Career Page URL", "")).strip()
        row_result: Dict[str, Any] = {
            "row_index": row_idx,
            "company": company,
            "role": role,
            "url": url,
            "status_before": str(row.get("Status", "")).strip(),
            "mode": "dry_run" if dry_run else "execute",
        }

        row_errors = _validate_row(row)
        assessment = _assess_queue_gate(row, fit_threshold=fit_threshold)
        resume_path = assessment.resume_path
        if not assessment.eligible:
            row_errors.extend(assessment.reasons)
            if can_mutate_tracker:
                row["Status"] = "Draft"
                row["Notes"] = _append_note(
                    str(row.get("Notes", "")),
                    (
                        f"Submission blocked by queue gate on {_today_iso()} "
                        f"(fit={assessment.score}/{fit_threshold}; reasons={','.join(assessment.reasons)})."
                    ),
                )

        adapter = _find_adapter(url, adapters)
        if adapter is None:
            row_errors.append("unsupported_site")

        if row_errors:
            row_result["result"] = "skipped"
            row_result["errors"] = row_errors
            report["results"].append(row_result)
            failed_count += 1
            continue

        assert resume_path is not None
        assert adapter is not None
        confirmation_path = _build_confirmation_path(company, role)
        task = SubmitTask(
            row_index=row_idx,
            company=company,
            role=role,
            url=url,
            resume_path=resume_path,
            confirmation_path=confirmation_path,
        )
        row_result["adapter"] = adapter.name
        row_result["resume_path"] = str(resume_path)
        row_result["confirmation_path"] = str(confirmation_path)

        if dry_run:
            row_result["result"] = "would_submit"
            report["results"].append(row_result)
            continue

        auth = auth_map.get(adapter.name, AdapterAuth())
        if require_secret_auth and adapter.name not in auth_map:
            row_result["result"] = "failed"
            row_result["errors"] = [f"missing_auth_for_adapter:{adapter.name}"]
            report["results"].append(row_result)
            failed_count += 1
            continue

        result = adapter.submit(task, profile, auth)
        row_result["adapter_details"] = result.details
        row_result["verified"] = result.verified
        row_result["screenshot"] = str(result.screenshot) if result.screenshot else None

        screenshot_ok = (
            result.screenshot is not None
            and result.screenshot.exists()
            and result.screenshot.stat().st_size > 0
        )
        if result.verified and screenshot_ok:
            row["Status"] = "Applied"
            row["Date Applied"] = _today_iso()
            if not str(row.get("Follow Up Date", "")).strip():
                row["Follow Up Date"] = _next_follow_up(7)
            row["Notes"] = _append_note(
                str(row.get("Notes", "")),
                (
                    f"CI submit verified on {_today_iso()} via {adapter.name}. "
                    f"Confirmation: {result.screenshot}"
                ),
            )
            row_result["result"] = "applied"
            applied_count += 1
        else:
            row_result["result"] = "failed"
            row_result["errors"] = [
                "verification_failed",
                "missing_or_empty_confirmation_screenshot" if not screenshot_ok else "",
            ]
            failed_count += 1

        report["results"].append(row_result)

    report["applied_count"] = applied_count
    report["failed_count"] = failed_count
    report["changed"] = bool(
        can_mutate_tracker
        and (applied_count or queue_promoted_count or queue_demoted_count)
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8"
    )

    if report["changed"]:
        _write_tracker(tracker_csv, fields, rows)

    print(
        f"Queue processed: ready={len(ready_indices)} applied={applied_count} failed={failed_count} dry_run={dry_run}"
    )
    print(f"Report: {report_path}")

    if fail_on_error and failed_count > 0:
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tracker", default=str(TRACKER_CSV), help="Tracker CSV path")
    ap.add_argument(
        "--report", default=str(DEFAULT_REPORT), help="Report JSON output path"
    )
    ap.add_argument(
        "--max-jobs", type=int, default=5, help="Max queued rows to process"
    )
    ap.add_argument(
        "--queue-only",
        action="store_true",
        help=(
            "Run only queue gating/promotion (Draft -> ReadyToSubmit for high-fit rows), "
            "without any submission attempts."
        ),
    )
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Execute real submissions. Default behavior is dry-run.",
    )
    ap.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Return non-zero if any queue item fails/skips gates.",
    )
    ap.add_argument(
        "--fit-threshold",
        type=int,
        default=70,
        help="Minimum fit score required to enter/remain in ReadyToSubmit queue.",
    )
    ap.add_argument(
        "--profile-env",
        default="CI_SUBMIT_PROFILE_JSON",
        help="Env var containing submit profile JSON.",
    )
    ap.add_argument(
        "--auth-env",
        default="CI_SUBMIT_AUTH_JSON",
        help="Env var containing per-adapter auth JSON.",
    )
    args = ap.parse_args()
    if args.execute and args.queue_only:
        print("ERROR: --execute and --queue-only are mutually exclusive.")
        return 2

    try:
        return run_pipeline(
            tracker_csv=Path(args.tracker),
            report_path=Path(args.report),
            dry_run=not args.execute,
            queue_only=args.queue_only,
            max_jobs=args.max_jobs,
            fail_on_error=args.fail_on_error,
            fit_threshold=args.fit_threshold,
            require_secret_auth=True,
            profile_env=args.profile_env,
            auth_env=args.auth_env,
        )
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
