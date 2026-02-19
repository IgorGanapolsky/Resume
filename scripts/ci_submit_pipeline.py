#!/usr/bin/env python3
"""CI submission pipeline with strict safety gates.

Design:
- Queue source: tracker rows with Status=ReadyToSubmit (or equivalent spelling).
- Secret-backed auth/profile: submit execution requires environment secrets.
- Secret-backed screener answers: required form answers come from env JSON.
- Site adapters: Ashby, Greenhouse, Lever (Playwright-based).
- Mandatory confirmation evidence: submission is counted only with screenshot.
- Tracker mutation rule: set Status=Applied only on verified success.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import os
import re
import traceback
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from xml.sax.saxutils import escape


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
NON_TECH_ROLE_RE = re.compile(
    r"(account executive|sales|recruiter|attorney|counsel|office assistant|marketing|"
    r"content manager|revenue operations|client support|customer support specialist|"
    r"operations manager|community manager)",
    re.IGNORECASE,
)
TECH_ROLE_RE = re.compile(
    r"(engineer|developer|devops|sre|site reliability|architect|ml|ai|data engineer|"
    r"backend|frontend|full[- ]?stack|platform|infrastructure|ios|android|qa)",
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
class SubmitAnswers:
    work_authorization_us: bool
    require_sponsorship: bool
    role_interest: str
    eeo_default: str


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
                context_kwargs: Dict[str, Any] = {}
                if storage_state_arg is not None:
                    context_kwargs["storage_state"] = storage_state_arg
                context = browser.new_context(**context_kwargs)
                page = context.new_page()
                page.goto(task.url, wait_until="domcontentloaded", timeout=60000)
                form_scope = self._resolve_form_scope(page)
                if form_scope is None:
                    browser.close()
                    return SubmitResult(
                        adapter=self.name,
                        verified=False,
                        screenshot=None,
                        details="missing_file_input",
                    )

                # Best-effort generic fill by common labels/placeholders.
                self._fill_text(form_scope, "First Name", profile.first_name)
                self._fill_text(form_scope, "Last Name", profile.last_name)
                self._fill_text(
                    form_scope, "Full Name", f"{profile.first_name} {profile.last_name}"
                )
                self._fill_text(
                    form_scope, "Name", f"{profile.first_name} {profile.last_name}"
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

                missing_answers = self._apply_required_answers(
                    form_scope, page, answers
                )
                if missing_answers:
                    task.confirmation_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        page.screenshot(path=str(task.confirmation_path), full_page=True)
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

                if not self._click_submit(form_scope, page):
                    browser.close()
                    return SubmitResult(
                        adapter=self.name,
                        verified=False,
                        screenshot=None,
                        details="submit_button_not_found",
                    )

                confirmed = self._wait_for_confirmation(page, form_scope)
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

    def _apply_required_answers(
        self, scope: Any, page: Any, answers: SubmitAnswers
    ) -> List[str]:
        return []

    def _resolve_form_scope(self, page: Any) -> Optional[Any]:
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

    def _fill_text(self, scope: Any, key: str, value: str) -> None:
        if not value:
            return
        attempts = [
            lambda: scope.get_by_label(key, exact=False).first.fill(value, timeout=1500),
            lambda: scope.get_by_placeholder(key).first.fill(value, timeout=1500),
            lambda: scope.locator(
                f"input[name*='{key.lower().replace(' ', '')}']"
            ).first.fill(value, timeout=1500),
        ]
        for fn in attempts:
            try:
                fn()
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
            name_hints=("authoriz", "workauth"),
        ):
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
            missing.append("require_sponsorship")

        if self._question_present(scope, page, interest_markers):
            filled = False
            for prompt in (
                "What interests you in this role",
                "Why this role",
                "Why are you interested in this role",
            ):
                before = self._snapshot_text_field(scope, prompt)
                self._fill_text(scope, prompt, answers.role_interest)
                after = self._snapshot_text_field(scope, prompt)
                if after and after != before:
                    filled = True
                    break
            if not filled and self._set_textarea_by_name(
                scope, answers.role_interest, ("interest", "motivation", "why")
            ):
                filled = True
            if not filled:
                missing.append("role_interest")

        # Voluntary EEO answers are best-effort but deterministic.
        self._set_prefer_not_to_say_defaults(scope, page, answers.eeo_default)
        return missing

    def _question_present(
        self, scope: Any, page: Any, markers: Sequence[str]
    ) -> bool:
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
            try:
                text_node = target.get_by_text(re.compile(re.escape(marker), re.I)).first
                if text_node.count() < 1:
                    continue
            except Exception:
                continue
            for xpath in (
                "xpath=ancestor::fieldset[1]",
                "xpath=ancestor::*[@role='group'][1]",
                "xpath=ancestor::div[1]",
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
            if self._fill_yes_no_text_in_container(container, answer_yes):
                return True

        value_hints = ("yes", "true", "1") if answer_yes else ("no", "false", "0")
        for hint in name_hints:
            for value_hint in value_hints:
                selector = (
                    "input[type='radio']"
                    f"[name*='{hint}'][value*='{value_hint}']"
                )
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
            field = container.locator("input[type='text'],textarea").first
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
            f"input[type='text'][name*='{hint}']",
            f"textarea[name*='{hint}']",
            f"input[type='text'][id*='{hint}']",
            f"textarea[id*='{hint}']",
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
            re.compile(r"choose not to", re.I),
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


def _adapter_name_for_url(url: str, adapters: Sequence[SiteAdapter]) -> Optional[str]:
    found = _find_adapter(url, adapters)
    return found.name if found is not None else None


def _infer_remote_profile(
    row: Dict[str, str],
    *,
    job_text: str,
) -> tuple[str, int, List[str]]:
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

    if host.endswith("remoteok.com") or host.endswith("remotive.com"):
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
    if NON_TECH_ROLE_RE.search(role) and not TECH_ROLE_RE.search(role):
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
    remote_policy, remote_score, remote_evidence = _infer_remote_profile(
        row, job_text=job_text
    )
    adapter_name = _adapter_name_for_url(str(row.get("Career Page URL", "")), adapters)
    submission_lane = f"ci_auto:{adapter_name}" if adapter_name else "manual"

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

    required_reasons = {
        "missing_resume_docx_or_pdf",
        "missing_tailored_resume_html",
        "missing_cover_letter",
        "non_technical_role",
        "unsupported_site_for_ci_submit",
    }
    eligible = (
        fit_ok
        and remote_ok
        and all(reason not in required_reasons for reason in reasons)
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

    if auth_yes_no is None or sponsor_yes_no is None:
        return None
    if not role_interest:
        return None
    if not eeo_default:
        return None

    return SubmitAnswers(
        work_authorization_us=auth_yes_no,
        require_sponsorship=sponsor_yes_no,
        role_interest=role_interest,
        eeo_default=eeo_default,
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
    count_skipped_as_failures: bool = False,
    fit_threshold: int = 70,
    remote_min_score: int = 50,
    require_secret_auth: bool = True,
    auto_promote_ready: bool = True,
    profile_env: str = "CI_SUBMIT_PROFILE_JSON",
    auth_env: str = "CI_SUBMIT_AUTH_JSON",
    answers_env: str = "CI_SUBMIT_ANSWERS_JSON",
    adapters: Optional[Sequence[SiteAdapter]] = None,
) -> int:
    fields, rows = _read_tracker(tracker_csv)
    adapters = list(adapters or [AshbyAdapter(), GreenhouseAdapter(), LeverAdapter()])
    fields = _ensure_tracker_fields(fields, rows, TRACKER_REMOTE_FIELDS)

    profile = _load_profile_from_env(profile_env)
    auth_map = _load_auth_by_adapter(auth_env)
    answers = _load_answers_from_env(answers_env)

    if not dry_run and not queue_only and require_secret_auth:
        if profile is None:
            print(f"ERROR: missing/invalid secret profile in ${profile_env}.")
            return 2
        if not auth_map:
            print(f"ERROR: missing/invalid secret auth map in ${auth_env}.")
            return 2
        if answers is None:
            print(f"ERROR: missing/invalid secret answers in ${answers_env}.")
            return 2
    elif profile is None:
        # Dry-run may proceed without secrets, but keep a sane placeholder profile.
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

    ready_indices = [
        i for i, row in enumerate(rows) if _is_ready_status(str(row.get("Status", "")))
    ][: max(0, max_jobs)]

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
        "ready_rows_total": len(ready_indices),
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
        url = str(row.get("Career Page URL", "")).strip()
        row_result: Dict[str, Any] = {
            "row_index": row_idx,
            "company": company,
            "role": role,
            "url": url,
            "status_before": str(row.get("Status", "")).strip(),
            "submission_lane": str(row.get("Submission Lane", "")).strip(),
            "mode": "dry_run" if dry_run else "execute",
        }

        row_errors = _validate_row(row)
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
        if adapter is None:
            row_errors.append("unsupported_site")

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
        if require_secret_auth and adapter.name not in auth_map:
            row_result["result"] = "failed"
            row_result["errors"] = [f"missing_auth_for_adapter:{adapter.name}"]
            report["results"].append(row_result)
            failed_count += 1
            continue

        result = adapter.submit(task, profile, auth, answers)
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
            row_errors = ["verification_failed"]
            if result.details.startswith("missing_required_answers:"):
                row_errors.append(result.details)
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
            count_skipped_as_failures=args.count_skipped_as_failures,
            fit_threshold=args.fit_threshold,
            remote_min_score=max(0, min(100, args.remote_min_score)),
            require_secret_auth=True,
            profile_env=args.profile_env,
            auth_env=args.auth_env,
            answers_env=args.answers_env,
        )
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
