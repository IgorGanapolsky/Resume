#!/usr/bin/env python3
"""Talentprise profile automation — login, update profile, browse matched jobs.

Talentprise is a profile-based AI matching platform. Recruiters find you based
on your profile quality. This script:
  1. Logs in via Google SSO using the local Chrome profile (existing session)
  2. Updates profile sections (biography, skills, languages, experience)
  3. Checks dashboard for recruiter matches and invitations
  4. Captures screenshots as evidence

Usage:
  python3 scripts/talentprise_automation.py                  # full flow
  python3 scripts/talentprise_automation.py --profile-only   # update profile only
  python3 scripts/talentprise_automation.py --dashboard-only # check matches only

Requires: pip install playwright && python -m playwright install chromium
"""

from __future__ import annotations

import argparse
import random
import time
from datetime import date
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "applications" / "talentprise" / "submissions"
TODAY = date.today().isoformat()

BASE_URL = "https://app.talentprise.com"
LOGIN_URL = f"{BASE_URL}/login"
PROFILE_BASE = f"{BASE_URL}/talent/profile/edit"
DASHBOARD_URL = f"{BASE_URL}/talent/dashboard"

# Profile section URLs
PROFILE_URLS = {
    "biography": f"{PROFILE_BASE}/biography",
    "languages": f"{PROFILE_BASE}/biography/languages",
    "skills": f"{PROFILE_BASE}/skills",
    "experience": f"{PROFILE_BASE}/experience",
    "expertise": f"{PROFILE_BASE}/expertise",
    "preferences": f"{PROFILE_BASE}/preferences",
}

# ---------------------------------------------------------------------------
# Profile data — sourced from application_answers.md
# ---------------------------------------------------------------------------

PROFILE = {
    "first_name": "Igor",
    "last_name": "Ganapolsky",
    "email": "iganapolsky@gmail.com",
    "phone": "(201) 639-1534",
    "location": "Coral Springs, FL, USA",
    "linkedin": "https://www.linkedin.com/in/igor-ganapolsky-859317343/",
    "github": "https://github.com/IgorGanapolsky",
    "current_company": "Subway",
    "current_title": "Senior Software Engineer",
}

SKILLS_IT = [
    "Python",
    "TypeScript",
    "React Native",
    "React",
    "Node.js",
    "PostgreSQL",
    "MongoDB",
    "Docker",
    "GitHub Actions",
    "Claude AI",
]

SKILLS_PERSONAL = [
    "Problem Solving",
    "Team Leadership",
    "Communication",
    "Adaptability",
    "Critical Thinking",
]

LANGUAGES = [
    {"name": "English", "level": "Native"},
    {"name": "Russian", "level": "Native"},
]

EXPERIENCE_SUMMARY = (
    "Senior Software Engineer with Fortune 500 experience at Subway. "
    "Led React Native New Architecture migration achieving 68% build time reduction "
    "and 99.5%+ crash-free sessions across millions of users. "
    "Built 26 production Claude AI skills with RLHF (Thompson Sampling, 76.6% positive rate). "
    "Created RAG pipelines using LanceDB/ChromaDB with 40-50% API cost reduction. "
    "17+ GitHub Actions CI/CD workflows. 13 autonomous agents orchestrating developer workflows."
)


# ---------------------------------------------------------------------------
# Human-like interaction helpers
# ---------------------------------------------------------------------------

def _wait(min_s: float = 1.0, max_s: float = 3.0) -> None:
    time.sleep(random.uniform(min_s, max_s))


def _type_human(locator: Any, text: str) -> None:
    """Type with randomized delays like a real person."""
    if not text:
        return
    try:
        locator.focus(timeout=3000)
        for char in text:
            locator.type(char, delay=random.randint(40, 120))
            if random.random() < 0.08:
                time.sleep(random.uniform(0.1, 0.3))
    except Exception:
        try:
            locator.fill(text, timeout=3000)
        except Exception:
            pass


def _click_human(locator: Any) -> None:
    time.sleep(random.uniform(0.3, 1.0))
    locator.click()


def _mouse_wander(page: Any) -> None:
    """Move mouse randomly to appear human."""
    viewport = page.viewport_size or {"width": 1280, "height": 800}
    for _ in range(3):
        x = random.randint(100, viewport["width"] - 100)
        y = random.randint(100, viewport["height"] - 100)
        page.mouse.move(x, y, steps=random.randint(5, 15))
        time.sleep(random.uniform(0.1, 0.4))


