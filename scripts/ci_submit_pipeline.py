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
import importlib.util
import json
import os
import random
import re
import signal
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

_SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))


def _load_learning_helpers() -> Tuple[Any, Any]:
    try:
        from rag.learning import load_arms as load_arms
        from rag.learning import rank_rows_by_learning as rank_rows

        return load_arms, rank_rows
    except ModuleNotFoundError as exc:
        if exc.name not in {"rag", "rag.learning"}:
            raise

    learning_path = _SCRIPT_ROOT / "rag" / "learning.py"
    spec = importlib.util.spec_from_file_location("_resume_rag_learning", learning_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load learning helpers from {learning_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module.load_arms, module.rank_rows_by_learning


_load_learning_arms, rank_rows_by_learning = _load_learning_helpers()

try:
    from playwright_stealth import stealth_sync  # type: ignore
except ImportError:
    try:
        from playwright_stealth import Stealth as _Stealth  # type: ignore

        def stealth_sync(page: Any) -> None:  # type: ignore[misc]
            _Stealth().apply_stealth_sync(page)

    except ImportError:
        stealth_sync = None


ROOT = _SCRIPT_ROOT
TRACKER_CSV = ROOT / "applications" / "job_applications" / "application_tracker.csv"
DEFAULT_REPORT = ROOT / "applications" / "job_applications" / "ci_submit_report.json"
DEFAULT_PROFILE_JSON = (
    ROOT / "applications" / "job_applications" / "candidate_profile.json"
)
DEFAULT_LEARNING_ARMS_JSON = ROOT / "rag" / "data" / "arms.json"
DEFAULT_READY_STATUS = "ReadyToSubmit"

READY_STATUS_KEYS = {
    "readytosubmit",
    "ready_to_submit",
    "ready to submit",
}
DRAFT_STATUS_KEYS = {
    "draft",
}
QUARANTINED_STATUS_KEYS = {
    "quarantined",
}
STALE_QUARANTINE_MARKERS = (
    "url incompatible with adapter",
    "url format incompatible with adapter",
    "broken embed url",
    "unsupported_site_for_ci_submit",
)
UNRECOVERABLE_QUARANTINE_MARKERS = (
    "manual browser submit required",
    "manual re-submit",
    "needs manual completion",
    "possible spam",
    "recaptcha",
    "captcha",
    "anti-bot",
    "anti bot",
    "missing_file_input",
    "manual submission required",
)

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
    r"(account executive|sales|business[- ]?development|acquisition lead|"
    r"market (associate|lead|manager)|recruiter|talent sourcer|attorney|"
    r"counsel|legal|policy|office assistant|marketing|communications|"
    r"product manager|content manager|revenue operations|business operations|programs? and "
    r"business operations|client support|customer support specialist|"
    r"customer experience|claims experience|operations manager|"
    r"community manager|accountant|intern\b)",
    re.IGNORECASE,
)
TECH_ROLE_RE = re.compile(
    r"(engineer|developer|devops|sre|site reliability|architect|ml|ai|data engineer|"
    r"data scientist|machine learning|research scientist|backend|frontend|"
    r"full[- ]?stack|platform|infrastructure|ios|android|qa|technical staff|"
    r"member of technical staff)",
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


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _norm_key(text: str) -> str:
    return re.sub(r"[\s_]+", "", (text or "").strip().lower())


def _today_iso() -> str:
    return dt.date.today().isoformat()


def _next_follow_up(days: int = 7) -> str:
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def _read_tracker(path: Path) -> tuple[List[str], List[Dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
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


def _rank_ready_rows_for_submit(
    rows: Sequence[Dict[str, str]],
    *,
    max_jobs: int,
    arms_path: Path = DEFAULT_LEARNING_ARMS_JSON,
    fit_threshold: int = 70,
    remote_min_score: int = 50,
    adapters: Optional[Sequence[Any]] = None,
) -> Tuple[List[int], List[Dict[str, Any]]]:
    learned_ranked = rank_rows_by_learning(
        rows,
        _load_learning_arms(arms_path),
        status_filter="ready",
        max_rows=0,
    )
    ranked: List[Dict[str, Any]] = []
    adapters = list(
        adapters
        or [
            OpenAIAshbyAdapter(),
            InferactAshbyAdapter(),
            AshbyAdapter(),
            GreenhouseAdapter(),
            LeverAdapter(),
            OracleAdapter(),
            TalentpriseAdapter(),
        ]
    )

    for item in learned_ranked:
        row_index = int(item["row_index"])
        row = rows[row_index]
        assessment = _assess_queue_gate(
            row,
            fit_threshold=fit_threshold,
            remote_min_score=remote_min_score,
            adapters=adapters,
        )

        adjusted_score = float(item.get("adjusted_score", 0.0) or 0.0)
        fit_component = max(0.0, min(1.0, assessment.score / 100.0))
        remote_component = max(0.0, min(1.0, assessment.remote_score / 100.0))
        track_bonus = 0.05 if assessment.role_track == "fde" else 0.0
        ranking_score = -1.0
        if assessment.eligible:
            ranking_score = (
                0.58 * fit_component
                + 0.22 * remote_component
                + 0.20 * adjusted_score
                + track_bonus
            )
        eligibility_priority = 0 if assessment.eligible else 1
        enriched = dict(item)
        enriched.update(
            {
                "eligible_for_submit": assessment.eligible,
                "eligibility_priority": eligibility_priority,
                "fit_score": assessment.score,
                "remote_policy": assessment.remote_policy,
                "remote_score": assessment.remote_score,
                "remote_evidence": assessment.remote_evidence,
                "role_track": assessment.role_track,
                "signals": assessment.signals,
                "queue_reasons": assessment.reasons,
                "ranking_score": round(ranking_score, 4),
            }
        )
        ranked.append(enriched)

    ranked.sort(
        key=lambda item: (
            int(item.get("eligibility_priority", 1) or 1),
            int(item.get("lane_priority", 1) or 1),
            -float(item.get("ranking_score", 0.0) or 0.0),
            -int(item.get("fit_score", 0) or 0),
            -int(item.get("remote_score", 0) or 0),
            str(item.get("company", "")).lower(),
            str(item.get("role", "")).lower(),
        )
    )
    if max_jobs > 0:
        ranked = ranked[:max_jobs]
    indices = [int(item["row_index"]) for item in ranked]
    return indices, ranked


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
class BrowserRuntime:
    browser: Any
    context: Any
    page: Any
    backend: str
    session_id: str = ""
    live_view_url: str = ""
    note: str = ""


@dataclass
class SubmitAnswers:
    work_authorization_us: bool
    require_sponsorship: bool
    role_interest: str
    eeo_default: str
    availability_text: str = "Immediate"


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
    browser_backend: str = "local_playwright"
    browser_note: str = ""


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


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _anchor_api_base() -> str:
    return str(
        os.getenv("ANCHOR_BROWSER_API_BASE", "https://api.anchorbrowser.io")
    ).rstrip("/")


def _anchor_request(
    method: str, path: str, payload: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    api_key = str(os.getenv("ANCHOR_BROWSER_API_KEY", "")).strip()
    if not api_key:
        raise RuntimeError("anchor_api_key_missing")
    url = _anchor_api_base() + path
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method.upper())
    req.add_header("anchor-api-key", api_key)
    req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            if not raw.strip():
                return {}
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
            raise RuntimeError("anchor_non_dict_response")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"anchor_http_{exc.code}:{detail[:400]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"anchor_network_error:{exc.reason}") from exc


def _build_anchor_session_payload() -> Dict[str, Any]:
    proxy_active = _env_flag("ANCHOR_BROWSER_PROXY_ACTIVE", default=True)
    proxy_country = (
        str(os.getenv("ANCHOR_BROWSER_PROXY_COUNTRY_CODE", "us")).strip().lower()
    )
    proxy_region = str(os.getenv("ANCHOR_BROWSER_PROXY_REGION", "")).strip().lower()
    proxy_city = str(os.getenv("ANCHOR_BROWSER_PROXY_CITY", "")).strip()
    profile_name = str(os.getenv("ANCHOR_BROWSER_PROFILE_NAME", "")).strip()
    persist_profile = _env_flag(
        "ANCHOR_BROWSER_PROFILE_PERSIST", default=bool(profile_name)
    )
    extra_stealth_active = _env_flag(
        "ANCHOR_BROWSER_EXTRA_STEALTH_ACTIVE", default=proxy_active
    )
    max_duration = max(
        60,
        int(
            str(os.getenv("ANCHOR_BROWSER_MAX_DURATION_SECONDS", "1800")).strip()
            or "1800"
        ),
    )
    idle_timeout = max(
        30,
        int(
            str(os.getenv("ANCHOR_BROWSER_IDLE_TIMEOUT_SECONDS", "300")).strip()
            or "300"
        ),
    )

    payload: Dict[str, Any] = {
        "timeout": {
            "max_duration": max_duration,
            "idle_timeout": idle_timeout,
        },
        "headless": {"active": True},
        "popup_blocker": {
            "active": _env_flag("ANCHOR_BROWSER_POPUP_BLOCKER", default=True)
        },
        "adblock": {"active": _env_flag("ANCHOR_BROWSER_ADBLOCK", default=False)},
    }
    if proxy_active:
        proxy: Dict[str, Any] = {
            "active": True,
            "type": "anchor_proxy",
        }
        if proxy_country:
            proxy["country_code"] = proxy_country
        if proxy_region:
            proxy["region"] = proxy_region
        if proxy_city:
            proxy["city"] = proxy_city
        payload["proxy"] = proxy
    if extra_stealth_active and proxy_active:
        payload["extra_stealth"] = {"active": True}
    if profile_name:
        payload["profile"] = {"name": profile_name, "persist": persist_profile}
    if _env_flag("ANCHOR_BROWSER_TRACING_ACTIVE", default=False):
        payload["tracing"] = {"active": True, "snapshots": True, "sources": True}
    return payload


def _create_anchor_session() -> tuple[str, str, str]:
    response = _anchor_request("POST", "/v1/sessions", _build_anchor_session_payload())
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    if not isinstance(data, dict):
        raise RuntimeError("anchor_missing_data")
    cdp_url = str(data.get("cdp_url", "")).strip()
    session_id = str(
        data.get("session_id") or data.get("id") or data.get("browser_session_id") or ""
    ).strip()
    live_view_url = str(data.get("live_view_url", "")).strip()
    if not cdp_url:
        raise RuntimeError("anchor_missing_cdp_url")
    if not session_id:
        raise RuntimeError("anchor_missing_session_id")
    return session_id, cdp_url, live_view_url


def _end_anchor_session(session_id: str) -> None:
    if not session_id:
        return
    try:
        _anchor_request(
            "DELETE",
            f"/v1/sessions/{urllib.parse.quote(session_id, safe='')}",
        )
    except Exception:
        return


def _apply_storage_state_to_context(
    context: Any, page: Any, storage_state: Any
) -> None:
    if not isinstance(storage_state, dict):
        return
    cookies = storage_state.get("cookies")
    if isinstance(cookies, list) and cookies:
        try:
            context.add_cookies(cookies)
        except Exception:
            pass
    origins = storage_state.get("origins")
    if not isinstance(origins, list):
        return
    for origin_entry in origins:
        if not isinstance(origin_entry, dict):
            continue
        origin = str(origin_entry.get("origin", "")).strip()
        local_storage = origin_entry.get("localStorage")
        if not origin or not isinstance(local_storage, list) or not local_storage:
            continue
        try:
            page.goto(origin, wait_until="domcontentloaded", timeout=20000)
            page.evaluate(
                """(entries) => {
                    for (const entry of entries) {
                        if (!entry || typeof entry.name !== "string") {
                            continue;
                        }
                        window.localStorage.setItem(entry.name, entry.value ?? "");
                    }
                }""",
                local_storage,
            )
        except Exception:
            continue


def _resolve_local_chrome_user_data_dir() -> str:
    override = str(os.getenv("CI_SUBMIT_CHROME_USER_DATA_DIR", "")).strip()
    if override:
        base_path = override
    elif sys.platform == "darwin":
        base_path = os.path.join(
            str(Path.home()),
            "Library",
            "Application Support",
            "resume-ci",
            "chrome-profile",
        )
    else:
        base_path = os.path.join(
            str(Path.home()),
            ".local",
            "share",
            "resume-ci",
            "chrome-profile",
        )
    user_data_dir = os.path.abspath(os.path.expanduser(base_path))
    os.makedirs(user_data_dir, exist_ok=True)
    return user_data_dir


def _clear_local_chrome_profile_lock(user_data_dir: str) -> int:
    resolved = os.path.abspath(os.path.expanduser(user_data_dir or ""))
    if not resolved:
        return 0

    killed: set[int] = set()
    try:
        proc = subprocess.run(
            ["pgrep", "-f", f"--user-data-dir={resolved}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        proc = None

    if proc is not None:
        for line in str(proc.stdout or "").splitlines():
            try:
                pid = int(line.strip())
            except Exception:
                continue
            if pid <= 0 or pid == os.getpid():
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                killed.add(pid)
            except ProcessLookupError:
                continue
            except Exception:
                continue

    if killed:
        time.sleep(0.5)
        for pid in list(killed):
            try:
                os.kill(pid, 0)
            except Exception:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                continue

    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.remove(os.path.join(resolved, name))
        except FileNotFoundError:
            continue
        except Exception:
            continue

    return len(killed)


def _resolve_local_browser_channel() -> str:
    return str(os.getenv("CI_SUBMIT_BROWSER_CHANNEL", "chromium")).strip()


def _resolve_local_browser_executable_path() -> str:
    return str(os.getenv("CI_SUBMIT_BROWSER_EXECUTABLE", "")).strip()


def _open_browser_runtime(
    pw: Any,
    storage_state: Optional[Any],
    use_local_chrome: bool = False,
    visible: bool = False,
) -> BrowserRuntime:
    strict_anchor = _env_flag("ANCHOR_BROWSER_STRICT", default=False)
    anchor_api_key = str(os.getenv("ANCHOR_BROWSER_API_KEY", "")).strip()
    if anchor_api_key and not use_local_chrome:
        try:
            session_id, cdp_url, live_view_url = _create_anchor_session()
            browser = pw.chromium.connect_over_cdp(cdp_url)
            context = (
                browser.contexts[0]
                if getattr(browser, "contexts", None)
                else browser.new_context()
            )
            page = context.new_page()
            if storage_state is not None:
                _apply_storage_state_to_context(context, page, storage_state)
            return BrowserRuntime(
                browser=browser,
                context=context,
                page=page,
                backend="anchor_browser",
                session_id=session_id,
                live_view_url=live_view_url,
            )
        except Exception as exc:
            if strict_anchor:
                raise
            note = f"anchor_fallback_local:{exc}"
        else:
            note = ""
    else:
        note = ""

    if use_local_chrome:
        user_data_dir = _resolve_local_chrome_user_data_dir()
        cleared_pids = _clear_local_chrome_profile_lock(user_data_dir)
        browser_kwargs: Dict[str, Any] = {
            "user_data_dir": user_data_dir,
            "headless": not visible,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        browser_channel = _resolve_local_browser_channel()
        browser_executable = _resolve_local_browser_executable_path()
        if browser_channel:
            browser_kwargs["channel"] = browser_channel
        if browser_executable:
            browser_kwargs["executable_path"] = browser_executable
        try:
            browser = pw.chromium.launch_persistent_context(
                **browser_kwargs,
            )
            channel_note = (
                f";browser_channel:{browser_channel}" if browser_channel else ""
            )
            executable_note = (
                f";browser_executable:{browser_executable}"
                if browser_executable
                else ""
            )
        except Exception as exc:
            if browser_channel:
                fallback_kwargs = dict(browser_kwargs)
                fallback_kwargs.pop("channel", None)
                browser = pw.chromium.launch_persistent_context(
                    **fallback_kwargs,
                )
                channel_note = f";browser_channel_fallback:{browser_channel}:{exc}"
                executable_note = (
                    f";browser_executable:{browser_executable}"
                    if browser_executable
                    else ""
                )
            else:
                raise
        context = browser
        page = context.new_page() if not context.pages else context.pages[0]
        return BrowserRuntime(
            browser=browser,
            context=context,
            page=page,
            backend="local_playwright_persistent",
            note=(
                f"local_chrome_profile:{user_data_dir}"
                + (f";cleared_profile_lock_pids:{cleared_pids}" if cleared_pids else "")
                + f"{channel_note}"
                + f"{executable_note}"
                + (f";{note}" if note else "")
            ),
        )

    browser = pw.chromium.launch(headless=not visible)
    context_kwargs: Dict[str, Any] = {}
    if storage_state is not None:
        context_kwargs["storage_state"] = storage_state
    context = browser.new_context(**context_kwargs)
    page = context.new_page()
    return BrowserRuntime(
        browser=browser,
        context=context,
        page=page,
        backend="local_playwright",
        note=note,
    )


def _close_browser_runtime(runtime: Optional[BrowserRuntime]) -> None:
    if runtime is None:
        return
    try:
        runtime.browser.close()
    except Exception:
        pass
    if runtime.session_id:
        _end_anchor_session(runtime.session_id)


class SiteAdapter:
    name = "base"
    auto_submit_supported = True

    def matches(self, url: str) -> bool:
        raise NotImplementedError

    def submit(
        self,
        task: SubmitTask,
        profile: Profile,
        auth: AdapterAuth,
        answers: SubmitAnswers,
        use_local_chrome: bool = False,
        visible: bool = False,
    ) -> SubmitResult:
        raise NotImplementedError


class _SubmitTimeout(BaseException):
    """Raised from SIGALRM to escape adapter-level exception handlers."""


class PlaywrightFormAdapter(SiteAdapter):
    host_patterns: Sequence[re.Pattern[str]] = ()
    submit_button_patterns: Sequence[str] = ()
    success_text_patterns: Sequence[str] = ()
    success_url_patterns: Sequence[str] = ()

    def matches(self, url: str) -> bool:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        return any(p.search(host) for p in self.host_patterns)

    def _type_human(self, locator: Any, text: str) -> None:
        """Type text with randomized delays between 50ms and 150ms per character."""
        if not text:
            return
        try:
            locator.focus(timeout=2000)
            for char in text:
                locator.type(char, delay=random.randint(50, 150))
                if random.random() < 0.1:  # 10% chance of an extra human-like delay
                    time.sleep(random.uniform(0.1, 0.3))
        except Exception:
            # Fallback to instant fill if type/focus fails (some custom fields)
            try:
                locator.fill(text, timeout=2000)
            except Exception:
                pass

    def _click_human(self, locator: Any) -> None:
        """Introduce randomized delay before clicking."""
        time.sleep(random.uniform(0.5, 1.5))
        locator.click()

    def _wait_human(self, min_s: float = 1.0, max_s: float = 3.0) -> None:
        """Introduce randomized wait time."""
        time.sleep(random.uniform(min_s, max_s))

    def _mouse_wander(self, page: Any, *, moves: int = 4) -> None:
        viewport = getattr(page, "viewport_size", None) or {
            "width": 1280,
            "height": 900,
        }
        width = max(300, int(viewport.get("width", 1280)))
        height = max(300, int(viewport.get("height", 900)))
        for _ in range(max(1, moves)):
            x = random.randint(80, max(81, width - 80))
            y = random.randint(80, max(81, height - 80))
            try:
                page.mouse.move(x, y, steps=random.randint(8, 20))
            except Exception:
                continue
            self._wait_human(0.1, 0.35)

    def _prime_page_session(
        self,
        runtime: BrowserRuntime,
        task: SubmitTask,
        profile: Profile,
        answers: SubmitAnswers,
    ) -> None:
        return None

    def submit(
        self,
        task: SubmitTask,
        profile: Profile,
        auth: AdapterAuth,
        answers: SubmitAnswers,
        use_local_chrome: bool = False,
        visible: bool = False,
    ) -> SubmitResult:
        try:
            from playwright.sync_api import (
                TimeoutError as PlaywrightTimeoutError,  # type: ignore
            )
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
                runtime: Optional[BrowserRuntime] = None
                try:
                    runtime = _open_browser_runtime(
                        pw,
                        storage_state_arg,
                        use_local_chrome=use_local_chrome,
                        visible=visible,
                    )
                    page = runtime.page

                    # Apply engine-level stealth if available
                    if stealth_sync:
                        try:
                            stealth_sync(page)
                        except Exception:
                            pass

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
                    self._prime_page_session(runtime, task, profile, answers)
                    page.goto(task.url, wait_until="domcontentloaded", timeout=60000)
                    form_scope = self._resolve_form_scope(page)
                    if form_scope is None:
                        task.confirmation_path.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            page.screenshot(
                                path=str(task.confirmation_path), full_page=True
                            )
                        except Exception:
                            pass
                        detail = self._missing_form_scope_detail(page)
                        return SubmitResult(
                            adapter=self.name,
                            verified=False,
                            screenshot=(
                                task.confirmation_path
                                if task.confirmation_path.exists()
                                else None
                            ),
                            details=detail,
                            browser_backend=runtime.backend,
                            browser_note=runtime.note,
                        )

                    # Best-effort generic fill by common labels/placeholders.
                    self._fill_text(form_scope, "First Name", profile.first_name)
                    self._fill_text(form_scope, "Last Name", profile.last_name)
                    self._fill_text(
                        form_scope,
                        "Full Name",
                        f"{profile.first_name} {profile.last_name}",
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
                        self._fill_text(
                            form_scope, "Current Location", profile.location
                        )
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

                    self._pre_submit_form_fill(form_scope, page, profile, answers)
                    pre_submit_blocker = self._pre_submit_blocker_detail(
                        form_scope, page
                    )
                    if pre_submit_blocker:
                        task.confirmation_path.parent.mkdir(parents=True, exist_ok=True)
                        screenshot_failed = False
                        try:
                            page.screenshot(
                                path=str(task.confirmation_path), full_page=True
                            )
                        except Exception:
                            screenshot_failed = True
                        return SubmitResult(
                            adapter=self.name,
                            verified=False,
                            screenshot=(
                                task.confirmation_path
                                if task.confirmation_path.exists() and not screenshot_failed
                                else None
                            ),
                            details=pre_submit_blocker,
                            browser_backend=runtime.backend,
                            browser_note=runtime.note,
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
                        return SubmitResult(
                            adapter=self.name,
                            verified=False,
                            screenshot=(
                                task.confirmation_path
                                if task.confirmation_path.exists()
                                else None
                            ),
                            details=(
                                "missing_required_answers:"
                                + ",".join(sorted(set(missing_answers)))
                            ),
                            browser_backend=runtime.backend,
                            browser_note=runtime.note,
                        )

                    # Resume upload is mandatory for this pipeline.
                    upload_ok, upload_details = self._upload_resume(
                        form_scope, page, task.resume_path
                    )
                    if not upload_ok:
                        task.confirmation_path.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            page.screenshot(
                                path=str(task.confirmation_path), full_page=True
                            )
                        except Exception:
                            pass
                        return SubmitResult(
                            adapter=self.name,
                            verified=False,
                            screenshot=(
                                task.confirmation_path
                                if task.confirmation_path.exists()
                                else None
                            ),
                            details=upload_details,
                            browser_backend=runtime.backend,
                            browser_note=runtime.note,
                        )
                    self._after_resume_upload(form_scope, page)

                    if not self._click_submit(form_scope, page):
                        return SubmitResult(
                            adapter=self.name,
                            verified=False,
                            screenshot=None,
                            details="submit_button_not_found",
                            browser_backend=runtime.backend,
                            browser_note=runtime.note,
                        )

                    confirmed = self._wait_for_confirmation(page, form_scope)
                    if not confirmed and self._post_submit_retry(
                        form_scope, page, profile, answers
                    ):
                        confirmed = self._wait_for_confirmation(page, form_scope)
                    failure_details = self._extract_failure_details(page, form_scope)
                    if not failure_details:
                        failure_details = submit_error_detail
                    task.confirmation_path.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(task.confirmation_path), full_page=True)

                    return SubmitResult(
                        adapter=self.name,
                        verified=confirmed and task.confirmation_path.exists(),
                        screenshot=(
                            task.confirmation_path
                            if task.confirmation_path.exists()
                            else None
                        ),
                        details=(
                            "confirmed"
                            if confirmed
                            else (failure_details or "confirmation_text_not_detected")
                        ),
                        browser_backend=runtime.backend,
                        browser_note=runtime.note,
                    )
                finally:
                    _close_browser_runtime(runtime)
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

    def _missing_form_scope_detail(self, page: Any) -> str:
        return "missing_file_input"

    def _find_resume_file_input(self, scope: Any, page: Any) -> Optional[Any]:
        try:
            file_input = scope.locator("input[type='file']").first
            if file_input.count() > 0:
                return file_input
        except Exception:
            return None
        return None

    def _upload_resume(
        self, scope: Any, page: Any, resume_path: Path
    ) -> tuple[bool, str]:
        file_input = self._find_resume_file_input(scope, page)
        if file_input is None:
            return False, self._missing_form_scope_detail(page)
        try:
            file_input.set_input_files(str(resume_path))
            return True, "resume_uploaded"
        except Exception as exc:
            return False, f"resume_upload_failed:{exc}"

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
        # Wait for lazy-loaded file inputs (some ATS forms render async)
        try:
            page.wait_for_selector("input[type='file']", timeout=4000)
            if page.locator("input[type='file']").count() > 0:
                return page
        except Exception:
            pass
        return None

    def _fill_text(
        self, scope: Any, key: str, value: str, *, exact_label: bool = False
    ) -> None:
        if not value:
            return
        label_pattern = re.compile(rf"^\\s*{re.escape(key)}\\s*[:*]?\\s*$", re.I)
        # Random jitter before filling
        self._wait_human(0.2, 0.8)

        attempts = [
            lambda: (
                scope.get_by_label(
                    label_pattern if exact_label else key,
                    exact=bool(exact_label),
                ).first
            ),
            lambda: scope.get_by_placeholder(key).first,
            lambda: (
                scope.locator(f"input[name*='{key.lower().replace(' ', '')}']").first
            ),
        ]
        for fn in attempts:
            try:
                locator = fn()
                if locator.count() > 0:
                    self._type_human(locator, value)
                    return
            except Exception:
                continue

    def _pre_submit_form_fill(
        self, scope: Any, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> None:
        return

    def _pre_submit_blocker_detail(self, scope: Any, page: Any) -> Optional[str]:
        return None

    def _click_submit(self, scope: Any, page: Any) -> bool:
        # Simulate 'reading' the form before submission
        self._wait_human(3.0, 7.0)
        for pattern in self.submit_button_patterns:
            for target in (scope, page):
                try:
                    btn = target.get_by_role(
                        "button", name=re.compile(pattern, re.I)
                    ).first
                    if btn.count() > 0:
                        self._click_human(btn)
                        return True
                except Exception:
                    continue
        try:
            for target in (scope, page):
                submit_input = target.locator("input[type='submit']").first
                if submit_input.count() > 0:
                    self._click_human(submit_input)
                    return True
        except Exception:
            pass
        return False

    def _wait_for_confirmation(self, page: Any, scope: Any) -> bool:
        # Progressive checks: wait up to 15s total (2026 redirects/confirmations are slower)
        for wait_ms in (4000, 3000, 3000, 5000):
            try:
                page.wait_for_timeout(wait_ms)
            except Exception:
                pass
            texts: List[str] = []
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
            if page_url and any(
                re.search(p, page_url, re.I) for p in self.success_url_patterns
            ):
                return True
        return False


class OracleAdapter(SiteAdapter):
    name = "oracle"
    auto_submit_supported = False
    host_patterns = (re.compile(r"oraclecloud\.com"),)

    def matches(self, url: str) -> bool:
        return any(p.search(url) for p in self.host_patterns)

    def submit(
        self,
        task: SubmitTask,
        profile: Profile,
        auth: AdapterAuth,
        answers: SubmitAnswers,
        use_local_chrome: bool = False,
        visible: bool = False,
    ) -> SubmitResult:
        return SubmitResult(
            adapter=self.name,
            verified=False,
            screenshot=None,
            details="Manual submission required for Oracle",
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
    spam_error_patterns = (
        r"we couldn'?t submit your application",
        r"flagged as possible spam",
        r"submit your application again",
    )

    def _missing_form_scope_detail(self, page: Any) -> str:
        text = ""
        try:
            text = str(page.inner_text("body") or "")
        except Exception:
            text = ""
        blob = text.lower()
        if "job not found" in blob or "job you requested was not found" in blob:
            return "ashby_job_not_found"
        if "verify you are human" in blob or "captcha" in blob or "cloudflare" in blob:
            return "ashby_antibot_challenge"
        if "apply for this job" in blob or "apply now" in blob:
            return "ashby_application_not_loaded"
        return "ashby_resume_input_missing"

    def _find_resume_file_input(self, scope: Any, page: Any) -> Optional[Any]:
        selectors = (
            "input#_systemfield_resume",
            "input[type='file'][id*='resume' i]",
            "input[type='file'][name*='resume' i]",
            "input[type='file']",
        )
        for selector in selectors:
            try:
                control = scope.locator(selector).first
                if control.count() > 0:
                    return control
            except Exception:
                continue
        return None

    def _locator_input_value(self, locator: Any) -> str:
        try:
            value = locator.input_value(timeout=1500)
        except Exception:
            value = ""
        return str(value or "").strip()

    def _fill_text_by_placeholder(
        self, target: Any, value: str, placeholders: Sequence[str]
    ) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        for placeholder in placeholders:
            try:
                field = target.get_by_placeholder(placeholder).first
            except Exception:
                continue
            try:
                if field.count() < 1:
                    continue
            except Exception:
                continue
            before = self._locator_input_value(field)
            try:
                field.click(timeout=1500)
            except Exception:
                pass
            try:
                field.fill(text, timeout=1500)
            except Exception:
                try:
                    field.type(text, delay=40)
                except Exception:
                    continue
            try:
                field.press("Tab", timeout=1000)
            except Exception:
                pass
            after = self._locator_input_value(field)
            if after and (after != before or not before):
                return True
        return False

    def _fill_start_availability(
        self, scope: Any, page: Any, availability_text: str
    ) -> bool:
        value = str(availability_text or "").strip()
        if not value:
            return False

        today = dt.date.today()
        candidate_values = [value]
        if re.search(r"\b(immediate|immediately|asap)\b", value, re.I):
            candidate_values = [today.strftime("%m/%d/%Y"), today.isoformat()]

        for candidate in candidate_values:
            if self._fill_text_by_placeholder(
                scope, candidate, ("Pick date...", "Pick a date")
            ):
                return True
            for prompt in (
                "When can you start a new role?",
                "When can you start",
                "Start date",
                "Available start date",
                "Availability",
            ):
                before = self._snapshot_text_field(scope, prompt)
                self._fill_text(scope, prompt, candidate)
                after = self._snapshot_text_field(scope, prompt)
                if after and (after != before or not before):
                    return True
            for selector in (
                "input[name*='start' i]",
                "input[id*='start' i]",
                "input[name*='availability' i]",
                "input[id*='availability' i]",
            ):
                try:
                    field = scope.locator(selector).first
                    if field.count() < 1:
                        continue
                except Exception:
                    continue
                before = self._locator_input_value(field)
                try:
                    field.click(timeout=1500)
                except Exception:
                    pass
                try:
                    field.fill(candidate, timeout=1500)
                except Exception:
                    try:
                        field.type(candidate, delay=40)
                    except Exception:
                        continue
                try:
                    field.press("Tab", timeout=1000)
                except Exception:
                    pass
                after = self._locator_input_value(field)
                if after and (after != before or not before):
                    return True
        return False

    def _check_checkbox_by_name_markers(
        self, target: Any, markers: Sequence[str]
    ) -> bool:
        try:
            checkboxes = target.locator("input[type='checkbox']")
            count = checkboxes.count()
        except Exception:
            return False

        normalized_markers = [
            re.sub(r"[^a-z0-9]+", " ", marker.lower()).strip()
            for marker in markers
            if str(marker).strip()
        ]
        for idx in range(count):
            try:
                checkbox = checkboxes.nth(idx)
            except Exception:
                continue
            attrs: List[str] = []
            for attr in ("name", "aria-label"):
                try:
                    value = checkbox.get_attribute(attr)
                except Exception:
                    value = ""
                if value:
                    attrs.append(str(value))
            blob = re.sub(r"[^a-z0-9]+", " ", " ".join(attrs).lower()).strip()
            if not blob:
                continue
            for marker in normalized_markers:
                tokens = [token for token in marker.split() if token]
                if tokens and all(token in blob for token in tokens):
                    try:
                        checkbox.check(timeout=1500)
                    except Exception:
                        try:
                            checkbox.click(timeout=1500)
                        except Exception:
                            continue
                    return True
        return False

    def _check_required_acknowledgement(
        self, scope: Any, page: Any, markers: Sequence[str]
    ) -> bool:
        for target in (scope, page):
            if self._check_checkbox_by_name_markers(target, markers):
                return True
        for target in (scope, page):
            for marker in markers:
                pattern = re.compile(
                    r"\s+".join(re.escape(part) for part in marker.split()), re.I
                )
                for probe in (
                    lambda: target.get_by_label(pattern, exact=False).first,
                    lambda: target.get_by_text(pattern).first,
                ):
                    try:
                        control = probe()
                        if control.count() < 1:
                            continue
                    except Exception:
                        continue
                    try:
                        control.check(timeout=1500)
                    except Exception:
                        try:
                            control.click(timeout=1500)
                        except Exception:
                            continue
                    return True
        return False

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
                lambda: container.get_by_role("button", name=choice_pattern).first,
                lambda: container.get_by_text(choice_pattern).first,
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
            page.wait_for_timeout(2000)
        except Exception:
            pass

        # Check for reCAPTCHA or human verification blocks
        blob = ""
        try:
            blob = str(page.inner_text("body") or "").lower()
        except Exception:
            pass

        if "verify you are human" in blob or "captcha" in blob or "cloudflare" in blob:
            # If in 'visible' mode or local, wait for manual resolution
            # In CI, this will likely timeout, but gives a window for recovery if running locally
            try:
                # Wait up to 30s for the block to disappear
                for _ in range(6):
                    time.sleep(5)
                    blob = str(page.inner_text("body") or "").lower()
                    if "verify you are human" not in blob and "captcha" not in blob:
                        break
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
            page.wait_for_timeout(2000)
        except Exception:
            pass
        return True

    def _extract_failure_details(self, page: Any, scope: Any) -> Optional[str]:
        if self._has_required_question_error(scope, page):
            return "required_questions_unanswered_after_retry"
        text_blobs: List[str] = []
        for target in (scope, page):
            try:
                text = str(target.inner_text("body") or "")
            except Exception:
                continue
            if text:
                text_blobs.append(text.lower())
        combined = "\n".join(text_blobs)
        if combined and all(
            re.search(pattern, combined, re.I) for pattern in self.spam_error_patterns
        ):
            return "recaptcha_score_below_threshold"
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
        # Mouse wander and natural scrolling before looking for the form
        self._mouse_wander(page)
        for scroll in (0.3, 0.6, 1.0):
            try:
                page.evaluate(
                    f"window.scrollTo(0, document.body.scrollHeight * {scroll})"
                )
                self._wait_human(0.5, 1.5)
                self._mouse_wander(page)
            except Exception:
                pass

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
            clicked = False
            for role in ("button", "link"):
                try:
                    control = page.get_by_role(
                        role, name=re.compile(pattern, re.I)
                    ).first
                    if control.count() > 0:
                        control.click(timeout=3000)
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                try:
                    page.wait_for_timeout(1000)
                except Exception:
                    pass
                scope = super()._resolve_form_scope(page)
                if scope is not None:
                    return scope

        try:
            direct_url = (
                str(getattr(page, "url", "") or "").rstrip("/") + "/application"
            )
            page.goto(direct_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1000)
            scope = super()._resolve_form_scope(page)
            if scope is not None:
                return scope
        except Exception:
            pass
        return None


class OpenAIAshbyAdapter(AshbyAdapter):
    name = "openai_ashby"
    openai_patterns = (
        re.compile(r"jobs\.ashbyhq\.com/openai/", re.I),
        re.compile(r"(^|[./])openai($|[./])", re.I),
    )

    def matches(self, url: str) -> bool:
        if not super().matches(url):
            return False
        return any(pattern.search(url) for pattern in self.openai_patterns)

    def _openai_careers_url(self, role: str) -> str:
        role_slug = _slug(role)
        return f"https://openai.com/careers/{role_slug}/"

    def _prime_page_session(
        self,
        runtime: BrowserRuntime,
        task: SubmitTask,
        profile: Profile,
        answers: SubmitAnswers,
    ) -> None:
        page = runtime.page
        visited: List[str] = []
        targets = ["https://openai.com/careers/"]
        specific = self._openai_careers_url(task.role)
        if specific not in targets:
            targets.append(specific)

        for url in targets:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                continue
            visited.append(url)
            self._wait_human(1.5, 3.5)
            try:
                page.mouse.wheel(0, random.randint(350, 900))
            except Exception:
                pass
            self._wait_human(0.4, 1.0)
            self._mouse_wander(page, moves=random.randint(3, 5))
            self._wait_human(0.4, 1.2)

        if visited:
            note = f"openai_careers_preflight:{visited[-1]}"
        else:
            note = "openai_careers_preflight_failed"
        runtime.note = f"{runtime.note};{note}" if runtime.note else note

    def _infer_role_location(self, page: Any) -> Optional[str]:
        haystacks: List[str] = [str(getattr(page, "url", "") or "")]
        try:
            title = str(page.title() or "")
        except Exception:
            title = ""
        if title:
            haystacks.append(title)
        try:
            body = str(page.inner_text("body") or "")
        except Exception:
            body = ""
        if body:
            haystacks.append(body)
        hay = " ".join(haystacks).lower()
        if "san francisco" in hay or re.search(r"\bsf\b", hay):
            return "San Francisco"
        if "seattle" in hay:
            return "Seattle"
        if "new york city" in hay or re.search(r"\bnyc\b", hay) or "new york" in hay:
            return "New York City"
        return None

    def _fill_openai_location(self, scope: Any, page: Any, profile: Profile) -> bool:
        role_location = self._infer_role_location(page)
        if not role_location:
            return False

        options_by_location = {
            "San Francisco": ["San Francisco, California, United States"],
            "Seattle": ["Seattle, Washington, United States"],
            "New York City": [
                "New York City, New York, United States",
                "New York, New York, United States",
            ],
        }
        expected_tokens = {
            "San Francisco": ("san francisco", "california", "united states"),
            "Seattle": ("seattle", "washington", "united states"),
            "New York City": ("new york", "united states"),
        }
        location_options = options_by_location.get(role_location, [])
        if not location_options:
            return False

        control = None
        for target in (scope, page):
            for probe in (
                lambda: target.get_by_label("Location", exact=False).first,
                lambda: target.get_by_role(
                    "combobox", name=re.compile(r"location", re.I)
                ).first,
                lambda: target.locator("input[role='combobox']").first,
            ):
                try:
                    candidate = probe()
                    if candidate.count() > 0:
                        control = candidate
                        break
                except Exception:
                    continue
            if control is not None:
                break
        if control is None:
            return False

        try:
            control.click(timeout=1500)
        except Exception:
            pass
        try:
            control.fill(role_location, timeout=1500)
        except Exception:
            try:
                control.type(role_location, delay=40)
            except Exception:
                return False

        selected_value = ""
        for option_text in location_options:
            try:
                option = page.get_by_role(
                    "option",
                    name=re.compile(rf"^\s*{re.escape(option_text)}\s*$", re.I),
                ).first
                if option.count() > 0:
                    option.click(timeout=1500)
                    selected_value = self._locator_input_value(control).lower()
                    if selected_value:
                        break
            except Exception:
                continue

        if not selected_value:
            try:
                control.fill(location_options[0], timeout=1500)
            except Exception:
                return False
            try:
                control.press("Tab", timeout=1000)
            except Exception:
                pass
            selected_value = self._locator_input_value(control).lower()

        tokens = expected_tokens.get(role_location, ())
        return bool(selected_value and all(token in selected_value for token in tokens))

    def _set_openai_yes_no(
        self,
        scope: Any,
        page: Any,
        question_markers: Sequence[str],
        answer_yes: bool,
        name_hints: Sequence[str],
    ) -> bool:
        return self._set_yes_no(
            scope,
            page,
            question_markers=question_markers,
            answer_yes=answer_yes,
            name_hints=name_hints,
        )

    def _pre_submit_form_fill(
        self, scope: Any, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> None:
        sponsorship_markers = (
            "require visa sponsorship",
            "require sponsorship",
            "employment visa sponsorship",
            "now or in the future require",
        )
        office_markers = (
            "are you able to work from our sf office three days per week",
            "are you able to work from our san francisco office three days per week",
            "are you able to work from our seattle office three days per week",
            "are you able to work from our new york office three days per week",
            "are you able to work from our nyc office three days per week",
        )
        arbitration_markers = (
            "I have read and agree to the Arbitration Agreement",
            "Arbitration Agreement",
        )
        certification_markers = (
            "I confirm I have read the above.",
            "I confirm I have read the above",
        )

        self._fill_openai_location(scope, page, profile)
        self._fill_start_availability(scope, page, answers.availability_text)
        self._set_openai_yes_no(
            scope,
            page,
            sponsorship_markers,
            answers.require_sponsorship,
            ("sponsor", "visa"),
        )
        self._set_openai_yes_no(
            scope,
            page,
            office_markers,
            True,
            ("office", "onsite", "inoffice"),
        )
        self._check_required_acknowledgement(scope, page, arbitration_markers)
        self._check_required_acknowledgement(scope, page, certification_markers)

    def _apply_required_answers(
        self, scope: Any, page: Any, answers: SubmitAnswers
    ) -> List[str]:
        missing = super()._apply_required_answers(scope, page, answers)
        start_date_markers = (
            "when can you start a new role",
            "when can you start",
            "start date",
        )
        arbitration_markers = (
            "I have read and agree to the Arbitration Agreement",
            "Arbitration Agreement",
        )
        certification_markers = (
            "I confirm I have read the above.",
            "I confirm I have read the above",
        )
        if self._question_present(
            scope, page, start_date_markers
        ) and not self._fill_start_availability(scope, page, answers.availability_text):
            missing.append("availability_text")
        if self._question_present(
            scope, page, arbitration_markers
        ) and not self._check_required_acknowledgement(
            scope, page, arbitration_markers
        ):
            missing.append("applicant_arbitration_acknowledgement")
        if self._question_present(
            scope, page, certification_markers
        ) and not self._check_required_acknowledgement(
            scope, page, certification_markers
        ):
            missing.append("applicant_certification")
        return missing

    def _post_submit_retry(
        self, scope: Any, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> bool:
        self._pre_submit_form_fill(scope, page, profile, answers)
        return super()._post_submit_retry(scope, page, profile, answers)

    def _click_submit(self, scope: Any, page: Any) -> bool:
        self._wait_human(6.0, 10.0)
        try:
            submit_button = scope.get_by_role(
                "button", name=re.compile(r"submit application", re.I)
            ).first
            if submit_button.count() > 0:
                try:
                    submit_button.scroll_into_view_if_needed(timeout=1500)
                except Exception:
                    pass
                self._mouse_wander(page, moves=2)
                self._click_human(submit_button)
                return True
        except Exception:
            pass
        return super()._click_submit(scope, page)


class InferactAshbyAdapter(AshbyAdapter):
    """Inferact-specific Ashby flow with a small trust-building preflight."""

    inferact_patterns = (
        re.compile(r"jobs\.ashbyhq\.com/inferact/", re.I),
        re.compile(r"(^|[./])inferact($|[./])", re.I),
    )

    def matches(self, url: str) -> bool:
        if not super().matches(url):
            return False
        return any(pattern.search(url) for pattern in self.inferact_patterns)

    def _pre_submit_form_fill(
        self, scope: Any, page: Any, profile: Profile, answers: SubmitAnswers
    ) -> None:
        # Inferact exposes a few custom required fields that are not covered by
        # the generic Ashby autofill path. Fill them explicitly before submit.
        location = (profile.location or "").strip()
        if location:
            for target in (scope, page):
                try:
                    control = None
                    for selector in (
                        "input[placeholder='Start typing...']",
                        "input[role='combobox']",
                    ):
                        try:
                            candidate = target.locator(selector).first
                            if candidate.count() > 0:
                                control = candidate
                                break
                        except Exception:
                            continue
                    if control is None:
                        control = target.get_by_label("Location", exact=False).first
                        if control.count() < 1:
                            continue
                    try:
                        control.click(timeout=1500)
                    except Exception:
                        pass
                    try:
                        control.fill(location, timeout=1500)
                    except Exception:
                        try:
                            control.type(location, delay=40)
                        except Exception:
                            pass
                    try:
                        control.press("Enter", timeout=1000)
                    except Exception:
                        pass
                    try:
                        control.press("Tab", timeout=1000)
                    except Exception:
                        pass
                    break
                except Exception:
                    continue

        project_link = (
            profile.github or profile.website or profile.linkedin or ""
        ).strip()
        if project_link:
            prompt_markers = (
                "Please share a link to a personal project",
                "open-source contribution",
                "technical blog post",
            )
            for target in (scope, page):
                try:
                    text_node = target.get_by_text(
                        re.compile(
                            "|".join(re.escape(marker) for marker in prompt_markers),
                            re.I,
                        )
                    ).first
                    if text_node.count() < 1:
                        continue
                    field = text_node.locator("xpath=following::textarea[1]").first
                    if field.count() < 1:
                        continue
                    try:
                        field.fill(project_link, timeout=1500)
                    except Exception:
                        try:
                            field.type(project_link, delay=40)
                        except Exception:
                            pass
                    break
                except Exception:
                    continue

        for checkbox_name in ("Cloud Orchestration",):
            for target in (scope, page):
                try:
                    checkbox = target.locator(
                        f"input[type='checkbox'][name='{checkbox_name}']"
                    ).first
                    if checkbox.count() < 1:
                        continue
                    try:
                        checkbox.check(timeout=1500)
                    except Exception:
                        try:
                            checkbox.click(timeout=1500)
                        except Exception:
                            pass
                    break
                except Exception:
                    continue

    def submit(
        self,
        task: SubmitTask,
        profile: Profile,
        auth: AdapterAuth,
        answers: SubmitAnswers,
        use_local_chrome: bool = False,
        visible: bool = False,
    ) -> SubmitResult:
        try:
            from playwright.sync_api import (
                TimeoutError as PlaywrightTimeoutError,  # type: ignore
            )
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
                runtime: Optional[BrowserRuntime] = None
                try:
                    runtime = _open_browser_runtime(
                        pw,
                        storage_state_arg,
                        use_local_chrome=use_local_chrome,
                        visible=visible,
                    )
                    page = runtime.page

                    if stealth_sync:
                        try:
                            stealth_sync(page)
                        except Exception:
                            pass

                    try:
                        page.goto(
                            "https://jobs.ashbyhq.com/inferact",
                            wait_until="domcontentloaded",
                            timeout=60000,
                        )
                        self._wait_human(1.2, 2.8)
                        try:
                            page.mouse.move(240, 180, steps=18)
                            page.mouse.wheel(0, 900)
                            page.mouse.move(760, 420, steps=22)
                        except Exception:
                            pass
                        self._wait_human(0.8, 1.8)
                    except Exception:
                        pass
                finally:
                    _close_browser_runtime(runtime)
        except PlaywrightTimeoutError:
            return SubmitResult(
                adapter=self.name,
                verified=False,
                screenshot=None,
                details="timeout",
            )
        except Exception:
            pass
        return super().submit(
            task,
            profile,
            auth,
            answers,
            use_local_chrome=use_local_chrome,
            visible=visible,
        )


class GreenhouseAdapter(PlaywrightFormAdapter):
    name = "greenhouse"
    host_patterns = (re.compile(r"greenhouse\.io"),)
    submit_button_patterns = (r"submit application", r"apply", r"submit")
    partner_handoff_markers = (
        "official hiring partner",
        "official recruiting partner",
        "you do not need to submit this greenhouse application",
    )

    def _prime_page_session(
        self,
        runtime: BrowserRuntime,
        task: SubmitTask,
        profile: Profile,
        answers: SubmitAnswers,
    ) -> None:
        """Visit company homepage before the job form to build reCAPTCHA trust.

        reCAPTCHA v3 scores browser sessions based on behavior history.
        Visiting the company site first establishes a referrer chain and
        mouse movement history that raises the trust score above the
        rejection threshold.
        """
        page = runtime.page
        try:
            parsed = urllib.parse.urlsplit(task.url)
            path_parts = [p for p in (parsed.path or "").split("/") if p]
            if len(path_parts) >= 1:
                company_slug = path_parts[0]
                for domain in [
                    f"https://{company_slug}.com",
                    f"https://www.{company_slug}.com",
                ]:
                    try:
                        page.goto(
                            domain, wait_until="domcontentloaded", timeout=8000
                        )
                        self._wait_human(2.0, 4.0)
                        self._mouse_wander(page)
                        return
                    except Exception:
                        continue
        except Exception:
            pass

    def _mouse_wander(self, page: Any) -> None:
        """Move mouse randomly to appear human — defeats reCAPTCHA v3."""
        viewport = getattr(page, "viewport_size", None) or {
            "width": 1440,
            "height": 900,
        }
        for _ in range(random.randint(3, 6)):
            x = random.randint(100, viewport["width"] - 100)
            y = random.randint(100, viewport["height"] - 100)
            try:
                page.mouse.move(x, y, steps=random.randint(5, 15))
                time.sleep(random.uniform(0.05, 0.2))
            except Exception:
                pass

    def _trust_building_preflight(self, page: Any, job_url: str) -> None:
        """Visit the company homepage first to build referrer trust for reCAPTCHA."""
        try:
            parsed = urllib.parse.urlsplit(job_url)
            # Extract company slug from greenhouse URL path
            path_parts = [p for p in (parsed.path or "").split("/") if p]
            if len(path_parts) >= 1:
                company_slug = path_parts[0]
                # Try visiting company homepage
                for domain in [
                    f"https://{company_slug}.com",
                    f"https://www.{company_slug}.com",
                ]:
                    try:
                        page.goto(domain, wait_until="domcontentloaded", timeout=8000)
                        self._wait_human(2.0, 4.0)
                        self._mouse_wander(page)
                        return
                    except Exception:
                        continue
        except Exception:
            pass

    success_text_patterns = (
        r"thank you for applying",
        r"application has been received",
        r"your application has been submitted",
        r"your application has been received",
        r"thank you for your interest",
        r"application submitted",
        r"thanks for applying",
        r"we have received your application",
        r"we.ll review your application",
        r"application.+successfully submitted",
    )
    success_url_patterns = (
        r"thank[-_]?you",
        r"submitted",
        r"confirmation",
        r"application.*complete",
        r"/thankyou",
    )

    def _resolve_form_scope(self, page: Any) -> Optional[Any]:
        self._mouse_wander(page)
        for scroll in (0.3, 0.6, 1.0):
            try:
                page.evaluate(
                    f"window.scrollTo(0, document.body.scrollHeight * {scroll})"
                )
                self._wait_human(0.5, 1.5)
                self._mouse_wander(page)
            except Exception:
                pass
        return super()._resolve_form_scope(page)

    def _pre_submit_blocker_detail(self, scope: Any, page: Any) -> Optional[str]:
        texts: List[str] = []
        for target in (scope, page):
            text = ""
            try:
                text = str(target.inner_text("body") or "")
            except Exception:
                text = ""
            if text:
                texts.append(text.lower())
        blob = "\n".join(texts)
        if not blob:
            return None
        if (
            self.partner_handoff_markers[2] in blob
            and any(marker in blob for marker in self.partner_handoff_markers[:2])
        ):
            partner = "external partner"
            if "constellation" in blob:
                partner = "Constellation"
            return (
                f"Manual submission required: complete {partner} partner application"
            )
        return None

    def _gh_select_dropdown(
        self, page: Any, qid: str, answer: str, use_filter: bool = True
    ) -> bool:
        """Click control div to open react-select, optionally filter, click option."""
        try:
            shell = page.locator(f"#{qid}").locator(
                'xpath=ancestor::div[contains(@class,"select-shell")]'
            )
            ctrl = shell.locator('div[class*="control"]').first
            ctrl.click()
            time.sleep(random.uniform(0.5, 1.0))
            if use_filter:
                combo = shell.locator('input[role="combobox"]').first
                if combo.count() > 0:
                    for ch in answer[:20]:
                        combo.type(ch, delay=random.randint(50, 120))
                    time.sleep(1)
            else:
                time.sleep(0.5)
            opts = page.locator('div[class*="option"]').all()
            for o in opts:
                if answer.lower()[:12] in o.text_content().strip().lower():
                    self._click_human(o)
                    time.sleep(0.8)
                    return True
            if opts:
                self._click_human(opts[0])
                time.sleep(0.8)
                return True
        except Exception:
            pass
        return False

    def _apply_required_answers(
        self, scope: Any, page: Any, answers: SubmitAnswers
    ) -> List[str]:
        self._wait_human(1.0, 2.0)
        self._mouse_wander(page)

        questions = page.evaluate("""() => {
            return Array.from(document.querySelectorAll(
                'input[id^="question_"], textarea[id^="question_"]'
            ))
                .filter(el => el.offsetParent !== null)
                .map(el => ({
                    id: el.id, tag: el.tagName,
                    label: (el.labels?.[0]?.textContent?.trim() || '').toLowerCase(),
                    isDropdown: el.closest('.select-shell') !== null
                }));
        }""")

        for q in questions:
            qid, label, is_dd, tag = q["id"], q["label"], q["isDropdown"], q["tag"]
            answer = None
            if "authorized to work" in label:
                answer = ("nationality", True, True)
            elif "visa" in label and "sponsorship" in label:
                answer = ("No", True, True)
            elif "based in" in label and "countries" in label:
                answer = ("United States", True, True)
            elif "acknowledge" in label and "privacy" in label:
                answer = ("Acknowledge", True, False)
            elif "double-check" in label or "accuracy" in label:
                answer = ("I have reviewed", True, False)
            elif "in-person" in label or "on-site" in label:
                answer = ("Yes", True, True)
            elif "relocation" in label:
                answer = ("Yes", True, True)
            elif "interviewed" in label or "previously worked" in label:
                answer = ("No", True, True)
            elif "ai policy" in label:
                answer = ("Yes", True, True)
            elif "linkedin" in label:
                answer = (
                    "https://www.linkedin.com/in/igor-ganapolsky-859317343/",
                    False,
                    False,
                )
            elif "github" in label:
                answer = ("https://github.com/IgorGanapolsky", False, False)
            elif "website" in label:
                answer = ("https://github.com/IgorGanapolsky", False, False)
            elif "how did you hear" in label:
                answer = ("Job Board", False, False)
            elif "earliest" in label or "when can you start" in label:
                answer = ("Immediately", False, False)
            elif "deadline" in label or "timeline" in label:
                answer = ("None", False, False)
            elif "address" in label and "working" in label:
                answer = (
                    "11909 Glenmore Dr, Coral Springs, FL 33071",
                    False,
                    False,
                )
            elif "why" in label and tag == "TEXTAREA":
                answer = (
                    answers.role_interest
                    or "I build production AI systems at scale — 26 Claude AI "
                    "skills with RLHF (76.6% positive rate), RAG pipelines "
                    "with LanceDB, and 13 autonomous agents.",
                    False,
                    False,
                )
            elif "additional" in label and tag == "TEXTAREA":
                answer = (
                    "Available immediately. US citizen, no sponsorship.",
                    False,
                    False,
                )
            if answer is None:
                continue
            text, should_dd, use_filter = answer
            try:
                if is_dd and should_dd:
                    self._gh_select_dropdown(page, qid, text, use_filter=use_filter)
                elif tag == "TEXTAREA":
                    ta = page.locator(f"#{qid}")
                    if ta.count() > 0 and not ta.input_value():
                        ta.fill(text)
                else:
                    inp = page.locator(f"#{qid}")
                    if inp.count() > 0 and not inp.input_value():
                        self._type_human(inp, text)
                self._mouse_wander(page)
            except Exception:
                pass

        # Country field (separate from phone code)
        try:
            shell = page.locator("#country").locator(
                'xpath=ancestor::div[contains(@class,"select-shell")]'
            )
            if shell.count() > 0:
                ctrl = shell.locator('div[class*="control"]').first
                ctrl.click()
                time.sleep(0.5)
                combo = shell.locator('input[role="combobox"]').first
                if combo.count() > 0:
                    for ch in "United States":
                        combo.type(ch, delay=random.randint(50, 100))
                    time.sleep(1)
                opt = page.locator(
                    'div[class*="option"]:has-text("United States")'
                ).first
                if opt.count() > 0:
                    self._click_human(opt)
        except Exception:
            pass

        # EEO demographics
        for eid in (
            "gender",
            "hispanic_ethnicity",
            "veteran_status",
            "disability_status",
        ):
            try:
                shell = page.locator(f"#{eid}").locator(
                    'xpath=ancestor::div[contains(@class,"select-shell")]'
                )
                ctrl = shell.locator('div[class*="control"]').first
                ctrl.click()
                time.sleep(0.3)
                combo = shell.locator('input[role="combobox"]').first
                if combo.count() > 0:
                    combo.fill("Decline")
                    time.sleep(0.5)
                opt = page.locator('div[class*="option"]').first
                if opt.count() > 0:
                    opt.click()
                    time.sleep(0.3)
            except Exception:
                pass

        self._wait_human(1.0, 2.0)
        return []


class LeverAdapter(PlaywrightFormAdapter):
    name = "lever"
    host_patterns = (re.compile(r"lever\.co"),)
    submit_button_patterns = (r"submit application", r"apply", r"submit")
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

    def _resolve_form_scope(self, page: Any) -> Optional[Any]:
        """Lever jobs show a description page first; navigate to /apply if needed."""
        scope = super()._resolve_form_scope(page)
        if scope is not None:
            return scope

        current_url = str(getattr(page, "url", "") or "")
        if "/apply" not in current_url.lower():
            apply_url = current_url.rstrip("/") + "/apply"
            try:
                page.goto(apply_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2500)
                scope = super()._resolve_form_scope(page)
                if scope is not None:
                    return scope
            except Exception:
                pass

        # Click apply controls as fallback
        for marker in (r"apply for this job", r"apply now", r"apply"):
            for role in ("link", "button"):
                try:
                    control = page.get_by_role(
                        role, name=re.compile(marker, re.I)
                    ).first
                    if control.count() > 0:
                        control.click(timeout=3000)
                        page.wait_for_timeout(2500)
                        scope = super()._resolve_form_scope(page)
                        if scope is not None:
                            return scope
                except Exception:
                    continue
        return None


class TalentpriseAdapter(SiteAdapter):
    """Talentprise profile-based job platform.

    Talentprise uses a profile-match model rather than per-job applications.
    The user maintains a profile at app.talentprise.com and the platform
    matches them to roles.  Auto-submit is not applicable — the adapter
    ensures the platform is recognized (not flagged as unsupported) and
    routes to manual profile-maintenance lane.
    """

    name = "talentprise"
    auto_submit_supported = False
    host_patterns = (re.compile(r"talentprise\.com"),)

    def matches(self, url: str) -> bool:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        return any(p.search(host) for p in self.host_patterns)

    def submit(
        self,
        task: SubmitTask,
        profile: Profile,
        auth: AdapterAuth,
        answers: SubmitAnswers,
        use_local_chrome: bool = False,
        visible: bool = False,
    ) -> SubmitResult:
        return SubmitResult(
            adapter=self.name,
            verified=False,
            screenshot=None,
            details="Talentprise uses profile-matching; maintain profile at app.talentprise.com/talent/profile",
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
    if FDE_ROLE_RE.search(role):
        signals.append("fde-role")
    if FDE_SIGNAL_RE.search(hay):
        signals.append("customer-integration")
    if PYTHON_RE.search(hay):
        signals.append("python")
    if VOICE_AUDIO_RE.search(hay):
        signals.append("voice-audio")
    # Only explicit FDE titles should trigger strict FDE scoring requirements.
    # Customer/integration language can appear in many non-FDE technical roles.
    track = "fde" if "fde-role" in signals else "general"
    return track, sorted(set(signals))


def _adapter_name_for_url(url: str, adapters: Sequence[SiteAdapter]) -> Optional[str]:
    found = _find_adapter(url, adapters)
    return found.name if found is not None else None


def _submission_lane_for_adapter(adapter: Optional[SiteAdapter]) -> str:
    if adapter is None:
        return "manual"
    if not getattr(adapter, "auto_submit_supported", True):
        return f"manual:{adapter.name}"
    return f"ci_auto:{adapter.name}"


def _is_manual_submission_only(reasons: Sequence[str]) -> bool:
    return "manual_submission_only" in {
        str(reason).strip().lower() for reason in reasons
    }


def _is_manual_submission_required_detail(detail: str) -> bool:
    return "manual submission required" in (detail or "").strip().lower()


def _submit_with_adapter(
    adapter: SiteAdapter,
    task: SubmitTask,
    profile: Profile,
    auth: AdapterAuth,
    answers: SubmitAnswers,
    *,
    use_local_chrome: bool = False,
    visible: bool = False,
    submit_timeout_seconds: int = 0,
) -> SubmitResult:
    timeout_seconds = max(0, int(submit_timeout_seconds or 0))
    previous_handler: Any = None
    alarm_enabled = timeout_seconds > 0 and hasattr(signal, "SIGALRM")

    def _handle_timeout(_signum: int, _frame: Any) -> None:
        raise _SubmitTimeout()

    if alarm_enabled:
        previous_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, _handle_timeout)
        signal.alarm(timeout_seconds)
    try:
        try:
            return adapter.submit(
                task,
                profile,
                auth,
                answers,
                use_local_chrome=use_local_chrome,
                visible=visible,
            )
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
        try:
            return adapter.submit(
                task,
                profile,
                auth,
                answers,
                use_local_chrome=use_local_chrome,
            )
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
        return adapter.submit(task, profile, auth, answers)
    except _SubmitTimeout:
        return SubmitResult(
            adapter=adapter.name,
            verified=False,
            screenshot=(
                task.confirmation_path
                if task.confirmation_path.exists()
                and task.confirmation_path.stat().st_size > 0
                else None
            ),
            details=f"submit_timeout_after_{timeout_seconds}s",
        )
    finally:
        if alarm_enabled:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)


def _host_matches_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _infer_remote_profile(
    row: Dict[str, str],
    *,
    job_text: str,
) -> tuple[str, int, List[str]]:
    csv_score = str(row.get("Remote Likelihood Score", "")).strip()
    csv_policy = str(row.get("Remote Policy", "")).strip().lower()
    if (
        csv_score and csv_score.isdigit() and int(csv_score) >= 100
    ) or csv_policy == "override":
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
    if resume_path is None:
        reasons.append("missing_resume_docx_or_pdf")
    if resume_html_path is None:
        reasons.append("missing_tailored_resume_html")
    if cover_path is None:
        reasons.append("missing_cover_letter")

    job_text = _read_text(job_path)
    resume_html_text = _read_text(resume_html_path).lower()
    track, signals = _role_track_and_signals(role, tags, notes, job_text)
    role_blob = " ".join([role, tags])
    role_is_technical = bool(TECH_ROLE_RE.search(role) or track == "fde")
    if NON_TECH_ROLE_RE.search(role_blob) or not role_is_technical:
        reasons.append("non_technical_role")
    remote_policy, remote_score, remote_evidence = _infer_remote_profile(
        row, job_text=job_text
    )
    adapter = _find_adapter(str(row.get("Career Page URL", "")), adapters)
    adapter_name = adapter.name if adapter is not None else None
    submission_lane = _submission_lane_for_adapter(adapter)

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
    elif not getattr(adapter, "auto_submit_supported", True):
        reasons.append("manual_submission_only")

    required_reasons = {
        "missing_resume_docx_or_pdf",
        "missing_tailored_resume_html",
        "missing_cover_letter",
        "non_technical_role",
        "unsupported_site_for_ci_submit",
        "manual_submission_only",
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


def _is_quarantined_status(status: str) -> bool:
    return _norm_key(status) in QUARANTINED_STATUS_KEYS


def _recover_stale_quarantined_rows(
    rows: Sequence[Dict[str, str]],
    *,
    fit_threshold: int,
    remote_min_score: int,
    adapters: Sequence[SiteAdapter],
) -> Tuple[int, int, List[Dict[str, Any]]]:
    recovered_count = 0
    metadata_updates = 0
    audit: List[Dict[str, Any]] = []

    for idx, row in enumerate(rows):
        status_raw = str(row.get("Status", "")).strip()
        if not _is_quarantined_status(status_raw):
            continue

        notes = str(row.get("Notes", "")).strip()
        notes_blob = notes.lower()
        if not any(marker in notes_blob for marker in STALE_QUARANTINE_MARKERS):
            continue
        if any(marker in notes_blob for marker in UNRECOVERABLE_QUARANTINE_MARKERS):
            continue

        assessment = _assess_queue_gate(
            row,
            fit_threshold=fit_threshold,
            remote_min_score=remote_min_score,
            adapters=adapters,
        )
        blocking_manual_reasons = {
            reason
            for reason in assessment.reasons
            if reason in {"unsupported_site_for_ci_submit", "manual_submission_only"}
        }
        if blocking_manual_reasons:
            continue

        remote_policy = assessment.remote_policy
        remote_score = str(assessment.remote_score)
        remote_evidence = ";".join(assessment.remote_evidence)
        submission_lane = assessment.submission_lane
        if str(row.get("Remote Policy", "")) != remote_policy:
            metadata_updates += 1
        if str(row.get("Remote Likelihood Score", "")) != remote_score:
            metadata_updates += 1
        if str(row.get("Remote Evidence", "")) != remote_evidence:
            metadata_updates += 1
        if str(row.get("Submission Lane", "")) != submission_lane:
            metadata_updates += 1
        row["Remote Policy"] = remote_policy
        row["Remote Likelihood Score"] = remote_score
        row["Remote Evidence"] = remote_evidence
        row["Submission Lane"] = submission_lane

        next_status = DEFAULT_READY_STATUS if assessment.eligible else "Draft"
        row["Status"] = next_status
        row["Notes"] = _append_note(
            notes,
            (
                f"Recovered stale quarantine on {_today_iso()} "
                f"(fit={assessment.score}/{fit_threshold}, "
                f"remote={assessment.remote_score}/{remote_min_score}, "
                f"lane={assessment.submission_lane}, next_status={next_status})."
            ),
        )
        recovered_count += 1
        audit.append(
            {
                "row_index": idx,
                "company": str(row.get("Company", "")).strip(),
                "role": str(row.get("Role", "")).strip(),
                "status_before": status_raw,
                "status_after": next_status,
                "fit_score": assessment.score,
                "remote_score": assessment.remote_score,
                "submission_lane": assessment.submission_lane,
                "reasons": assessment.reasons,
            }
        )

    return recovered_count, metadata_updates, audit


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


def _profile_from_payload(payload: Any) -> Optional[Profile]:
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


def _load_profile_from_env(env_name: str) -> Optional[Profile]:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return _profile_from_payload(payload)


def _load_profile_from_file(path: Path = DEFAULT_PROFILE_JSON) -> Optional[Profile]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return _profile_from_payload(payload)


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
    availability_text = str(
        payload.get(
            "availability_text",
            payload.get("availability", payload.get("start_availability", "Immediate")),
        )
    ).strip()

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
        availability_text=availability_text or "Immediate",
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


def _auth_env_is_malformed(env_name: str) -> bool:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return False
    try:
        payload = json.loads(raw)
    except Exception:
        return True
    if not isinstance(payload, dict):
        return True
    for name, item in payload.items():
        if not isinstance(name, str) or not isinstance(item, dict):
            return True
        storage = item.get("storage_state")
        if storage is not None and not isinstance(storage, dict):
            return True
    return False


def validate_secret_payloads(
    *,
    profile_env: str = "CI_SUBMIT_PROFILE_JSON",
    auth_env: str = "CI_SUBMIT_AUTH_JSON",
    answers_env: str = "CI_SUBMIT_ANSWERS_JSON",
) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if _load_profile_from_env(profile_env) is None:
        errors.append(f"invalid_profile:{profile_env}")
    if _auth_env_is_malformed(auth_env):
        errors.append(f"invalid_auth:{auth_env}")
    if _load_answers_from_env(answers_env) is None:
        errors.append(f"invalid_answers:{answers_env}")
    return (not errors, errors)


def _append_note(existing: str, note: str) -> str:
    base = (existing or "").strip()
    if note in base:
        return base
    if not base:
        return note
    return f"{base}\n{note}"


def _is_ready_status(status: str) -> bool:
    return _norm_key(status) in READY_STATUS_KEYS


def _ashby_auto_submit_url_ok(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if not (host == "ashbyhq.com" or host.endswith(".ashbyhq.com")):
        return False
    if "/form/" in path:
        return False
    segments = [segment for segment in path.split("/") if segment]
    return len(segments) >= 2


def _find_adapter(url: str, adapters: Sequence[SiteAdapter]) -> Optional[SiteAdapter]:
    for adapter in adapters:
        if adapter.name == "ashby" and not _ashby_auto_submit_url_ok(url):
            continue
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


NORMALIZE_APPLIED_NOTES_MARKERS = (
    "pending review and submission",
    "retry needed",
    "manual browser submit required",
    "manual submit required",
    "possible spam",
    "submit blocked",
    "auto-quarantined",
    "antibot",
    "captcha",
)
PROOF_APPLIED_NOTES_MARKERS = (
    "submitted ",
    "submitted.",
    "submitted via",
    "confirmation:",
    "confirmation screenshot",
    "application has been received",
    "application was successfully submitted",
    "thank you for applying",
    "we'll contact you",
)


def _submission_proof_missing_reasons(row: Dict[str, str]) -> List[str]:
    reasons: List[str] = []
    if not str(row.get("Date Applied", "")).strip():
        reasons.append("missing_date_applied")
    submitted_resume_raw = str(row.get("Submitted Resume Path", "")).strip()
    if not submitted_resume_raw:
        reasons.append("missing_submitted_resume_path")

    submission_evidence_raw = str(row.get("Submission Evidence Path", "")).strip()
    if not submission_evidence_raw:
        reasons.append("missing_submission_evidence_path")

    if not str(row.get("Submission Verified At", "")).strip():
        reasons.append("missing_submission_verified_at")
    return reasons


def _should_preserve_applied_status(
    row: Dict[str, str], *, missing: Sequence[str]
) -> bool:
    notes = str(row.get("Notes", "")).strip().lower()
    if any(marker in notes for marker in NORMALIZE_APPLIED_NOTES_MARKERS):
        return False

    has_submission_claim = any(
        marker in notes for marker in PROOF_APPLIED_NOTES_MARKERS
    )
    has_submission_fields = any(
        str(row.get(field, "")).strip()
        for field in (
            "Submitted Resume Path",
            "Submission Evidence Path",
            "Submission Verified At",
        )
    )
    if has_submission_claim or has_submission_fields:
        return True

    return not bool(missing)


def _reconcile_applied_integrity(
    rows: Sequence[Dict[str, str]], *, mutate: bool
) -> Tuple[int, List[Dict[str, Any]], set[int]]:
    """Enforce: Status=Applied only when submission proof fields are present."""
    demoted = 0
    issues: List[Dict[str, Any]] = []
    demoted_rows: set[int] = set()
    for idx, row in enumerate(rows):
        status_raw = str(row.get("Status", "")).strip()
        if _norm_key(status_raw) != "applied":
            continue
        missing = _submission_proof_missing_reasons(row)
        if _should_preserve_applied_status(row, missing=missing):
            continue
        issue = {
            "row_index": idx,
            "company": str(row.get("Company", "")).strip(),
            "role": str(row.get("Role", "")).strip(),
            "status_before": status_raw,
            "missing": missing,
        }
        issues.append(issue)
        if not mutate:
            continue
        row["Status"] = "Draft"
        row["Date Applied"] = ""
        row["Follow Up Date"] = ""
        row["Notes"] = _append_note(
            str(row.get("Notes", "")),
            (
                f"Auto-demoted invalid Applied status on {_today_iso()} "
                f"(missing={','.join(missing)})."
            ),
        )
        demoted += 1
        demoted_rows.add(idx)
    return demoted, issues, demoted_rows


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
    quarantine_blocked: bool = False,
    target_applied: int = 0,
    max_cycles: int = 1,
    require_secret_auth: bool = True,
    auto_promote_ready: bool = True,
    profile_env: str = "CI_SUBMIT_PROFILE_JSON",
    auth_env: str = "CI_SUBMIT_AUTH_JSON",
    answers_env: str = "CI_SUBMIT_ANSWERS_JSON",
    adapters: Optional[Sequence[SiteAdapter]] = None,
    use_local_chrome: bool = False,
    visible: bool = False,
    submit_timeout_seconds: int = 180,
    companies_filter: Optional[Sequence[str]] = None,
    role_contains_filter: Optional[Sequence[str]] = None,
) -> int:
    fields, rows = _read_tracker(tracker_csv)
    adapters = list(
        adapters
        or [
            OpenAIAshbyAdapter(),
            InferactAshbyAdapter(),
            AshbyAdapter(),
            GreenhouseAdapter(),
            LeverAdapter(),
            OracleAdapter(),
            TalentpriseAdapter(),
        ]
    )
    fields = _ensure_tracker_fields(
        fields, rows, TRACKER_REMOTE_FIELDS + TRACKER_SUBMISSION_FIELDS
    )

    profile = _load_profile_from_env(profile_env)
    if profile is None:
        profile = _load_profile_from_file()
    auth_env_configured = bool(os.getenv(auth_env, "").strip())
    auth_env_malformed = _auth_env_is_malformed(auth_env)
    auth_map = _load_auth_by_adapter(auth_env)
    answers = _load_answers_from_env(answers_env)

    if not dry_run and not queue_only and require_secret_auth:
        if profile is None:
            print(f"ERROR: missing/invalid secret profile in ${profile_env}.")
            return 2
        if auth_env_malformed:
            print(f"ERROR: malformed secret auth map in ${auth_env}.")
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
            availability_text="Immediate",
        )

    can_mutate_tracker = (not dry_run) or queue_only
    quarantine_recovered_count, quarantine_metadata_updates, quarantine_recovery_audit = (
        _recover_stale_quarantined_rows(
            rows,
            fit_threshold=fit_threshold,
            remote_min_score=remote_min_score,
            adapters=adapters,
        )
    )
    applied_integrity_demoted_count, applied_integrity_issues, applied_demoted_rows = (
        _reconcile_applied_integrity(rows, mutate=can_mutate_tracker)
    )
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
            blocked_by_integrity_demotion = idx in applied_demoted_rows
            eligible_for_ready = (
                assessment.eligible and not blocked_by_integrity_demotion
            )
            audit_reasons = list(assessment.reasons)
            if blocked_by_integrity_demotion:
                audit_reasons.append("integrity_demotion_same_run")
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
                "eligible_for_ready": eligible_for_ready,
                "reasons": audit_reasons,
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

            if _is_draft_status(status_raw) and eligible_for_ready:
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
            elif (
                _is_draft_status(status_raw)
                and blocked_by_integrity_demotion
                and can_mutate_tracker
            ):
                row["Notes"] = _append_note(
                    str(row.get("Notes", "")),
                    (
                        f"Queue auto-promotion skipped on {_today_iso()} "
                        "(reason=integrity_demotion_same_run)."
                    ),
                )
            elif _is_ready_status(status_raw) and not assessment.eligible:
                queue_demoted_count += 1
                if can_mutate_tracker:
                    if _is_manual_submission_only(assessment.reasons):
                        row["Status"] = "Quarantined"
                        row["Notes"] = _append_note(
                            str(row.get("Notes", "")),
                            (
                                f"Queue gate quarantined on {_today_iso()} "
                                f"(fit={assessment.score}/{fit_threshold}, remote={assessment.remote_score}/{remote_min_score}; "
                                f"reasons={','.join(assessment.reasons)}). "
                                "Needs manual completion."
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

    target_applied = max(0, int(target_applied))
    max_cycles = max(1, int(max_cycles))

    def _apply_company_filter(
        indices: List[int], ranked: List[Dict[str, Any]]
    ) -> Tuple[List[int], List[Dict[str, Any]]]:
        if not companies_filter and not role_contains_filter:
            return indices, ranked
        allowed_cos = (
            {str(c).strip().lower() for c in companies_filter if str(c).strip()}
            if companies_filter
            else None
        )
        needles = (
            [str(n).strip().lower() for n in role_contains_filter if str(n).strip()]
            if role_contains_filter
            else None
        )

        def keep(item: Dict[str, Any]) -> bool:
            if allowed_cos and str(item.get("company", "")).strip().lower() not in allowed_cos:
                return False
            if needles:
                role_lower = str(item.get("role", "")).lower()
                if not any(n in role_lower for n in needles):
                    return False
            return True

        filtered_ranked = [item for item in ranked if keep(item)]
        filtered_indices = [int(item["row_index"]) for item in filtered_ranked]
        return filtered_indices, filtered_ranked

    any_filter_active = bool(companies_filter or role_contains_filter)
    ready_indices, ready_ranked = _rank_ready_rows_for_submit(
        rows,
        max_jobs=0 if any_filter_active else max_jobs,
        fit_threshold=fit_threshold,
        remote_min_score=remote_min_score,
        adapters=adapters,
    )
    ready_indices, ready_ranked = _apply_company_filter(ready_indices, ready_ranked)
    if any_filter_active:
        ready_indices = ready_indices[:max_jobs]
        ready_ranked = ready_ranked[:max_jobs]

    report: Dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dry_run": dry_run,
        "queue_only": queue_only,
        "max_jobs": max_jobs,
        "fit_threshold": fit_threshold,
        "remote_min_score": remote_min_score,
        "tracker_csv": str(tracker_csv),
        "auth_env_configured": auth_env_configured,
        "auth_adapters_available": sorted(
            name for name, auth in auth_map.items() if auth.storage_state is not None
        ),
        "quarantine_recovered_count": quarantine_recovered_count,
        "quarantine_recovery_audit": quarantine_recovery_audit,
        "applied_integrity_demoted_count": applied_integrity_demoted_count,
        "applied_integrity_issues": applied_integrity_issues,
        "queue_promoted_count": queue_promoted_count,
        "queue_demoted_count": queue_demoted_count,
        "queue_audit": queue_audit,
        "ready_rows_total": len(ready_indices),
        "ready_queue_ranked": ready_ranked,
        "learning_arms_json": str(DEFAULT_LEARNING_ARMS_JSON),
        "target_applied": target_applied,
        "max_cycles": max_cycles,
        "quarantine_blocked": bool(quarantine_blocked),
        "results": [],
    }

    applied_count = 0
    failed_count = 0
    skipped_count = 0

    if not dry_run and not queue_only:
        configured = sorted(
            name for name, auth in auth_map.items() if auth.storage_state is not None
        )
        if configured:
            print("Adapter auth state loaded for: " + ", ".join(configured))
        else:
            print(
                f"WARN: ${auth_env} is empty or has no usable storage state; "
                "proceeding with fresh browser contexts."
            )

    if queue_only:
        report["applied_count"] = 0
        report["failed_count"] = 0
        report["skipped_count"] = 0
        report["changed"] = bool(
            can_mutate_tracker
            and (
                quarantine_recovered_count
                or applied_integrity_demoted_count
                or queue_promoted_count
                or queue_demoted_count
                or queue_metadata_updates
                or quarantine_metadata_updates
            )
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8"
        )
        if report["changed"]:
            _write_tracker(tracker_csv, fields, rows)
        print(
            "Queue gate processed: "
            f"applied_demoted={applied_integrity_demoted_count} "
            f"promoted={queue_promoted_count} demoted={queue_demoted_count} "
            f"ready_now={len(ready_indices)}"
        )
        print(f"Report: {report_path}")
        if fail_on_error and queue_demoted_count > 0:
            return 1
        return 0

    cycles_run = 0
    while True:
        if cycles_run >= max_cycles:
            break
        ready_indices, ready_ranked = _rank_ready_rows_for_submit(
            rows,
            max_jobs=0 if any_filter_active else max_jobs,
            fit_threshold=fit_threshold,
            remote_min_score=remote_min_score,
            adapters=adapters,
        )
        ready_indices, ready_ranked = _apply_company_filter(ready_indices, ready_ranked)
        if any_filter_active:
            ready_indices = ready_indices[:max_jobs]
            ready_ranked = ready_ranked[:max_jobs]
        report["ready_queue_ranked"] = ready_ranked
        if not ready_indices:
            break
        cycles_run += 1
        cycle_applied = 0
        ready_rank_map = {int(item["row_index"]): item for item in ready_ranked}

        for row_idx in ready_indices:
            row = rows[row_idx]
            company = str(row.get("Company", "")).strip()
            role = str(row.get("Role", "")).strip()
            url = str(row.get("Career Page URL", "")).strip()
            row_result: Dict[str, Any] = {
                "cycle": cycles_run,
                "row_index": row_idx,
                "company": company,
                "role": role,
                "url": url,
                "status_before": str(row.get("Status", "")).strip(),
                "submission_lane": str(row.get("Submission Lane", "")).strip(),
                "mode": "dry_run" if dry_run else "execute",
            }
            learning_priority = ready_rank_map.get(row_idx)
            if learning_priority is not None:
                row_result["learning_priority"] = {
                    "adjusted_score": learning_priority.get("adjusted_score"),
                    "learned_score": learning_priority.get("learned_score"),
                    "method": learning_priority.get("method"),
                    "matched_positive_tags": learning_priority.get(
                        "matched_positive_tags"
                    ),
                    "matched_negative_tags": learning_priority.get(
                        "matched_negative_tags"
                    ),
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
                    if _is_manual_submission_only(assessment.reasons):
                        row["Status"] = "Quarantined"
                        row["Notes"] = _append_note(
                            str(row.get("Notes", "")),
                            (
                                f"Submission quarantined by queue gate on {_today_iso()} "
                                f"(fit={assessment.score}/{fit_threshold}, remote={assessment.remote_score}/{remote_min_score}; "
                                f"reasons={','.join(assessment.reasons)}). "
                                "Needs manual completion."
                            ),
                        )
                    else:
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
            row_result["auth_mode"] = (
                "storage_state" if auth.storage_state is not None else "fresh_context"
            )

            result = _submit_with_adapter(
                adapter,
                task,
                profile,
                auth,
                answers,
                use_local_chrome=use_local_chrome,
                visible=visible,
                submit_timeout_seconds=submit_timeout_seconds,
            )
            row_result["adapter_details"] = result.details
            row_result["verified"] = result.verified
            row_result["browser_backend"] = result.browser_backend
            if result.browser_note:
                row_result["browser_note"] = result.browser_note
            row_result["screenshot"] = (
                str(result.screenshot) if result.screenshot else None
            )

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
                row["Submission Verified At"] = dt.datetime.now(
                    dt.timezone.utc
                ).isoformat()
                row["Notes"] = _append_note(
                    str(row.get("Notes", "")),
                    (
                        f"CI submit verified on {_today_iso()} via {adapter.name}. "
                        f"Confirmation: {result.screenshot}"
                    ),
                )
                row_result["result"] = "applied"
                applied_count += 1
                cycle_applied += 1
            elif result.details.startswith("recaptcha_score_below_threshold"):
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
                row_result["manual_rescue_ready"] = bool(
                    screenshot_ok and resume_exists
                )
                if screenshot_ok:
                    row_result["manual_rescue_evidence"] = str(result.screenshot)
                row["Status"] = DEFAULT_READY_STATUS
                row["Notes"] = _append_note(
                    str(row.get("Notes", "")),
                    (
                        f"CI submit blocked by anti-bot on {_today_iso()} via {adapter.name}. "
                        f"Reason={result.details}. Manual browser submit required."
                        + (
                            f" Manual rescue evidence: {result.screenshot}."
                            if screenshot_ok and result.screenshot is not None
                            else ""
                        )
                    ),
                )
                queue_metadata_updates += 1
                skipped_count += 1
                if count_skipped_as_failures:
                    failed_count += 1
            else:
                details = (result.details or "").strip()
                quarantinable = (
                    details.startswith("missing_required_answers:")
                    or details.startswith("required_fields_unanswered_after_retry:")
                    or details == "required_questions_unanswered_after_retry"
                    or _is_manual_submission_required_detail(details)
                    or details
                    in {
                        "ashby_job_not_found",
                        "ashby_antibot_challenge",
                        "ashby_application_not_loaded",
                        "ashby_resume_input_missing",
                        "missing_file_input",
                    }
                    or (details == "confirmation_text_not_detected" and screenshot_ok)
                )
                if quarantine_blocked and quarantinable:
                    row_result["result"] = "skipped"
                    row_errors = [
                        "manual_submit_required",
                        "quarantinable_submit_blocker",
                        details or "required_fields_blocked",
                    ]
                    if not resume_exists:
                        row_errors.append("missing_or_invalid_submitted_resume_path")
                    if not screenshot_ok:
                        row_errors.append("missing_or_empty_confirmation_screenshot")
                    row_result["errors"] = row_errors
                    row["Status"] = (
                        "Closed" if details == "ashby_job_not_found" else "Quarantined"
                    )
                    row["Notes"] = _append_note(
                        str(row.get("Notes", "")),
                        (
                            f"CI submit blocked on {_today_iso()} via {adapter.name}. "
                            f"Reason={details or 'required_fields_blocked'}. "
                            + (
                                "Posting appears closed/not found."
                                if details == "ashby_job_not_found"
                                else "Needs manual completion."
                            )
                        ),
                    )
                    queue_metadata_updates += 1
                    skipped_count += 1
                    if count_skipped_as_failures:
                        failed_count += 1
                else:
                    # Soft failures: keep in queue for retry on next CI run
                    # instead of permanently marking as failed.
                    row_result["result"] = "skipped"
                    row_errors = ["verification_failed_will_retry"]
                    if details:
                        row_errors.append(details)
                    if not resume_exists:
                        row_errors.append("missing_or_invalid_submitted_resume_path")
                    if not screenshot_ok:
                        row_errors.append("missing_or_empty_confirmation_screenshot")
                    row_result["errors"] = row_errors
                    if can_mutate_tracker:
                        row["Status"] = DEFAULT_READY_STATUS
                        row["Notes"] = _append_note(
                            str(row.get("Notes", "")),
                            (
                                f"CI submit unconfirmed on {_today_iso()} via {adapter.name}. "
                                f"Reason={details}. Will retry next run."
                            ),
                        )
                        queue_metadata_updates += 1
                    skipped_count += 1

            report["results"].append(row_result)

        if dry_run:
            break
        if target_applied > 0 and applied_count >= target_applied:
            break
        next_ready_indices, _ = _rank_ready_rows_for_submit(
            rows,
            max_jobs=max_jobs,
            fit_threshold=fit_threshold,
            remote_min_score=remote_min_score,
            adapters=adapters,
        )
        if not next_ready_indices:
            break
        # Prevent no-progress loops when the same rows remain ready between cycles.
        if cycle_applied == 0 and next_ready_indices == ready_indices:
            break

    report["applied_count"] = applied_count
    report["failed_count"] = failed_count
    report["skipped_count"] = skipped_count
    report["cycles_run"] = cycles_run
    report["changed"] = bool(
        can_mutate_tracker
        and (
            applied_integrity_demoted_count
            or applied_count
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
        help="Optional env var containing per-adapter auth JSON.",
    )
    ap.add_argument(
        "--answers-env",
        default="CI_SUBMIT_ANSWERS_JSON",
        help="Env var containing required screener answers JSON.",
    )
    ap.add_argument(
        "--quarantine-blocked",
        action="store_true",
        help="Auto-quarantine rows blocked by required fields that need manual submit.",
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
        "--validate-secrets-only",
        action="store_true",
        help=(
            "Validate submit secret payloads and exit without reading the tracker "
            "or attempting queue/submission work."
        ),
    )
    ap.add_argument(
        "--use-local-chrome",
        action="store_true",
        help="Force use of a dedicated local Chrome automation profile",
    )
    ap.add_argument(
        "--visible",
        action="store_true",
        help="Run browser in visible mode (non-headless)",
    )
    ap.add_argument(
        "--submit-timeout-seconds",
        type=int,
        default=180,
        help=(
            "Maximum wall-clock seconds for one portal submission attempt "
            "before skipping that row."
        ),
    )
    ap.add_argument(
        "--companies",
        default="",
        help=(
            "Comma-separated company names to filter the ready queue to. "
            "Bypasses alphabetical tie-breaking and lets you target e.g. "
            "--companies 'OpenAI,Anthropic' without re-ranking everything."
        ),
    )
    ap.add_argument(
        "--role-contains",
        default="",
        help=(
            "Comma-separated substrings; row's Role must contain at least one "
            "(case-insensitive). Pairs with --companies to target e.g. "
            "OpenAI 'forward deployed,deployment engineer,codex' for a canary."
        ),
    )
    args = ap.parse_args()
    if args.execute and args.queue_only:
        print("ERROR: --execute and --queue-only are mutually exclusive.")
        return 2
    if args.validate_secrets_only and (args.execute or args.queue_only):
        print(
            "ERROR: --validate-secrets-only cannot be combined with "
            "--execute or --queue-only."
        )
        return 2

    try:
        if args.validate_secrets_only:
            ok, _errors = validate_secret_payloads(
                profile_env=args.profile_env,
                auth_env=args.auth_env,
                answers_env=args.answers_env,
            )
            if ok:
                print("Secret payload validation passed.")
                return 0
            print("Secret payload validation failed.")
            return 2
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
            quarantine_blocked=args.quarantine_blocked,
            target_applied=max(0, int(args.target_applied)),
            max_cycles=max(1, int(args.max_cycles)),
            require_secret_auth=True,
            profile_env=args.profile_env,
            auth_env=args.auth_env,
            answers_env=args.answers_env,
            use_local_chrome=args.use_local_chrome,
            visible=args.visible,
            submit_timeout_seconds=max(0, int(args.submit_timeout_seconds)),
            companies_filter=[c.strip() for c in args.companies.split(",") if c.strip()]
            or None,
            role_contains_filter=[
                n.strip() for n in args.role_contains.split(",") if n.strip()
            ]
            or None,
        )
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
