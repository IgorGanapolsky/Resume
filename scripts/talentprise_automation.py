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

from candidate_data import load_candidate_profile

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
# Profile data — sourced from candidate_profile.json
# ---------------------------------------------------------------------------

PROFILE = load_candidate_profile()
PROFILE["current_title"] = "Senior Software Engineer"

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

# Skills to remove if present (low-relevance for target roles)
SKILLS_IT_REMOVE = [
    "Sophos",
    "JSON-RPC",
    "Chromium",
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

def _click_sidebar(page: Any, label: str) -> bool:
    """Click a sidebar navigation link by its visible text."""
    for selector in [
        f"a:has-text('{label}')",
        f"div:has-text('{label}')",
        f"span:has-text('{label}')",
        f"li:has-text('{label}')",
    ]:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0 and loc.is_visible():
                _click_human(loc)
                _wait(2, 4)
                return True
        except Exception:
            continue
    print(f"  Could not find sidebar link: {label}")
    return False


def _dismiss_cookies(page: Any) -> None:
    """Dismiss cookie consent banner if present."""
    for selector in [
        "button:has-text('Accept All')",
        "button:has-text('Accept')",
        "[data-cky-tag='accept-button']",
    ]:
        try:
            btn = page.locator(selector).first
            if btn.count() > 0 and btn.is_visible(timeout=1000):
                _click_human(btn)
                _wait(0.5, 1.0)
                return
        except Exception:
            continue


def _navigate_to_profile(page: Any) -> None:
    """Navigate to the profile/biography base page."""
    page.goto(PROFILE_URLS["biography"], wait_until="domcontentloaded", timeout=30000)
    _wait(2, 4)
    _dismiss_cookies(page)


def update_biography(page: Any) -> None:
    """Update the biography section — upload resume and fill basic fields."""
    print("\nUpdating biography (Education section)...")
    _navigate_to_profile(page)
    _screenshot(page, "biography_before")

    # The biography page shows Education by default.
    # Try to upload resume if there's a file input on the page
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

    _screenshot(page, "biography_after")


def update_languages(page: Any) -> None:
    """Update the languages section via sidebar navigation."""
    print("\nUpdating languages...")
    _navigate_to_profile(page)
    _click_sidebar(page, "Languages")
    _screenshot(page, "languages_before")

    # Check which languages already exist
    page_text = page.locator("body").text_content() or ""

    for lang in LANGUAGES:
        # Skip if language already exists on the page
        if lang["name"] in page_text:
            print(f"  {lang['name']} already listed — skipping.")
            continue

        # Click "ADD LANGUAGES" button to open the add form
        add_btn = None
        for selector in [
            "button:has-text('ADD LANGUAGES')",
            "button:has-text('Add Languages')",
            "a:has-text('ADD LANGUAGES')",
        ]:
            try:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible():
                    add_btn = loc
                    break
            except Exception:
                continue

        if add_btn is None:
            print(f"  Could not find 'ADD LANGUAGES' button.")
            continue

        _click_human(add_btn)
        _wait(2, 3)

        # Now the Language* and Level form fields should appear
        # Find ALL visible inputs on the page
        try:
            inputs = page.locator("input:visible").all()
            if len(inputs) >= 1:
                # First input = Language
                inputs[0].click()
                _wait(0.3, 0.5)
                inputs[0].fill("")
                _type_human(inputs[0], lang["name"])
                _wait(1, 2)

                # Try to select from autocomplete dropdown
                for sel in [
                    f"li:has-text('{lang['name']}')",
                    f"div[role='option']:has-text('{lang['name']}')",
                    f"[class*='option']:has-text('{lang['name']}')",
                ]:
                    try:
                        opt = page.locator(sel).first
                        if opt.count() > 0 and opt.is_visible():
                            _click_human(opt)
                            print(f"  Selected language: {lang['name']}")
                            break
                    except Exception:
                        continue
                else:
                    page.keyboard.press("Enter")

            if len(inputs) >= 2:
                # Second input = Level
                _wait(0.5, 1.0)
                inputs[1].click()
                _wait(0.3, 0.5)
                inputs[1].fill("")
                _type_human(inputs[1], lang["level"])
                _wait(1, 2)

                for sel in [
                    f"li:has-text('{lang['level']}')",
                    f"div[role='option']:has-text('{lang['level']}')",
                ]:
                    try:
                        opt = page.locator(sel).first
                        if opt.count() > 0 and opt.is_visible():
                            _click_human(opt)
                            break
                    except Exception:
                        continue
                else:
                    page.keyboard.press("Enter")
                print(f"  Set level: {lang['level']}")

        except Exception as e:
            print(f"  Language form filling failed: {e}")

        # Click SAVE
        _try_save(page)
        _wait(2, 3)

    _screenshot(page, "languages_after")


def _delete_skill_by_name(page: Any, skill_name: str) -> bool:
    """Delete a skill by finding its row and clicking the trash icon."""
    # Each skill row has text like "Advanced in Python with 5 years..."
    # and a trash icon next to it. Find the row containing the skill name.
    try:
        # Look for delete buttons (trash icons) near the skill text
        # Strategy: find the text, then find the closest delete button
        rows = page.locator("div:has-text('" + skill_name + "')").all()
        for row in rows:
            try:
                # Look for delete/trash icon within or near this row
                trash = row.locator("button, a, [class*='delete'], [class*='trash'], svg").first
                if trash.count() > 0 and trash.is_visible():
                    _click_human(trash)
                    _wait(1, 2)
                    # Confirm deletion if there's a confirmation dialog
                    for confirm_sel in [
                        "button:has-text('Yes')",
                        "button:has-text('Delete')",
                        "button:has-text('Confirm')",
                        "button:has-text('OK')",
                    ]:
                        try:
                            confirm = page.locator(confirm_sel).first
                            if confirm.count() > 0 and confirm.is_visible():
                                _click_human(confirm)
                                _wait(1, 2)
                                break
                        except Exception:
                            continue
                    print(f"  Deleted: {skill_name}")
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def update_skills(page: Any) -> None:
    """Update skills via sidebar — remove low-value, add high-value."""
    # IT Skills
    print("\nOptimizing IT Skills...")
    _navigate_to_profile(page)
    _click_sidebar(page, "IT Skills")
    _screenshot(page, "it_skills_before")

    page_text = page.locator("body").text_content() or ""

    # Step 1: Delete low-value skills to make room
    deleted_any = False
    for skill in SKILLS_IT_REMOVE:
        if skill in page_text:
            print(f"  Removing low-value skill: {skill}")
            _delete_skill_by_name(page, skill)
            deleted_any = True
            _wait(1, 2)

    # Re-navigate to IT Skills after deletions (deletion may redirect to profile view)
    if deleted_any:
        _navigate_to_profile(page)
        _click_sidebar(page, "IT Skills")
        _wait(2, 3)

    page_text = page.locator("body").text_content() or ""

    # Step 2: Add missing high-value skills
    missing_it = [s for s in SKILLS_IT if s not in page_text]
    if missing_it:
        print(f"  Adding IT skills: {', '.join(missing_it)}")
        _add_skills_via_button(page, missing_it, "ADD IT SKILLS")
    else:
        print("  All target IT skills already present.")
    _screenshot(page, "it_skills_after")

    # Personal Skills — uses tag chips with "SAVE CHANGES" button
    print("\nChecking Personal Skills...")
    _click_sidebar(page, "Personal Skills")
    _wait(2, 3)
    _screenshot(page, "personal_skills_before")

    page_text = page.locator("body").text_content() or ""
    # Personal skills already has 10+ — note "Only first 10 will be displayed"
    # Just report status, don't try to modify since it's already at capacity
    print(f"  Personal skills section loaded. Current skills visible on page.")
    _screenshot(page, "personal_skills_after")


def _add_skills_via_button(page: Any, skills: list, button_text: str) -> None:
    """Add skills by clicking the green ADD button, filling the form, and saving."""
    for skill in skills:
        # Click the ADD button
        add_clicked = False
        for selector in [
            f"button:has-text('{button_text}')",
            f"a:has-text('{button_text}')",
        ]:
            try:
                btn = page.locator(selector).first
                if btn.count() > 0 and btn.is_visible():
                    _click_human(btn)
                    add_clicked = True
                    _wait(2, 3)
                    break
            except Exception:
                continue

        if not add_clicked:
            print(f"  Could not find '{button_text}' button.")
            break

        # Fill the skill name in the form that appears
        try:
            inputs = page.locator("input:visible").all()
            if inputs:
                inputs[0].click()
                inputs[0].fill("")
                _type_human(inputs[0], skill)
                _wait(1, 2)

                # Select from autocomplete
                for sel in [
                    f"li:has-text('{skill}')",
                    f"div[role='option']:has-text('{skill}')",
                ]:
                    try:
                        opt = page.locator(sel).first
                        if opt.count() > 0 and opt.is_visible():
                            _click_human(opt)
                            print(f"  Added: {skill}")
                            break
                    except Exception:
                        continue
                else:
                    page.keyboard.press("Enter")
                    print(f"  Typed: {skill}")
        except Exception as e:
            print(f"  Skill form failed: {e}")

        _try_save(page)
        _wait(1, 2)


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
    """Update the experience section via sidebar."""
    print("\nUpdating experience...")
    _navigate_to_profile(page)
    _click_sidebar(page, "Experience")
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
    """Check dashboard via 'My Page' link and review notifications."""
    print("\nChecking dashboard...")

    # Click "My Page" link in the top nav (this is the dashboard equivalent)
    my_page_clicked = False
    for selector in [
        "a:has-text('My Page')",
        "span:has-text('My Page')",
        "div:has-text('My Page')",
    ]:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0 and loc.is_visible():
                _click_human(loc)
                my_page_clicked = True
                _wait(3, 5)
                break
        except Exception:
            continue

    if not my_page_clicked:
        # Fallback to navigating directly
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        _wait(3, 5)

    _mouse_wander(page)
    _screenshot(page, "dashboard")

    # Check notification bell (42 notifications seen in screenshot)
    try:
        bell = page.locator("[class*='notification'], [class*='bell'], [aria-label*='notification']").first
        if bell.count() > 0 and bell.is_visible():
            _click_human(bell)
            _wait(2, 3)
            _screenshot(page, "notifications")
            # Read notification text
            try:
                notif_text = page.locator("[class*='notification-list'], [class*='dropdown']").first.text_content()
                if notif_text:
                    print(f"  Notifications: {notif_text.strip()[:200]}")
            except Exception:
                pass
            # Close notification panel by clicking elsewhere
            page.mouse.click(100, 100)
            _wait(1, 2)
    except Exception:
        pass

    # Check for messages (chat icon)
    try:
        chat = page.locator("[class*='message'], [class*='chat'], [aria-label*='message']").first
        if chat.count() > 0 and chat.is_visible():
            _click_human(chat)
            _wait(2, 3)
            _screenshot(page, "messages")
            page.mouse.click(100, 100)
            _wait(1, 2)
    except Exception:
        pass

    # Read any visible stats/metrics on the page
    page_text = ""
    try:
        page_text = page.locator("body").text_content() or ""
    except Exception:
        pass
    for keyword in ["match", "score", "profile", "view", "click"]:
        for line in page_text.split("\n"):
            if keyword.lower() in line.lower() and line.strip():
                print(f"  {line.strip()[:100]}")
                break


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

def _export_storage_state(context: Any) -> Optional[str]:
    """Export Playwright storage state as JSON for CI secret storage."""
    import json as _json
    try:
        state = context.storage_state()
        return _json.dumps(state, ensure_ascii=True)
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile-only", action="store_true", help="Only update profile")
    ap.add_argument("--dashboard-only", action="store_true", help="Only check dashboard")
    ap.add_argument(
        "--headless", action="store_true",
        help="Run headless (uses saved storage state, no SSO popup)",
    )
    ap.add_argument(
        "--capture-auth", action="store_true",
        help="Login interactively, export storage state for CI, then exit",
    )
    ap.add_argument(
        "--storage-state-env", default="TALENTPRISE_AUTH_JSON",
        help="Env var name containing Playwright storage state JSON (for CI)",
    )
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: Install playwright: pip install playwright && python -m playwright install chromium")
        return 1

    visible = not args.headless
    print(f"Launching browser (visible={visible})...")

    with sync_playwright() as pw:
        import json as _json
        import os as _os

        # CI mode: use storage state from environment variable (no persistent profile needed)
        storage_state_json = _os.getenv(args.storage_state_env, "").strip()
        use_storage_state = bool(storage_state_json) and not args.capture_auth

        if use_storage_state:
            # CI mode — launch with saved storage state, headless
            print("  CI mode: using stored auth from environment.")
            try:
                state = _json.loads(storage_state_json)
            except Exception:
                print("  ERROR: Invalid storage state JSON in env var.")
                return 1
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                storage_state=state,
                viewport={"width": 1440, "height": 900},
            )
            page = context.new_page()
        else:
            # Local mode — use persistent profile for Google SSO
            profile_dir = ROOT / ".talentprise_profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            browser = None  # persistent context IS the browser
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
            if not use_storage_state:
                # Ensure Google session exists in persistent profile
                session_marker = (ROOT / ".talentprise_profile" / ".google_session_ok")
                if not session_marker.exists():
                    print("\nFirst run — establishing Google session in Playwright profile.")
                    page.goto(
                        "https://accounts.google.com/signin",
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                    _wait(2, 3)
                    _screenshot(page, "google_signin_initial")
                    if "myaccount.google.com" in page.url or "SignOutOptions" in (page.content() or ""):
                        print("  Google session already active!")
                        session_marker.write_text("ok")
                    else:
                        print(f"\n  Please sign in with: {PROFILE['email']}")
                        print("  Press ENTER here once you've completed sign-in...")
                        input()
                        _screenshot(page, "google_signin_complete")
                        session_marker.write_text("ok")

            # Login to Talentprise
            if not login_google_sso(page):
                print("\nLogin failed.")
                _screenshot(page, "login_failed")
                return 1

            # --capture-auth: export storage state and exit
            if args.capture_auth:
                state_json = _export_storage_state(context)
                if state_json:
                    auth_path = ROOT / ".talentprise_auth.json"
                    auth_path.write_text(state_json, encoding="utf-8")
                    print(f"\nAuth state exported to: {auth_path}")
                    print("To set as CI secret:")
                    print(f'  gh secret set TALENTPRISE_AUTH_JSON < {auth_path}')
                return 0

            if args.dashboard_only:
                check_dashboard(page)
                return 0

            # Update profile sections
            update_biography(page)
            update_languages(page)
            update_skills(page)
            update_experience(page)

            if args.profile_only:
                print("\nProfile update complete!")
                return 0

            # Check dashboard and browse jobs
            check_dashboard(page)
            browse_jobs(page)

            print(f"\nDone! Evidence screenshots saved to: {EVIDENCE_DIR.relative_to(ROOT)}/")
            return 0
        finally:
            context.close()
            if browser:
                browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