def _screenshot(page: Any, name: str) -> Path:
    """Capture a full-page screenshot as evidence."""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"{TODAY}_talentprise_{name}.png"
    page.screenshot(path=str(path), full_page=True)
    print(f"  Screenshot: {path.relative_to(ROOT)}")
    return path


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------

def login_google_sso(page: Any) -> bool:
    """Log in to Talentprise using Google SSO via local Chrome profile.

    Since we use launch_persistent_context with the real Chrome profile,
    the Google session cookies are already present. We just need to click
    the Google sign-in button and handle the OAuth popup/redirect.
    """
    print("Navigating to Talentprise login...")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    _wait(2, 4)
    _mouse_wander(page)

    # Dismiss cookie consent banner if present
    for cookie_selector in [
        "button:has-text('Accept All')",
        "button:has-text('Accept')",
        "[data-cky-tag='accept-button']",
    ]:
        try:
            btn = page.locator(cookie_selector).first
            if btn.count() > 0 and btn.is_visible():
                _click_human(btn)
                print("  Dismissed cookie banner.")
                _wait(1, 2)
                break
        except Exception:
            continue

    # Check if already logged in (redirected to dashboard)
    if "/dashboard" in page.url or "/talent/" in page.url:
        print("  Already logged in!")
        _screenshot(page, "already_logged_in")
        return True

    _screenshot(page, "login_page")

    # The Google "Sign in with Google" button is rendered by Google Identity
    # Services (GIS) inside a cross-origin iframe. Standard locators can't
    # reach into the iframe's shadow DOM. We need to:
    # 1. Find the GIS iframe
    # 2. Click inside it using frame_locator
    google_clicked = False

    # Strategy 1: Find the GIS iframe and click the button, catching the popup
    try:
        gsi_frames = page.frame_locator("iframe[src*='accounts.google.com/gsi']")
        gsi_btn = gsi_frames.locator("div[role='button']").first
        if gsi_btn.count() > 0:
            print("  Found Google GIS button inside iframe.")
            # Use expect_popup to catch the OAuth window
            try:
                with page.expect_popup(timeout=10000) as popup_info:
                    _click_human(gsi_btn)
                popup_page = popup_info.value
                if popup_page:
                    google_clicked = True
                    print(f"  OAuth popup opened: {popup_page.url[:80]}")
            except Exception:
                # Popup might not open (e.g., no Google session)
                _click_human(gsi_btn)
                google_clicked = True
                print("  GIS button clicked (no popup caught).")
    except Exception as e:
        print(f"  GIS iframe strategy 1 failed: {e}")

    # Strategy 2: Find any iframe from Google and click
    if not google_clicked:
        try:
            for frame in page.frames:
                if "accounts.google.com" in (frame.url or ""):
                    btn = frame.locator("div[role='button']").first
                    if btn.count() > 0:
                        _click_human(btn)
                        google_clicked = True
                        print("  Clicked Google button via frame iteration.")
                        break
        except Exception as e:
            print(f"  GIS iframe strategy 2 failed: {e}")

    # Strategy 3: Click the container div around the iframe using coordinates
    # The GIS button renders inside a div with class S9gUrf-YoZ4jf or similar
    if not google_clicked:
        try:
            # Look for the GIS container or any iframe from Google
            for selector in [
                "div.S9gUrf-YoZ4jf",
                "div:has(> iframe[src*='google'])",
                "iframe[src*='accounts.google.com']",
            ]:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible():
                    box = loc.bounding_box()
                    if box:
                        # Click in the center of the element
                        page.mouse.click(
                            box["x"] + box["width"] / 2,
                            box["y"] + box["height"] / 2,
                        )
                        google_clicked = True
                        print(f"  Clicked Google SSO via coordinates ({selector}).")
                        break
        except Exception as e:
            print(f"  GIS coordinate click failed: {e}")

    # Strategy 4: Scroll down and look for a non-iframe Google button
    # (some renders use a plain div/button instead of iframe)
    if not google_clicked:
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            _wait(1, 2)
            for selector in [
                "div.nsm7Bb-HzV7m-LgbsSe",  # GIS button class
                "[data-provider='google']",
                "button:has-text('Google')",
                "a:has-text('Google')",
            ]:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible():
                    _click_human(loc)
                    google_clicked = True
                    print(f"  Clicked Google SSO via fallback ({selector}).")
                    break
        except Exception as e:
            print(f"  Fallback click failed: {e}")

    if not google_clicked:
        print("  ERROR: Could not find or click Google SSO button.")
        _screenshot(page, "no_google_button")
        return False

    # Google OAuth opens a popup for account selection.
    # Wait for it and handle the account picker.
    print("  Waiting for Google OAuth popup...")
    _wait(3, 6)

    # Check all open pages/popups for the Google accounts flow
    popup = None
    for p in page.context.pages:
        if p != page and "accounts.google.com" in (p.url or ""):
            popup = p
            break

    if popup:
        print(f"  Google OAuth popup found: {popup.url[:80]}")
        try:
            popup.wait_for_load_state("domcontentloaded", timeout=15000)
            _wait(2, 3)

            # Take screenshot of the Google account picker
            try:
                popup_screenshot = EVIDENCE_DIR / f"{TODAY}_google_account_picker.png"
                popup.screenshot(path=str(popup_screenshot), full_page=True)
                print(f"  Screenshot: {popup_screenshot.relative_to(ROOT)}")
            except Exception:
                pass

            # Try to select the correct account
            email = PROFILE["email"]
            account_selected = False

            for selector in [
                f"div[data-email='{email}']",
                f"[data-identifier='{email}']",
                f"li:has-text('{email}')",
                f"div:has-text('{email}')",
            ]:
                try:
                    loc = popup.locator(selector).first
                    if loc.count() > 0 and loc.is_visible():
                        _click_human(loc)
                        account_selected = True
                        print(f"  Selected account: {email}")
                        break
                except Exception:
                    continue

            if not account_selected:
                # May need to enter email manually
                print("  Account not pre-listed. Trying email input...")
                try:
                    email_input = popup.locator("input[type='email']").first
                    if email_input.count() > 0:
                        _type_human(email_input, email)
                        _wait(0.5, 1)
                        # Click Next
                        next_btn = popup.locator("button:has-text('Next'), #identifierNext").first
                        if next_btn.count() > 0:
                            _click_human(next_btn)
                            account_selected = True
                            print("  Entered email and clicked Next.")
                except Exception as e:
                    print(f"  Email entry failed: {e}")

            _wait(5, 10)

        except Exception as e:
            print(f"  OAuth popup handling error: {e}")
    else:
        print("  No Google popup detected — may have auto-redirected or need manual login.")
        # GIS may use redirect mode instead of popup — wait for redirect
        _wait(5, 8)

    # Wait for redirect back to Talentprise
    try:
        page.wait_for_url("**/talent/**", timeout=30000)
    except Exception:
        _wait(3, 5)

    logged_in = "/talent/" in page.url or "/dashboard" in page.url
    if logged_in:
        print("  Login successful!")
        _screenshot(page, "login_success")
    else:
        print(f"  Login status unclear. Current URL: {page.url}")
        _screenshot(page, "login_result")
        # If still on login page, user may need to complete auth manually first
        if "/login" in page.url:
            print("\n  Google SSO requires an active Google session.")
            print("  Opening Google login so you can sign in...")
            # Navigate to Google to establish session in this profile
            page.goto("https://accounts.google.com/signin", wait_until="domcontentloaded", timeout=30000)
            _wait(2, 3)
            _screenshot(page, "google_signin_page")
            print("  Please sign into Google in the browser window that opened.")
            print("  Once signed in, re-run this script.")
            return False

    return logged_in


