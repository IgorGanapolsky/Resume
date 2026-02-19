#!/usr/bin/env python3
"""
Anthropic Job Application Automation v3 - Complete field-by-field fill
"""

import asyncio
from playwright.async_api import async_playwright

RESUME_DOCX = "/Users/ganapolsky_i/workspace/git/igor/Resume/applications/anthropic/tailored_resumes/2026-02-17_anthropic_autonomous-agent-infrastructure_resume.docx"
COVER_LETTER_TXT = "/Users/ganapolsky_i/workspace/git/igor/Resume/applications/anthropic/cover_letters/2026-02-17_anthropic_autonomous-agent-infrastructure.txt"
SCREENSHOT_DIR = (
    "/Users/ganapolsky_i/workspace/git/igor/Resume/applications/anthropic/submissions"
)
FINAL_SCREENSHOT = f"{SCREENSHOT_DIR}/2026-02-18_anthropic_submission.png"

with open(COVER_LETTER_TXT, "r") as f:
    COVER_LETTER_TEXT = f.read()

WHY_ANTHROPIC = (
    "I've been building production AI agent infrastructure at Subway — Thompson Sampling RLHF systems, "
    "LanceDB hybrid search, 13 autonomous Claude-powered agents. Anthropic is where that work leads. "
    "The Autonomous Agent Infrastructure role is a direct extension of what I do: designing the systems "
    "that make agents reliable, observable, and safe at scale. I want to work on the hard problems at the frontier."
)


async def screenshot(page, name):
    path = f"{SCREENSHOT_DIR}/2026-02-18_{name}.png"
    await page.screenshot(path=path, full_page=True)
    print(f"  [ss] {name}")
    return path


async def select_option_in_greenhouse_dropdown(page, field_id, option_text_or_contains):
    """Click the Greenhouse custom React dropdown and select an option."""
    # Greenhouse uses a hidden text input + visible custom select
    # The actual clickable control is the sibling div to the input

    # Find the input by its ID
    inp = page.locator(f"#{field_id}")
    if await inp.count() == 0:
        print(f"  WARNING: field #{field_id} not found")
        return False

    # The control element is the parent's child with select__control class
    parent = inp.locator("xpath=ancestor::div[contains(@class,'field')]").last
    control = parent.locator("[class*='select__control']").first

    if await control.count() == 0:
        # Try finding the control near the input
        control = (
            page.locator(f"#{field_id}").locator("xpath=following-sibling::*[1]").first
        )

    if await control.count() == 0:
        print(f"  WARNING: No dropdown control found for #{field_id}")
        return False

    # Scroll to the control
    await control.scroll_into_view_if_needed()
    await page.wait_for_timeout(300)
    await control.click()
    await page.wait_for_timeout(700)

    # Find the option in the open menu
    option_loc = page.locator("[class*='select__option']").filter(
        has_text=option_text_or_contains
    )
    opt_count = await option_loc.count()

    if opt_count == 0:
        # Try exact match via role=option
        option_loc = page.locator("[role='option']").filter(
            has_text=option_text_or_contains
        )
        opt_count = await option_loc.count()

    if opt_count > 0:
        await option_loc.first.click()
        print(f"  Selected '{option_text_or_contains}' for #{field_id}")
        await page.wait_for_timeout(300)
        return True
    else:
        # Print available options for debugging
        all_opts = await page.locator("[class*='select__option']").all_text_contents()
        print(
            f"  WARNING: Option '{option_text_or_contains}' not found for #{field_id}. Available: {all_opts}"
        )
        await page.keyboard.press("Escape")
        return False


async def check_errors(page):
    """Check and report validation errors."""
    errors = await page.evaluate("""() => {
        const errEls = document.querySelectorAll('.error, .field-error, [class*="error"], [aria-invalid="true"]');
        return Array.from(errEls).map(e => ({
            text: e.textContent.trim().substring(0, 100),
            id: e.id || '',
            class: e.className || ''
        })).filter(e => e.text.length > 0);
    }""")
    if errors:
        print(f"\n  VALIDATION ERRORS ({len(errors)}):")
        for e in errors:
            print(f"    - {e['text']} (id={e['id']})")
    return errors


