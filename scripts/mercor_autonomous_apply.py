#!/usr/bin/env python3
"""Guarded Mercor application lane using the local resume-ci browser profile."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET_URL = (
    "https://work.mercor.com/jobs/apply/"
    "candidate_AAABnfNdSblZtboWnE1Bb4FA?returnPath=%2Fexplore"
)
DEFAULT_REPORT = (
    ROOT / "applications" / "job_applications" / "mercor_autonomous_apply_report.json"
)
DEFAULT_PROFILE_JSON = (
    ROOT / "applications" / "job_applications" / "candidate_profile.json"
)
DEFAULT_RESUME_DIR = ROOT / "applications" / "mercor" / "tailored_resumes"
DEFAULT_SUBMISSIONS_DIR = ROOT / "applications" / "mercor" / "submissions"
DEFAULT_CHROME_PROFILE = (
    Path.home() / "Library" / "Application Support" / "resume-ci" / "chrome-profile"
)

SAFE_CLICK_TEXTS = (
    "continue application",
    "apply anyway",
    "apply",
    "submit application",
    "submit",
)

SUBMITTED_MARKERS = (
    "your application has been submitted",
    "application submitted",
    "submitted successfully",
    "thanks for applying",
)

INTERVIEW_MARKERS = (
    "domain expert interview",
    "ai interview",
    "start interview",
    "take assessment",
    "begin assessment",
)

DOB_MARKERS = ("date of birth", "birth date", "dob")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "mercor"


def _today_iso() -> str:
    return dt.datetime.now().date().isoformat()


def load_profile(path: Path = DEFAULT_PROFILE_JSON) -> Dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    return {str(k): str(v) for k, v in payload.items() if v is not None}


def choose_resume(path: Path = DEFAULT_RESUME_DIR) -> Optional[Path]:
    if path.is_file():
        return path
    if not path.exists():
        return None
    candidates = sorted(
        [p for p in path.glob("*.pdf") if p.is_file()]
        + [p for p in path.glob("*.docx") if p.is_file()],
        key=lambda p: (p.stat().st_mtime, p.name),
        reverse=True,
    )
    return candidates[0] if candidates else None


def detect_status(text: str) -> str:
    text_l = (text or "").lower()
    if "sign in" in text_l and "mercor" in text_l:
        return "not_logged_in"
    if "not accepting applications" in text_l or "applications currently" in text_l:
        return "closed"
    if any(marker in text_l for marker in SUBMITTED_MARKERS):
        return "submitted"
    if any(marker in text_l for marker in DOB_MARKERS):
        return "dob_required"
    if any(marker in text_l for marker in INTERVIEW_MARKERS):
        if "not done" in text_l or "0 of" in text_l or "start" in text_l:
            return "manual_interview_required"
    if "continue application" in text_l:
        return "in_progress"
    if "apply" in text_l:
        return "ready"
    return "unknown"


def _text_value(profile: Dict[str, str], *keys: str) -> str:
    for key in keys:
        value = profile.get(key, "").strip()
        if value:
            return value
    return ""


def _fill_known_fields(page: Any, profile: Dict[str, str]) -> List[str]:
    filled: List[str] = []
    field_map: Sequence[Tuple[str, str]] = (
        ("Full name", _text_value(profile, "full_name", "name")),
        (
            "Name",
            " ".join(
                p
                for p in [
                    _text_value(profile, "first_name"),
                    _text_value(profile, "last_name"),
                ]
                if p
            ),
        ),
        ("Email", _text_value(profile, "email")),
        ("Phone", _text_value(profile, "phone")),
        ("LinkedIn URL", _text_value(profile, "linkedin")),
        ("LinkedIn", _text_value(profile, "linkedin")),
        ("GitHub", _text_value(profile, "github")),
        ("Location", _text_value(profile, "location", "city")),
    )
    for label, value in field_map:
        if not value:
            continue
        filled_label = False
        try:
            control = page.get_by_label(label, exact=False).first
            if control.count() > 0:
                existing = ""
                try:
                    existing = str(control.input_value(timeout=800) or "").strip()
                except Exception:
                    existing = ""
                if not existing:
                    control.fill(value, timeout=1200)
                    filled_label = True
        except Exception:
            filled_label = False
        if filled_label:
            filled.append(label)
    return filled


def _upload_resume(page: Any, resume: Optional[Path]) -> bool:
    if resume is None or not resume.exists():
        return False
    try:
        file_input = page.locator("input[type='file']").first
        if file_input.count() <= 0:
            return False
        file_input.set_input_files(str(resume), timeout=4000)
        return True
    except Exception:
        return False


def _click_safe_button(page: Any) -> Optional[str]:
    for label in SAFE_CLICK_TEXTS:
        clicked = False
        try:
            button = page.get_by_role("button", name=re.compile(label, re.I)).first
            if button.count() <= 0:
                clicked = False
            elif not button.is_enabled(timeout=1000):
                clicked = False
            else:
                button.scroll_into_view_if_needed(timeout=1000)
                button.click(timeout=2500)
                clicked = True
        except Exception:
            clicked = False
        if clicked:
            return label
    return None


def run_mercor_lane(
    *,
    url: str,
    report_path: Path,
    resume_path: Optional[Path],
    headless: bool,
    browser_channel: str,
    chrome_user_data_dir: Path,
    max_clicks: int,
    timeout_ms: int,
) -> int:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:
        print(f"ERROR: playwright unavailable: {exc}", file=sys.stderr)
        return 2

    profile = load_profile()
    resume = choose_resume(resume_path or DEFAULT_RESUME_DIR)
    today = _today_iso()
    DEFAULT_SUBMISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    screenshot_path = (
        DEFAULT_SUBMISSIONS_DIR
        / f"{today}_mercor_autonomous_apply_{_slug(url)[:80]}.png"
    )

    report: Dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "url": url,
        "headless": headless,
        "browser_channel": browser_channel,
        "chrome_user_data_dir": str(chrome_user_data_dir),
        "resume_path": str(resume) if resume else None,
        "screenshot": str(screenshot_path),
        "filled_fields": [],
        "clicked": [],
        "status": "unknown",
        "submitted": False,
        "blocked_reason": "",
    }

    with sync_playwright() as pw:
        launch_kwargs: Dict[str, Any] = {
            "headless": headless,
            "user_data_dir": str(chrome_user_data_dir),
        }
        if browser_channel:
            launch_kwargs["channel"] = browser_channel
        context = pw.chromium.launch_persistent_context(**launch_kwargs)
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(2500)
            text = page.locator("body").inner_text(timeout=timeout_ms)
            status = detect_status(text)

            for _ in range(max(0, max_clicks)):
                if status in {
                    "submitted",
                    "not_logged_in",
                    "closed",
                    "dob_required",
                    "manual_interview_required",
                }:
                    break
                report["filled_fields"].extend(_fill_known_fields(page, profile))
                if _upload_resume(page, resume):
                    report["resume_uploaded"] = True
                clicked = _click_safe_button(page)
                if not clicked:
                    break
                report["clicked"].append(clicked)
                page.wait_for_timeout(3000)
                text = page.locator("body").inner_text(timeout=timeout_ms)
                status = detect_status(text)

            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_path), full_page=True)
            report["status"] = status
            report["submitted"] = status == "submitted"
            if status in {"not_logged_in", "closed", "dob_required", "manual_interview_required"}:
                report["blocked_reason"] = status
            elif status != "submitted":
                report["blocked_reason"] = "unverified_or_no_safe_action"
        finally:
            context.close()

    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    print(f"STATUS={report['status']}")
    print(f"SUBMITTED={str(report['submitted']).lower()}")
    print(f"SCREENSHOT={screenshot_path}")
    print(f"REPORT={report_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_TARGET_URL)
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--resume", default="")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--browser-channel", default="chromium")
    ap.add_argument("--chrome-user-data-dir", default=str(DEFAULT_CHROME_PROFILE))
    ap.add_argument("--max-clicks", type=int, default=4)
    ap.add_argument("--timeout-ms", type=int, default=45000)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return run_mercor_lane(
        url=args.url,
        report_path=Path(args.report),
        resume_path=Path(args.resume).expanduser() if args.resume else None,
        headless=bool(args.headless),
        browser_channel=str(args.browser_channel or "").strip(),
        chrome_user_data_dir=Path(args.chrome_user_data_dir).expanduser(),
        max_clicks=max(0, int(args.max_clicks)),
        timeout_ms=max(5000, int(args.timeout_ms)),
    )


if __name__ == "__main__":
    raise SystemExit(main())