# ---------------------------------------------------------------------------
# Profile update flows
# ---------------------------------------------------------------------------

def update_biography(page: Any) -> None:
    """Update the biography/personal info section."""
    print("\nUpdating biography...")
    page.goto(PROFILE_URLS["biography"], wait_until="domcontentloaded", timeout=30000)
    _wait(2, 4)
    _mouse_wander(page)
    _screenshot(page, "biography_before")

    # Look for and fill common biography fields
    field_map = {
        "first_name": PROFILE["first_name"],
        "last_name": PROFILE["last_name"],
        "phone": PROFILE["phone"],
        "linkedin": PROFILE["linkedin"],
    }

    for field_key, value in field_map.items():
        for selector in [
            f"input[name*='{field_key}']",
            f"input[id*='{field_key}']",
            f"input[placeholder*='{field_key.replace('_', ' ')}']",
        ]:
            try:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible():
                    loc.clear()
                    _type_human(loc, value)
                    _wait(0.5, 1.0)
                    break
            except Exception:
                continue

    # Look for a summary/bio textarea
    for selector in [
        "textarea[name*='summary']",
        "textarea[name*='bio']",
        "textarea[name*='about']",
        "textarea[name*='description']",
        "textarea[placeholder*='about']",
    ]:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0 and loc.is_visible():
                loc.clear()
                _type_human(loc, EXPERIENCE_SUMMARY)
                break
        except Exception:
            continue

    # Try to upload resume if there's a file input
    resume_candidates = list(
        (ROOT / "resumes").glob("Igor_Ganapolsky*.pdf")
    ) + list((ROOT / "resumes").glob("Igor_Ganapolsky*.docx"))
    if resume_candidates:
        resume_path = sorted(resume_candidates, key=lambda p: p.stat().st_mtime)[-1]
        try:
            file_input = page.locator("input[type='file']").first
            if file_input.count() > 0:
                file_input.set_input_files(str(resume_path))
                print(f"  Uploaded resume: {resume_path.name}")
                _wait(3, 5)
        except Exception as e:
            print(f"  Resume upload skipped: {e}")

    _try_save(page)
    _screenshot(page, "biography_after")


