#!/usr/bin/env python3
"""CI submission pipeline with strict safety gates.

Design:
- Queue source: tracker rows with Status=ReadyToSubmit (or equivalent spelling).
- Profile/answers come from env JSON first, with fallback to
  applications/job_applications/application_answers.md.
- Per-adapter auth map is optional for public ATS adapters.
- Site adapters: Ashby, Greenhouse, Lever (Playwright-based).
- Mandatory confirmation evidence: submission is counted only with screenshot.
- Tracker mutation rule: set Status=Applied only on verified success.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import fcntl
import html
import json
import os
import re
import subprocess
import traceback
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from xml.sax.saxutils import escape

import agent_identity

try:
    from playwright_stealth import Stealth as _PlaywrightStealth
except ImportError:
    _PlaywrightStealth = None


ROOT = Path(__file__).resolve().parents[1]
TRACKER_CSV = ROOT / "applications" / "job_applications" / "application_tracker.csv"
DEFAULT_REPORT = ROOT / "applications" / "job_applications" / "ci_submit_report.json"
DEFAULT_ANSWERS_MD = (
    ROOT / "applications" / "job_applications" / "application_answers.md"
)
DEFAULT_READY_STATUS = "ReadyToSubmit"

READY_STATUS_KEYS = {
    "readytosubmit",
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
    r"(customer[- ]facing|customer engineering|customer engineers?|strategic customers|embedded with customer|"
    r"implementation partner|executive stakeholder|work directly with customers)",
    re.IGNORECASE,
)
INTEGRATION_SIGNAL_RE = re.compile(
    r"(api integration|integration-heavy|integrations|api|integration|implementation|end-to-end)",
    re.IGNORECASE,
)
PYTHON_RE = re.compile(r"\bpython\b", re.IGNORECASE)
VOICE_AUDIO_RE = re.compile(r"(voice|audio|speech|tts|asr|ivr)", re.IGNORECASE)
GENERIC_RESUME_PHRASES = (
    "why i may be a good fit",
    "added by ralph loop ci",
)
ROLE_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.-]{1,}")
ROLE_STOPWORDS = {
    "and",
    "or",
    "for",
    "with",
    "the",
    "a",
    "an",
    "to",
    "of",
    "sr",
    "senior",
    "staff",
    "principal",
    "lead",
    "engineer",
    "engineering",
    "developer",
    "software",
    "full",
    "stack",
    "remote",
}
NON_TECH_ROLE_RE = re.compile(
    r"(account executive|sales|recruiter|attorney|counsel|office assistant|marketing|"
    r"content manager|revenue operations|client support|customer support specialist|"
    r"operations manager|community manager|people business partner|customer success|"
    r"talent\b|hr\b|human resources|legal\b|finance\b|designer\b|"
    r"representative\b|specialist\b|business partner)",
    re.IGNORECASE,
)
TECH_ROLE_RE = re.compile(
    r"(engineer|developer|devops|sre|site reliability|architect|ml|ai|data engineer|"
    r"backend|frontend|full[- ]?stack|platform|infrastructure|ios|android|qa|"
    r"scientist|researcher)",
    re.IGNORECASE,
)
REMOTE_POSITIVE_RE = re.compile(
    r"(remote|work from home|wfh|distributed|anywhere|home[- ]?based)",
    re.IGNORECASE,
)
REMOTE_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)
REMOTE_NEGATIVE_RE = re.compile(
    r"(on[- ]?site|onsite|in[- ]?office|office[- ]?based|relocation required)",
    re.IGNORECASE,
)
REMOTE_US_ONLY_RE = re.compile(
    r"(us only|usa only|united states only|remote.*us)",
    re.IGNORECASE,
)
TRACKER_REMOTE_FIELDS = (
    "Remote Policy",
    "Remote Likelihood Score",
    "Remote Evidence",
    "Submission Lane",
)
TRACKER_SUBMISSION_FIELDS = (
    "Submitted Resume Path",
    "Submission Evidence Path",
    "Submission Verified At",
)
QUARANTINED_STATUS = "Quarantined"
AGGREGATOR_HOST_RE = re.compile(r"(?:^|\.)(remoteok\.com|remotive\.com)$", re.I)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _norm_key(text: str) -> str:
    return re.sub(r"[\s_]+", "", (text or "").strip().lower())


def _host_matches_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _today_iso() -> str:
    return dt.date.today().isoformat()


def _next_follow_up(days: int = 7) -> str:
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def _sanitize_tracker_row(row: Dict[str, Any]) -> Dict[str, str]:
    cleaned: Dict[str, str] = {}
    for key, value in row.items():
        if key is None:
            continue
        cleaned[str(key)] = "" if value is None else str(value)
    return cleaned


def _read_tracker(path: Path) -> tuple[List[str], List[Dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [_sanitize_tracker_row(row) for row in reader]
        fields = list(reader.fieldnames or [])
    return fields, rows


def _write_tracker(
    path: Path, fields: Sequence[str], rows: Sequence[Dict[str, str]]
) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=list(fields), extrasaction="ignore", restval=""
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(_sanitize_tracker_row(row))


def _ensure_tracker_fields(
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


def _write_tracker_row_atomic(
    path: Path,
    fields: Sequence[str],
    row_index: int,
    updated_row: Dict[str, str],
) -> None:
    """Atomically update a single row in the tracker CSV using file locking.

    Acquires an exclusive lock (``fcntl.LOCK_EX``) on the CSV before reading,
    mutating the target row in-memory, and rewriting the file.  This makes
    concurrent calls from multiple threads safe — each writer serialises
    through the lock.

    Parameters
    ----------
    path:
        Path to the tracker CSV file.
    fields:
        Ordered column names (must include any new columns already ensured).
    row_index:
        Zero-based index of the data row to update (header is not counted).
    updated_row:
        The full dict for the row that should replace the existing one.
    """
    lock_path = path.with_suffix(".csv.lock")
    lock_path.touch(exist_ok=True)
    with lock_path.open("r") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            # Read the current state of the file (another thread may have
            # written since we last read).
            current_fields, current_rows = _read_tracker(path)
            # Ensure fields are a superset — parallel threads may have added
            # columns we haven't seen yet.
            merged_fields = list(current_fields)
            for f in fields:
                if f not in merged_fields:
                    merged_fields.append(f)
            if row_index < len(current_rows):
                current_rows[row_index] = updated_row
            # Rewrite the entire file while still holding the lock.
            _write_tracker(path, merged_fields, current_rows)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


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
    current_company: str = ""


@dataclass
class AdapterAuth:
    storage_state: Optional[Dict[str, Any]] = None


@dataclass
class SubmitAnswers:
    work_authorization_us: bool
    require_sponsorship: bool
    role_interest: str
    eeo_default: str
    country: str = "United States"
    based_in_us_or_canada: bool = True
    inference_systems_experience: bool = True
    early_stage_startup_experience: bool = False
    side_projects_text: str = ""
    phonetic_name: str = ""
    match_justification: str = ""


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
    remote_policy: str
    remote_score: int
    remote_evidence: List[str]
    submission_lane: str
    resume_path: Optional[Path]
    resume_html_path: Optional[Path]
    cover_path: Optional[Path]


class SiteAdapter:
    name = "base"

    def matches(self, url: str) -> bool:
        raise NotImplementedError

    def submit(
        self,
        task: SubmitTask,
        profile: Profile,
        auth: AdapterAuth,
        answers: SubmitAnswers,
    ) -> SubmitResult:
        raise NotImplementedError


class PlaywrightFormAdapter(SiteAdapter):
    host_patterns: Sequence[re.Pattern[str]] = ()
    submit_button_patterns: Sequence[str] = ()
    success_text_patterns: Sequence[str] = ()
    success_url_patterns: Sequence[str] = ()

    def matches(self, url: str) -> bool:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        return any(p.search(host) for p in self.host_patterns)

    def submit(
        self,
        task: SubmitTask,
        profile: Profile,
        auth: AdapterAuth,
        answers: SubmitAnswers,
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
                # Use a realistic user-agent and randomized viewport
                user_agent = (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )
                context_kwargs: Dict[str, Any] = {
                    "user_agent": user_agent,
                    "viewport": {"width": 1280, "height": 800},
                    "device_scale_factor": 2,
                }
                if storage_state_arg is not None:
                    context_kwargs["storage_state"] = storage_state_arg
                context = browser.new_context(**context_kwargs)
                page = context.new_page()

                # Apply stealth if available
                if _PlaywrightStealth:
                    _PlaywrightStealth().apply_stealth_sync(page)

                submit_error_detail: Optional[str] = None

                def _capture_submit_error(response: Any) -> None:
                    nonlocal submit_error_detail
                    if submit_error_detail:
                        return
                    try:
                        detail = self._extract_submit_error_detail(response)
                    except Exception:
                        detail = None
                    if detail:
                        submit_error_detail = detail

                try:
                    page.on("response", _capture_submit_error)
                except Exception:
                    pass

                # Add natural human-like behavior (dwell/scroll/mouse)
                self._human_dwell(page)

                page.goto(task.url, wait_until="domcontentloaded", timeout=60000)
                form_scope = self._resolve_form_scope(page)
                if form_scope is None:
                    anti_bot_detail = self._detect_antibot_block(page)
                    screenshot_path: Optional[Path] = None
                    if anti_bot_detail:
                        task.confirmation_path.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            page.screenshot(
                                path=str(task.confirmation_path), full_page=True
                            )
                            if task.confirmation_path.exists():
                                screenshot_path = task.confirmation_path
                        except Exception:
                            screenshot_path = None
                    browser.close()
                    return SubmitResult(
                        adapter=self.name,
                        verified=False,
                        screenshot=screenshot_path,
                        details=anti_bot_detail or "missing_file_input",
                    )

                # Best-effort generic fill by common labels/placeholders.
                self._fill_text(form_scope, "First Name", profile.first_name)
                self._fill_text(form_scope, "Last Name", profile.last_name)
                self._fill_text(
                    form_scope, "Full Name", f"{profile.first_name} {profile.last_name}"
                )
                self._fill_text(
                    form_scope,
                    "Name",
                    f"{profile.first_name} {profile.last_name}",
                    exact_label=True,
                )
                self._fill_text(form_scope, "Email", profile.email)
                self._fill_text(form_scope, "Phone", profile.phone)
                if profile.location:
                    self._fill_text(form_scope, "Location", profile.location)
                    self._fill_text(form_scope, "Current Location", profile.location)
                if profile.linkedin:
                    self._fill_text(form_scope, "LinkedIn", profile.linkedin)
                if profile.github:
                    self._fill_text(form_scope, "GitHub", profile.github)
                if profile.website:
                    self._fill_text(form_scope, "Website", profile.website)
                if profile.current_company:
                    self._fill_text(
                        form_scope, "Current Company", profile.current_company
                    )
                    self._fill_text(
                        form_scope, "Current Employer", profile.current_company
                    )

                missing_answers = self._apply_required_answers(
                    form_scope, page, answers
                )
                if missing_answers:
                    task.confirmation_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        page.screenshot(
                            path=str(task.confirmation_path), full_page=True
                        )
                    except Exception:
                        pass
                    browser.close()
                    return SubmitResult(
                        adapter=self.name,
                        verified=False,
                        screenshot=task.confirmation_path
                        if task.confirmation_path.exists()
                        else None,
                        details=(
                            "missing_required_answers:"
                            + ",".join(sorted(set(missing_answers)))
                        ),
                    )

                # Resume upload is mandatory for this pipeline.
                file_input = form_scope.locator("input[type='file']").first
                file_input.set_input_files(str(task.resume_path))
                self._after_resume_upload(form_scope, page)
                self._pre_submit_fill(form_scope, page, profile, answers)

                if not self._click_submit(form_scope, page):
                    browser.close()
                    return SubmitResult(
                        adapter=self.name,
                        verified=False,
                        screenshot=None,
                        details="submit_button_not_found",
                    )

                confirmed = self._wait_for_confirmation(page, form_scope)
                if not confirmed and self._post_submit_retry(
                    form_scope, page, profile, answers
                ):
                    confirmed = self._wait_for_confirmation(page, form_scope)
                failure_details = self._extract_failure_details(page, form_scope)
                if not failure_details:
                    failure_details = submit_error_detail
                if not confirmed and not failure_details:
                    failure_details = self._detect_antibot_block(page)
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
                    else (failure_details or "confirmation_text_not_detected"),
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

    def _human_dwell(self, page: Any, duration_ms: int = 1500) -> None:
        """Simulate mouse movement and scrolling to look human."""
        import random

        try:
            viewport = page.viewport_size or {"width": 1280, "height": 800}
            w, h = viewport["width"] or 1280, viewport["height"] or 800

            # Initial random mouse move
            page.mouse.move(random.randint(0, w), random.randint(0, h))
            page.wait_for_timeout(random.randint(200, 500))

            # Small scroll
            page.mouse.wheel(0, random.randint(100, 300))
            page.wait_for_timeout(random.randint(200, 500))

            # Move mouse again
            page.mouse.move(random.randint(0, w), random.randint(0, h))

            if duration_ms > 1000:
                page.wait_for_timeout(duration_ms - 1000)
        except Exception:
            pass

    def _apply_required_answers(
        self, scope: Any, page: Any, answers: SubmitAnswers
    ) -> List[str]:
        return []

    def _post_submit_retry(
        self, scope: Any, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> bool:
        return False

    def _extract_failure_details(self, page: Any, scope: Any) -> Optional[str]:
        return None

    def _extract_submit_error_detail(self, response: Any) -> Optional[str]:
        return None

    def _after_resume_upload(self, scope: Any, page: Any) -> None:
        return None

    def _pre_submit_fill(
        self, scope: Any, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> None:
        """Hook for adapter-specific field filling before the first submit click."""
        return None

    def _detect_antibot_block(self, page: Any) -> Optional[str]:
        try:
            text = str(page.inner_text("body") or "").lower()
        except Exception:
            text = ""
        html_text = ""
        try:
            html_text = str(page.content() or "").lower()
        except Exception:
            html_text = ""
        frame_urls = " ".join(
            str(getattr(frame, "url", "") or "").lower()
            for frame in (getattr(page, "frames", []) or [])
        )
        captcha_markers = (
            "captcha",
            "recaptcha",
            "hcaptcha",
            "turnstile",
            "arkose",
            "arkoselabs",
            "friendlycaptcha",
            "verify you are human",
            "i'm not a robot",
            "choose the animal",
        )
        if (
            "flagged as possible spam" in text
            or ("possible spam" in text and "submit your application again" in text)
            or any(marker in text for marker in captcha_markers)
            or any(marker in html_text for marker in captcha_markers)
            or any(marker in frame_urls for marker in captcha_markers)
        ):
            return "recaptcha_score_below_threshold"
        return None

    def _resolve_form_scope(self, page: Any) -> Optional[Any]:
        def _scan_for_file_scope() -> Optional[Any]:
            try:
                if page.locator("input[type='file']").count() > 0:
                    return page
            except Exception:
                pass
            for frame in getattr(page, "frames", []):
                try:
                    if frame.locator("input[type='file']").count() > 0:
                        return frame
                except Exception:
                    continue
            return None

        scope = _scan_for_file_scope()
        if scope is not None:
            return scope

        apply_markers = (
            "apply for this job",
            "apply now",
            "apply",
        )
        for marker in apply_markers:
            clicked = False
            try:
                btn = page.get_by_role("button", name=re.compile(marker, re.I)).first
                if btn.count() > 0:
                    btn.click(timeout=1200)
                    clicked = True
            except Exception:
                pass
            if not clicked:
                try:
                    link = page.get_by_role("link", name=re.compile(marker, re.I)).first
                    if link.count() > 0:
                        link.click(timeout=1200)
                        clicked = True
                except Exception:
                    pass
            if clicked:
                try:
                    page.wait_for_timeout(350)
                except Exception:
                    pass
                scope = _scan_for_file_scope()
                if scope is not None:
                    return scope

        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        scope = _scan_for_file_scope()
        if scope is not None:
            return scope
        return None

    def _fill_text(
        self, scope: Any, key: str, value: str, *, exact_label: bool = False
    ) -> None:
        if not value:
            return
        label_pattern = re.compile(rf"^\\s*{re.escape(key)}\\s*[:*]?\\s*$", re.I)
        loose_label_pattern = re.compile(
            r"\b" + r"\s*".join(re.escape(part) for part in key.split()) + r"\b",
            re.I,
        )
        compact = re.sub(r"[^a-z0-9]", "", key.lower())
        snake = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
        dashed = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")

        def _fill_candidates(locator: Any) -> bool:
            try:
                count = int(locator.count())
            except Exception:
                return False
            for idx in range(min(count, 8)):
                candidate = locator.nth(idx)
                try:
                    candidate.fill(value, timeout=1200)
                    try:
                        is_combo = bool(
                            candidate.evaluate(
                                "el => (el.getAttribute('role') === 'combobox') || Boolean(el.getAttribute('aria-autocomplete'))"
                            )
                        )
                    except Exception:
                        is_combo = False
                    if is_combo:
                        try:
                            field_id = str(candidate.get_attribute("id") or "").strip()
                        except Exception:
                            field_id = ""
                        option_pattern = re.compile(
                            rf"^\\s*{re.escape(value)}\\s*$", re.I
                        )
                        option_clickers = [
                            lambda: (
                                scope.locator(
                                    f"[id^='react-select-{field_id}-option-']"
                                )
                                .filter(has_text=option_pattern)
                                .first.click(timeout=800)
                            ),
                            lambda: scope.locator(
                                f"[id^='react-select-{field_id}-option-']"
                            ).first.click(timeout=800),
                        ]
                        for click_option in option_clickers:
                            try:
                                if not field_id:
                                    continue
                                click_option()
                                break
                            except Exception:
                                continue
                        try:
                            candidate.blur(timeout=300)
                        except Exception:
                            pass
                    return True
                except Exception:
                    continue
            return False

        primary_label = label_pattern if not exact_label else label_pattern
        attempts = [
            lambda: _fill_candidates(
                scope.locator(
                    f"input[id='{snake}'],input[id='{dashed}'],input[id='{compact}'],"
                    f"textarea[id='{snake}'],textarea[id='{dashed}'],textarea[id='{compact}']"
                )
            ),
            lambda: _fill_candidates(
                scope.locator(
                    f"input[name='{snake}'],input[name='{dashed}'],input[name='{compact}'],"
                    f"textarea[name='{snake}'],textarea[name='{dashed}'],textarea[name='{compact}']"
                )
            ),
            lambda: _fill_candidates(
                scope.locator(
                    f"input[id*='{compact}'],input[id*='{snake}'],input[id*='{dashed}'],"
                    f"textarea[id*='{compact}'],textarea[id*='{snake}'],textarea[id*='{dashed}']"
                )
            ),
            lambda: _fill_candidates(
                scope.locator(
                    f"input[name*='{compact}'],input[name*='{snake}'],input[name*='{dashed}'],"
                    f"textarea[name*='{compact}'],textarea[name*='{snake}'],textarea[name*='{dashed}']"
                )
            ),
            lambda: _fill_candidates(
                scope.get_by_label(
                    primary_label,
                    exact=False,
                )
            ),
            lambda: _fill_candidates(scope.get_by_placeholder(key)),
        ]
        if len(key.split()) <= 3:
            attempts.insert(
                5, lambda: _fill_candidates(scope.get_by_label(loose_label_pattern))
            )
        for attempt in attempts:
            try:
                if attempt():
                    return
            except Exception:
                continue

    def _click_submit(self, scope: Any, page: Any) -> bool:
        for pattern in self.submit_button_patterns:
            for target in (scope, page):
                try:
                    btn = target.get_by_role(
                        "button", name=re.compile(pattern, re.I)
                    ).first
                    if btn.count() > 0:
                        btn.click(timeout=3000)
                        return True
                except Exception:
                    continue
        try:
            for target in (scope, page):
                submit_input = target.locator("input[type='submit']").first
                if submit_input.count() > 0:
                    submit_input.click(timeout=3000)
                    return True
        except Exception:
            pass
        return False

    def _wait_for_confirmation(self, page: Any, scope: Any) -> bool:
        texts: List[str] = []
        try:
            page.wait_for_timeout(2000)
        except Exception:
            pass
        for target in (scope, page):
            try:
                text = target.inner_text("body")
                if text:
                    texts.append(text)
            except Exception:
                continue
        normalized = "\n".join(texts).lower()
        if any(re.search(p, normalized, re.I) for p in self.success_text_patterns):
            return True
        page_url = str(getattr(page, "url", "") or "").lower()
        return bool(
            page_url
            and any(re.search(p, page_url, re.I) for p in self.success_url_patterns)
        )


class OracleAdapter(SiteAdapter):
    name = "oracle"
    host_patterns = (re.compile(r"oraclecloud\.com"),)

    def matches(self, url: str) -> bool:
        return any(p.search(url) for p in self.host_patterns)

    def submit(self, task: SubmitTask, profile: Profile, answers: SubmitAnswers) -> SubmitResult:
        return SubmitResult(
            success=False,
            message="Manual submission required for Oracle",
            status_after="Quarantined"
        )

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
        r"thanks for applying",
        r"thanks for your application",
        r"thanks for your interest",
        r"your application has been submitted",
        r"application submitted",
        r"application received",
        r"application was successfully submitted",
        r"we'll be in touch",
    )
    success_url_patterns = (
        r"thank[-_]?you",
        r"submitted",
        r"confirmation",
        r"application.*complete",
    )

    def _apply_required_answers(
        self, scope: Any, page: Any, answers: SubmitAnswers
    ) -> List[str]:
        missing: List[str] = []
        diagnostics: List[str] = []
        auth_markers = (
            "legally authorized to work",
            "authorized to work in the united states",
            "authorized to work in the country",
        )
        sponsorship_markers = (
            "require visa sponsorship",
            "require sponsorship",
            "employment visa sponsorship",
            "now or in the future require",
        )
        interest_markers = (
            "what interests you in this role",
            "why this role",
            "why are you interested in this role",
            "why do you want this role",
        )

        if self._question_present(scope, page, auth_markers) and not self._set_yes_no(
            scope,
            page,
            question_markers=auth_markers,
            answer_yes=answers.work_authorization_us,
            name_hints=("authoriz", "authorization", "workauth", "eligible"),
        ):
            auth_value = "Yes" if answers.work_authorization_us else "No"
            filled = False
            for prompt in (
                "Are you legally authorized to work in the United States",
                "Are you authorized to work in the United States",
                "Authorized to work in the country",
            ):
                before = self._snapshot_text_field(scope, prompt)
                self._fill_text(scope, prompt, auth_value)
                after = self._snapshot_text_field(scope, prompt)
                if after and after != before:
                    filled = True
                    break
            if not filled and self._set_yes_no_following_question(
                scope, page, auth_markers, answers.work_authorization_us
            ):
                filled = True
            if not filled:
                diagnostics.append(
                    "work_authorization_us:"
                    + self._diagnose_question_controls(scope, page, auth_markers)
                )
                missing.append("work_authorization_us")

        if self._question_present(
            scope, page, sponsorship_markers
        ) and not self._set_yes_no(
            scope,
            page,
            question_markers=sponsorship_markers,
            answer_yes=answers.require_sponsorship,
            name_hints=("sponsor", "visa"),
        ):
            sponsorship_value = "Yes" if answers.require_sponsorship else "No"
            filled = False
            for prompt in (
                "Will you now or will you in the future require employment visa sponsorship",
                "Do you require sponsorship",
                "Require sponsorship",
            ):
                before = self._snapshot_text_field(scope, prompt)
                self._fill_text(scope, prompt, sponsorship_value)
                after = self._snapshot_text_field(scope, prompt)
                if after and after != before:
                    filled = True
                    break
            if not filled and self._set_yes_no_following_question(
                scope, page, sponsorship_markers, answers.require_sponsorship
            ):
                filled = True
            if not filled:
                diagnostics.append(
                    "sponsorship:"
                    + self._diagnose_question_controls(scope, page, sponsorship_markers)
                )
                missing.append("require_sponsorship")

        if self._question_present(scope, page, interest_markers):
            filled = False
            for prompt in (
                "What interests you in this role",
                "Why this role",
                "Why are you interested in this role",
            ):
                before = self._snapshot_text_field(scope, prompt)
                self._fill_text(
                    scope, prompt, answers.match_justification or answers.role_interest
                )
                after = self._snapshot_text_field(scope, prompt)
                if after and after != before:
                    filled = True
                    break
            if not filled and self._set_textarea_by_name(
                scope,
                answers.match_justification or answers.role_interest,
                ("interest", "motivation", "why", "additional"),
            ):
                filled = True
            if not filled:
                missing.append("role_interest")

        # Voluntary EEO answers are best-effort but deterministic.
        self._set_prefer_not_to_say_defaults(scope, page, answers.eeo_default)
        if diagnostics:
            missing.extend(diagnostics)
        return missing

    def _question_present(self, scope: Any, page: Any, markers: Sequence[str]) -> bool:
        texts: List[str] = []
        for target in (scope, page):
            try:
                text = target.inner_text("body")
            except Exception:
                continue
            if text:
                texts.append(str(text).lower())
        if not texts:
            return False
        return any(marker.lower() in blob for blob in texts for marker in markers)

    def _locate_question_container(
        self, target: Any, markers: Sequence[str]
    ) -> Optional[Any]:
        for marker in markers:
            marker_pattern = re.compile(
                r"\s+".join(re.escape(part) for part in marker.split()), re.I
            )
            try:
                text_node = target.get_by_text(marker_pattern).first
                if text_node.count() < 1:
                    continue
            except Exception:
                continue
            for xpath in (
                "xpath=ancestor::fieldset[1]",
                "xpath=ancestor::*[@role='group'][1]",
                "xpath=ancestor::div[1]",
                "xpath=ancestor::div[2]",
                "xpath=ancestor::div[3]",
                "xpath=ancestor::section[1]",
                "xpath=ancestor::form[1]",
            ):
                try:
                    container = text_node.locator(xpath).first
                    if container.count() > 0:
                        return container
                except Exception:
                    continue
        return None

    def _set_yes_no(
        self,
        scope: Any,
        page: Any,
        *,
        question_markers: Sequence[str],
        answer_yes: bool,
        name_hints: Sequence[str],
    ) -> bool:
        choice = "yes" if answer_yes else "no"
        choice_patterns = (
            re.compile(rf"^{choice}$", re.I),
            re.compile(rf"\b{choice}\b", re.I),
        )
        for target in (scope, page):
            container = self._locate_question_container(target, question_markers)
            if container is None:
                continue
            if self._click_choice_in_container(container, choice_patterns):
                return True
            if self._select_choice_in_container(container, answer_yes):
                return True
            if self._set_yes_no_custom_combobox(container, page, answer_yes):
                return True
            if self._fill_yes_no_text_in_container(container, answer_yes):
                return True

        value_hints = ("yes", "true", "1") if answer_yes else ("no", "false", "0")
        for hint in name_hints:
            for value_hint in value_hints:
                selector = f"input[type='radio'][name*='{hint}'][value*='{value_hint}']"
                try:
                    radio = scope.locator(selector).first
                    if radio.count() > 0:
                        radio.check(timeout=1500)
                        return True
                except Exception:
                    continue
            select_selector = f"select[name*='{hint}']"
            try:
                select = scope.locator(select_selector).first
                if select.count() > 0 and self._select_yes_no_on_select(
                    select, answer_yes
                ):
                    return True
            except Exception:
                continue
            if self._fill_yes_no_by_hint(scope, hint, answer_yes):
                return True
        return False

    def _set_yes_no_custom_combobox(
        self, container: Any, page: Any, answer_yes: bool
    ) -> bool:
        value = "Yes" if answer_yes else "No"
        for trigger in (
            lambda: container.get_by_role("combobox").first,
            lambda: container.get_by_role("button").first,
            lambda: container.locator("[aria-haspopup='listbox']").first,
        ):
            try:
                control = trigger()
                if control.count() < 1:
                    continue
                control.click(timeout=1500)
                try:
                    control.fill(value, timeout=1000)
                    control.press("Enter", timeout=1000)
                    return True
                except Exception:
                    pass
                try:
                    option = page.get_by_role(
                        "option", name=re.compile(rf"\b{value}\b", re.I)
                    ).first
                    if option.count() > 0:
                        option.click(timeout=1500)
                        return True
                except Exception:
                    pass
            except Exception:
                continue
        return False

    def _click_choice_in_container(
        self, container: Any, choice_patterns: Sequence[re.Pattern[str]]
    ) -> bool:
        for choice_pattern in choice_patterns:
            for probe in (
                lambda: container.get_by_label(choice_pattern).first,
                lambda: container.get_by_role("radio", name=choice_pattern).first,
            ):
                try:
                    control = probe()
                    if control.count() > 0:
                        try:
                            control.check(timeout=1500)
                        except Exception:
                            control.click(timeout=1500)
                        return True
                except Exception:
                    continue
        return False

    def _select_choice_in_container(self, container: Any, answer_yes: bool) -> bool:
        try:
            select_count = container.locator("select").count()
        except Exception:
            return False
        for idx in range(select_count):
            try:
                select = container.locator("select").nth(idx)
            except Exception:
                continue
            if self._select_yes_no_on_select(select, answer_yes):
                return True
        return False

    def _select_yes_no_on_select(self, select: Any, answer_yes: bool) -> bool:
        match = "yes" if answer_yes else "no"
        try:
            option_count = select.locator("option").count()
        except Exception:
            return False
        for opt_idx in range(option_count):
            try:
                option = select.locator("option").nth(opt_idx)
                option_text = str(option.inner_text(timeout=500) or "").strip()
            except Exception:
                continue
            if not option_text:
                continue
            if re.fullmatch(match, option_text, re.I) or re.search(
                rf"\b{match}\b", option_text, re.I
            ):
                try:
                    select.select_option(label=option_text, timeout=1500)
                    return True
                except Exception:
                    continue
        return False

    def _fill_yes_no_text_in_container(self, container: Any, answer_yes: bool) -> bool:
        value = "Yes" if answer_yes else "No"
        try:
            field = container.locator(
                "input:not([type]),input[type='text'],input[type='search'],textarea"
            ).first
            if field.count() > 0:
                field.fill(value, timeout=1500)
                return True
        except Exception:
            pass
        for role in ("textbox", "combobox"):
            try:
                control = container.get_by_role(role).first
                if control.count() > 0:
                    control.fill(value, timeout=1500)
                    return True
            except Exception:
                continue
        return False

    def _fill_yes_no_by_hint(self, scope: Any, hint: str, answer_yes: bool) -> bool:
        value = "Yes" if answer_yes else "No"
        selectors = (
            f"input:not([type])[name*='{hint}']",
            f"input[type='text'][name*='{hint}']",
            f"textarea[name*='{hint}']",
            f"input[type='search'][name*='{hint}']",
            f"input:not([type])[id*='{hint}']",
            f"input[type='text'][id*='{hint}']",
            f"textarea[id*='{hint}']",
            f"input[type='search'][id*='{hint}']",
            f"input[aria-label*='{hint}']",
            f"textarea[aria-label*='{hint}']",
        )
        for selector in selectors:
            try:
                field = scope.locator(selector).first
                if field.count() > 0:
                    field.fill(value, timeout=1500)
                    return True
            except Exception:
                continue
        return False

    def _set_yes_no_following_question(
        self, scope: Any, page: Any, markers: Sequence[str], answer_yes: bool
    ) -> bool:
        value = "Yes" if answer_yes else "No"
        for target in (scope, page):
            for marker in markers:
                marker_pattern = re.compile(
                    r"\s+".join(re.escape(part) for part in marker.split()), re.I
                )
                try:
                    text_node = target.get_by_text(marker_pattern).first
                    if text_node.count() < 1:
                        continue
                except Exception:
                    continue

                for xpath in (
                    "xpath=following::input[not(@type) or @type='text' or @type='search'][1]",
                    "xpath=following::textarea[1]",
                ):
                    try:
                        field = text_node.locator(xpath).first
                        if field.count() > 0:
                            field.fill(value, timeout=1500)
                            return True
                    except Exception:
                        continue

                for xpath in (
                    "xpath=following::*[@role='combobox'][1]",
                    "xpath=following::*[@aria-haspopup='listbox'][1]",
                ):
                    try:
                        control = text_node.locator(xpath).first
                        if control.count() < 1:
                            continue
                        control.click(timeout=1500)
                        try:
                            control.fill(value, timeout=1000)
                            control.press("Enter", timeout=1000)
                            return True
                        except Exception:
                            pass
                        try:
                            option = page.get_by_role(
                                "option", name=re.compile(rf"\b{value}\b", re.I)
                            ).first
                            if option.count() > 0:
                                option.click(timeout=1500)
                                return True
                        except Exception:
                            continue
                    except Exception:
                        continue
        return False

    def _snapshot_text_field(self, scope: Any, prompt: str) -> str:
        try:
            field = scope.get_by_label(prompt, exact=False).first
            if field.count() > 0:
                value = field.input_value(timeout=1500)
                return str(value or "")
        except Exception:
            pass
        return ""

    def _set_textarea_by_name(
        self, scope: Any, value: str, name_hints: Sequence[str]
    ) -> bool:
        for hint in name_hints:
            selector = f"textarea[name*='{hint}'],input[name*='{hint}']"
            try:
                field = scope.locator(selector).first
                if field.count() > 0:
                    field.fill(value, timeout=1500)
                    return True
            except Exception:
                continue
        return False

    def _set_prefer_not_to_say_defaults(
        self, scope: Any, page: Any, default_text: str
    ) -> None:
        if not (default_text or "").strip():
            return
        preferred_patterns = (
            re.compile(r"prefer not", re.I),
            re.compile(r"decline to", re.I),
            re.compile(r"do not wish", re.I),
            re.compile(r"don't wish", re.I),
            re.compile(r"do n't wish", re.I),
            re.compile(r"choose not to", re.I),
            re.compile(r"don't want", re.I),
        )
        for target in (scope, page):
            try:
                select_count = target.locator("select").count()
            except Exception:
                continue
            for idx in range(select_count):
                try:
                    select = target.locator("select").nth(idx)
                    option_count = select.locator("option").count()
                    selected = False
                    for opt_idx in range(option_count):
                        option = select.locator("option").nth(opt_idx)
                        option_text = str(option.inner_text(timeout=500) or "").strip()
                        if any(p.search(option_text) for p in preferred_patterns):
                            select.select_option(label=option_text, timeout=1500)
                            selected = True
                            break
                    if selected:
                        continue
                except Exception:
                    continue

        eeo_sections = (
            "gender",
            "hispanic",
            "race",
            "veteran",
            "disability",
        )
        for target in (scope, page):
            for marker in eeo_sections:
                container = self._locate_question_container(target, (marker,))
                if container is None:
                    continue
                self._click_choice_in_container(container, preferred_patterns)

    def _post_submit_retry(
        self, scope: Any, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> bool:
        try:
            page.wait_for_timeout(1000)
        except Exception:
            pass
        if not self._has_required_question_error(scope, page):
            return False
        self._set_prefer_not_to_say_defaults(scope, page, answers.eeo_default)
        self._fill_unanswered_radio_groups(scope, page)
        self._fill_unanswered_selects(scope, page)
        self._apply_required_answers(scope, page, answers)
        if not self._click_submit(scope, page):
            return False
        try:
            page.wait_for_timeout(1000)
        except Exception:
            pass
        return True

    def _extract_failure_details(self, page: Any, scope: Any) -> Optional[str]:
        anti_bot = self._detect_antibot_block(page)
        if anti_bot:
            return anti_bot
        if self._has_required_question_error(scope, page):
            return "required_questions_unanswered_after_retry"
        return None

    def _extract_submit_error_detail(self, response: Any) -> Optional[str]:
        url = str(getattr(response, "url", "") or "")
        if "ApiSubmitMultipleFormsAction" not in url:
            return None
        try:
            payload = response.json()
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        errors = payload.get("errors")
        if not isinstance(errors, list):
            return None
        for entry in errors:
            if not isinstance(entry, dict):
                continue
            extensions = entry.get("extensions")
            error_type = ""
            error_id = ""
            if isinstance(extensions, dict):
                error_type = str(extensions.get("ashbyErrorType", "")).strip()
                error_id = str(extensions.get("ashbyErrorId", "")).strip()
            message = str(entry.get("message", "")).strip().lower()
            if error_type == "RECAPTCHA_SCORE_BELOW_THRESHOLD" or (
                "possible spam" in message
                and "submit your application again" in message
            ):
                suffix = f":{error_id}" if error_id else ""
                return f"recaptcha_score_below_threshold{suffix}"
        return None

    def _after_resume_upload(self, scope: Any, page: Any) -> None:
        # Ashby asynchronously parses the uploaded resume. Submitting before this
        # settles can drop the click without creating a submit request.
        try:
            page.wait_for_timeout(2500)
        except Exception:
            return
        for _ in range(12):
            text = ""
            for target in (scope, page):
                try:
                    text = str(target.inner_text("body") or "")
                except Exception:
                    continue
                if text:
                    break
            normalized = text.lower()
            if (
                "parsing your resume" in normalized
                or "autofilling key fields" in normalized
            ):
                try:
                    page.wait_for_timeout(500)
                except Exception:
                    break
                continue
            break

    def _has_required_question_error(self, scope: Any, page: Any) -> bool:
        patterns = (
            re.compile(r"review and answer all required questions", re.I),
            re.compile(r"answer all required questions", re.I),
            re.compile(r"please complete all required fields", re.I),
            re.compile(r"required question", re.I),
        )
        verification_markers = (
            "verification code was sent",
            "security code",
            "8-character code",
            "confirm you're a human",
        )
        texts: List[str] = []
        for target in (scope, page):
            text = ""
            try:
                text = target.inner_text("body")
            except Exception:
                text = ""
            if not text:
                try:
                    text = str(
                        target.evaluate(
                            "() => (document.body && document.body.innerText) || "
                            "(document.documentElement && document.documentElement.innerText) || ''"
                        )
                        or ""
                    )
                except Exception:
                    text = ""
            if text:
                texts.append(str(text))
        blob = "\n".join(texts)
        normalized = blob.lower()
        if any(marker in normalized for marker in verification_markers):
            return False
        return any(pattern.search(blob) for pattern in patterns)

    def _fill_unanswered_radio_groups(self, scope: Any, page: Any) -> int:
        total = 0
        preferred = [
            "prefer not",
            "decline to",
            "do not wish",
            "don't wish",
            "do n't wish",
            "choose not to",
            "don't want",
            "do not want",
        ]
        no_like = [" no ", "no,", "no.", "none", "not"]
        script = """
        ({preferred, noLike}) => {
          const normalize = (value) => (value || "").toLowerCase();
          const radioGroups = new Map();
          for (const input of Array.from(document.querySelectorAll("input[type='radio'][name]"))) {
            if (!input.name) continue;
            if (!radioGroups.has(input.name)) radioGroups.set(input.name, []);
            radioGroups.get(input.name).push(input);
          }
          const labelFor = (input) => {
            const byId = input.id ? document.querySelector(`label[for="${input.id}"]`) : null;
            if (byId && byId.textContent) return byId.textContent;
            const wrapped = input.closest("label");
            if (wrapped && wrapped.textContent) return wrapped.textContent;
            const aria = input.getAttribute("aria-label");
            if (aria) return aria;
            const parentText = input.parentElement && input.parentElement.textContent;
            return parentText || "";
          };
          const hasToken = (value, tokens) => tokens.some((token) => normalize(value).includes(token));
          let changed = 0;
          for (const options of radioGroups.values()) {
            if (options.some((option) => option.checked)) continue;
            const ranked = options.map((option) => ({ option, text: normalize(labelFor(option)) }));
            let choice = ranked.find((item) => hasToken(item.text, preferred));
            if (!choice) {
              choice = ranked.find((item) => noLike.some((token) => item.text.includes(token)));
            }
            if (!choice && ranked.length > 0) {
              choice = ranked[0];
            }
            if (!choice) continue;
            choice.option.checked = true;
            choice.option.dispatchEvent(new Event("input", { bubbles: true }));
            choice.option.dispatchEvent(new Event("change", { bubbles: true }));
            changed += 1;
          }
          return changed;
        }
        """
        for target in (scope, page):
            try:
                changed = int(
                    target.evaluate(
                        script,
                        {
                            "preferred": preferred,
                            "noLike": no_like,
                        },
                    )
                    or 0
                )
            except Exception:
                continue
            total += changed
        return total

    def _fill_unanswered_selects(self, scope: Any, page: Any) -> int:
        total = 0
        preferred = [
            "prefer not",
            "decline to",
            "do not wish",
            "don't wish",
            "choose not to",
            "don't want",
            "do not want",
        ]
        no_like = [" no ", "no,", "no.", "none", "not"]
        script = """
        ({preferred, noLike}) => {
          const normalize = (value) => (value || "").toLowerCase();
          const hasToken = (value, tokens) => tokens.some((token) => normalize(value).includes(token));
          let changed = 0;
          for (const select of Array.from(document.querySelectorAll("select"))) {
            const current = normalize(select.value);
            if (current) continue;
            const options = Array.from(select.options || []).filter((option) => normalize(option.value));
            if (!options.length) continue;
            let choice = options.find((option) => hasToken(option.textContent, preferred));
            if (!choice) {
              choice = options.find((option) => noLike.some((token) => normalize(option.textContent).includes(token)));
            }
            if (!choice) {
              choice = options[0];
            }
            if (!choice) continue;
            select.value = choice.value;
            select.dispatchEvent(new Event("input", { bubbles: true }));
            select.dispatchEvent(new Event("change", { bubbles: true }));
            changed += 1;
          }
          return changed;
        }
        """
        for target in (scope, page):
            try:
                changed = int(
                    target.evaluate(
                        script,
                        {
                            "preferred": preferred,
                            "noLike": no_like,
                        },
                    )
                    or 0
                )
            except Exception:
                continue
            total += changed
        return total

    def _diagnose_question_controls(
        self, scope: Any, page: Any, markers: Sequence[str]
    ) -> str:
        for target in (scope, page):
            container = self._locate_question_container(target, markers)
            if container is None:
                continue
            try:
                radios = container.locator("input[type='radio']").count()
            except Exception:
                radios = -1
            try:
                selects = container.locator("select").count()
            except Exception:
                selects = -1
            try:
                text_inputs = container.locator(
                    "input:not([type]),input[type='text'],input[type='search'],textarea"
                ).count()
            except Exception:
                text_inputs = -1
            try:
                combos = container.get_by_role("combobox").count()
            except Exception:
                combos = -1
            return (
                f"container=found,radios={radios},selects={selects},"
                f"text_inputs={text_inputs},comboboxes={combos}"
            )
        return "container=missing"

    def _resolve_form_scope(self, page: Any) -> Optional[Any]:
        scope = super()._resolve_form_scope(page)
        if scope is not None:
            return scope

        open_patterns = (
            r"apply for this job",
            r"apply now",
            r"start application",
            r"apply",
        )
        for pattern in open_patterns:
            for role in ("button", "link"):
                try:
                    control = page.get_by_role(
                        role, name=re.compile(pattern, re.I)
                    ).first
                    if control.count() > 0:
                        control.click(timeout=3000)
                        break
                except Exception:
                    continue
            try:
                page.wait_for_timeout(500)
            except Exception:
                pass
            scope = super()._resolve_form_scope(page)
            if scope is not None:
                return scope
        return None


class GreenhouseAdapter(PlaywrightFormAdapter):
    name = "greenhouse"

    def _fill_custom(self, scope: Any, page: Any, profile: Profile, answers: SubmitAnswers) -> None:
        # Anthropic-specific: Why Anthropic?
        why_anthropic = (
            "As the core maintainer of 'igor'—an open-source RLHF stack for AI coding assistants—I've spent the last year "
            "obsessed with the same problems Anthropic is solving: reliability, agentic memory, and safe tool execution. "
            "My work on Thompson Sampling-based feedback loops and Hive-based self-healing guardrails aligns directly "
            "with your mission of building steerable, reliable AI. I've shipped production AI to millions at Subway and "
            "Google, but I'm most excited about the frontier infrastructure of autonomy."
        )
        self._fill_text(scope, "Why Anthropic", why_anthropic)
        self._fill_text(scope, "Why are you interested", why_anthropic)

        # Keep this legacy hook aligned with the submit path helpers.
        self._set_yes_no_question_by_markers(
            page,
            ("open to working in-person", "offices 25% of the time", "in office 25%"),
            True,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("ai policy for application", "ai policy", "confirm your understanding"),
            True,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("do you require visa sponsorship", "require visa sponsorship"),
            answers.require_sponsorship,
        )
        self._set_yes_no_question_by_markers(
            page,
            (
                "will you now or will you in the future require employment visa sponsorship",
                "future require employment visa sponsorship",
                "future visa sponsorship",
            ),
            answers.require_sponsorship,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("have you ever interviewed at anthropic before",),
            False,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("open to relocation", "willing to relocate"),
            False,
        )

    host_patterns = (re.compile(r"greenhouse\.io"),)
    submit_button_patterns = (r"submit application", r"apply", r"submit")
    success_text_patterns = (
        r"thank you for applying",
        r"application has been received",
        r"your application has been submitted",
        r"your application has been received",
        r"thank you for your interest",
        r"application submitted",
    )
    success_url_patterns = (
        r"thank[-_]?you",
        r"submitted",
        r"confirmation",
    )

    def _wait_for_confirmation(self, page: Any, scope: Any) -> bool:
        """Greenhouse confirmation with verification-code exclusion."""
        confirmed = super()._wait_for_confirmation(page, scope)
        if not confirmed:
            return False
        # Exclude false positives from verification code challenge pages
        try:
            text = str(page.inner_text("body") or "").lower()
        except Exception:
            text = ""
        verification_markers = (
            "verification code was sent",
            "security code",
            "8-character code",
            "enter the 8-character",
            "confirm you're a human",
        )
        if any(m in text for m in verification_markers):
            return False
        return True

    def _pre_submit_fill(
        self, scope: Any, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> None:
        """Fill company-specific Greenhouse fields before the first submit."""
        self._apply_anthropic_field_map(page, profile, answers)
        self._apply_xapo_field_map(page, profile, answers)
        self._apply_wikimedia_field_map(page, profile, answers)
        self._apply_generic_greenhouse_screener(page, profile, answers)

    def _fill_by_id(self, page: Any, field_id: str, value: str) -> bool:
        if not field_id or not value:
            return False
        script = """
        ({fieldId, value}) => {
          const el = document.getElementById(fieldId);
          if (!el) return false;
          try {
            if (typeof el.scrollIntoView === "function") {
              el.scrollIntoView({ block: "center", behavior: "instant" });
            }
            el.focus();
            el.value = value;
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
            el.blur();
            return true;
          } catch (_e) {
            return false;
          }
        }
        """
        try:
            return bool(page.evaluate(script, {"fieldId": field_id, "value": value}))
        except Exception:
            return False

    def _fill_by_locator(self, page: Any, field_id: str, value: str) -> bool:
        """Fill using Playwright's native .fill() which properly triggers React state."""
        if not field_id or not value:
            return False
        try:
            loc = page.locator(f"#{field_id}")
            if loc.count() > 0:
                loc.scroll_into_view_if_needed(timeout=2000)
                loc.fill(value, timeout=2000)
                return True
        except Exception:
            pass
        return self._fill_by_id(page, field_id, value)

    def _fill_autocomplete_select(
        self, page: Any, field_id: str, search_text: str
    ) -> bool:
        """Fill a React Select autocomplete field by typing and selecting the first match."""
        if not field_id or not search_text:
            return False
        try:
            loc = page.locator(f"#{field_id}")
            if loc.count() == 0:
                return False
            loc.scroll_into_view_if_needed(timeout=2000)
            loc.click(timeout=2000)
            page.wait_for_timeout(300)
            loc.fill(search_text, timeout=2000)
            page.wait_for_timeout(2000)
            option = (
                page.locator("[class*='select__option']")
                .filter(has_text=re.compile(re.escape(search_text), re.I))
                .first
            )
            if option.count() > 0:
                option.click(timeout=2000)
                page.wait_for_timeout(300)
                return True
        except Exception:
            pass
        return False

    def _select_react_option_by_id(
        self, page: Any, field_id: str, option_text: str
    ) -> bool:
        if not field_id or not option_text:
            return False
        coord_script = """
        ({fieldId}) => {
          const inp = document.getElementById(fieldId);
          if (!inp) return null;
          let el = inp;
          for (let i = 0; i < 12; i++) {
            el = el.parentElement;
            if (!el) return null;
            if (el.className && String(el.className).includes("select-shell")) {
              const ctrl = el.querySelector('[class*="select__control"]');
              if (!ctrl) return null;
              if (typeof ctrl.scrollIntoView === "function") {
                ctrl.scrollIntoView({ block: "center", behavior: "instant" });
              }
              const r = ctrl.getBoundingClientRect();
              if (r.width <= 0 || r.height <= 0) return null;
              return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
            }
          }
          return null;
        }
        """
        try:
            coords = page.evaluate(coord_script, {"fieldId": field_id})
        except Exception:
            coords = None
        if not isinstance(coords, dict):
            return False
        try:
            page.mouse.click(float(coords.get("x", 0)), float(coords.get("y", 0)))
            page.wait_for_timeout(600)
        except Exception:
            return False

        option = (
            page.locator("[class*='select__option']")
            .filter(has_text=re.compile(re.escape(option_text), re.I))
            .first
        )
        try:
            if option.count() > 0:
                option.click(timeout=1500)
                page.wait_for_timeout(250)
                return True
        except Exception:
            pass
        try:
            page.keyboard.type(option_text, delay=20)
            page.wait_for_timeout(1500)
            option = (
                page.locator("[class*='select__option']")
                .filter(has_text=re.compile(re.escape(option_text), re.I))
                .first
            )
            if option.count() > 0:
                option.click(timeout=1500)
                page.wait_for_timeout(250)
                return True
        except Exception:
            pass
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False

    def _fill_react_select_by_markers(
        self, page: Any, markers: Sequence[str], value: str
    ) -> bool:
        """Fill a React Select combobox found by matching label/legend text markers.

        Detects React Select controls within question containers identified by
        marker text, then uses Playwright interactions (click, type, select) instead
        of DOM manipulation which React Select ignores.
        """
        if not value:
            return False
        # Step 1: Use JS to find the container and the React Select control coordinates
        find_script = """
        ({markers}) => {
          const normalize = (v) => (v || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const markerSet = markers.map((m) => normalize(m));
          const nodes = Array.from(document.querySelectorAll('label, legend, p, h3, h4'));
          const textMatches = (text) => {
            const t = normalize(text);
            return markerSet.some((m) => t.includes(m));
          };
          for (const node of nodes) {
            if (!textMatches(node.textContent || '')) continue;
            const container = node.closest(
              'fieldset, .field-wrapper, .select__container, .text-input-wrapper, .input-wrapper, .application-question, .question, .field, .multi_value'
            ) || node.parentElement;
            if (!container) continue;
            // Look for React Select control within this container
            const ctrl = container.querySelector('[class*="select__control"]');
            if (!ctrl) continue;
            if (typeof ctrl.scrollIntoView === 'function') {
              ctrl.scrollIntoView({ block: 'center', behavior: 'instant' });
            }
            const r = ctrl.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) continue;
            // Also try to find the hidden input for typing
            const input = container.querySelector(
              'input[role="combobox"], input[aria-autocomplete]'
            );
            const inputId = input ? input.getAttribute('id') : null;
            return {
              x: r.x + r.width / 2,
              y: r.y + r.height / 2,
              inputId: inputId
            };
          }
          return null;
        }
        """
        try:
            coords = page.evaluate(find_script, {"markers": list(markers)})
        except Exception:
            coords = None
        if not isinstance(coords, dict):
            return False

        # Step 2: Click the React Select control to open the dropdown
        try:
            page.mouse.click(float(coords.get("x", 0)), float(coords.get("y", 0)))
            page.wait_for_timeout(600)
        except Exception:
            return False

        # Step 3: Try to find a matching option in the opened menu
        option = (
            page.locator("[class*='select__option']")
            .filter(has_text=re.compile(re.escape(value), re.I))
            .first
        )
        try:
            if option.count() > 0:
                option.click(timeout=1500)
                page.wait_for_timeout(250)
                return True
        except Exception:
            pass

        # Step 4: Type to filter options, then select
        try:
            page.keyboard.type(value, delay=20)
            page.wait_for_timeout(1500)
            option = (
                page.locator("[class*='select__option']")
                .filter(has_text=re.compile(re.escape(value), re.I))
                .first
            )
            if option.count() > 0:
                option.click(timeout=1500)
                page.wait_for_timeout(250)
                return True
        except Exception:
            pass

        # Step 5: Dismiss menu on failure
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False

    def _apply_anthropic_field_map(
        self, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> None:
        page_url = str(getattr(page, "url", "") or "").lower()
        if "job-boards.greenhouse.io/anthropic/" not in page_url:
            return

        anthropic_why = (
            "I want to help build safe, reliable AI systems at production scale. "
            "I have experience shipping LLM-enabled systems with strong reliability, "
            "observability, and practical delivery discipline."
        )

        # Use Playwright's native fill path for Greenhouse text inputs so React
        # form state is updated instead of only mutating DOM values.
        self._fill_by_locator(page, "first_name", profile.first_name)
        self._fill_by_locator(page, "last_name", profile.last_name)
        self._fill_by_locator(page, "email", profile.email)
        self._fill_by_locator(page, "phone", profile.phone)
        if profile.website or profile.github:
            self._fill_text(page, "Website", profile.website or profile.github or "")
        if profile.linkedin:
            self._fill_text(page, "LinkedIn Profile", profile.linkedin)
        self._fill_text(page, "Why Anthropic", anthropic_why)

        self._select_react_option_by_id(
            page, "country", answers.country or "United States"
        )
        self._set_yes_no_question_by_markers(
            page,
            ("open to working in-person", "offices 25% of the time", "in office 25%"),
            True,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("ai policy for application", "ai policy"),
            True,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("do you require visa sponsorship", "require visa sponsorship"),
            answers.require_sponsorship,
        )
        self._set_yes_no_question_by_markers(
            page,
            (
                "will you now or will you in the future require employment visa sponsorship",
                "future require employment visa sponsorship",
                "future visa sponsorship",
            ),
            answers.require_sponsorship,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("open to relocation", "willing to relocate"),
            False,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("have you ever interviewed at anthropic before",),
            False,
        )

    def _apply_xapo_field_map(
        self, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> None:
        """Fill Xapo Bank Greenhouse custom screener fields using actual question IDs."""
        page_url = str(getattr(page, "url", "") or "").lower()
        if "greenhouse.io/xapo" not in page_url:
            return

        # React Select dropdowns first (these can trigger re-renders)
        self._select_react_option_by_id(
            page, "country", answers.country or "United States"
        )
        # Xapo SE (7572065003) React Select fields:
        self._select_react_option_by_id(
            page, "question_29097201003", answers.country or "United States"
        )
        self._select_react_option_by_id(page, "question_29351026003", "5-10")
        self._select_react_option_by_id(page, "question_29351044003", "Above $50,000")
        self._select_react_option_by_id(
            page, "question_29351265003", "CAR - Customer Acquisition and Retention"
        )
        # Xapo Android (7575864003) React Select field:
        self._select_react_option_by_id(
            page, "question_29137541003", answers.country or "United States"
        )

        # Text fields LAST (after React Select to avoid re-render clearing)
        self._fill_by_locator(page, "first_name", profile.first_name)
        self._fill_by_locator(page, "last_name", profile.last_name)
        self._fill_by_locator(page, "email", profile.email)
        self._fill_by_locator(page, "phone", profile.phone)
        self._fill_by_locator(page, "question_29097200003", profile.linkedin)
        self._fill_by_locator(page, "question_29137540003", profile.linkedin)
        # Justification textarea (Playwright .fill() for React compat)
        self._fill_by_locator(
            page,
            "question_29351266003",
            (
                "15+ years building production backend systems at Google, Subway, "
                "and KPMG. Expert in Python, cloud infrastructure (GCP/AWS), and "
                "API design. Recent work includes production RAG pipelines and "
                "AI-powered microservices serving millions of users."
            ),
        )

    def _apply_wikimedia_field_map(
        self, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> None:
        """Fill Wikimedia Foundation Greenhouse custom screener fields using actual question IDs."""
        page_url = str(getattr(page, "url", "") or "").lower()
        if "greenhouse.io/wikimedia" not in page_url:
            return

        # ALL React Select dropdowns FIRST (these trigger React re-renders)
        self._select_react_option_by_id(
            page, "country", answers.country or "United States"
        )
        self._fill_autocomplete_select(page, "candidate-location", "Coral Springs")
        self._select_react_option_by_id(page, "question_62842886", "Yes")
        self._select_react_option_by_id(page, "question_62842887", "No")
        self._select_react_option_by_id(
            page,
            "question_62841399",
            "Yes" if answers.work_authorization_us else "No",
        )
        self._select_react_option_by_id(
            page,
            "question_62841400",
            "Yes" if answers.require_sponsorship else "No",
        )
        self._select_react_option_by_id(
            page, "question_62841401", answers.country or "United States"
        )
        self._select_react_option_by_id(
            page, "question_62841402", "UTC-5: Eastern Time (US), Colombia"
        )
        self._select_react_option_by_id(page, "question_62841405", "No")
        self._select_react_option_by_id(page, "question_62841407", "Yes")
        self._select_react_option_by_id(page, "question_62841409", "Yes")
        # EEO React Select fields
        self._select_react_option_by_id(page, "gender", "Decline To Self Identify")
        self._select_react_option_by_id(
            page, "hispanic_ethnicity", "Decline To Self Identify"
        )
        self._select_react_option_by_id(
            page, "veteran_status", "I don't wish to answer"
        )
        self._select_react_option_by_id(
            page, "disability_status", "I don't wish to answer"
        )

        # Text fields LAST (Playwright .fill() for React compat, after Select re-renders)
        self._fill_by_locator(page, "first_name", profile.first_name)
        self._fill_by_locator(page, "last_name", profile.last_name)
        self._fill_by_locator(page, "preferred_name", profile.first_name)
        self._fill_by_locator(page, "email", profile.email)
        self._fill_by_locator(page, "phone", profile.phone)
        self._fill_by_locator(page, "question_62841403", "33071")
        self._fill_by_locator(page, "question_62841404", "Direct application")
        self._fill_by_locator(page, "question_62841406", profile.linkedin)
        self._fill_by_locator(
            page,
            "question_62841408",
            f"{profile.first_name} {profile.last_name}",
        )

    def _apply_generic_greenhouse_screener(
        self, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> None:
        """Generic fallback for common Greenhouse screener fields across all companies."""
        # Standard ID-based fields
        self._fill_by_locator(page, "last_name", profile.last_name)
        self._fill_by_locator(page, "first_name", profile.first_name)
        self._fill_by_locator(page, "email", profile.email)
        self._fill_by_locator(page, "phone", profile.phone)

        # Country (select dropdown)
        self._select_react_option_by_id(
            page, "country", answers.country or "United States"
        )

        # Preferred first name (Wikimedia and others)
        self._set_value_question_by_markers(
            page,
            ("preferred first name", "preferred name"),
            profile.first_name,
        )

        # Country of residence (text or select)
        self._set_value_question_by_markers(
            page,
            (
                "country of residence",
                "what is your country of residence",
                "select your country of residence",
                "please select your country",
            ),
            answers.country or "United States",
        )

        # Location / city
        self._set_value_question_by_markers(
            page,
            ("location (city)", "location city"),
            profile.location or "Coral Springs, FL",
        )

        # Years of experience
        self._set_value_question_by_markers(
            page,
            ("how many years of experience", "years of experience"),
            "15+",
        )

        # Salary
        self._set_value_question_by_markers(
            page,
            (
                "salary expectations",
                "salary expect",
                "annual gross salary",
                "gross salary expectations",
                "compensation expectations",
                "desired salary",
            ),
            "$150,000 - $200,000",
        )

        # Yes/No: sponsorship
        self._set_yes_no_question_by_markers(
            page,
            ("require sponsorship", "visa sponsorship", "need sponsorship"),
            False,
        )

        # Yes/No: work authorization
        self._set_yes_no_question_by_markers(
            page,
            (
                "authorized to work",
                "work authorization",
                "legally authorized",
                "eligible to work",
            ),
            True,
        )

        # Yes/No: years of experience questions
        self._set_yes_no_question_by_markers(
            page,
            (
                "7 years of software engineering",
                "at least 7 years",
                "years of experience with a focus",
            ),
            True,
        )

        # Yes/No: timezone
        self._set_yes_no_question_by_markers(
            page,
            ("utc-3", "utc+3", "time zone"),
            True,
        )

    def _apply_required_answers(
        self, scope: Any, page: Any, answers: SubmitAnswers
    ) -> List[str]:
        auth_value = "Yes" if answers.work_authorization_us else "No"
        sponsor_value = "Yes" if answers.require_sponsorship else "No"
        anthropic_why = (
            "I want to help ship safe, reliable AI systems at production scale, "
            "especially where model quality, tooling, and user impact meet."
        )
        prompt_map = [
            ("Country", answers.country),
            (
                "Country",
                "United States +1"
                if answers.country.lower().startswith("united states")
                else answers.country,
            ),
            (
                "LinkedIn Profile",
                "https://www.linkedin.com/in/igor-ganapolsky-859317343/",
            ),
            ("Github or Personal website", "https://github.com/IgorGanapolsky"),
            ("GitHub or Personal website", "https://github.com/IgorGanapolsky"),
            (
                "Why do you want to work at",
                answers.match_justification or answers.role_interest,
            ),
            (
                "Why do you want to work here",
                answers.match_justification or answers.role_interest,
            ),
            ("How did you hear about us?", "Direct application"),
            ("How did you hear about us", "Direct application"),
            (
                "Do you have experience owning or being a primary contributor to inference systems for ML products?",
                "Yes" if answers.inference_systems_experience else "No",
            ),
            (
                "Do you have experience working at an early stage startup?",
                "Yes" if answers.early_stage_startup_experience else "No",
            ),
            (
                "Do you have any side projects you're excited about, or something new you're learning?",
                "Yes" if (answers.side_projects_text or "").strip() else "No",
            ),
            (
                "Are you currently based in the U.S. or Canada?",
                "Yes" if answers.based_in_us_or_canada else "No",
            ),
            (
                "If located in the US, are you currently authorized to work in the US?",
                auth_value,
            ),
            (
                "So we can pronounce it correctly, what is the phonetic spelling of your name",
                answers.phonetic_name or "EE-gor guh-NA-pol-skee",
            ),
            (
                "If you answered yes to the above, what are you working on/learning about?",
                answers.side_projects_text or answers.role_interest,
            ),
            ("Why Anthropic?", anthropic_why),
            (
                "Please review and acknowledge Anthropic's Candidate Privacy Policy.",
                "Yes",
            ),
            ("AI Policy for Application", "Yes"),
        ]
        for prompt, value in prompt_map:
            self._fill_text(scope, prompt, value)
        self._set_value_question_by_markers(page, ("country",), answers.country)
        self._set_value_question_by_markers(
            page,
            ("why anthropic", "why do you want to work at anthropic"),
            anthropic_why,
        )
        self._set_value_question_by_markers(
            page,
            (
                "github or personal website",
                "github",
                "website",
                "portfolio",
            ),
            "https://github.com/IgorGanapolsky",
        )
        self._set_value_question_by_markers(
            page,
            ("linkedin profile", "linkedin"),
            "https://www.linkedin.com/in/igor-ganapolsky-859317343/",
        )
        self._set_yes_no_question_by_markers(
            page,
            ("open to working in-person", "offices 25% of the time", "in office 25%"),
            True,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("ai policy for application", "ai policy"),
            True,
        )
        self._set_value_question_by_markers(
            page,
            ("why anthropic", "why do you want to work at anthropic"),
            anthropic_why,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("do you require visa sponsorship", "require visa sponsorship"),
            answers.require_sponsorship,
        )
        self._set_yes_no_question_by_markers(
            page,
            (
                "will you now or will you in the future require employment visa sponsorship",
                "future require employment visa sponsorship",
                "future visa sponsorship",
            ),
            answers.require_sponsorship,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("candidate privacy policy", "acknowledge anthropic"),
            True,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("do you hold an active us security clearance",),
            False,
        )
        self._set_yes_no_question_by_markers(
            page,
            (
                "7+ years of experience in a pre-sales technical role",
                "pre-sales technical role",
            ),
            False,
        )
        self._set_yes_no_question_by_markers(
            page,
            (
                "hands-on experience building or deploying ai/ml solutions",
                "hands-on experience building or deploying ai",
            ),
            True,
        )
        self._set_yes_no_question_by_markers(
            page,
            (
                "delivered technical demonstrations",
                "presented architectural solutions to enterprise c-level",
            ),
            False,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("open to relocation", "willing to relocate"),
            False,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("have you ever interviewed at anthropic before",),
            False,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("require sponsorship", "visa sponsorship"),
            answers.require_sponsorship,
        )
        self._set_value_question_by_markers(
            page,
            ("require visa sponsorship", "visa sponsorship"),
            sponsor_value,
        )
        self._set_value_question_by_markers(
            page,
            ("authorized to work in the us", "authorized to work in the united states"),
            auth_value,
        )
        self._set_yes_no_question_by_markers(
            page,
            (
                "legally authorized to work in the country with which you reside",
                "authorized to work in the country with which you reside",
            ),
            answers.work_authorization_us,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("utc-3 - utc+3", "utc-3 to utc+3", "timezone", "time zone"),
            True,
        )
        self._set_yes_no_question_by_markers(
            page,
            (
                "require sponsorship to work in the country with which you reside",
                "sponsorship to work in the country with which you reside",
            ),
            answers.require_sponsorship,
        )

        # --- Generic screener fields (Xapo, Wikimedia, and others) ---
        self._set_value_question_by_markers(
            page,
            (
                "country of residence",
                "what is your country of residence",
                "please select your country of residence",
            ),
            answers.country or "United States",
        )
        self._set_value_question_by_markers(
            page,
            ("how many years of experience", "years of experience"),
            "15+",
        )
        self._set_value_question_by_markers(
            page,
            (
                "annual gross salary expectations",
                "salary expectations",
                "salary expect",
                "compensation expectations",
                "desired salary",
            ),
            "$150,000 - $200,000",
        )
        self._set_value_question_by_markers(
            page,
            (
                "business domains best aligns",
                "business domains",
                "professional background and technical expertise",
            ),
            "Backend Engineering",
        )
        self._set_value_question_by_markers(
            page,
            (
                "brief justification for your choice",
                "highlighting relevant projects or skills",
            ),
            (
                "15+ years building production backend systems at Google, Subway, "
                "and KPMG. Expert in Python, cloud infrastructure (GCP/AWS), and "
                "API design. Recent work includes production RAG pipelines and "
                "AI-powered microservices serving millions of users."
            ),
        )
        self._set_value_question_by_markers(
            page,
            ("cover letter", "letter of interest"),
            (
                "I bring 15+ years of software engineering experience including "
                "production systems at Google. Deep expertise in Python, cloud "
                "infrastructure, and scalable platform engineering."
            ),
        )
        self._set_value_question_by_markers(
            page,
            (
                "why do you want to work at",
                "why do you want to work here",
                "why this role",
            ),
            answers.match_justification or answers.role_interest,
        )
        self._set_value_question_by_markers(
            page,
            ("how did you hear about us", "how did you hear about this role"),
            "Direct application",
        )

        return []

    def _select_choice_by_label(
        self, scope: Any, page: Any, label_text: str, choice: str
    ) -> bool:
        if not label_text or not choice:
            return False
        label_pattern = re.compile(rf"^\\s*{re.escape(label_text)}\\s*[:*]?\\s*$", re.I)
        option_pattern = re.compile(rf"^\\s*{re.escape(choice)}\\b", re.I)
        try:
            field = scope.get_by_label(label_pattern).first
        except Exception:
            return False
        try:
            if field.count() <= 0:
                return False
        except Exception:
            return False

        try:
            field.click(timeout=1200)
        except Exception:
            pass
        try:
            field.fill(choice, timeout=1200)
        except Exception:
            pass

        clickers = [
            lambda: page.get_by_role("option", name=option_pattern).first.click(
                timeout=1200
            ),
            lambda: (
                page.locator("[role='option']")
                .filter(has_text=option_pattern)
                .first.click(timeout=1200)
            ),
            lambda: (
                page.locator(".select__option")
                .filter(has_text=option_pattern)
                .first.click(timeout=1200)
            ),
            lambda: (
                page.locator("li")
                .filter(has_text=option_pattern)
                .first.click(timeout=1200)
            ),
        ]
        for clicker in clickers:
            try:
                clicker()
                return True
            except Exception:
                continue
        return False

    def _set_yes_no_question_by_markers(
        self, page: Any, markers: Sequence[str], answer_yes: bool
    ) -> bool:
        value = "Yes" if answer_yes else "No"
        # First try: radios and native selects via fast JS evaluation
        radio_select_script = """
        ({markers, answerYes}) => {
          const normalize = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const markerSet = markers.map((m) => normalize(m));
          const desired = answerYes ? 'yes' : 'no';
          const nodes = Array.from(document.querySelectorAll('label, legend, p, h3, h4'));
          const textMatches = (text) => {
            const t = normalize(text);
            return markerSet.some((m) => t.includes(m));
          };
          const choiceText = (input) => {
            const id = input.getAttribute('id');
            if (id) {
              const byFor = document.querySelector(`label[for="${id}"]`);
              if (byFor && byFor.textContent) return byFor.textContent;
            }
            const wrap = input.closest('label');
            if (wrap && wrap.textContent) return wrap.textContent;
            return (input.parentElement && input.parentElement.textContent) || '';
          };
          for (const node of nodes) {
            if (!textMatches(node.textContent || '')) continue;
            const container = node.closest('fieldset, .field-wrapper, .select__container, .text-input-wrapper, .input-wrapper, .application-question, .question, .field') || node.parentElement;
            if (!container) continue;
            const radios = Array.from(container.querySelectorAll('input[type="radio"]'));
            for (const radio of radios) {
              const txt = normalize(choiceText(radio));
              if (txt.includes(desired)) {
                radio.click();
                radio.dispatchEvent(new Event('input', { bubbles: true }));
                radio.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
              }
            }
            const selects = Array.from(container.querySelectorAll('select'));
            for (const select of selects) {
              const options = Array.from(select.options || []);
              const match = options.find((option) => normalize(option.textContent || '').includes(desired));
              if (match) {
                select.value = match.value;
                select.dispatchEvent(new Event('input', { bubbles: true }));
                select.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
              }
            }
          }
          return false;
        }
        """
        try:
            if page.evaluate(
                radio_select_script,
                {"markers": list(markers), "answerYes": bool(answer_yes)},
            ):
                return True
        except Exception:
            pass

        # Second try: React Select combobox (needs Playwright click interaction)
        if self._fill_react_select_by_markers(page, markers, value):
            return True

        # Third try: plain text inputs via DOM manipulation (last resort)
        text_script = """
        ({markers, answerYes}) => {
          const normalize = (v) => (v || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const markerSet = markers.map((m) => normalize(m));
          const nodes = Array.from(document.querySelectorAll('label, legend, p, h3, h4'));
          const textMatches = (text) => {
            const t = normalize(text);
            return markerSet.some((m) => t.includes(m));
          };
          for (const node of nodes) {
            if (!textMatches(node.textContent || '')) continue;
            const container = node.closest('fieldset, .field-wrapper, .select__container, .text-input-wrapper, .input-wrapper, .application-question, .question, .field') || node.parentElement;
            if (!container) continue;
            // Skip React Select containers (handled above)
            if (container.querySelector('[class*="select__control"]')) continue;
            const textInputs = Array.from(
              container.querySelectorAll(
                "input[type='text'], input[type='search'], textarea"
              )
            );
            for (const input of textInputs) {
              try {
                input.focus();
                input.value = answerYes ? 'Yes' : 'No';
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
              } catch (_e) {}
            }
          }
          return false;
        }
        """
        try:
            return bool(
                page.evaluate(
                    text_script,
                    {"markers": list(markers), "answerYes": bool(answer_yes)},
                )
            )
        except Exception:
            return False

    def _set_value_question_by_markers(
        self, page: Any, markers: Sequence[str], value: str
    ) -> bool:
        if not value:
            return False
        # Try React Select interaction first (Playwright click-based),
        # since DOM manipulation (el.value=) does not work for React Select.
        if self._fill_react_select_by_markers(page, markers, value):
            return True
        # Fallback: native <select> and text input via DOM manipulation
        script = """
        ({markers, value}) => {
          const normalize = (v) => (v || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const markerSet = markers.map((m) => normalize(m));
          const nodes = Array.from(document.querySelectorAll('label, legend, p, h3, h4'));
          const textMatches = (text) => {
            const t = normalize(text);
            return markerSet.some((m) => t.includes(m));
          };
          for (const node of nodes) {
            if (!textMatches(node.textContent || '')) continue;
            const container = node.closest('fieldset, .field-wrapper, .select__container, .text-input-wrapper, .input-wrapper, .application-question, .question, .field, .multi_value') || node.parentElement;
            if (!container) continue;
            // Skip containers that have React Select (already tried above)
            if (container.querySelector('[class*="select__control"]')) continue;
            const selects = Array.from(container.querySelectorAll('select'));
            for (const select of selects) {
              const options = Array.from(select.options || []);
              const match = options.find((option) => normalize(option.textContent || '').includes(normalize(value)));
              if (match) {
                select.value = match.value;
                select.dispatchEvent(new Event('input', { bubbles: true }));
                select.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
              }
            }
            const fields = Array.from(
              container.querySelectorAll(
                "textarea, input[type='text'], input[type='search'], input[type='url'], input[role='combobox'], input[aria-autocomplete], input:not([type])"
              )
            );
            for (const field of fields) {
              try {
                field.focus();
                field.value = value;
                field.dispatchEvent(new Event('input', { bubbles: true }));
                field.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
              } catch (_e) {}
            }
          }
          return false;
        }
        """
        try:
            return bool(
                page.evaluate(script, {"markers": list(markers), "value": value})
            )
        except Exception:
            return False

    def _post_submit_retry(
        self, scope: Any, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> bool:
        # Greenhouse often reports inline required-field errors after first submit.
        current_failure = self._extract_failure_details(page, scope)
        if current_failure and current_failure.startswith("verification_code_required"):
            return False
        self._apply_anthropic_field_map(page, profile, answers)
        self._apply_xapo_field_map(page, profile, answers)
        self._apply_wikimedia_field_map(page, profile, answers)
        self._apply_generic_greenhouse_screener(page, profile, answers)
        self._apply_required_answers(scope, page, answers)
        try:
            page.wait_for_timeout(500)
        except Exception:
            pass

        fallback_script = """
        ({roleInterest, eeoDefault, authYes, sponsorYes, basedInUsCanada, inferenceYes, startupYes, firstName, lastName, email, phone, location, linkedin, github, website, country, sideProjectsText, phoneticName, currentCompany}) => {
          const lower = (v) => (v || "").toLowerCase();
          const empty = (v) => !v || !String(v).trim();
          const has = (txt, needles) => needles.some((n) => lower(txt).includes(n));
          const normalize = (v) => (v || "").toLowerCase().replace(/\\s+/g, " ").trim();
          let changed = 0;

          const commitInput = (input, value) => {
            if (!input || empty(value)) return false;
            try {
              input.focus();
              input.value = value;
              input.dispatchEvent(new Event("input", { bubbles: true }));
              input.dispatchEvent(new Event("change", { bubbles: true }));
              input.blur();
              return true;
            } catch (_e) {
              return false;
            }
          };

          const fillById = (id, value) => {
            if (empty(value)) return;
            const input = document.getElementById(id);
            if (commitInput(input, value)) changed += 1;
          };

          const fillByLabelMarkers = (markers, value) => {
            if (empty(value)) return false;
            const wanted = markers.map((m) => normalize(m));
            const labels = Array.from(document.querySelectorAll("label[for]"));
            for (const label of labels) {
              const text = normalize(label.textContent || "");
              if (!wanted.some((m) => text.includes(m))) continue;
              const id = label.getAttribute("for");
              if (!id) continue;
              const input = document.getElementById(id);
              if (!input) continue;
              if (commitInput(input, value)) {
                changed += 1;
                return true;
              }
            }
            return false;
          };

          fillById("first_name", firstName);
          fillById("last_name", lastName);
          fillById("email", email);
          fillById("phone", phone);
          fillById("country", country);
          fillByLabelMarkers(["linkedin profile"], linkedin);
          fillByLabelMarkers(["github or personal website", "github"], website || github);
          fillByLabelMarkers(["inference systems for ml products"], inferenceYes);
          fillByLabelMarkers(["early stage startup"], startupYes);
          fillByLabelMarkers(["side projects you're excited about"], sideProjectsText ? "Yes" : "No");
          fillByLabelMarkers(["based in the u.s. or canada"], basedInUsCanada);
          fillByLabelMarkers(["authorized to work in the us"], authYes);
          fillByLabelMarkers(["phonetic spelling"], phoneticName);
          fillByLabelMarkers(["if you answered yes to the above"], sideProjectsText || roleInterest);

          const radioGroups = new Map();
          for (const input of Array.from(document.querySelectorAll("input[type='radio'][name]"))) {
            if (!input.name) continue;
            if (!radioGroups.has(input.name)) radioGroups.set(input.name, []);
            radioGroups.get(input.name).push(input);
          }
          for (const options of radioGroups.values()) {
            if (options.some((o) => o.checked)) continue;
            const labelText = (opt) => {
              const byFor = opt.id ? document.querySelector(`label[for="${opt.id}"]`) : null;
              if (byFor && byFor.textContent) return byFor.textContent;
              const wrap = opt.closest("label");
              if (wrap && wrap.textContent) return wrap.textContent;
              return (opt.parentElement && opt.parentElement.textContent) || "";
            };
            const questionContext = (opt) => {
              const container = opt.closest("fieldset, .application-question, .question, .field, .multi_value");
              if (container && container.textContent) return container.textContent;
              const parent = opt.parentElement;
              return (parent && parent.textContent) || "";
            };
            const ranked = options.map((opt) => ({opt, text: lower(labelText(opt))}));
            const contextBlob = lower(questionContext(options[0]));
            let desired = "";
            if (has(contextBlob, ["based in the u.s. or canada", "based in us or canada"])) desired = lower(basedInUsCanada);
            else if (has(contextBlob, ["authorized to work in the us", "authorized to work in the united states", "legally authorized to work", "authorized to work in the country"])) desired = lower(authYes);
            else if (has(contextBlob, ["inference systems for ml products", "inference systems"])) desired = lower(inferenceYes);
            else if (has(contextBlob, ["early stage startup"])) desired = lower(startupYes);
            else if (has(contextBlob, ["sponsorship", "visa"])) desired = lower(sponsorYes);
            else if (has(contextBlob, ["7 years of software engineering", "at least 7 years", "years of experience with a focus on backend"])) desired = "yes";
            else if (has(contextBlob, ["utc-3", "utc+3", "time zone"])) desired = "yes";

            let pick = desired ? ranked.find((r) => has(r.text, [desired])) : null;
            if (!pick) pick = ranked.find((r) => has(r.text, ["prefer not", "decline", "do not wish", "choose not"]));
            if (!pick) pick = ranked.find((r) => has(r.text, [" no ", " no", "no ", "none"]));
            if (!pick) pick = ranked.find((r) => has(r.text, [" yes ", " yes", "yes "]));
            if (!pick) pick = ranked[0];
            if (!pick) continue;
            pick.opt.checked = true;
            pick.opt.dispatchEvent(new Event("input", { bubbles: true }));
            pick.opt.dispatchEvent(new Event("change", { bubbles: true }));
            changed += 1;
          }

          for (const select of Array.from(document.querySelectorAll("select"))) {
            if (!empty(select.value)) continue;
            const options = Array.from(select.options || []).filter((o) => !empty(o.value));
            if (!options.length) continue;
            const containerText = (select.closest("fieldset, .application-question, .question, .field, .multi_value") || select.parentElement || {}).textContent || "";
            const labelBlob = lower((select.name || "") + " " + (select.id || "") + " " + (select.getAttribute("aria-label") || "") + " " + containerText);
            let pick = options.find((o) => has(o.textContent, ["prefer not", "decline", "do not wish", "choose not"]));
            if (!pick && has(labelBlob, ["country"])) {
              pick = options.find((o) => has(o.textContent, [lower(country), "united states", "usa"]));
            }
            if (!pick && has(labelBlob, ["sponsor", "visa"])) {
              pick = options.find((o) => has(o.textContent, [lower(sponsorYes)]));
            }
            if (!pick && has(labelBlob, ["authoriz", "work"])) {
              pick = options.find((o) => has(o.textContent, [lower(authYes)]));
            }
            if (!pick) pick = options.find((o) => has(o.textContent, [" no ", "no,", "no.", "none"]));
            if (!pick) pick = options[0];
            if (!pick) continue;
            select.value = pick.value;
            select.dispatchEvent(new Event("input", { bubbles: true }));
            select.dispatchEvent(new Event("change", { bubbles: true }));
            changed += 1;
          }

          const textInputs = Array.from(document.querySelectorAll("input[type='text'], input[type='search'], input[type='email'], input[type='tel'], input[type='url'], input:not([type]), textarea"));
          for (const field of textInputs) {
            const required = field.required || field.getAttribute("aria-required") === "true";
            const invalid = field.getAttribute("aria-invalid") === "true";
            if (!(required || invalid) || !empty(field.value)) continue;
            let labelText = "";
            if (field.id) {
              const byFor = document.querySelector(`label[for="${field.id}"]`);
              if (byFor && byFor.textContent) labelText = byFor.textContent;
            }
            if (!labelText) {
              const wrap = field.closest("label");
              if (wrap && wrap.textContent) labelText = wrap.textContent;
            }
            const containerText = (field.closest("fieldset, .application-question, .question, .field, .multi_value") || field.parentElement || {}).textContent || "";
            const blob = lower((field.name || "") + " " + (field.id || "") + " " + (field.getAttribute("aria-label") || "") + " " + (field.placeholder || "") + " " + labelText + " " + containerText);
            if (has(blob, ["first", "given"])) {
              field.value = firstName || field.value;
            } else if (has(blob, ["preferred first name"])) {
              field.value = firstName || field.value;
            } else if (has(blob, ["full legal name"])) {
              field.value = `${firstName || ""} ${lastName || ""}`.trim() || field.value;
            } else if (has(blob, ["last", "family", "surname"])) {
              field.value = lastName || field.value;
            } else if (has(blob, ["email"])) {
              field.value = email || field.value;
            } else if (has(blob, ["phone", "mobile", "tel"])) {
              field.value = phone || field.value;
            } else if (has(blob, ["country"])) {
              field.value = country || field.value;
            } else if (has(blob, ["location (city)", "location city", "location", "city", "state"])) {
              field.value = location || field.value;
            } else if (has(blob, ["current company", "employer", "company"])) {
              field.value = currentCompany || field.value;
            } else if (has(blob, ["why", "interest", "motivation", "cover"])) {
              field.value = roleInterest;
            } else if (has(blob, ["side projects", "something new you're learning", "new you're learning"])) {
              field.value = sideProjectsText || roleInterest;
            } else if (has(blob, ["phonetic"])) {
              field.value = phoneticName || field.value;
            } else if (has(blob, ["how did you hear", "source", "referral"])) {
              field.value = "Direct application";
            } else if (has(blob, ["linkedin"])) {
              field.value = linkedin || field.value;
            } else if (has(blob, ["github"])) {
              field.value = github || field.value;
            } else if (has(blob, ["website", "portfolio"])) {
              field.value = website || github || field.value;
            } else {
              continue;
            }
            field.dispatchEvent(new Event("input", { bubbles: true }));
            field.dispatchEvent(new Event("change", { bubbles: true }));
            changed += 1;
          }

          return changed;
        }
        """
        try:
            scope.evaluate(
                fallback_script,
                {
                    "roleInterest": answers.match_justification
                    or answers.role_interest,
                    "eeoDefault": answers.eeo_default,
                    "authYes": "Yes" if answers.work_authorization_us else "No",
                    "sponsorYes": "Yes" if answers.require_sponsorship else "No",
                    "basedInUsCanada": "Yes" if answers.based_in_us_or_canada else "No",
                    "inferenceYes": "Yes"
                    if answers.inference_systems_experience
                    else "No",
                    "startupYes": "Yes"
                    if answers.early_stage_startup_experience
                    else "No",
                    "firstName": profile.first_name,
                    "lastName": profile.last_name,
                    "email": profile.email,
                    "phone": profile.phone,
                    "location": profile.location,
                    "linkedin": profile.linkedin,
                    "github": profile.github,
                    "website": profile.website,
                    "country": answers.country,
                    "sideProjectsText": answers.side_projects_text,
                    "phoneticName": answers.phonetic_name,
                    "currentCompany": profile.current_company,
                },
            )
        except Exception:
            try:
                page.evaluate(
                    fallback_script,
                    {
                        "roleInterest": answers.match_justification
                        or answers.role_interest,
                        "eeoDefault": answers.eeo_default,
                        "authYes": "Yes" if answers.work_authorization_us else "No",
                        "sponsorYes": "Yes" if answers.require_sponsorship else "No",
                        "basedInUsCanada": "Yes"
                        if answers.based_in_us_or_canada
                        else "No",
                        "inferenceYes": "Yes"
                        if answers.inference_systems_experience
                        else "No",
                        "startupYes": "Yes"
                        if answers.early_stage_startup_experience
                        else "No",
                        "firstName": profile.first_name,
                        "lastName": profile.last_name,
                        "email": profile.email,
                        "phone": profile.phone,
                        "location": profile.location,
                        "linkedin": profile.linkedin,
                        "github": profile.github,
                        "website": profile.website,
                        "country": answers.country,
                        "sideProjectsText": answers.side_projects_text,
                        "phoneticName": answers.phonetic_name,
                        "currentCompany": profile.current_company,
                    },
                )
            except Exception:
                pass
        if not self._click_submit(scope, page):
            return False
        try:
            page.wait_for_timeout(1500)
        except Exception:
            pass
        return True

    def _extract_failure_details(self, page: Any, scope: Any) -> Optional[str]:
        patterns = (
            r"required field",
            r"this field is required",
            r"please complete",
            r"please fill",
            r"missing required",
        )
        texts: List[str] = []
        for target in (scope, page):
            try:
                text = target.inner_text("body")
            except Exception:
                text = ""
            if text:
                texts.append(str(text).lower())
        blob = "\n".join(texts)
        verification_markers = (
            "verification code was sent",
            "security code",
            "confirm you're a human",
            "8-character code",
        )
        if any(marker in blob for marker in verification_markers):
            match = re.search(r"verification code was sent to\s+([^\s]+)", blob, re.I)
            if match:
                recipient = match.group(1).strip().strip(".,;:")
                return f"verification_code_required:{recipient}"
            return "verification_code_required"
        if any(re.search(p, blob, re.I) for p in patterns):
            try:
                fields = page.evaluate(
                    """
                    () => {
                      const invalid = Array.from(document.querySelectorAll('[aria-invalid="true"], .field_with_errors input, .field_with_errors textarea, .field_with_errors select'));
                      const labels = [];
                      for (const el of invalid) {
                        const id = el.getAttribute('id');
                        let label = '';
                        if (id) {
                          const byFor = document.querySelector(`label[for="${id}"]`);
                          if (byFor && byFor.textContent) label = byFor.textContent;
                        }
                        if (!label) {
                          const wrap = el.closest('label');
                          if (wrap && wrap.textContent) label = wrap.textContent;
                        }
                        if (!label) label = el.getAttribute('aria-label') || '';
                        if (!label) label = el.getAttribute('name') || '';
                        label = String(label || '').replace(/\\s+/g, ' ').trim();
                        if (label) labels.push(label);
                      }
                      return Array.from(new Set(labels)).slice(0, 8);
                    }
                    """
                )
            except Exception:
                fields = []
            if isinstance(fields, list) and fields:
                return "required_fields_unanswered_after_retry:" + ",".join(
                    str(v).strip() for v in fields if str(v).strip()
                )
            return "required_fields_unanswered_after_retry"
        return None


class LeverAdapter(PlaywrightFormAdapter):
    name = "lever"
    host_patterns = (re.compile(r"lever\.co"),)
    submit_button_patterns = (r"submit application", r"submit", r"apply")
    success_text_patterns = (
        r"thank you",
        r"thanks for applying",
        r"application has been submitted",
        r"application submitted",
        r"application received",
        r"we'll be in touch",
        r"we have received your application",
        r"your application was submitted",
    )
    success_url_patterns = (
        r"thank[-_]?you",
        r"submitted",
        r"confirmation",
    )

    # ------------------------------------------------------------------ #
    #  Form scope resolution (Lever-specific)                             #
    # ------------------------------------------------------------------ #

    def _resolve_form_scope(self, page: Any) -> Optional[Any]:
        """Lever jobs show a description page first; navigate to /apply if needed."""
        # First try the base approach (already on the form page).
        scope = super()._resolve_form_scope(page)
        if scope is not None:
            return scope

        # Lever pages have an "Apply for this job" link that navigates to /apply
        current_url = str(getattr(page, "url", "") or "")
        if "/apply" not in current_url.lower():
            # Try direct navigation to /apply variant
            apply_url = current_url.rstrip("/") + "/apply"
            try:
                page.goto(apply_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(1500)
                scope = super()._resolve_form_scope(page)
                if scope is not None:
                    return scope
            except Exception:
                pass

        # Try clicking various Lever apply controls with longer waits
        lever_apply_markers = (
            r"apply for this job",
            r"apply now",
            r"apply",
        )
        for marker in lever_apply_markers:
            for role in ("link", "button"):
                try:
                    control = page.get_by_role(
                        role, name=re.compile(marker, re.I)
                    ).first
                    if control.count() > 0:
                        control.click(timeout=3000)
                        # Lever may do a full page navigation; wait for it
                        page.wait_for_timeout(2000)
                        scope = super()._resolve_form_scope(page)
                        if scope is not None:
                            return scope
                except Exception:
                    continue

        # Last resort: check if we landed on an /apply page after navigation
        try:
            page.wait_for_timeout(1000)
        except Exception:
            pass
        return super()._resolve_form_scope(page)

    # ------------------------------------------------------------------ #
    #  After resume upload: wait for Lever to process                     #
    # ------------------------------------------------------------------ #

    def _after_resume_upload(self, scope: Any, page: Any) -> None:
        """Wait for Lever to finish processing the uploaded resume."""
        try:
            page.wait_for_timeout(2000)
        except Exception:
            return
        # Some Lever forms show a loading/processing indicator
        for _ in range(8):
            try:
                text = str(page.inner_text("body") or "").lower()
            except Exception:
                text = ""
            if "uploading" in text or "processing" in text:
                try:
                    page.wait_for_timeout(1000)
                except Exception:
                    break
            else:
                break

    # ------------------------------------------------------------------ #
    #  Yes/No question helper (radios + selects)                          #
    # ------------------------------------------------------------------ #

    def _set_yes_no_question_by_markers(
        self, page: Any, markers: Sequence[str], answer_yes: bool
    ) -> bool:
        script = """
        ({markers, answerYes}) => {
          const normalize = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const markerSet = markers.map((m) => normalize(m));
          const desired = answerYes ? 'yes' : 'no';
          const nodes = Array.from(document.querySelectorAll('label, legend, p, span, div, h3, h4'));
          const textMatches = (text) => {
            const t = normalize(text);
            return markerSet.some((m) => t.includes(m));
          };
          const choiceText = (input) => {
            const id = input.getAttribute('id');
            if (id) {
              const byFor = document.querySelector(`label[for="${id}"]`);
              if (byFor && byFor.textContent) return byFor.textContent;
            }
            const wrap = input.closest('label');
            if (wrap && wrap.textContent) return wrap.textContent;
            return (input.parentElement && input.parentElement.textContent) || '';
          };
          for (const node of nodes) {
            if (!textMatches(node.textContent || '')) continue;
            const container = node.closest('fieldset, .application-question, .question, .field, .application-additional, [class*="custom-question"]') || node.parentElement;
            if (!container) continue;
            const radios = Array.from(container.querySelectorAll('input[type="radio"]'));
            for (const radio of radios) {
              const txt = normalize(choiceText(radio));
              if (txt.includes(desired)) {
                radio.click();
                radio.dispatchEvent(new Event('input', { bubbles: true }));
                radio.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
              }
            }
            const selects = Array.from(container.querySelectorAll('select'));
            for (const select of selects) {
              const options = Array.from(select.options || []);
              const match = options.find((option) => normalize(option.textContent || '').includes(desired));
              if (match) {
                select.value = match.value;
                select.dispatchEvent(new Event('input', { bubbles: true }));
                select.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
              }
            }
          }
          return false;
        }
        """
        try:
            return bool(
                page.evaluate(
                    script,
                    {"markers": list(markers), "answerYes": bool(answer_yes)},
                )
            )
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    #  Fill unanswered selects with safe defaults                         #
    # ------------------------------------------------------------------ #

    def _fill_unanswered_selects(self, scope: Any, page: Any) -> int:
        """Fill any unanswered <select> elements with safe defaults."""
        preferred = [
            "prefer not",
            "decline to",
            "do not wish",
            "don't wish",
            "choose not to",
        ]
        no_like = [" no ", "no,", "no.", "none", "not"]
        script = """
        ({preferred, noLike}) => {
          const normalize = (value) => (value || "").toLowerCase();
          const hasToken = (value, tokens) => tokens.some((token) => normalize(value).includes(token));
          let changed = 0;
          for (const select of Array.from(document.querySelectorAll("select"))) {
            const current = normalize(select.value);
            if (current) continue;
            const options = Array.from(select.options || []).filter((option) => normalize(option.value));
            if (!options.length) continue;
            let choice = options.find((option) => hasToken(option.textContent, preferred));
            if (!choice) {
              choice = options.find((option) => noLike.some((token) => normalize(option.textContent).includes(token)));
            }
            if (!choice) {
              choice = options[0];
            }
            if (!choice) continue;
            select.value = choice.value;
            select.dispatchEvent(new Event("input", { bubbles: true }));
            select.dispatchEvent(new Event("change", { bubbles: true }));
            changed += 1;
          }
          return changed;
        }
        """
        total = 0
        for target in (scope, page):
            try:
                changed = int(
                    target.evaluate(script, {"preferred": preferred, "noLike": no_like})
                    or 0
                )
            except Exception:
                continue
            total += changed
        return total

    # ------------------------------------------------------------------ #
    #  Fill unanswered radio groups with safe defaults                    #
    # ------------------------------------------------------------------ #

    def _fill_unanswered_radio_groups(self, scope: Any, page: Any) -> int:
        """Fill any unanswered radio button groups with safe defaults."""
        preferred = [
            "prefer not",
            "decline to",
            "do not wish",
            "don't wish",
            "choose not to",
        ]
        no_like = [" no ", "no,", "no.", "none", "not"]
        script = """
        ({preferred, noLike}) => {
          const normalize = (value) => (value || "").toLowerCase();
          const radioGroups = new Map();
          for (const input of Array.from(document.querySelectorAll("input[type='radio'][name]"))) {
            if (!input.name) continue;
            if (!radioGroups.has(input.name)) radioGroups.set(input.name, []);
            radioGroups.get(input.name).push(input);
          }
          const labelFor = (input) => {
            const byId = input.id ? document.querySelector(`label[for="${input.id}"]`) : null;
            if (byId && byId.textContent) return byId.textContent;
            const wrapped = input.closest("label");
            if (wrapped && wrapped.textContent) return wrapped.textContent;
            const aria = input.getAttribute("aria-label");
            if (aria) return aria;
            const parentText = input.parentElement && input.parentElement.textContent;
            return parentText || "";
          };
          const hasToken = (value, tokens) => tokens.some((token) => normalize(value).includes(token));
          let changed = 0;
          for (const options of radioGroups.values()) {
            if (options.some((option) => option.checked)) continue;
            const ranked = options.map((option) => ({ option, text: normalize(labelFor(option)) }));
            let choice = ranked.find((item) => hasToken(item.text, preferred));
            if (!choice) {
              choice = ranked.find((item) => noLike.some((token) => item.text.includes(token)));
            }
            if (!choice && ranked.length > 0) {
              choice = ranked[0];
            }
            if (!choice) continue;
            choice.option.checked = true;
            choice.option.dispatchEvent(new Event("input", { bubbles: true }));
            choice.option.dispatchEvent(new Event("change", { bubbles: true }));
            changed += 1;
          }
          return changed;
        }
        """
        total = 0
        for target in (scope, page):
            try:
                changed = int(
                    target.evaluate(script, {"preferred": preferred, "noLike": no_like})
                    or 0
                )
            except Exception:
                continue
            total += changed
        return total

    # ------------------------------------------------------------------ #
    #  Required answers (Lever-specific fields)                           #
    # ------------------------------------------------------------------ #

    def _apply_required_answers(
        self, scope: Any, page: Any, answers: SubmitAnswers
    ) -> List[str]:
        # Text fields
        self._fill_text(scope, "Current location", "Coral Springs, FL, USA")
        self._fill_text(scope, "Location", "Coral Springs, FL, USA")
        self._fill_text(scope, "City", "Coral Springs")
        self._fill_text(scope, "Current company", "Subway")
        self._fill_text(scope, "Current title", "Software Engineer")
        self._fill_text(scope, "Years of experience", "15")
        self._fill_text(scope, "Salary expectations", "Open to discussion")
        self._fill_text(scope, "Desired salary", "Open to discussion")
        self._fill_text(scope, "How did you hear about this role", "Job board")
        self._fill_text(scope, "How did you hear about us", "Job board")
        self._fill_text(scope, "How did you find us", "Job board")
        self._fill_text(scope, "Where did you hear about this position", "Job board")

        cover_text = (
            f"{answers.role_interest}\n\n{answers.match_justification}".strip()
            if answers.match_justification
            else answers.role_interest
        )
        self._fill_text(scope, "Cover letter", cover_text)
        self._fill_text(
            scope, "Anything else", answers.match_justification or answers.role_interest
        )
        self._fill_text(
            scope,
            "Additional information",
            answers.match_justification or answers.role_interest,
        )
        self._fill_text(scope, "Why are you interested", answers.role_interest)
        self._fill_text(scope, "Why do you want to work", answers.role_interest)
        self._fill_text(scope, "What interests you", answers.role_interest)

        # Yes/No questions via radios and selects
        self._set_yes_no_question_by_markers(
            page,
            (
                "based in the united states or canada",
                "based in us/canada",
                "currently based in",
                "are you located in",
            ),
            answers.based_in_us_or_canada,
        )
        self._set_yes_no_question_by_markers(
            page,
            (
                "legally authorized to work in the u.s.",
                "authorized to work in the us",
                "authorized to work in the united states",
                "work authorization",
                "eligible to work",
            ),
            answers.work_authorization_us,
        )
        self._set_yes_no_question_by_markers(
            page,
            (
                "require visa sponsorship",
                "sponsorship now or in the future",
                "visa sponsorship",
                "require sponsorship",
                "immigration sponsorship",
            ),
            answers.require_sponsorship,
        )
        self._set_yes_no_question_by_markers(
            page,
            ("18 years of age or older", "at least 18", "over 18"),
            True,
        )

        # Fill any remaining unanswered selects and radios with safe defaults
        self._fill_unanswered_selects(scope, page)
        self._fill_unanswered_radio_groups(scope, page)

        return []

    # ------------------------------------------------------------------ #
    #  Pre-submit fill (additional Lever fields)                          #
    # ------------------------------------------------------------------ #

    def _pre_submit_fill(
        self, scope: Any, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> None:
        """Fill Lever-specific fields that may appear after resume upload."""
        # Lever forms sometimes auto-populate fields from the resume but leave
        # some blank. Re-fill core fields to be safe.
        self._fill_text(scope, "First Name", profile.first_name)
        self._fill_text(scope, "Last Name", profile.last_name)
        self._fill_text(scope, "Full Name", f"{profile.first_name} {profile.last_name}")
        self._fill_text(scope, "Email", profile.email)
        self._fill_text(scope, "Phone", profile.phone)
        if profile.linkedin:
            self._fill_text(scope, "LinkedIn", profile.linkedin)
            self._fill_text(scope, "LinkedIn URL", profile.linkedin)
            self._fill_text(scope, "LinkedIn Profile", profile.linkedin)
        if profile.github:
            self._fill_text(scope, "GitHub", profile.github)
            self._fill_text(scope, "GitHub URL", profile.github)
        if profile.website:
            self._fill_text(scope, "Website", profile.website)
            self._fill_text(scope, "Portfolio", profile.website)
            self._fill_text(scope, "Personal website", profile.website)

        # Lever custom questions via JS fallback
        self._lever_js_fallback_fill(page, profile, answers)

    def _lever_js_fallback_fill(
        self, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> None:
        """JS-based fallback fill for Lever form fields that Playwright selectors miss."""
        script = """
        ({firstName, lastName, email, phone, linkedin, github, website, location, company, coverText, roleInterest}) => {
          const empty = (v) => !v || !String(v).trim();
          const normalize = (v) => (v || "").toLowerCase().replace(/\\s+/g, " ").trim();
          let changed = 0;

          const commitInput = (input, value) => {
            if (!input || empty(value)) return false;
            if (!empty(input.value)) return false;
            try {
              input.focus();
              input.value = value;
              input.dispatchEvent(new Event("input", { bubbles: true }));
              input.dispatchEvent(new Event("change", { bubbles: true }));
              input.blur();
              return true;
            } catch (_e) { return false; }
          };

          const fillByAttr = (attr, patterns, value) => {
            if (empty(value)) return;
            for (const input of Array.from(document.querySelectorAll("input, textarea"))) {
              const attrVal = normalize(input.getAttribute(attr) || "");
              if (patterns.some((p) => attrVal.includes(p))) {
                if (commitInput(input, value)) { changed += 1; return; }
              }
            }
          };

          const fillByLabel = (markers, value) => {
            if (empty(value)) return;
            const wanted = markers.map((m) => normalize(m));
            for (const label of Array.from(document.querySelectorAll("label[for]"))) {
              const text = normalize(label.textContent || "");
              if (!wanted.some((m) => text.includes(m))) continue;
              const forId = label.getAttribute("for");
              if (!forId) continue;
              const input = document.getElementById(forId);
              if (commitInput(input, value)) { changed += 1; return; }
            }
          };

          // Core fields by name/id/data-qa patterns
          fillByAttr("name", ["name", "full-name", "fullname"], firstName + " " + lastName);
          fillByAttr("name", ["first"], firstName);
          fillByAttr("name", ["last"], lastName);
          fillByAttr("name", ["email", "e-mail"], email);
          fillByAttr("name", ["phone", "tel"], phone);
          fillByAttr("name", ["linkedin"], linkedin);
          fillByAttr("name", ["github"], github);
          fillByAttr("name", ["website", "portfolio", "url"], website || github);
          fillByAttr("name", ["location", "city"], location);
          fillByAttr("name", ["company", "employer", "org"], company);

          // Also try by data-qa attribute (some Lever forms use this)
          fillByAttr("data-qa", ["name"], firstName + " " + lastName);
          fillByAttr("data-qa", ["email"], email);
          fillByAttr("data-qa", ["phone"], phone);

          // Fill by label text
          fillByLabel(["linkedin"], linkedin);
          fillByLabel(["github"], github);
          fillByLabel(["website", "portfolio"], website || github);
          fillByLabel(["cover letter"], coverText);
          fillByLabel(["why are you interested", "what interests you"], roleInterest);
          fillByLabel(["how did you hear", "how did you find", "where did you hear"], "Job board");

          return changed;
        }
        """
        cover_text = (
            f"{answers.role_interest}\n\n{answers.match_justification}".strip()
            if answers.match_justification
            else answers.role_interest
        )
        try:
            page.evaluate(
                script,
                {
                    "firstName": profile.first_name,
                    "lastName": profile.last_name,
                    "email": profile.email,
                    "phone": profile.phone,
                    "linkedin": profile.linkedin,
                    "github": profile.github,
                    "website": profile.website,
                    "location": profile.location or "Coral Springs, FL, USA",
                    "company": profile.current_company or "Subway",
                    "coverText": cover_text,
                    "roleInterest": answers.role_interest,
                },
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Post-submit retry with validation error recovery                   #
    # ------------------------------------------------------------------ #

    def _post_submit_retry(
        self, scope: Any, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> bool:
        """Retry submission after detecting validation errors."""
        try:
            page.wait_for_timeout(1000)
        except Exception:
            pass

        # Check for validation errors
        has_errors = False
        error_markers = (
            "required",
            "this field is required",
            "please complete",
            "please fill",
            "missing required",
        )
        for target in (scope, page):
            try:
                text = str(target.inner_text("body") or "").lower()
            except Exception:
                text = ""
            if any(marker in text for marker in error_markers):
                has_errors = True
                break

        if not has_errors:
            return False

        # Re-fill everything
        self._apply_required_answers(scope, page, answers)
        self._pre_submit_fill(scope, page, profile, answers)

        # Fill any still-empty required fields via JS
        try:
            page.evaluate("""
            () => {
              const required = Array.from(document.querySelectorAll('[required], [aria-required="true"]'));
              for (const el of required) {
                if (el.value && String(el.value).trim()) continue;
                if (el.tagName === 'SELECT') {
                  const opts = Array.from(el.options || []).filter(o => o.value);
                  if (opts.length > 0) {
                    el.value = opts[0].value;
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                  }
                }
              }
            }
            """)
        except Exception:
            pass

        if not self._click_submit(scope, page):
            return False
        try:
            page.wait_for_timeout(1500)
        except Exception:
            pass
        return True

    # ------------------------------------------------------------------ #
    #  Failure detail extraction                                          #
    # ------------------------------------------------------------------ #

    def _extract_failure_details(self, page: Any, scope: Any) -> Optional[str]:
        anti_bot = self._detect_antibot_block(page)
        if anti_bot:
            return anti_bot

        checks = (
            "required field",
            "this field is required",
            "please complete",
            "please fill",
            "missing required",
        )
        for target in (scope, page):
            try:
                text = str(target.inner_text("body") or "").lower()
            except Exception:
                text = ""
            if any(check in text for check in checks):
                # Try to identify which fields are still missing
                try:
                    fields = page.evaluate("""
                    () => {
                      const invalid = Array.from(document.querySelectorAll(
                        '[aria-invalid="true"], .error input, .error textarea, .error select, '
                        + '.has-error input, .has-error textarea, .has-error select, '
                        + '[required]:invalid'
                      ));
                      const labels = [];
                      for (const el of invalid) {
                        const id = el.getAttribute('id');
                        let label = '';
                        if (id) {
                          const byFor = document.querySelector('label[for="' + id + '"]');
                          if (byFor && byFor.textContent) label = byFor.textContent;
                        }
                        if (!label) {
                          const wrap = el.closest('label');
                          if (wrap && wrap.textContent) label = wrap.textContent;
                        }
                        if (!label) label = el.getAttribute('aria-label') || '';
                        if (!label) label = el.getAttribute('name') || '';
                        label = String(label || '').replace(/\\s+/g, ' ').trim();
                        if (label) labels.push(label);
                      }
                      return Array.from(new Set(labels)).slice(0, 8);
                    }
                    """)
                except Exception:
                    fields = []
                if isinstance(fields, list) and fields:
                    return "required_fields_unanswered_after_retry:" + ",".join(
                        str(v).strip() for v in fields if str(v).strip()
                    )
                return "required_fields_unanswered_after_retry"
        return None


class WorkdayAdapter(SiteAdapter):
    """Adapter for Workday-based career portals (*.myworkdayjobs.com, *.wd*.myworkday.com).

    Workday uses a multi-page application flow with ``data-automation-id``
    attributes on form controls.  The adapter navigates through each page
    (resume upload, personal info, experience, self-identification, review)
    by clicking the Next button, fills fields using ``data-automation-id``
    selectors, and captures confirmation evidence on success.
    """

    name = "workday"

    _HOST_PATTERNS = (
        re.compile(r"myworkdayjobs\.com$"),
        re.compile(r"\.wd\d+\.myworkday\.com$"),
        re.compile(r"myworkday\.com$"),
    )

    _ALREADY_APPLIED_MARKERS = (
        "you have already applied",
        "already submitted an application",
        "previously applied",
        "duplicate application",
    )

    _SUCCESS_MARKERS = (
        "thank you for applying",
        "application has been submitted",
        "application submitted",
        "your application has been received",
        "successfully submitted",
        "thank you for your interest",
        "we have received your application",
        "application complete",
    )

    _SUCCESS_URL_PATTERNS = (
        re.compile(r"thank[-_]?you", re.I),
        re.compile(r"submitted", re.I),
        re.compile(r"confirmation", re.I),
        re.compile(r"application.*complete", re.I),
    )

    _NEXT_BTN = 'button[data-automation-id="bottom-navigation-next-button"]'
    _SUBMIT_BTN_SELECTORS = ('button[data-automation-id="submit-button"]',)
    _SUBMIT_BTN_TEXT_PATTERNS = (
        re.compile(r"^submit$", re.I),
        re.compile(r"^submit application$", re.I),
    )
    _FILE_INPUT = 'input[data-automation-id="file-upload-input-ref"]'
    _FILE_DROP = '[data-automation-id="file-upload-drop-zone"]'

    # ---- SiteAdapter interface ----

    def matches(self, url: str) -> bool:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        return any(p.search(host) for p in self._HOST_PATTERNS)

    def submit(
        self,
        task: SubmitTask,
        profile: Profile,
        auth: AdapterAuth,
        answers: SubmitAnswers,
    ) -> SubmitResult:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            return SubmitResult(
                adapter=self.name,
                verified=False,
                screenshot=None,
                details=f"playwright_unavailable: {e}",
            )

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                user_agent = (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )
                context_kwargs: Dict[str, Any] = {
                    "user_agent": user_agent,
                    "viewport": {"width": 1280, "height": 800},
                    "device_scale_factor": 2,
                }
                if auth.storage_state is not None:
                    context_kwargs["storage_state"] = auth.storage_state
                context = browser.new_context(**context_kwargs)
                page = context.new_page()

                if _PlaywrightStealth:
                    _PlaywrightStealth().apply_stealth_sync(page)

                # Navigate to the application URL
                page.goto(task.url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)

                # Check for "already applied" before proceeding
                already = self._check_already_applied(page)
                if already:
                    task.confirmation_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        page.screenshot(
                            path=str(task.confirmation_path), full_page=True
                        )
                    except Exception:
                        pass
                    browser.close()
                    return SubmitResult(
                        adapter=self.name,
                        verified=False,
                        screenshot=task.confirmation_path
                        if task.confirmation_path.exists()
                        else None,
                        details="already_applied",
                    )

                # Try to reach the application form.
                # Workday may land on a job description page first.
                if not self._has_workday_form(page):
                    self._navigate_to_apply(page)

                if not self._has_workday_form(page):
                    anti_bot = self._detect_antibot(page)
                    task.confirmation_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        page.screenshot(
                            path=str(task.confirmation_path), full_page=True
                        )
                    except Exception:
                        pass
                    browser.close()
                    return SubmitResult(
                        adapter=self.name,
                        verified=False,
                        screenshot=task.confirmation_path
                        if task.confirmation_path.exists()
                        else None,
                        details=anti_bot or "workday_form_not_found",
                    )

                # -- Step 1: Resume upload page --
                if not self._upload_resume(page, task.resume_path):
                    task.confirmation_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        page.screenshot(
                            path=str(task.confirmation_path), full_page=True
                        )
                    except Exception:
                        pass
                    browser.close()
                    return SubmitResult(
                        adapter=self.name,
                        verified=False,
                        screenshot=task.confirmation_path
                        if task.confirmation_path.exists()
                        else None,
                        details="resume_upload_failed",
                    )

                # Advance past the resume page
                if not self._click_next(page):
                    # Some Workday portals combine resume + info on one page
                    pass

                # -- Step 2: Personal information page --
                self._fill_personal_info(page, profile, answers)

                # Advance through remaining pages (experience, EEO, review)
                # Keep clicking Next until we reach Submit or run out of pages
                max_pages = 8
                for _ in range(max_pages):
                    # Check if we're on a confirmation page already
                    if self._check_confirmation(page):
                        break

                    # Try to fill EEO / self-identification defaults on every page
                    self._fill_eeo_defaults(page)

                    # Look for a Submit button (means we're on the review page)
                    if self._find_submit_button(page) is not None:
                        break

                    # Try Next
                    if not self._click_next(page):
                        break

                # Click Submit if found
                submit_btn = self._find_submit_button(page)
                if submit_btn is not None:
                    try:
                        submit_btn.click(timeout=5000)
                        page.wait_for_timeout(3000)
                    except Exception:
                        pass

                confirmed = self._check_confirmation(page)
                task.confirmation_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    page.screenshot(path=str(task.confirmation_path), full_page=True)
                except Exception:
                    pass

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
        except Exception as e:
            if "Timeout" in type(e).__name__:
                return SubmitResult(
                    adapter=self.name,
                    verified=False,
                    screenshot=None,
                    details="timeout",
                )
            return SubmitResult(
                adapter=self.name,
                verified=False,
                screenshot=None,
                details=f"exception: {e}",
            )

    # ---- Internal helpers ----

    def _check_already_applied(self, page: Any) -> bool:
        try:
            text = str(page.inner_text("body") or "").lower()
        except Exception:
            return False
        return any(m in text for m in self._ALREADY_APPLIED_MARKERS)

    def _check_confirmation(self, page: Any) -> bool:
        try:
            text = str(page.inner_text("body") or "").lower()
        except Exception:
            text = ""
        if any(m in text for m in self._SUCCESS_MARKERS):
            return True
        page_url = str(getattr(page, "url", "") or "").lower()
        return any(p.search(page_url) for p in self._SUCCESS_URL_PATTERNS)

    def _has_workday_form(self, page: Any) -> bool:
        """Check if the current page has Workday form elements."""
        for selector in (
            self._FILE_INPUT,
            self._FILE_DROP,
            self._NEXT_BTN,
            '[data-automation-id="legalNameSection_firstName"]',
            '[data-automation-id="email"]',
        ):
            try:
                if page.locator(selector).count() > 0:
                    return True
            except Exception:
                continue
        return False

    def _navigate_to_apply(self, page: Any) -> None:
        """Try to reach the application form from the job description page."""
        # Workday "Apply" buttons
        apply_selectors = (
            'a[data-automation-id="jobPostingApplyButton"]',
            'button[data-automation-id="jobPostingApplyButton"]',
        )
        for sel in apply_selectors:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0:
                    btn.click(timeout=5000)
                    page.wait_for_timeout(3000)
                    if self._has_workday_form(page):
                        return
            except Exception:
                continue

        # Generic "Apply" text buttons/links
        for pattern in ("Apply", "Apply Now", "Apply Manually"):
            for role in ("button", "link"):
                try:
                    control = page.get_by_role(
                        role, name=re.compile(rf"^{re.escape(pattern)}$", re.I)
                    ).first
                    if control.count() > 0:
                        control.click(timeout=5000)
                        page.wait_for_timeout(3000)
                        if self._has_workday_form(page):
                            return
                except Exception:
                    continue

        # Handle "Sign In to Apply" flow -- look for guest apply options
        guest_patterns = (
            "apply manually",
            "apply without",
            "continue without",
            "guest",
            "use my last application",
        )
        for pat in guest_patterns:
            try:
                link = page.get_by_text(re.compile(pat, re.I)).first
                if link.count() > 0:
                    link.click(timeout=3000)
                    page.wait_for_timeout(2000)
                    if self._has_workday_form(page):
                        return
            except Exception:
                continue

    def _upload_resume(self, page: Any, resume_path: Path) -> bool:
        """Upload resume via the Workday file input."""
        try:
            file_input = page.locator(self._FILE_INPUT).first
            if file_input.count() > 0:
                file_input.set_input_files(str(resume_path))
                page.wait_for_timeout(2000)
                return True
        except Exception:
            pass

        # Fallback: any file input on the page
        try:
            generic_input = page.locator("input[type='file']").first
            if generic_input.count() > 0:
                generic_input.set_input_files(str(resume_path))
                page.wait_for_timeout(2000)
                return True
        except Exception:
            pass

        return False

    def _fill_personal_info(
        self, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> None:
        """Fill personal information fields on the Workday form."""
        # Direct data-automation-id fields
        field_map: List[tuple] = [
            ("legalNameSection_firstName", profile.first_name),
            ("legalNameSection_lastName", profile.last_name),
            ("email", profile.email),
            ("phone-number", profile.phone),
        ]
        for automation_id, value in field_map:
            if not value:
                continue
            self._fill_workday_field(page, automation_id, value)

        # Country/region dropdown
        if profile.location:
            self._select_workday_dropdown(
                page, "addressSection_countryRegion", "United States of America"
            )
            # Try to set state if location contains state info
            state = self._extract_state(profile.location)
            if state:
                self._select_workday_dropdown(
                    page, "addressSection_countryRegionState", state
                )

        # LinkedIn / website fields (if present)
        if profile.linkedin:
            self._fill_workday_field(page, "linkedin", profile.linkedin)
            self._fill_workday_field(page, "linkedInProfileURL", profile.linkedin)
        if profile.website:
            self._fill_workday_field(page, "website", profile.website)

        # Fallback: use label-based filling for fields not found by automation-id
        self._fill_by_label(page, "First Name", profile.first_name)
        self._fill_by_label(page, "Last Name", profile.last_name)
        self._fill_by_label(page, "Email", profile.email)
        self._fill_by_label(page, "Phone", profile.phone)

    def _fill_workday_field(self, page: Any, automation_id: str, value: str) -> bool:
        """Fill a Workday field identified by data-automation-id."""
        if not value:
            return False
        selectors = (
            f'input[data-automation-id="{automation_id}"]',
            f'textarea[data-automation-id="{automation_id}"]',
            f'[data-automation-id="{automation_id}"] input',
            f'[data-automation-id="{automation_id}"] textarea',
        )
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed(timeout=2000)
                    loc.fill(value, timeout=2000)
                    return True
            except Exception:
                continue
        return False

    def _select_workday_dropdown(
        self, page: Any, automation_id: str, option_text: str
    ) -> bool:
        """Select an option from a Workday dropdown (button-based)."""
        if not option_text:
            return False
        btn_selectors = (
            f'button[data-automation-id="{automation_id}"]',
            f'[data-automation-id="{automation_id}"] button',
            f'[data-automation-id="{automation_id}"]',
        )
        for sel in btn_selectors:
            try:
                btn = page.locator(sel).first
                if btn.count() == 0:
                    continue
                btn.click(timeout=3000)
                page.wait_for_timeout(500)

                # Try to find the option in the opened listbox
                option = page.get_by_role(
                    "option", name=re.compile(re.escape(option_text), re.I)
                ).first
                if option.count() > 0:
                    option.click(timeout=2000)
                    return True

                # Try listbox items
                li = (
                    page.locator('[role="listbox"] [role="option"]')
                    .filter(has_text=re.compile(re.escape(option_text), re.I))
                    .first
                )
                if li.count() > 0:
                    li.click(timeout=2000)
                    return True

                # Close the dropdown if no match
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
            except Exception:
                continue
        return False

    def _fill_by_label(self, page: Any, label: str, value: str) -> bool:
        """Fallback: fill by label text."""
        if not value:
            return False
        try:
            field = page.get_by_label(label, exact=False).first
            if field.count() > 0:
                field.fill(value, timeout=2000)
                return True
        except Exception:
            pass
        return False

    def _fill_eeo_defaults(self, page: Any) -> None:
        """Select 'Decline to Self-Identify' on EEO / self-ID pages."""
        decline_patterns = (
            re.compile(r"decline", re.I),
            re.compile(r"prefer not", re.I),
            re.compile(r"do not wish", re.I),
            re.compile(r"choose not", re.I),
        )

        eeo_sections = (
            "gender",
            "ethnicity",
            "veteran",
            "disability",
            "race",
            "selfIdentification",
        )
        for section in eeo_sections:
            try:
                container = page.locator(f'[data-automation-id*="{section}"]').first
                if container.count() == 0:
                    continue
                for pat in decline_patterns:
                    try:
                        radio = container.get_by_label(pat).first
                        if radio.count() > 0:
                            radio.check(timeout=1500)
                            break
                    except Exception:
                        continue
            except Exception:
                continue

        # Also try selecting "Decline" in any radio on the page
        for pat in decline_patterns:
            try:
                options = page.get_by_role("radio", name=pat)
                count = options.count()
                for i in range(count):
                    try:
                        options.nth(i).check(timeout=1000)
                    except Exception:
                        continue
            except Exception:
                continue

    def _click_next(self, page: Any) -> bool:
        """Click the Workday 'Next' button and wait for page transition."""
        try:
            btn = page.locator(self._NEXT_BTN).first
            if btn.count() > 0:
                btn.scroll_into_view_if_needed(timeout=2000)
                btn.click(timeout=5000)
                page.wait_for_timeout(2000)
                return True
        except Exception:
            pass
        return False

    def _find_submit_button(self, page: Any) -> Any:
        """Locate the Submit button on the review page."""
        for sel in self._SUBMIT_BTN_SELECTORS:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0:
                    return btn
            except Exception:
                continue

        for pat in self._SUBMIT_BTN_TEXT_PATTERNS:
            try:
                btn = page.get_by_role("button", name=pat).first
                if btn.count() > 0:
                    return btn
            except Exception:
                continue

        return None

    def _detect_antibot(self, page: Any) -> Optional[str]:
        """Detect anti-bot / CAPTCHA challenges on Workday pages."""
        try:
            text = str(page.inner_text("body") or "").lower()
        except Exception:
            text = ""
        try:
            html_text = str(page.content() or "").lower()
        except Exception:
            html_text = ""
        captcha_markers = (
            "captcha",
            "recaptcha",
            "hcaptcha",
            "turnstile",
            "verify you are human",
            "i'm not a robot",
            "security challenge",
        )
        if any(m in text for m in captcha_markers) or any(
            m in html_text for m in captcha_markers
        ):
            return "antibot_challenge_detected"
        sign_in_markers = (
            "sign in to apply",
            "create account to apply",
            "log in to apply",
        )
        if any(m in text for m in sign_in_markers):
            return "sign_in_required"
        return None

    @staticmethod
    def _extract_state(location: str) -> str:
        """Extract US state from a location string like 'Coral Springs, FL, USA'."""
        us_states = {
            "AL": "Alabama",
            "AK": "Alaska",
            "AZ": "Arizona",
            "AR": "Arkansas",
            "CA": "California",
            "CO": "Colorado",
            "CT": "Connecticut",
            "DE": "Delaware",
            "FL": "Florida",
            "GA": "Georgia",
            "HI": "Hawaii",
            "ID": "Idaho",
            "IL": "Illinois",
            "IN": "Indiana",
            "IA": "Iowa",
            "KS": "Kansas",
            "KY": "Kentucky",
            "LA": "Louisiana",
            "ME": "Maine",
            "MD": "Maryland",
            "MA": "Massachusetts",
            "MI": "Michigan",
            "MN": "Minnesota",
            "MS": "Mississippi",
            "MO": "Missouri",
            "MT": "Montana",
            "NE": "Nebraska",
            "NV": "Nevada",
            "NH": "New Hampshire",
            "NJ": "New Jersey",
            "NM": "New Mexico",
            "NY": "New York",
            "NC": "North Carolina",
            "ND": "North Dakota",
            "OH": "Ohio",
            "OK": "Oklahoma",
            "OR": "Oregon",
            "PA": "Pennsylvania",
            "RI": "Rhode Island",
            "SC": "South Carolina",
            "SD": "South Dakota",
            "TN": "Tennessee",
            "TX": "Texas",
            "UT": "Utah",
            "VT": "Vermont",
            "VA": "Virginia",
            "WA": "Washington",
            "WV": "West Virginia",
            "WI": "Wisconsin",
            "WY": "Wyoming",
            "DC": "District of Columbia",
        }
        parts = [p.strip() for p in location.split(",")]
        for part in parts:
            upper = part.upper()
            if upper in us_states:
                return us_states[upper]
            for _code, full_name in us_states.items():
                if part.strip().lower() == full_name.lower():
                    return full_name
        return ""


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


def _html_to_text(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", value or "")
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\\s*>", "\n\n", text)
    text = re.sub(r"(?i)</li\\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [re.sub(r"\\s+", " ", line).strip() for line in text.splitlines()]
    compact = [line for line in lines if line]
    return "\n".join(compact) + ("\n" if compact else "")


def _write_simple_docx(text: str, out_path: Path) -> None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        lines = ["Resume"]

    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{escape(line)}</w:t></w:r></w:p>'
        for line in lines
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}"
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("word/document.xml", document_xml)


def _create_docx_from_html(resume_html_path: Path) -> Optional[Path]:
    if not resume_html_path.exists():
        return None
    docx_path = resume_html_path.with_suffix(".docx")
    if docx_path.exists():
        return docx_path
    try:
        html_text = resume_html_path.read_text(encoding="utf-8", errors="replace")
        _write_simple_docx(_html_to_text(html_text), docx_path)
    except Exception:
        return None
    return docx_path if docx_path.exists() else None


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


def _resolve_resume_jsonld(company: str, role: str) -> Optional[Path]:
    company_slug = _slug(company)
    role_slug = _slug(role)
    base = ROOT / "applications" / company_slug / "tailored_resumes"
    if not base.exists():
        return None
    return _select_best_artifact(list(base.glob("*.jsonld")), role_slug)


def _enhance_answers_with_jsonld(
    answers: SubmitAnswers, company: str, role: str
) -> SubmitAnswers:
    import copy
    import json

    new_answers = copy.copy(answers)
    jsonld_path = _resolve_resume_jsonld(company, role)
    if jsonld_path and jsonld_path.exists():
        try:
            data = json.loads(jsonld_path.read_text(encoding="utf-8"))
            if "description" in data and data["description"]:
                new_answers.match_justification = data["description"]
        except Exception:
            pass
    return new_answers


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
    has_fde_role = bool(FDE_ROLE_RE.search(hay))
    has_fde_signal = bool(FDE_SIGNAL_RE.search(hay))
    has_integration_signal = bool(INTEGRATION_SIGNAL_RE.search(hay))
    if has_fde_role:
        signals.append("fde-role")
    if has_fde_signal:
        signals.append("customer-integration")
    if has_integration_signal:
        signals.append("integration-heavy")
    if PYTHON_RE.search(hay):
        signals.append("python")
    if VOICE_AUDIO_RE.search(hay):
        signals.append("voice-audio")
    # Only explicit FDE titles should trigger strict FDE scoring requirements.
    # Customer/integration language can appear in many non-FDE technical roles.
    track = "fde" if "fde-role" in signals else "general"
    return track, sorted(set(signals))


def _role_keywords(role: str) -> List[str]:
    tokens = [t.lower() for t in ROLE_TOKEN_RE.findall(role or "")]
    out: List[str] = []
    seen = set()
    for token in tokens:
        if token in ROLE_STOPWORDS:
            continue
        if len(token) <= 2 and token not in {"ai", "ml"}:
            continue
        if token not in seen:
            out.append(token)
            seen.add(token)
    return out


def _adapter_name_for_url(url: str, adapters: Sequence[SiteAdapter]) -> Optional[str]:
    found = _find_adapter(url, adapters)
    return found.name if found is not None else None


def _host_matches_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _infer_remote_profile(
    row: Dict[str, str],
    *,
    job_text: str,
) -> tuple[str, int, List[str]]:
    csv_score = str(row.get("Remote Likelihood Score", "")).strip()
    csv_policy = str(row.get("Remote Policy", "")).strip().lower()
    if (csv_score and csv_score.isdigit() and int(csv_score) >= 100) or csv_policy == "override":
        return "override", 100, ["csv_override"]

    location = str(row.get("Location", "") or "")
    notes = str(row.get("Notes", "") or "")
    tags = str(row.get("Tags", "") or "")
    url = str(row.get("Career Page URL", "") or "")
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    hay = " ".join([location, tags, notes, job_text]).lower()

    policy = "unknown"
    score = 45
    evidence: List[str] = []

    if REMOTE_NEGATIVE_RE.search(hay):
        policy = "onsite"
        score = 10
        evidence.append("onsite_keyword")
    elif REMOTE_HYBRID_RE.search(hay):
        policy = "hybrid"
        score = 65
        evidence.append("hybrid_keyword")
    elif REMOTE_POSITIVE_RE.search(hay):
        policy = "remote"
        score = 85
        evidence.append("remote_keyword")

    if _host_matches_domain(host, "remoteok.com") or _host_matches_domain(
        host, "remotive.com"
    ):
        evidence.append("remote_feed_source")
        score = min(95, score + 5)

    if REMOTE_US_ONLY_RE.search(hay):
        evidence.append("remote_us_scope")
        score = min(100, score + 3)

    if "hybrid_keyword" in evidence and "onsite_keyword" not in evidence:
        score = max(55, min(score, 75))
    if policy == "onsite":
        score = min(score, 25)

    score = max(0, min(100, int(score)))
    return policy, score, sorted(set(evidence))


def _assess_queue_gate(
    row: Dict[str, str],
    *,
    fit_threshold: int,
    remote_min_score: int,
    adapters: Sequence[SiteAdapter],
) -> QueueGateAssessment:
    company = str(row.get("Company", "")).strip()
    role = str(row.get("Role", "")).strip()
    tags = str(row.get("Tags", "")).strip()
    notes = str(row.get("Notes", "")).strip()

    resume_html_path = _resolve_resume_html(company, role)
    resume_path = _resolve_resume(company, role)
    if resume_path is None and resume_html_path is not None:
        resume_path = _create_docx_from_html(resume_html_path)
    cover_path = _resolve_cover_letter(company, role)
    job_path = _resolve_job_capture(company, role)

    reasons: List[str] = []
    has_tech_title = bool(TECH_ROLE_RE.search(role))
    if (not has_tech_title) or NON_TECH_ROLE_RE.search(role):
        reasons.append("non_technical_role")
    if resume_path is None:
        reasons.append("missing_resume_docx_or_pdf")
    if resume_html_path is None:
        reasons.append("missing_tailored_resume_html")
    if cover_path is None:
        reasons.append("missing_cover_letter")

    job_text = _read_text(job_path)
    resume_html_text = _read_text(resume_html_path).lower()
    track, signals = _role_track_and_signals(role, tags, notes, job_text)
    role_keywords = _role_keywords(role)
    matched_role_keywords = [kw for kw in role_keywords if kw in resume_html_text]
    required_role_keywords = (
        0
        if not role_keywords
        else (1 if len(role_keywords) <= 2 else min(3, len(role_keywords)))
    )
    remote_policy, remote_score, remote_evidence = _infer_remote_profile(
        row, job_text=job_text
    )
    submission_url = _row_submission_url(row, adapters)
    adapter_name = _adapter_name_for_url(submission_url, adapters)
    submission_lane = f"ci_auto:{adapter_name}" if adapter_name else "manual"
    if _is_aggregator_url(submission_url):
        reasons.append("non_direct_application_url")

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
        if "core competencies" in resume_html_text:
            score += 10

    if required_role_keywords:
        score += min(10, len(matched_role_keywords) * 3)
        if len(matched_role_keywords) < required_role_keywords:
            reasons.append(
                "role_keyword_alignment_too_low:"
                f"{len(matched_role_keywords)}/{required_role_keywords}"
            )

    for phrase in GENERIC_RESUME_PHRASES:
        if phrase in resume_html_text:
            reasons.append(f"resume_generic_phrase_detected:{phrase}")

    python_required = "python" in signals
    if python_required and PYTHON_RE.search(resume_html_text):
        score += 10
    if python_required and not PYTHON_RE.search(resume_html_text):
        reasons.append("python_requested_not_explicit_in_resume")

    voice_required = "voice-audio" in signals
    if voice_required and VOICE_AUDIO_RE.search(resume_html_text):
        score += 5
    score += max(0, (remote_score - 50) // 10)

    fit_ok = score >= fit_threshold
    remote_ok = remote_score >= remote_min_score
    if not fit_ok:
        reasons.append(f"fit_score_below_threshold:{score}<{fit_threshold}")
    if not remote_ok:
        reasons.append(
            f"remote_likelihood_below_threshold:{remote_score}<{remote_min_score}"
        )
    if adapter_name is None:
        reasons.append("unsupported_site_for_ci_submit")

    blocking_reasons = {
        "missing_resume_docx_or_pdf",
        "missing_tailored_resume_html",
        "missing_cover_letter",
        "non_technical_role",
        "unsupported_site_for_ci_submit",
        "non_direct_application_url",
    }
    has_quality_block = any(
        reason.startswith("role_keyword_alignment_too_low:")
        or reason.startswith("resume_generic_phrase_detected:")
        for reason in reasons
    )
    eligible = (
        fit_ok
        and remote_ok
        and all(reason not in blocking_reasons for reason in reasons)
        and not has_quality_block
    )
    return QueueGateAssessment(
        eligible=eligible,
        score=score,
        reasons=sorted(set(reasons)),
        role_track=track,
        signals=signals,
        remote_policy=remote_policy,
        remote_score=remote_score,
        remote_evidence=remote_evidence,
        submission_lane=submission_lane,
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
        current_company=str(
            payload.get("current_company", payload.get("current_employer", ""))
        ).strip(),
    )


def _extract_markdown_answer(text: str, label: str) -> str:
    pattern = re.compile(rf"\*\*{re.escape(label)}:\*\*\s*(.+)", re.I)
    match = pattern.search(text or "")
    return match.group(1).strip() if match else ""


def _extract_markdown_bullet_answer(text: str, question_contains: str) -> str:
    pattern = re.compile(r"-\s+\*\*(.+?)\*\*\s+(.+)")
    for q, answer in pattern.findall(text or ""):
        if question_contains.lower() in q.lower():
            return answer.strip()
    return ""


def _load_profile_from_answers_markdown(path: Path) -> Optional[Profile]:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    full_name = _extract_markdown_answer(text, "Name")
    email = _extract_markdown_answer(text, "Email")
    phone = _extract_markdown_answer(text, "Phone")
    if not full_name or not email or not phone:
        return None
    parts = [part for part in full_name.split() if part]
    if len(parts) < 2:
        return None
    first_name = parts[0]
    last_name = " ".join(parts[1:])
    return Profile(
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
        location=_extract_markdown_answer(text, "Current Location"),
        linkedin=_extract_markdown_answer(text, "LinkedIn"),
        github=_extract_markdown_answer(text, "GitHub"),
        website=_extract_markdown_answer(text, "Website"),
        current_company=_extract_markdown_answer(text, "Current Company"),
    )


def _parse_yes_no(value: Any) -> Optional[bool]:
    key = _norm_key(str(value))
    if key in {"yes", "y", "true", "1"}:
        return True
    if key in {"no", "n", "false", "0"}:
        return False
    return None


def _load_answers_from_env(env_name: str) -> Optional[SubmitAnswers]:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    auth_yes_no = _parse_yes_no(
        payload.get("work_authorization_us", payload.get("authorized_to_work_us", ""))
    )
    sponsor_yes_no = _parse_yes_no(
        payload.get("require_sponsorship", payload.get("visa_sponsorship_required", ""))
    )
    role_interest = str(
        payload.get("role_interest", payload.get("why_this_role", ""))
    ).strip()
    eeo_default = str(payload.get("eeo_default", "")).strip()
    country = str(payload.get("country", "United States")).strip() or "United States"
    based_in_us_or_canada = _parse_yes_no(
        payload.get("based_in_us_or_canada", payload.get("located_us_or_canada", "yes"))
    )
    inference_systems_experience = _parse_yes_no(
        payload.get(
            "inference_systems_experience",
            payload.get("inference_experience", "yes"),
        )
    )
    early_stage_startup_experience = _parse_yes_no(
        payload.get(
            "early_stage_startup_experience",
            payload.get("startup_experience", "no"),
        )
    )
    side_projects_text = str(payload.get("side_projects_text", "")).strip()
    phonetic_name = str(payload.get("phonetic_name", "")).strip()

    if auth_yes_no is None or sponsor_yes_no is None:
        return None
    if not role_interest:
        return None
    if not eeo_default:
        return None
    if based_in_us_or_canada is None:
        based_in_us_or_canada = True
    if inference_systems_experience is None:
        inference_systems_experience = True
    if early_stage_startup_experience is None:
        early_stage_startup_experience = False

    return SubmitAnswers(
        work_authorization_us=auth_yes_no,
        require_sponsorship=sponsor_yes_no,
        role_interest=role_interest,
        eeo_default=eeo_default,
        country=country,
        based_in_us_or_canada=based_in_us_or_canada,
        inference_systems_experience=inference_systems_experience,
        early_stage_startup_experience=early_stage_startup_experience,
        side_projects_text=side_projects_text,
        phonetic_name=phonetic_name,
    )


def _load_answers_from_markdown(path: Path) -> Optional[SubmitAnswers]:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    auth_yes_no = _parse_yes_no(
        _extract_markdown_bullet_answer(text, "legally authorized to work")
    )
    sponsor_yes_no = _parse_yes_no(
        _extract_markdown_bullet_answer(text, "Require visa sponsorship")
    )
    based_in_us_or_canada = _parse_yes_no(
        _extract_markdown_bullet_answer(text, "based in us/canada")
    )
    role_interest = _extract_markdown_bullet_answer(text, "interests you in this role")
    eeo_default = _extract_markdown_bullet_answer(text, "Voluntary EEO default")
    if auth_yes_no is None or sponsor_yes_no is None:
        return None
    if not role_interest or not eeo_default:
        return None
    return SubmitAnswers(
        work_authorization_us=auth_yes_no,
        require_sponsorship=sponsor_yes_no,
        role_interest=role_interest,
        eeo_default=eeo_default,
        country="United States",
        based_in_us_or_canada=True
        if based_in_us_or_canada is None
        else based_in_us_or_canada,
        inference_systems_experience=True,
        early_stage_startup_experience=False,
        side_projects_text=(
            "I build side projects around production AI systems, including a multi-model "
            "LLM gateway and autonomous reliability tooling."
        ),
        phonetic_name="EE-gor guh-NA-pol-skee",
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


def _is_manual_submit_blocker(details: str) -> bool:
    value = str(details or "")
    return (
        value == "missing_file_input"
        or value.startswith("required_fields_unanswered_after_retry")
        or value.startswith("verification_code_required")
    )


def _is_ready_status(status: str) -> bool:
    return _norm_key(status) in READY_STATUS_KEYS


def _is_aggregator_url(url: str) -> bool:
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    return bool(host and AGGREGATOR_HOST_RE.search(host))


def _row_submission_url(
    row: Dict[str, str], adapters: Optional[Sequence[SiteAdapter]] = None
) -> str:
    candidates = [
        str(row.get("Application Link", "")).strip(),
        str(row.get("Career Page URL", "")).strip(),
    ]
    candidates = [url for url in candidates if url]
    if not candidates:
        return ""
    if adapters:
        for url in candidates:
            if _find_adapter(url, adapters) is not None:
                return url
    for url in candidates:
        if not _is_aggregator_url(url):
            return url
    return candidates[0]


def _find_adapter(url: str, adapters: Sequence[SiteAdapter]) -> Optional[SiteAdapter]:
    for adapter in adapters:
        if adapter.matches(url):
            return adapter
    return None


def _validate_row(row: Dict[str, str], adapters: Sequence[SiteAdapter]) -> List[str]:
    errs = []
    if not str(row.get("Company", "")).strip():
        errs.append("missing_company")
    if not str(row.get("Role", "")).strip():
        errs.append("missing_role")
    submission_url = _row_submission_url(row, adapters)
    if not submission_url:
        errs.append("missing_url")
    elif _find_adapter(submission_url, adapters) is None:
        errs.append("unsupported_site")
    return errs


def run_pipeline(
    *,
    tracker_csv: Path,
    report_path: Path,
    dry_run: bool,
    queue_only: bool,
    max_jobs: int,
    fail_on_error: bool,
    count_skipped_as_failures: bool = False,
    fit_threshold: int = 70,
    remote_min_score: int = 50,
    require_secret_auth: bool = True,
    auto_promote_ready: bool = True,
    profile_env: str = "CI_SUBMIT_PROFILE_JSON",
    auth_env: str = "CI_SUBMIT_AUTH_JSON",
    answers_env: str = "CI_SUBMIT_ANSWERS_JSON",
    adapters: Optional[Sequence[SiteAdapter]] = None,
    quarantine_blocked: bool = False,
) -> int:
    fields, rows = _read_tracker(tracker_csv)
    adapters = list(
        adapters
        or [AshbyAdapter(), GreenhouseAdapter(), LeverAdapter(), WorkdayAdapter()]
    )
    fields = _ensure_tracker_fields(
        fields, rows, TRACKER_REMOTE_FIELDS + TRACKER_SUBMISSION_FIELDS
    )

    profile = _load_profile_from_env(
        profile_env
    ) or _load_profile_from_answers_markdown(DEFAULT_ANSWERS_MD)
    auth_map = _load_auth_by_adapter(auth_env)
    answers = _load_answers_from_env(answers_env) or _load_answers_from_markdown(
        DEFAULT_ANSWERS_MD
    )

    is_real_submit = (not dry_run) and (not queue_only)
    if is_real_submit and require_secret_auth:
        if profile is None:
            print(
                f"ERROR: missing/invalid submit profile in ${profile_env} "
                f"and no fallback in {DEFAULT_ANSWERS_MD}."
            )
            return 2
        if answers is None:
            print(
                f"ERROR: missing/invalid submit answers in ${answers_env} "
                f"and no fallback in {DEFAULT_ANSWERS_MD}."
            )
            return 2
    
    # Apply placeholders only for dry-runs or queue-only when secrets are missing
    if profile is None:
        profile = Profile(
            first_name="Dry",
            last_name="Run",
            email="dry.run@example.com",
            phone="0000000000",
        )
    if answers is None:
        answers = SubmitAnswers(
            work_authorization_us=True,
            require_sponsorship=False,
            role_interest="AI-heavy, integration-first role focused on production impact.",
            eeo_default="Prefer not to say",
            country="United States",
            based_in_us_or_canada=True,
            inference_systems_experience=True,
            early_stage_startup_experience=False,
            side_projects_text=(
                "I build side projects around production AI systems, including a multi-model "
                "LLM gateway and autonomous reliability tooling."
            ),
            phonetic_name="EE-gor guh-NA-pol-skee",
        )

    can_mutate_tracker = (not dry_run) or queue_only
    queue_promoted_count = 0
    queue_demoted_count = 0
    queue_metadata_updates = 0
    queue_audit: List[Dict[str, Any]] = []

    if auto_promote_ready:
        for idx, row in enumerate(rows):
            status_raw = str(row.get("Status", ""))
            if not (_is_draft_status(status_raw) or _is_ready_status(status_raw)):
                continue
            assessment = _assess_queue_gate(
                row,
                fit_threshold=fit_threshold,
                remote_min_score=remote_min_score,
                adapters=adapters,
            )
            audit_item = {
                "row_index": idx,
                "company": str(row.get("Company", "")).strip(),
                "role": str(row.get("Role", "")).strip(),
                "status_before": status_raw.strip(),
                "role_track": assessment.role_track,
                "signals": assessment.signals,
                "fit_score": assessment.score,
                "remote_policy": assessment.remote_policy,
                "remote_score": assessment.remote_score,
                "remote_evidence": assessment.remote_evidence,
                "submission_lane": assessment.submission_lane,
                "eligible_for_ready": assessment.eligible,
                "reasons": assessment.reasons,
            }
            if can_mutate_tracker:
                remote_policy = assessment.remote_policy
                remote_score = str(assessment.remote_score)
                remote_evidence = ";".join(assessment.remote_evidence)
                submission_lane = assessment.submission_lane
                if str(row.get("Remote Policy", "")) != remote_policy:
                    queue_metadata_updates += 1
                if str(row.get("Remote Likelihood Score", "")) != remote_score:
                    queue_metadata_updates += 1
                if str(row.get("Remote Evidence", "")) != remote_evidence:
                    queue_metadata_updates += 1
                if str(row.get("Submission Lane", "")) != submission_lane:
                    queue_metadata_updates += 1
                row["Remote Policy"] = remote_policy
                row["Remote Likelihood Score"] = remote_score
                row["Remote Evidence"] = remote_evidence
                row["Submission Lane"] = submission_lane

            if _is_draft_status(status_raw) and assessment.eligible:
                queue_promoted_count += 1
                if can_mutate_tracker:
                    row["Status"] = DEFAULT_READY_STATUS
                    row["Notes"] = _append_note(
                        str(row.get("Notes", "")),
                        (
                            f"Queue gate passed on {_today_iso()} "
                            f"(fit={assessment.score}/{fit_threshold}, remote={assessment.remote_score}/{remote_min_score}, "
                            f"track={assessment.role_track}, lane={assessment.submission_lane})."
                        ),
                    )
            elif _is_ready_status(status_raw) and not assessment.eligible:
                queue_demoted_count += 1
                if can_mutate_tracker:
                    hard_quarantine_reasons = {
                        "non_technical_role",
                        "unsupported_site_for_ci_submit",
                        "non_direct_application_url",
                    }
                    quarantine_reasons = [
                        reason
                        for reason in assessment.reasons
                        if reason in hard_quarantine_reasons
                    ]
                    if quarantine_reasons:
                        row["Status"] = QUARANTINED_STATUS
                        row["Submission Lane"] = "manual:quarantined"
                        reason_codes = ",".join(sorted(set(quarantine_reasons)))
                        row["Notes"] = _append_note(
                            str(row.get("Notes", "")),
                            (
                                f"Auto-quarantined by queue gate on {_today_iso()} "
                                f"(reason_codes={reason_codes}; fit={assessment.score}/{fit_threshold}, "
                                f"remote={assessment.remote_score}/{remote_min_score})."
                            ),
                        )
                    else:
                        row["Status"] = "Draft"
                        row["Notes"] = _append_note(
                            str(row.get("Notes", "")),
                            (
                                f"Queue gate demoted on {_today_iso()} "
                                f"(fit={assessment.score}/{fit_threshold}, remote={assessment.remote_score}/{remote_min_score}; "
                                f"reasons={','.join(assessment.reasons)})."
                            ),
                        )
            queue_audit.append(audit_item)

    ready_candidates = [
        i for i, row in enumerate(rows) if _is_ready_status(str(row.get("Status", "")))
    ]
    adapter_rank = {"ashby": 0, "lever": 1, "greenhouse": 2}

    def _ready_priority(row_idx: int) -> tuple[int, int, int]:
        row = rows[row_idx]
        url = _row_submission_url(row, adapters)
        adapter = _find_adapter(url, adapters) if url else None
        adapter_name = adapter.name if adapter else ""
        notes_text = str(row.get("Notes", "") or "").lower()
        quarantined_or_blocked = int(
            "antibot_blocked_requires_manual_submit" in notes_text
            or "recaptcha_score_below_threshold" in notes_text
            or "required_fields_unanswered_after_retry" in notes_text
            or "verification_code_required" in notes_text
        )
        try:
            remote_score = int(
                str(row.get("Remote Likelihood Score", "0")).strip() or 0
            )
        except Exception:
            remote_score = 0
        return (
            quarantined_or_blocked,
            adapter_rank.get(adapter_name, 9),
            -remote_score,
        )

    ready_indices = sorted(ready_candidates, key=_ready_priority)[: max(0, max_jobs)]
    ready_candidates_total = len(ready_candidates)

    report: Dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dry_run": dry_run,
        "queue_only": queue_only,
        "max_jobs": max_jobs,
        "fit_threshold": fit_threshold,
        "remote_min_score": remote_min_score,
        "tracker_csv": str(tracker_csv),
        "queue_promoted_count": queue_promoted_count,
        "queue_demoted_count": queue_demoted_count,
        "queue_audit": queue_audit,
        "ready_rows_total": ready_candidates_total,
        "ready_rows_selected": len(ready_indices),
        "results": [],
    }

    applied_count = 0
    failed_count = 0
    skipped_count = 0

    if queue_only:
        report["applied_count"] = 0
        report["failed_count"] = 0
        report["skipped_count"] = 0
        report["changed"] = bool(
            can_mutate_tracker
            and (queue_promoted_count or queue_demoted_count or queue_metadata_updates)
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
        url = _row_submission_url(row, adapters)
        row_result: Dict[str, Any] = {
            "row_index": row_idx,
            "company": company,
            "role": role,
            "url": url,
            "status_before": str(row.get("Status", "")).strip(),
            "submission_lane": str(row.get("Submission Lane", "")).strip(),
            "mode": "dry_run" if dry_run else "execute",
        }
        notes_text = str(row.get("Notes", "") or "").lower()
        if (
            "antibot_blocked_requires_manual_submit" in notes_text
            or "recaptcha_score_below_threshold" in notes_text
        ):
            row_result["result"] = "skipped"
            row_result["errors"] = ["known_antibot_block"]
            if quarantine_blocked and can_mutate_tracker:
                row["Status"] = QUARANTINED_STATUS
                row["Submission Lane"] = "manual:quarantined"
                row["Notes"] = _append_note(
                    str(row.get("Notes", "")),
                    f"Auto-quarantined on {_today_iso()} after anti-bot block.",
                )
                queue_metadata_updates += 1
            report["results"].append(row_result)
            skipped_count += 1
            if count_skipped_as_failures:
                failed_count += 1
            continue

        row_errors = _validate_row(row, adapters)
        assessment = _assess_queue_gate(
            row,
            fit_threshold=fit_threshold,
            remote_min_score=remote_min_score,
            adapters=adapters,
        )
        if can_mutate_tracker:
            row["Remote Policy"] = assessment.remote_policy
            row["Remote Likelihood Score"] = str(assessment.remote_score)
            row["Remote Evidence"] = ";".join(assessment.remote_evidence)
            row["Submission Lane"] = assessment.submission_lane
        resume_path = assessment.resume_path
        if not assessment.eligible:
            row_errors.extend(assessment.reasons)
            if can_mutate_tracker:
                row["Status"] = "Draft"
                row["Notes"] = _append_note(
                    str(row.get("Notes", "")),
                    (
                        f"Submission blocked by queue gate on {_today_iso()} "
                        f"(fit={assessment.score}/{fit_threshold}, remote={assessment.remote_score}/{remote_min_score}; "
                        f"reasons={','.join(assessment.reasons)})."
                    ),
                )

        adapter = _find_adapter(url, adapters)

        if row_errors:
            row_result["result"] = "skipped"
            row_result["errors"] = row_errors
            report["results"].append(row_result)
            skipped_count += 1
            if count_skipped_as_failures:
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
        if require_secret_auth and auth_map and adapter.name not in auth_map:
            row_result["result"] = "failed"
            row_result["errors"] = [f"missing_auth_for_adapter:{adapter.name}"]
            report["results"].append(row_result)
            failed_count += 1
            continue

        result = adapter.submit(
            task, profile, auth, _enhance_answers_with_jsonld(answers, company, role)
        )
        row_result["adapter_details"] = result.details
        row_result["verified"] = result.verified
        row_result["screenshot"] = str(result.screenshot) if result.screenshot else None

        resume_exists = resume_path.exists()
        screenshot_ok = (
            result.screenshot is not None
            and result.screenshot.exists()
            and result.screenshot.stat().st_size > 0
        )
        if result.verified and screenshot_ok and resume_exists:
            row["Status"] = "Applied"
            row["Date Applied"] = _today_iso()
            if not str(row.get("Follow Up Date", "")).strip():
                row["Follow Up Date"] = _next_follow_up(7)
            row["Submitted Resume Path"] = str(resume_path)
            row["Submission Evidence Path"] = str(result.screenshot)
            row["Submission Verified At"] = dt.datetime.now(dt.timezone.utc).isoformat()
            row["Notes"] = _append_note(
                str(row.get("Notes", "")),
                (
                    f"CI submit verified on {_today_iso()} via {adapter.name}. "
                    f"Confirmation: {result.screenshot}. "
                    f"Sig: {agent_identity.sign_artifact({'date': _today_iso(), 'adapter': adapter.name, 'screenshot': result.screenshot})[:16]}"
                ),
            )
            row_result["result"] = "applied"
            applied_count += 1
        elif (
            result.details.startswith("recaptcha_score_below_threshold")
            or "captcha" in result.details.lower()
            or "antibot" in result.details.lower()
        ):
            row_result["result"] = "skipped"
            row_errors = [
                "antibot_blocked_requires_manual_submit",
                result.details,
            ]
            if not resume_exists:
                row_errors.append("missing_or_invalid_submitted_resume_path")
            if not screenshot_ok:
                row_errors.append("missing_or_empty_confirmation_screenshot")
            row_result["errors"] = row_errors
            row["Notes"] = _append_note(
                str(row.get("Notes", "")),
                (
                    f"CI submit blocked by anti-bot on {_today_iso()} via {adapter.name}. "
                    f"Reason={result.details}. Manual browser submit required."
                ),
            )
            if quarantine_blocked:
                row["Status"] = QUARANTINED_STATUS
                row["Submission Lane"] = "manual:quarantined"
            else:
                row["Status"] = DEFAULT_READY_STATUS
            queue_metadata_updates += 1
            skipped_count += 1
            if count_skipped_as_failures:
                failed_count += 1
        elif _is_manual_submit_blocker(result.details):
            row_result["result"] = "skipped"
            row_errors = [
                "manual_submit_required",
                "quarantinable_submit_blocker",
                result.details,
            ]
            if not resume_exists:
                row_errors.append("missing_or_invalid_submitted_resume_path")
            if not screenshot_ok:
                row_errors.append("missing_or_empty_confirmation_screenshot")
            row_result["errors"] = row_errors
            row["Status"] = "Quarantined"
            row["Submission Lane"] = "manual:quarantined"
            row["Notes"] = _append_note(
                str(row.get("Notes", "")),
                (
                    f"Auto-quarantined on {_today_iso()} after submit blocker "
                    f"via {adapter.name}: {result.details}"
                ),
            )
            queue_metadata_updates += 1
            skipped_count += 1
            if count_skipped_as_failures:
                failed_count += 1
        else:
            # Known non-actionable UI blockers should not fail the whole run:
            # they require manual handling and are optionally quarantined.
            is_manual_blocker = _is_manual_submit_blocker(result.details)
            if is_manual_blocker:
                row_result["result"] = "skipped"
                row_errors = [
                    "manual_submit_required",
                    "quarantinable_submit_blocker",
                    result.details,
                ]
                if not resume_exists:
                    row_errors.append("missing_or_invalid_submitted_resume_path")
                if not screenshot_ok:
                    row_errors.append("missing_or_empty_confirmation_screenshot")
                row_result["errors"] = row_errors
                if can_mutate_tracker:
                    if quarantine_blocked:
                        row["Status"] = QUARANTINED_STATUS
                        row["Submission Lane"] = "manual:quarantined"
                        row["Notes"] = _append_note(
                            str(row.get("Notes", "")),
                            (
                                f"Auto-quarantined on {_today_iso()} after submit blocker "
                                f"via {adapter.name}: {result.details}"
                            ),
                        )
                    else:
                        row["Status"] = DEFAULT_READY_STATUS
                        row["Notes"] = _append_note(
                            str(row.get("Notes", "")),
                            (
                                f"CI submit blocked on {_today_iso()} via {adapter.name}: "
                                f"{result.details}. Manual browser submit required."
                            ),
                        )
                    queue_metadata_updates += 1
                skipped_count += 1
                if count_skipped_as_failures:
                    failed_count += 1
            else:
                row_result["result"] = "failed"
                row_errors = ["verification_failed"]
                if result.details.startswith("missing_required_answers:"):
                    row_errors.append(result.details)
                if not resume_exists:
                    row_errors.append("missing_or_invalid_submitted_resume_path")
                if not screenshot_ok:
                    row_errors.append("missing_or_empty_confirmation_screenshot")
                row_result["errors"] = row_errors
                failed_count += 1

        report["results"].append(row_result)

    report["applied_count"] = applied_count
    report["failed_count"] = failed_count
    report["skipped_count"] = skipped_count
    report["changed"] = bool(
        can_mutate_tracker
        and (
            applied_count
            or queue_promoted_count
            or queue_demoted_count
            or queue_metadata_updates
        )
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8"
    )

    if report["changed"]:
        _write_tracker(tracker_csv, fields, rows)

    print(
        "Queue processed: "
        f"ready={len(ready_indices)} applied={applied_count} "
        f"failed={failed_count} skipped={skipped_count} dry_run={dry_run}"
    )
    if failed_count > 0 or skipped_count > 0:
        print("Queue result details (up to 10 rows):")
        emitted = 0
        for item in report["results"]:
            outcome = str(item.get("result", "")).strip()
            if outcome not in {"failed", "skipped"}:
                continue
            company = str(item.get("company", "")).strip()
            role = str(item.get("role", "")).strip()
            detail_parts: List[str] = []
            errors = item.get("errors")
            if isinstance(errors, list) and errors:
                detail_parts.append(
                    "errors=" + ",".join(str(err).strip() for err in errors if err)
                )
            adapter_details = str(item.get("adapter_details", "")).strip()
            if adapter_details:
                detail_parts.append(f"adapter_details={adapter_details}")
            verified = item.get("verified")
            if isinstance(verified, bool):
                detail_parts.append(f"verified={verified}")
            screenshot = str(item.get("screenshot", "")).strip()
            if screenshot:
                detail_parts.append(f"screenshot={screenshot}")
            joined = " | ".join(detail_parts) if detail_parts else "no_details"
            print(f"- {outcome}: {company} | {role} | {joined}")
            emitted += 1
            if emitted >= 10:
                break
    print(f"Report: {report_path}")

    if fail_on_error and failed_count > 0:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Parallel submission pipeline
# ---------------------------------------------------------------------------


def _submit_single_row(
    *,
    row: Dict[str, str],
    row_idx: int,
    fields: List[str],
    tracker_csv: Path,
    adapters: Sequence[SiteAdapter],
    profile: "Profile",
    auth_map: Dict[str, "AdapterAuth"],
    answers: "SubmitAnswers",
    fit_threshold: int,
    remote_min_score: int,
    require_secret_auth: bool,
    quarantine_blocked: bool,
    dry_run: bool,
) -> Dict[str, Any]:
    """Process a single submission row.  Designed for thread-pool execution.

    Returns a result dict compatible with the ``report["results"]`` schema
    plus a private ``_outcome`` key used for aggregation.  The *row* dict is
    mutated in-place and atomically persisted to the tracker CSV when a
    status change occurs.
    """
    company = str(row.get("Company", "")).strip()
    role = str(row.get("Role", "")).strip()
    url = _row_submission_url(row, adapters)
    can_mutate = not dry_run

    row_result: Dict[str, Any] = {
        "row_index": row_idx,
        "company": company,
        "role": role,
        "url": url,
        "status_before": str(row.get("Status", "")).strip(),
        "submission_lane": str(row.get("Submission Lane", "")).strip(),
        "mode": "dry_run" if dry_run else "execute",
    }

    # ---- antibot block fast-path -------------------------------------------
    notes_text = str(row.get("Notes", "") or "").lower()
    if (
        "antibot_blocked_requires_manual_submit" in notes_text
        or "recaptcha_score_below_threshold" in notes_text
    ):
        row_result["result"] = "skipped"
        row_result["errors"] = ["known_antibot_block"]
        if quarantine_blocked and can_mutate:
            row["Status"] = QUARANTINED_STATUS
            row["Submission Lane"] = "manual:quarantined"
            row["Notes"] = _append_note(
                str(row.get("Notes", "")),
                f"Auto-quarantined on {_today_iso()} after anti-bot block.",
            )
            _write_tracker_row_atomic(tracker_csv, fields, row_idx, row)
        row_result["_outcome"] = "skipped"
        return row_result

    # ---- validation & gate -------------------------------------------------
    row_errors = _validate_row(row, adapters)
    assessment = _assess_queue_gate(
        row,
        fit_threshold=fit_threshold,
        remote_min_score=remote_min_score,
        adapters=adapters,
    )
    if can_mutate:
        row["Remote Policy"] = assessment.remote_policy
        row["Remote Likelihood Score"] = str(assessment.remote_score)
        row["Remote Evidence"] = ";".join(assessment.remote_evidence)
        row["Submission Lane"] = assessment.submission_lane
    resume_path = assessment.resume_path
    if not assessment.eligible:
        row_errors.extend(assessment.reasons)
        if can_mutate:
            row["Status"] = "Draft"
            row["Notes"] = _append_note(
                str(row.get("Notes", "")),
                (
                    f"Submission blocked by queue gate on {_today_iso()} "
                    f"(fit={assessment.score}/{fit_threshold}, "
                    f"remote={assessment.remote_score}/{remote_min_score}; "
                    f"reasons={','.join(assessment.reasons)})."
                ),
            )
            _write_tracker_row_atomic(tracker_csv, fields, row_idx, row)

    adapter = _find_adapter(url, adapters)

    if row_errors:
        row_result["result"] = "skipped"
        row_result["errors"] = row_errors
        row_result["_outcome"] = "skipped"
        return row_result

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
        row_result["_outcome"] = "dry_run"
        return row_result

    auth = auth_map.get(adapter.name, AdapterAuth())
    if require_secret_auth and auth_map and adapter.name not in auth_map:
        row_result["result"] = "failed"
        row_result["errors"] = [f"missing_auth_for_adapter:{adapter.name}"]
        row_result["_outcome"] = "failed"
        return row_result

    # ---- actual Playwright submission (thread-isolated) --------------------
    try:
        result = adapter.submit(
            task, profile, auth, _enhance_answers_with_jsonld(answers, company, role)
        )
    except Exception as exc:
        row_result["result"] = "failed"
        row_result["errors"] = [f"adapter_exception:{exc!r}"]
        row_result["_outcome"] = "failed"
        return row_result

    row_result["adapter_details"] = result.details
    row_result["verified"] = result.verified
    row_result["screenshot"] = str(result.screenshot) if result.screenshot else None

    resume_exists = resume_path.exists()
    screenshot_ok = (
        result.screenshot is not None
        and result.screenshot.exists()
        and result.screenshot.stat().st_size > 0
    )

    if result.verified and screenshot_ok and resume_exists:
        row["Status"] = "Applied"
        row["Date Applied"] = _today_iso()
        if not str(row.get("Follow Up Date", "")).strip():
            row["Follow Up Date"] = _next_follow_up(7)
        row["Submitted Resume Path"] = str(resume_path)
        row["Submission Evidence Path"] = str(result.screenshot)
        row["Submission Verified At"] = dt.datetime.now(dt.timezone.utc).isoformat()
        row["Notes"] = _append_note(
            str(row.get("Notes", "")),
            (
                f"CI submit verified on {_today_iso()} via {adapter.name}. "
                f"Confirmation: {result.screenshot}. "
                f"Sig: {agent_identity.sign_artifact({'date': _today_iso(), 'adapter': adapter.name, 'screenshot': result.screenshot})[:16]}"
            ),
        )
        row_result["result"] = "applied"
        row_result["_outcome"] = "applied"
        _write_tracker_row_atomic(tracker_csv, fields, row_idx, row)
    elif (
        result.details.startswith("recaptcha_score_below_threshold")
        or "captcha" in result.details.lower()
        or "antibot" in result.details.lower()
    ):
        row_result["result"] = "skipped"
        row_errs = [
            "antibot_blocked_requires_manual_submit",
            result.details,
        ]
        if not resume_exists:
            row_errs.append("missing_or_invalid_submitted_resume_path")
        if not screenshot_ok:
            row_errs.append("missing_or_empty_confirmation_screenshot")
        row_result["errors"] = row_errs
        row["Notes"] = _append_note(
            str(row.get("Notes", "")),
            (
                f"CI submit blocked by anti-bot on {_today_iso()} via {adapter.name}. "
                f"Reason={result.details}. Manual browser submit required."
            ),
        )
        if quarantine_blocked:
            row["Status"] = QUARANTINED_STATUS
            row["Submission Lane"] = "manual:quarantined"
        else:
            row["Status"] = DEFAULT_READY_STATUS
        row_result["_outcome"] = "skipped"
        _write_tracker_row_atomic(tracker_csv, fields, row_idx, row)
    else:
        is_manual_blocker = _is_manual_submit_blocker(result.details)
        if is_manual_blocker:
            row_result["result"] = "skipped"
            row_errs = [
                "manual_submit_required",
                "quarantinable_submit_blocker",
                result.details,
            ]
            if not resume_exists:
                row_errs.append("missing_or_invalid_submitted_resume_path")
            if not screenshot_ok:
                row_errs.append("missing_or_empty_confirmation_screenshot")
            row_result["errors"] = row_errs
            if can_mutate:
                if quarantine_blocked:
                    row["Status"] = QUARANTINED_STATUS
                    row["Submission Lane"] = "manual:quarantined"
                    row["Notes"] = _append_note(
                        str(row.get("Notes", "")),
                        (
                            f"Auto-quarantined on {_today_iso()} after submit blocker "
                            f"via {adapter.name}: {result.details}"
                        ),
                    )
                else:
                    row["Status"] = DEFAULT_READY_STATUS
                    row["Notes"] = _append_note(
                        str(row.get("Notes", "")),
                        (
                            f"CI submit blocked on {_today_iso()} via {adapter.name}: "
                            f"{result.details}. Manual browser submit required."
                        ),
                    )
                _write_tracker_row_atomic(tracker_csv, fields, row_idx, row)
            row_result["_outcome"] = "skipped"
        else:
            row_result["result"] = "failed"
            row_errs = ["verification_failed"]
            if result.details.startswith("missing_required_answers:"):
                row_errs.append(result.details)
            if not resume_exists:
                row_errs.append("missing_or_invalid_submitted_resume_path")
            if not screenshot_ok:
                row_errs.append("missing_or_empty_confirmation_screenshot")
            row_result["errors"] = row_errs
            row_result["_outcome"] = "failed"

    return row_result


def run_pipeline_parallel(
    *,
    tracker_csv: Path,
    report_path: Path,
    dry_run: bool,
    queue_only: bool,
    max_jobs: int,
    fail_on_error: bool,
    count_skipped_as_failures: bool = False,
    fit_threshold: int = 70,
    remote_min_score: int = 50,
    require_secret_auth: bool = True,
    auto_promote_ready: bool = True,
    profile_env: str = "CI_SUBMIT_PROFILE_JSON",
    auth_env: str = "CI_SUBMIT_AUTH_JSON",
    answers_env: str = "CI_SUBMIT_ANSWERS_JSON",
    adapters: Optional[Sequence[SiteAdapter]] = None,
    quarantine_blocked: bool = False,
    max_workers: int = 5,
) -> int:
    """Parallel variant of ``run_pipeline()``.

    The queue gating / audit phase is executed sequentially (it reads and
    writes the CSV tracker).  Only the Playwright-based submission phase is
    parallelized across ``max_workers`` threads.

    Each thread creates its own Playwright browser context (the adapters
    instantiate browsers per-call) and uses ``_write_tracker_row_atomic()``
    for safe concurrent CSV updates.
    """
    import copy

    fields, rows = _read_tracker(tracker_csv)
    adapters = list(
        adapters
        or [AshbyAdapter(), GreenhouseAdapter(), LeverAdapter(), WorkdayAdapter()]
    )
    fields = _ensure_tracker_fields(
        fields, rows, TRACKER_REMOTE_FIELDS + TRACKER_SUBMISSION_FIELDS
    )

    profile = _load_profile_from_env(
        profile_env
    ) or _load_profile_from_answers_markdown(DEFAULT_ANSWERS_MD)
    auth_map = _load_auth_by_adapter(auth_env)
    answers = _load_answers_from_env(answers_env) or _load_answers_from_markdown(
        DEFAULT_ANSWERS_MD
    )

    is_real_submit = (not dry_run) and (not queue_only)
    if is_real_submit and require_secret_auth:
        if profile is None:
            print(
                f"ERROR: missing/invalid submit profile in ${profile_env} "
                f"and no fallback in {DEFAULT_ANSWERS_MD}."
            )
            return 2
        if answers is None:
            print(
                f"ERROR: missing/invalid submit answers in ${answers_env} "
                f"and no fallback in {DEFAULT_ANSWERS_MD}."
            )
            return 2

    # Apply placeholders only for dry-runs or queue-only when secrets are missing
    if profile is None:
        profile = Profile(
            first_name="Dry",
            last_name="Run",
            email="dry.run@example.com",
            phone="0000000000",
        )
    if answers is None:
        answers = SubmitAnswers(
            work_authorization_us=True,
            require_sponsorship=False,
            role_interest="AI-heavy, integration-first role focused on production impact.",
            eeo_default="Prefer not to say",
            country="United States",
            based_in_us_or_canada=True,
            inference_systems_experience=True,
            early_stage_startup_experience=False,
            side_projects_text=(
                "I build side projects around production AI systems, including a multi-model "
                "LLM gateway and autonomous reliability tooling."
            ),
            phonetic_name="EE-gor guh-NA-pol-skee",
        )

    can_mutate_tracker = (not dry_run) or queue_only
    queue_promoted_count = 0
    queue_demoted_count = 0
    queue_metadata_updates = 0
    queue_audit: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Phase 1: Sequential queue gating (identical to run_pipeline)
    # ------------------------------------------------------------------
    if auto_promote_ready:
        for idx, row in enumerate(rows):
            status_raw = str(row.get("Status", ""))
            if not (_is_draft_status(status_raw) or _is_ready_status(status_raw)):
                continue
            assessment = _assess_queue_gate(
                row,
                fit_threshold=fit_threshold,
                remote_min_score=remote_min_score,
                adapters=adapters,
            )
            audit_item = {
                "row_index": idx,
                "company": str(row.get("Company", "")).strip(),
                "role": str(row.get("Role", "")).strip(),
                "status_before": status_raw.strip(),
                "role_track": assessment.role_track,
                "signals": assessment.signals,
                "fit_score": assessment.score,
                "remote_policy": assessment.remote_policy,
                "remote_score": assessment.remote_score,
                "remote_evidence": assessment.remote_evidence,
                "submission_lane": assessment.submission_lane,
                "eligible_for_ready": assessment.eligible,
                "reasons": assessment.reasons,
            }
            if can_mutate_tracker:
                remote_policy = assessment.remote_policy
                remote_score = str(assessment.remote_score)
                remote_evidence = ";".join(assessment.remote_evidence)
                submission_lane = assessment.submission_lane
                if str(row.get("Remote Policy", "")) != remote_policy:
                    queue_metadata_updates += 1
                if str(row.get("Remote Likelihood Score", "")) != remote_score:
                    queue_metadata_updates += 1
                if str(row.get("Remote Evidence", "")) != remote_evidence:
                    queue_metadata_updates += 1
                if str(row.get("Submission Lane", "")) != submission_lane:
                    queue_metadata_updates += 1
                row["Remote Policy"] = remote_policy
                row["Remote Likelihood Score"] = remote_score
                row["Remote Evidence"] = remote_evidence
                row["Submission Lane"] = submission_lane

            if _is_draft_status(status_raw) and assessment.eligible:
                queue_promoted_count += 1
                if can_mutate_tracker:
                    row["Status"] = DEFAULT_READY_STATUS
                    row["Notes"] = _append_note(
                        str(row.get("Notes", "")),
                        (
                            f"Queue gate passed on {_today_iso()} "
                            f"(fit={assessment.score}/{fit_threshold}, remote={assessment.remote_score}/{remote_min_score}, "
                            f"track={assessment.role_track}, lane={assessment.submission_lane})."
                        ),
                    )
            elif _is_ready_status(status_raw) and not assessment.eligible:
                queue_demoted_count += 1
                if can_mutate_tracker:
                    hard_quarantine_reasons = {
                        "non_technical_role",
                        "unsupported_site_for_ci_submit",
                        "non_direct_application_url",
                    }
                    quarantine_reasons = [
                        reason
                        for reason in assessment.reasons
                        if reason in hard_quarantine_reasons
                    ]
                    if quarantine_reasons:
                        row["Status"] = QUARANTINED_STATUS
                        row["Submission Lane"] = "manual:quarantined"
                        reason_codes = ",".join(sorted(set(quarantine_reasons)))
                        row["Notes"] = _append_note(
                            str(row.get("Notes", "")),
                            (
                                f"Auto-quarantined by queue gate on {_today_iso()} "
                                f"(reason_codes={reason_codes}; fit={assessment.score}/{fit_threshold}, "
                                f"remote={assessment.remote_score}/{remote_min_score})."
                            ),
                        )
                    else:
                        row["Status"] = "Draft"
                        row["Notes"] = _append_note(
                            str(row.get("Notes", "")),
                            (
                                f"Queue gate demoted on {_today_iso()} "
                                f"(fit={assessment.score}/{fit_threshold}, remote={assessment.remote_score}/{remote_min_score}; "
                                f"reasons={','.join(assessment.reasons)})."
                            ),
                        )
            queue_audit.append(audit_item)

    # Persist queue-gate mutations before entering the parallel phase.
    if can_mutate_tracker and (
        queue_promoted_count or queue_demoted_count or queue_metadata_updates
    ):
        _write_tracker(tracker_csv, fields, rows)

    # ------------------------------------------------------------------
    # Select ready candidates (identical priority logic)
    # ------------------------------------------------------------------
    ready_candidates = [
        i for i, row in enumerate(rows) if _is_ready_status(str(row.get("Status", "")))
    ]
    adapter_rank = {"ashby": 0, "lever": 1, "greenhouse": 2}

    def _ready_priority_p(row_idx: int) -> tuple:
        _row = rows[row_idx]
        _url = _row_submission_url(_row, adapters)
        _adapter = _find_adapter(_url, adapters) if _url else None
        _adapter_name = _adapter.name if _adapter else ""
        _notes_text = str(_row.get("Notes", "") or "").lower()
        _quarantined_or_blocked = int(
            "antibot_blocked_requires_manual_submit" in _notes_text
            or "recaptcha_score_below_threshold" in _notes_text
            or "required_fields_unanswered_after_retry" in _notes_text
            or "verification_code_required" in _notes_text
        )
        try:
            _remote_score = int(
                str(_row.get("Remote Likelihood Score", "0")).strip() or 0
            )
        except Exception:
            _remote_score = 0
        return (
            _quarantined_or_blocked,
            adapter_rank.get(_adapter_name, 9),
            -_remote_score,
        )

    ready_indices = sorted(ready_candidates, key=_ready_priority_p)[: max(0, max_jobs)]
    ready_candidates_total = len(ready_candidates)

    report: Dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dry_run": dry_run,
        "queue_only": queue_only,
        "max_jobs": max_jobs,
        "max_workers": max_workers,
        "parallel": True,
        "fit_threshold": fit_threshold,
        "remote_min_score": remote_min_score,
        "tracker_csv": str(tracker_csv),
        "queue_promoted_count": queue_promoted_count,
        "queue_demoted_count": queue_demoted_count,
        "queue_audit": queue_audit,
        "ready_rows_total": ready_candidates_total,
        "ready_rows_selected": len(ready_indices),
        "results": [],
    }

    if queue_only:
        report["applied_count"] = 0
        report["failed_count"] = 0
        report["skipped_count"] = 0
        report["changed"] = bool(
            can_mutate_tracker
            and (queue_promoted_count or queue_demoted_count or queue_metadata_updates)
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8"
        )
        print(
            "Queue gate processed (parallel mode): "
            f"promoted={queue_promoted_count} demoted={queue_demoted_count} "
            f"ready_now={len(ready_indices)}"
        )
        print(f"Report: {report_path}")
        if fail_on_error and queue_demoted_count > 0:
            return 1
        return 0

    # ------------------------------------------------------------------
    # Phase 2: Parallel submission via ThreadPoolExecutor
    # ------------------------------------------------------------------
    effective_workers = min(max_workers, len(ready_indices)) or 1

    # Build isolated row snapshots so threads don't share mutable state.
    submission_items: List[tuple] = []
    for row_idx in ready_indices:
        row_copy = copy.deepcopy(rows[row_idx])
        submission_items.append((row_idx, row_copy))

    def _worker(item: tuple) -> Dict[str, Any]:
        idx, row_snapshot = item
        try:
            return _submit_single_row(
                row=row_snapshot,
                row_idx=idx,
                fields=fields,
                tracker_csv=tracker_csv,
                adapters=adapters,
                profile=profile,
                auth_map=auth_map,
                answers=answers,
                fit_threshold=fit_threshold,
                remote_min_score=remote_min_score,
                require_secret_auth=require_secret_auth,
                quarantine_blocked=quarantine_blocked,
                dry_run=dry_run,
            )
        except Exception as exc:
            return {
                "row_index": idx,
                "company": str(row_snapshot.get("Company", "")).strip(),
                "role": str(row_snapshot.get("Role", "")).strip(),
                "result": "failed",
                "errors": [f"thread_exception:{exc!r}"],
                "_outcome": "failed",
            }

    all_results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=effective_workers,
    ) as executor:
        future_to_item = {
            executor.submit(_worker, item): item for item in submission_items
        }
        for future in concurrent.futures.as_completed(future_to_item):
            try:
                row_result = future.result()
            except Exception as exc:
                item = future_to_item[future]
                idx, row_snapshot = item
                row_result = {
                    "row_index": idx,
                    "company": str(row_snapshot.get("Company", "")).strip(),
                    "role": str(row_snapshot.get("Role", "")).strip(),
                    "result": "failed",
                    "errors": [f"future_exception:{exc!r}"],
                    "_outcome": "failed",
                }
            all_results.append(row_result)

    # ------------------------------------------------------------------
    # Phase 3: Aggregate results
    # ------------------------------------------------------------------
    applied_count = 0
    failed_count = 0
    skipped_count = 0

    for row_result in all_results:
        outcome = row_result.pop("_outcome", "")
        if outcome == "applied":
            applied_count += 1
        elif outcome == "failed":
            failed_count += 1
        elif outcome == "skipped":
            skipped_count += 1
            if count_skipped_as_failures:
                failed_count += 1
        report["results"].append(row_result)

    report["applied_count"] = applied_count
    report["failed_count"] = failed_count
    report["skipped_count"] = skipped_count
    report["changed"] = bool(
        can_mutate_tracker
        and (
            applied_count
            or queue_promoted_count
            or queue_demoted_count
            or queue_metadata_updates
        )
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8"
    )

    print(
        "Queue processed (parallel): "
        f"ready={len(ready_indices)} applied={applied_count} "
        f"failed={failed_count} skipped={skipped_count} "
        f"workers={effective_workers} dry_run={dry_run}"
    )
    if failed_count > 0 or skipped_count > 0:
        print("Queue result details (up to 10 rows):")
        emitted = 0
        for item in report["results"]:
            outcome = str(item.get("result", "")).strip()
            if outcome not in {"failed", "skipped"}:
                continue
            company = str(item.get("company", "")).strip()
            role = str(item.get("role", "")).strip()
            detail_parts: List[str] = []
            errors = item.get("errors")
            if isinstance(errors, list) and errors:
                detail_parts.append(
                    "errors=" + ",".join(str(err).strip() for err in errors if err)
                )
            adapter_details = str(item.get("adapter_details", "")).strip()
            if adapter_details:
                detail_parts.append(f"adapter_details={adapter_details}")
            verified = item.get("verified")
            if isinstance(verified, bool):
                detail_parts.append(f"verified={verified}")
            screenshot = str(item.get("screenshot", "")).strip()
            if screenshot:
                detail_parts.append(f"screenshot={screenshot}")
            joined = " | ".join(detail_parts) if detail_parts else "no_details"
            print(f"- {outcome}: {company} | {role} | {joined}")
            emitted += 1
            if emitted >= 10:
                break
    print(f"Report: {report_path}")

    if fail_on_error and failed_count > 0:
        return 1
    return 0


def _run_replacement_discovery(
    *,
    max_new_jobs: int,
    max_board_discovery: int,
    board_discovery_timeout_s: int,
    include_aggregator_feeds: bool,
) -> tuple[int, str, str]:
    cmd = [
        "python3",
        "scripts/ralph_loop_ci.py",
        "--max-new-jobs",
        str(max(1, max_new_jobs)),
        "--max-board-discovery",
        str(max(1, max_board_discovery)),
        "--board-discovery-timeout-s",
        str(max(1, board_discovery_timeout_s)),
        "--greenhouse-seeds",
        "",
        "--lever-seeds",
        "",
        "--ashby-seeds",
        "",
        "--direct-only",
    ]
    if include_aggregator_feeds:
        cmd.append("--include-aggregator-feeds")
    timeout_s = max(30, max(1, board_discovery_timeout_s) * 4)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            timeout=timeout_s,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + f"\nreplacement_discovery_timeout_s={timeout_s}"
        return 124, stdout, stderr


def run_until_target_applied(
    *,
    tracker_csv: Path,
    report_path: Path,
    max_jobs: int,
    fit_threshold: int,
    remote_min_score: int,
    profile_env: str,
    auth_env: str,
    answers_env: str,
    require_secret_auth: bool,
    quarantine_blocked: bool,
    target_applied: int,
    max_cycles: int,
    auto_source_replacements: bool,
    replacement_max_new_jobs: int,
    replacement_max_board_discovery: int,
    replacement_board_discovery_timeout_s: int,
    replacement_include_aggregator_feeds: bool,
) -> int:
    applied_total = 0
    cycle_reports: List[Dict[str, Any]] = []
    any_cycle_failures = False

    for cycle in range(1, max(1, max_cycles) + 1):
        cycle_report_path = (
            report_path
            if max_cycles <= 1
            else report_path.with_name(
                f"{report_path.stem}_cycle{cycle}{report_path.suffix}"
            )
        )
        rc = run_pipeline(
            tracker_csv=tracker_csv,
            report_path=cycle_report_path,
            dry_run=False,
            queue_only=False,
            max_jobs=max_jobs,
            fail_on_error=False,
            count_skipped_as_failures=False,
            fit_threshold=fit_threshold,
            remote_min_score=remote_min_score,
            require_secret_auth=require_secret_auth,
            auto_promote_ready=True,
            profile_env=profile_env,
            auth_env=auth_env,
            answers_env=answers_env,
            quarantine_blocked=quarantine_blocked,
        )
        try:
            payload = json.loads(cycle_report_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}

        cycle_applied = int(payload.get("applied_count", 0) or 0)
        cycle_failed = int(payload.get("failed_count", 0) or 0)
        cycle_ready_total = int(payload.get("ready_rows_total", 0) or 0)
        applied_total += cycle_applied
        any_cycle_failures = any_cycle_failures or cycle_failed > 0 or rc != 0
        cycle_meta: Dict[str, Any] = {
            "cycle": cycle,
            "report_path": str(cycle_report_path),
            "returncode": rc,
            "applied_count": cycle_applied,
            "failed_count": cycle_failed,
            "ready_rows_total": cycle_ready_total,
        }

        if applied_total >= target_applied:
            cycle_meta["termination"] = "target_applied_reached"
            cycle_reports.append(cycle_meta)
            break

        if cycle >= max_cycles:
            cycle_meta["termination"] = "max_cycles_reached"
            cycle_reports.append(cycle_meta)
            break

        if auto_source_replacements:
            discover_rc, discover_stdout, discover_stderr = _run_replacement_discovery(
                max_new_jobs=replacement_max_new_jobs,
                max_board_discovery=replacement_max_board_discovery,
                board_discovery_timeout_s=replacement_board_discovery_timeout_s,
                include_aggregator_feeds=replacement_include_aggregator_feeds,
            )
            cycle_meta["replacement_discovery"] = {
                "returncode": discover_rc,
                "stdout_preview": discover_stdout[:1000],
                "stderr_preview": discover_stderr[:1000],
            }
        cycle_reports.append(cycle_meta)

    consolidated = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "mode": "execute_until_target_applied",
        "target_applied": target_applied,
        "applied_total": applied_total,
        "max_cycles": max_cycles,
        "cycles": cycle_reports,
        "success": applied_total >= target_applied,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(consolidated, ensure_ascii=True, indent=2), encoding="utf-8"
    )
    if applied_total >= target_applied:
        return 0
    if any_cycle_failures:
        return 1
    return 1


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
        help="Return non-zero when any submission attempt fails.",
    )
    ap.add_argument(
        "--count-skipped-as-failures",
        action="store_true",
        help=(
            "Treat gate-blocked/skipped rows as failures when combined with "
            "--fail-on-error."
        ),
    )
    ap.add_argument(
        "--fit-threshold",
        type=int,
        default=70,
        help="Minimum fit score required to enter/remain in ReadyToSubmit queue.",
    )
    ap.add_argument(
        "--remote-min-score",
        type=int,
        default=50,
        help="Minimum remote-likelihood score required for CI auto-submit lane.",
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
    ap.add_argument(
        "--answers-env",
        default="CI_SUBMIT_ANSWERS_JSON",
        help="Env var containing required screener answers JSON.",
    )
    ap.add_argument(
        "--quarantine-blocked",
        action="store_true",
        help="Auto-quarantine anti-bot and repeated required-field blockers.",
    )
    ap.add_argument(
        "--target-applied",
        type=int,
        default=0,
        help=(
            "When executing submissions, keep cycling until at least this many "
            "verified Applied outcomes are reached."
        ),
    )
    ap.add_argument(
        "--max-cycles",
        type=int,
        default=1,
        help="Max execute cycles when --target-applied is used.",
    )
    ap.add_argument(
        "--auto-source-replacements",
        action="store_true",
        help=(
            "When a cycle does not reach target applied, run direct-ATS discovery "
            "to source replacement postings before the next cycle."
        ),
    )
    ap.add_argument(
        "--replacement-max-new-jobs",
        type=int,
        default=25,
        help="Max postings to add per replacement discovery cycle.",
    )
    ap.add_argument(
        "--replacement-max-board-discovery",
        type=int,
        default=150,
        help="Max direct ATS board/org tokens scanned per replacement discovery cycle.",
    )
    ap.add_argument(
        "--replacement-board-discovery-timeout-s",
        type=int,
        default=45,
        help="Time budget in seconds for each replacement discovery cycle.",
    )
    ap.add_argument(
        "--replacement-include-aggregator-feeds",
        action="store_true",
        help="Allow remotive/remoteok during replacement discovery.",
    )
    ap.add_argument(
        "--parallel",
        action="store_true",
        help=(
            "Run submissions in parallel using a thread pool. "
            "Queue gating stays sequential; only the Playwright submit phase is parallelized."
        ),
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=5,
        help="Max parallel worker threads when --parallel is used (default 5).",
    )
    args = ap.parse_args()
    if args.execute and args.queue_only:
        print("ERROR: --execute and --queue-only are mutually exclusive.")
        return 2

    try:
        if args.execute and args.target_applied > 0:
            return run_until_target_applied(
                tracker_csv=Path(args.tracker),
                report_path=Path(args.report),
                max_jobs=max(1, args.max_jobs),
                fit_threshold=args.fit_threshold,
                remote_min_score=max(0, min(100, args.remote_min_score)),
                profile_env=args.profile_env,
                auth_env=args.auth_env,
                answers_env=args.answers_env,
                require_secret_auth=True,
                quarantine_blocked=args.quarantine_blocked,
                target_applied=max(1, args.target_applied),
                max_cycles=max(1, args.max_cycles),
                auto_source_replacements=args.auto_source_replacements,
                replacement_max_new_jobs=max(1, args.replacement_max_new_jobs),
                replacement_max_board_discovery=max(
                    1, args.replacement_max_board_discovery
                ),
                replacement_board_discovery_timeout_s=max(
                    1, args.replacement_board_discovery_timeout_s
                ),
                replacement_include_aggregator_feeds=args.replacement_include_aggregator_feeds,
            )
        if args.parallel:
            return run_pipeline_parallel(
                tracker_csv=Path(args.tracker),
                report_path=Path(args.report),
                dry_run=not args.execute,
                queue_only=args.queue_only,
                max_jobs=args.max_jobs,
                fail_on_error=args.fail_on_error,
                count_skipped_as_failures=args.count_skipped_as_failures,
                fit_threshold=args.fit_threshold,
                remote_min_score=max(0, min(100, args.remote_min_score)),
                require_secret_auth=True,
                profile_env=args.profile_env,
                auth_env=args.auth_env,
                answers_env=args.answers_env,
                quarantine_blocked=args.quarantine_blocked,
                max_workers=max(1, args.workers),
            )
        return run_pipeline(
            tracker_csv=Path(args.tracker),
            report_path=Path(args.report),
            dry_run=not args.execute,
            queue_only=args.queue_only,
            max_jobs=args.max_jobs,
            fail_on_error=args.fail_on_error,
            count_skipped_as_failures=args.count_skipped_as_failures,
            fit_threshold=args.fit_threshold,
            remote_min_score=max(0, min(100, args.remote_min_score)),
            require_secret_auth=True,
            profile_env=args.profile_env,
            auth_env=args.auth_env,
            answers_env=args.answers_env,
            quarantine_blocked=args.quarantine_blocked,
        )
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