async def fill_application():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=200)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        # ---- NAVIGATE ----
        print("Navigating to job page...")
        await page.goto(
            "https://job-boards.greenhouse.io/anthropic/jobs/5065894008",
            wait_until="networkidle",
        )
        await screenshot(page, "10_job_page")

        # ---- CLICK APPLY ----
        apply_btn = page.locator(
            "button:has-text('Apply'), a:has-text('Apply for this Job'), a:has-text('Apply')"
        )
        if await apply_btn.count() > 0:
            await apply_btn.first.click()
            await page.wait_for_load_state("networkidle")
            print("Clicked Apply")
        await page.wait_for_timeout(2000)
        await screenshot(page, "11_form_loaded")

        # ---- BASIC FIELDS ----
        print("\nFilling basic personal info...")
        await page.locator("#first_name").fill("Igor")
        await page.locator("#last_name").fill("Ganapolsky")
        await page.locator("#email").fill("iganapolsky@gmail.com")

        # Phone - Greenhouse intl tel input
        phone = page.locator("#phone")
        await phone.click()
        await phone.fill("")
        await phone.type("2016391534")
        print("  Filled phone")

        # ---- RESUME UPLOAD ----
        print("Uploading resume...")
        resume_input = page.locator("input[type='file']#resume")
        if await resume_input.count() == 0:
            resume_input = page.locator("input[type='file']").first
        await resume_input.set_input_files(RESUME_DOCX)
        await page.wait_for_timeout(3000)  # wait for upload to complete
        print("  Uploaded resume")

        await screenshot(page, "12_basic_info")

        # ---- GREENHOUSE CUSTOM QUESTION FIELDS ----
        # These are all type=text inputs that feed into custom React dropdowns
        # Field IDs discovered from inspection:
        # question_14439952008 = (Optional) Personal Preferences
        # question_14439953008 = Website
        # question_14439954008 = Are you open to working in-person 25%
        # question_14439955008 = When is earliest you'd want to start
        # question_14439956008 = Do you have deadlines/timeline
        # question_14439957008 = AI Policy for Application (REQUIRED)
        # question_14439958008 = Why Anthropic (TEXTAREA, REQUIRED)
        # question_14439959008 = Do you require visa sponsorship (REQUIRED)
        # question_14439960008 = Will you in future require visa sponsorship (REQUIRED)
        # question_14439961008 = Additional Information (TEXTAREA)
        # question_14439962008 = LinkedIn Profile
        # question_14439963008 = Are you open to relocation (REQUIRED)
        # question_14439964008 = Working address
        # question_14439965008 = Have you ever interviewed at Anthropic (REQUIRED)

        print("\nFilling custom question fields...")

        # Website/GitHub
        await page.locator("#question_14439953008").fill(
            "https://github.com/IgorGanapolsky"
        )
        print("  Filled Website/GitHub")

        # LinkedIn Profile
        await page.locator("#question_14439962008").fill(
            "https://www.linkedin.com/in/igor-ganapolsky-859317343/"
        )
        print("  Filled LinkedIn")

        # Working address
        await page.locator("#question_14439964008").fill(
            "11909 Glenmore Dr, Coral Springs, FL 33071"
        )
        print("  Filled working address")

        # When earliest to start
        await page.locator("#question_14439955008").fill("Immediately / 2 weeks notice")
        print("  Filled start date")

        # Timeline considerations
        await page.locator("#question_14439956008").fill("No specific deadline")
        print("  Filled timeline")

        # Why Anthropic (TEXTAREA - required)
        why_textarea = page.locator("#question_14439958008")
        await why_textarea.fill(WHY_ANTHROPIC)
        print("  Filled 'Why Anthropic?' textarea")

        # Additional Information textarea
        additional_textarea = page.locator("#question_14439961008")
        await additional_textarea.fill(
            "Cover letter available upon request. "
            "GitHub: https://github.com/IgorGanapolsky — includes open-source trading system with LLM gateway."
        )
        print("  Filled Additional Information")

        await screenshot(page, "13_text_fields_filled")
        await page.evaluate("window.scrollBy(0, 300)")
        await page.wait_for_timeout(500)

        # ---- DROPDOWN FIELDS ----
        print("\nHandling dropdown fields...")

        # "Are you open to working in-person 25%?" - REQUIRED
        await select_option_in_greenhouse_dropdown(page, "question_14439954008", "Yes")

        # "AI Policy for Application" - REQUIRED
        # This is likely a Yes/No or agreement field
        ai_policy_options = []
        ai_ctrl = (
            page.locator("#question_14439957008")
            .locator("xpath=ancestor::div[contains(@class,'field')]")
            .last
        )
        ai_control_btn = ai_ctrl.locator("[class*='select__control']").first
        if await ai_control_btn.count() > 0:
            await ai_control_btn.scroll_into_view_if_needed()
            await ai_control_btn.click()
            await page.wait_for_timeout(500)
            ai_policy_options = await page.locator(
                "[class*='select__option']"
            ).all_text_contents()
            print(f"  AI Policy options: {ai_policy_options}")
            if ai_policy_options:
                # Select the first meaningful option (usually "I confirm" or "Yes")
                first_opt = page.locator("[class*='select__option']").first
                await first_opt.click()
                print(f"  Selected first AI Policy option: {ai_policy_options[0][:50]}")
            else:
                await page.keyboard.press("Escape")

        # "Do you require visa sponsorship?" - REQUIRED -> No
        await select_option_in_greenhouse_dropdown(page, "question_14439959008", "No")

        # "Will you in future require visa sponsorship?" - REQUIRED -> No
        await select_option_in_greenhouse_dropdown(page, "question_14439960008", "No")

        # "Are you open to relocation?" - REQUIRED -> No
        await select_option_in_greenhouse_dropdown(page, "question_14439963008", "No")

        # "Have you ever interviewed at Anthropic?" - REQUIRED -> No
        await select_option_in_greenhouse_dropdown(page, "question_14439965008", "No")

        await screenshot(page, "14_dropdowns_filled")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)
        await screenshot(page, "15_bottom_of_form")

        # ---- CHECK CURRENT STATE OF REQUIRED FIELDS ----
        print("\nVerifying all required fields filled...")

        # Check what values the custom inputs currently have
        field_values = await page.evaluate("""() => {
            const inputs = document.querySelectorAll('input[type=text], textarea');
            return Array.from(inputs).map(el => ({
                id: el.id,
                value: el.value,
                label: (el.labels && el.labels[0]) ? el.labels[0].textContent.trim() : ''
            })).filter(x => x.id);
        }""")

        for fv in field_values:
            required = "*" in fv["label"]
            status = (
                "OK"
                if fv["value"]
                else ("REQUIRED but EMPTY" if required else "optional/empty")
            )
            print(
                f"  {fv['id']}: {status} | label='{fv['label'][:60]}' | value='{str(fv['value'])[:50]}'"
            )

        # ---- SUBMIT ----
        print("\nAttempting to submit...")
        submit_btn = page.locator("input[type='submit'], button[type='submit']").filter(
            has_text=lambda t: True  # get all
        )
        # More targeted
        submit_btn = page.locator(
            "input[type='submit'][value*='Submit'], button:has-text('Submit application')"
        )

        sub_count = await submit_btn.count()
        print(f"  Submit button count: {sub_count}")

        if sub_count > 0:
            await submit_btn.first.scroll_into_view_if_needed()
            await screenshot(page, "16_pre_submit")
            await submit_btn.first.click()
            print("  Clicked Submit...")
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(3000)
        else:
            # fallback: find any submit via JS
            btns = await page.evaluate("""() => {
                const b = document.querySelectorAll('button[type=submit], input[type=submit]');
                return Array.from(b).map(x => x.value || x.textContent);
            }""")
            print(f"  JS-found buttons: {btns}")

        await screenshot(page, "17_post_submit")

        # ---- CHECK FOR ERRORS OR SUCCESS ----
        current_url = page.url
        print(f"\n  Post-submit URL: {current_url}")

        await check_errors(page)

        body_text = await page.inner_text("body")
        success_kws = [
            "thank you for applying",
            "application has been received",
            "application submitted",
            "confirmation",
        ]
        error_kws = [
            "can't be blank",
            "is required",
            "please fill",
            "This field is required",
        ]

        success = any(kw.lower() in body_text.lower() for kw in success_kws)
        has_errors = any(kw.lower() in body_text.lower() for kw in error_kws)

        print(f"  Success indicators: {success}")
        print(f"  Error indicators: {has_errors}")

        if has_errors:
            # Find error messages
            error_text_snippet = ""
            for line in body_text.split("\n"):
                if any(kw.lower() in line.lower() for kw in error_kws):
                    error_text_snippet += line.strip() + "\n"
            print(f"\n  ERRORS FOUND:\n{error_text_snippet[:500]}")

        # Save final screenshot
        await page.screenshot(path=FINAL_SCREENSHOT, full_page=True)
        print(f"\n  Final screenshot: {FINAL_SCREENSHOT}")

        await browser.close()
        return success, has_errors, current_url


if __name__ == "__main__":
    success, has_errors, url = asyncio.run(fill_application())
    print("\n=== FINAL RESULT ===")
    print(f"  Success: {success}")
    print(f"  Has errors: {has_errors}")
    print(f"  Final URL: {url}")