def update_languages(page: Any) -> None:
    """Update the languages section."""
    print("\nUpdating languages...")
    page.goto(PROFILE_URLS["languages"], wait_until="domcontentloaded", timeout=30000)
    _wait(2, 4)
    _screenshot(page, "languages_before")

    for lang in LANGUAGES:
        # Try to add a language via an "Add" button
        for selector in [
            "button:has-text('Add')",
            "button:has-text('Add Language')",
            "a:has-text('Add')",
        ]:
            try:
                btn = page.locator(selector).first
                if btn.count() > 0 and btn.is_visible():
                    _click_human(btn)
                    _wait(1, 2)
                    break
            except Exception:
                continue

        # Fill language name
        for selector in [
            "input[name*='language']",
            "input[placeholder*='language']",
            "input[placeholder*='Language']",
            "select[name*='language']",
        ]:
            try:
                loc = page.locator(selector).last
                if loc.count() > 0 and loc.is_visible():
                    tag = loc.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "select":
                        loc.select_option(label=lang["name"])
                    else:
                        loc.clear()
                        _type_human(loc, lang["name"])
                        _wait(0.5, 1.0)
                        # Select from autocomplete dropdown if present
                        try:
                            dropdown = page.locator(
                                f"li:has-text('{lang['name']}')"
                            ).first
                            if dropdown.count() > 0:
                                _click_human(dropdown)
                        except Exception:
                            pass
                    break
            except Exception:
                continue

        # Set proficiency level
        for selector in [
            "select[name*='level']",
            "select[name*='proficiency']",
        ]:
            try:
                loc = page.locator(selector).last
                if loc.count() > 0 and loc.is_visible():
                    loc.select_option(label=lang["level"])
                    break
            except Exception:
                continue

    _try_save(page)
    _screenshot(page, "languages_after")


def update_skills(page: Any) -> None:
    """Update the skills section."""
    print("\nUpdating skills...")
    page.goto(PROFILE_URLS["skills"], wait_until="domcontentloaded", timeout=30000)
    _wait(2, 4)
    _mouse_wander(page)
    _screenshot(page, "skills_before")

    # IT Skills
    _add_skills_to_section(page, SKILLS_IT, section_hint="it")
    _wait(1, 2)

    # Personal Skills
    _add_skills_to_section(page, SKILLS_PERSONAL, section_hint="personal")

    _try_save(page)
    _screenshot(page, "skills_after")


def _add_skills_to_section(page: Any, skills: list, section_hint: str = "") -> None:
    """Add skills to a section via input + autocomplete or tag-input pattern."""
    for skill in skills:
        # Look for a skill input field
        for selector in [
            f"input[name*='{section_hint}']" if section_hint else None,
            "input[placeholder*='skill']",
            "input[placeholder*='Skill']",
            "input[placeholder*='Add']",
            "input[placeholder*='Search']",
            "input[placeholder*='Type']",
        ]:
            if selector is None:
                continue
            try:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible():
                    loc.clear()
                    _type_human(loc, skill)
                    _wait(0.8, 1.5)
                    # Select from autocomplete
                    try:
                        option = page.locator(
                            f"li:has-text('{skill}'), div[role='option']:has-text('{skill}')"
                        ).first
                        if option.count() > 0:
                            _click_human(option)
                        else:
                            page.keyboard.press("Enter")
                    except Exception:
                        page.keyboard.press("Enter")
                    _wait(0.5, 1.0)
                    break
            except Exception:
                continue


def update_experience(page: Any) -> None:
    """Update the experience section."""
    print("\nUpdating experience...")
    page.goto(PROFILE_URLS["experience"], wait_until="domcontentloaded", timeout=30000)
    _wait(2, 4)
    _mouse_wander(page)
    _screenshot(page, "experience_before")

    # Look for years of experience field
    for selector in [
        "input[name*='years']",
        "input[name*='experience']",
        "select[name*='years']",
    ]:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0 and loc.is_visible():
                tag = loc.evaluate("el => el.tagName.toLowerCase()")
                if tag == "select":
                    loc.select_option(label="10+")
                else:
                    loc.clear()
                    _type_human(loc, "12")
                break
        except Exception:
            continue

    # Occupation / Job title
    for selector in [
        "input[name*='occupation']",
        "input[name*='title']",
        "input[placeholder*='occupation']",
        "input[placeholder*='Job Title']",
    ]:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0 and loc.is_visible():
                loc.clear()
                _type_human(loc, "Senior Software Engineer")
                _wait(0.8, 1.5)
                try:
                    option = page.locator("li:has-text('Software Engineer')").first
                    if option.count() > 0:
                        _click_human(option)
                except Exception:
                    page.keyboard.press("Enter")
                break
        except Exception:
            continue

    _try_save(page)
    _screenshot(page, "experience_after")


def _try_save(page: Any) -> None:
    """Try to find and click a save/submit button."""
    _wait(1, 2)
    for selector in [
        "button:has-text('Save')",
        "button:has-text('Update')",
        "button:has-text('Submit')",
        "button:has-text('Next')",
        "button[type='submit']",
        "input[type='submit']",
    ]:
        try:
            btn = page.locator(selector).first
            if btn.count() > 0 and btn.is_visible():
                _click_human(btn)
                _wait(2, 4)
                print("  Saved.")
                return
        except Exception:
            continue
    print("  No save button found (page may auto-save).")


# ---------------------------------------------------------------------------
# Dashboard & job matching
# ---------------------------------------------------------------------------

def check_dashboard(page: Any) -> None:
    """Check dashboard for matches, recruiter contacts, and notifications."""
    print("\nChecking dashboard...")
    page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30000)
    _wait(3, 5)
    _mouse_wander(page)
    _screenshot(page, "dashboard")

    # Look for notification badges or recruiter messages
    for selector in [
        "[class*='notification']",
        "[class*='badge']",
        "[class*='message']",
        "[class*='chat']",
        "button:has-text('Messages')",
        "a:has-text('Messages')",
    ]:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0 and loc.is_visible():
                text = loc.text_content() or ""
                if text.strip():
                    print(f"  Notification found: {text.strip()[:100]}")
        except Exception:
            continue

    # Check profile completeness / match stats
    for selector in [
        "[class*='progress']",
        "[class*='score']",
        "[class*='match']",
        "[class*='stat']",
    ]:
        try:
            elements = page.locator(selector).all()
            for el in elements[:5]:
                text = el.text_content() or ""
                if text.strip():
                    print(f"  Stat: {text.strip()[:100]}")
        except Exception:
            continue


def browse_jobs(page: Any) -> None:
    """Browse available jobs / matches on the platform."""
    print("\nBrowsing job matches...")

    # Navigate to jobs section
    for url_suffix in ["/talent/jobs", "/talent/matches", "/talent/opportunities"]:
        try:
            page.goto(BASE_URL + url_suffix, wait_until="domcontentloaded", timeout=15000)
            _wait(2, 3)
            if page.url and "404" not in page.url and "error" not in page.url.lower():
                _screenshot(page, "jobs_page")
                break
        except Exception:
            continue

    # Look for job cards and try to apply/express interest
    job_cards = []
    for selector in [
        "[class*='job-card']",
        "[class*='JobCard']",
        "[class*='opportunity']",
        "[class*='match-card']",
        "div[class*='card']:has(h3)",
        "article",
    ]:
        try:
            cards = page.locator(selector).all()
            if cards:
                job_cards = cards
                break
        except Exception:
            continue

    if not job_cards:
        print("  No job cards found on current page.")
        return

    applied_count = 0
    for i, card in enumerate(job_cards[:10]):  # Process up to 10 jobs
        try:
            title_text = card.text_content() or ""
            title_preview = " ".join(title_text.split()[:10])
            print(f"  Job {i + 1}: {title_preview}...")

            # Try to click "Apply" / "Express Interest" / "I'm interested"
            for btn_text in [
                "Apply",
                "Express Interest",
                "Interested",
                "I'm interested",
                "Connect",
            ]:
                try:
                    btn = card.locator(f"button:has-text('{btn_text}')").first
                    if btn.count() > 0 and btn.is_visible():
                        _click_human(btn)
                        _wait(1, 3)
                        applied_count += 1
                        _screenshot(page, f"job_applied_{i + 1}")
                        print(f"    Applied/Expressed interest!")
                        break
                except Exception:
                    continue
        except Exception:
            continue

    print(f"  Processed {len(job_cards[:10])} jobs, applied to {applied_count}.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile-only", action="store_true", help="Only update profile")
    ap.add_argument("--dashboard-only", action="store_true", help="Only check dashboard")
    ap.add_argument(
        "--headless", action="store_true",
        help="Run headless (NOT recommended for Google SSO — use visible mode)",
    )
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: Install playwright: pip install playwright && python -m playwright install chromium")
        return 1

    # Use visible browser for Google SSO.
    # Google SSO opens a popup window — we need to handle it.
    # We use a *separate* persistent profile (not Chrome's locked Default)
    # so Playwright doesn't conflict with a running Chrome.
    visible = not args.headless
    print(f"Launching browser (visible={visible})...")

    with sync_playwright() as pw:
        # Use a dedicated Playwright profile that persists Google session cookies
        profile_dir = ROOT / ".talentprise_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)

        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=not visible,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page() if not context.pages else context.pages[0]

        try:
            # Step 0: Ensure Google session exists in this profile
            # On first run, user needs to sign into Google interactively.
            session_marker = profile_dir / ".google_session_ok"
            if not session_marker.exists():
                print("\nFirst run — establishing Google session in Playwright profile.")
                print("Navigating to Google sign-in...")
                page.goto(
                    "https://accounts.google.com/signin",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                _wait(2, 3)
                _screenshot(page, "google_signin_initial")

                # Check if already signed in
                if "myaccount.google.com" in page.url or "SignOutOptions" in (page.content() or ""):
                    print("  Google session already active!")
                    session_marker.write_text("ok")
                else:
                    print("\n  A browser window has opened to Google sign-in.")
                    print(f"  Please sign in with: {PROFILE['email']}")
                    print("  Press ENTER here once you've completed sign-in...")
                    input()
                    _screenshot(page, "google_signin_complete")
                    session_marker.write_text("ok")
                    print("  Google session saved to Playwright profile.")

            # Step 1: Login to Talentprise
            if not login_google_sso(page):
                print("\nLogin failed. You may need to:")
                print("  1. Delete .talentprise_profile/ and re-run")
                print("  2. Sign into Google when prompted")
                _screenshot(page, "login_failed")
                return 1

            if args.dashboard_only:
                check_dashboard(page)
                return 0

            # Step 2: Update profile sections
            update_biography(page)
            update_languages(page)
            update_skills(page)
            update_experience(page)

            if args.profile_only:
                print("\nProfile update complete!")
                return 0

            # Step 3: Check dashboard and browse jobs
            check_dashboard(page)
            browse_jobs(page)

            print(f"\nDone! Evidence screenshots saved to: {EVIDENCE_DIR.relative_to(ROOT)}/")
            return 0
        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
