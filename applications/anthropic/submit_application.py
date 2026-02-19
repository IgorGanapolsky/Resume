#!/usr/bin/env python3
"""
Anthropic Job Application Automation
Job: Senior/Staff+ Software Engineer, Autonomous Agent Infrastructure
URL: https://job-boards.greenhouse.io/anthropic/jobs/5065894008
"""

import asyncio
from playwright.async_api import async_playwright

RESUME_DOCX = "/Users/ganapolsky_i/workspace/git/igor/Resume/applications/anthropic/tailored_resumes/2026-02-17_anthropic_autonomous-agent-infrastructure_resume.docx"
COVER_LETTER_TXT = "/Users/ganapolsky_i/workspace/git/igor/Resume/applications/anthropic/cover_letters/2026-02-17_anthropic_autonomous-agent-infrastructure.txt"
SCREENSHOT_DIR = "/Users/ganapolsky_i/workspace/git/igor/Resume/applications/anthropic/submissions"
FINAL_SCREENSHOT = f"{SCREENSHOT_DIR}/2026-02-18_anthropic_submission.png"

PERSONAL_INFO = {
    "first_name": "Igor",
    "last_name": "Ganapolsky",
    "email": "iganapolsky@gmail.com",
    "phone": "(201) 639-1534",
    "address": "11909 Glenmore Dr",
    "city": "Coral Springs",
    "state": "FL",
    "zip": "33071",
    "linkedin": "https://www.linkedin.com/in/igor-ganapolsky-859317343/",
    "github": "https://github.com/IgorGanapolsky",
    "current_company": "Subway",
    "location": "Coral Springs, FL, USA",
}

WHY_ANTHROPIC = (
    "I've been building production AI agent infrastructure at Subway — Thompson Sampling RLHF systems, "
    "LanceDB hybrid search, 13 autonomous Claude-powered agents. Anthropic is where that work leads. "
    "The Autonomous Agent Infrastructure role is a direct extension of what I do: designing the systems "
    "that make agents reliable, observable, and safe at scale. I want to work on the hard problems at the frontier."
)


async def take_screenshot(page, name):
    path = f"{SCREENSHOT_DIR}/2026-02-18_{name}.png"
    await page.screenshot(path=path, full_page=True)
    print(f"Screenshot saved: {path}")
    return path


async def fill_application():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=500)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        print("Navigating to job application page...")
        await page.goto("https://job-boards.greenhouse.io/anthropic/jobs/5065894008", wait_until="networkidle")
        await take_screenshot(page, "01_job_page")

        # Click Apply button
        print("Looking for Apply button...")
        apply_btn = page.locator("a:has-text('Apply'), button:has-text('Apply'), a:has-text('Apply for this Job')")
        if await apply_btn.count() > 0:
            await apply_btn.first.click()
            await page.wait_for_load_state("networkidle")
            print("Clicked Apply button")
        else:
            print("No Apply button found - may already be on application form")

        await take_screenshot(page, "02_application_form")

        # ---- FILL PERSONAL INFO ----
        print("Filling personal information...")

        # First name
        first_name_field = page.locator("input#first_name, input[name='job_application[first_name]'], input[placeholder*='First']")
        if await first_name_field.count() > 0:
            await first_name_field.first.fill(PERSONAL_INFO["first_name"])
            print("Filled first name")

        # Last name
        last_name_field = page.locator("input#last_name, input[name='job_application[last_name]'], input[placeholder*='Last']")
        if await last_name_field.count() > 0:
            await last_name_field.first.fill(PERSONAL_INFO["last_name"])
            print("Filled last name")

        # Email
        email_field = page.locator("input#email, input[name='job_application[email]'], input[type='email']")
        if await email_field.count() > 0:
            await email_field.first.fill(PERSONAL_INFO["email"])
            print("Filled email")

        # Phone
        phone_field = page.locator("input#phone, input[name='job_application[phone]'], input[type='tel']")
        if await phone_field.count() > 0:
            await phone_field.first.fill(PERSONAL_INFO["phone"])
            print("Filled phone")

        # ---- RESUME UPLOAD ----
        print("Uploading resume...")
        resume_input = page.locator("input[type='file']").first
        if await resume_input.count() > 0:
            await resume_input.set_input_files(RESUME_DOCX)
            print(f"Uploaded resume: {RESUME_DOCX}")
            await page.wait_for_timeout(2000)

        # ---- COVER LETTER ----
        print("Looking for cover letter field...")
        cover_letter_inputs = page.locator("input[type='file']")
        count = await cover_letter_inputs.count()
        print(f"Found {count} file inputs")
        if count >= 2:
            await cover_letter_inputs.nth(1).set_input_files(COVER_LETTER_TXT)
            print(f"Uploaded cover letter: {COVER_LETTER_TXT}")
            await page.wait_for_timeout(2000)

        # Cover letter textarea
        cover_letter_textarea = page.locator("textarea[name*='cover'], textarea[id*='cover']")
        if await cover_letter_textarea.count() > 0:
            with open(COVER_LETTER_TXT, 'r') as f:
                cover_text = f.read()
            await cover_letter_textarea.first.fill(cover_text)
            print("Filled cover letter textarea")

        # ---- LOCATION ----
        location_field = page.locator("input#job_application_location, input[id*='location'], input[name*='location']")
        if await location_field.count() > 0:
            await location_field.first.fill(PERSONAL_INFO["location"])
            print("Filled location")

        # ---- LINKEDIN ----
        linkedin_field = page.locator("input[id*='linkedin'], input[name*='linkedin']")
        if await linkedin_field.count() > 0:
            await linkedin_field.first.fill(PERSONAL_INFO["linkedin"])
            print("Filled LinkedIn")

        # ---- CURRENT COMPANY ----
        company_field = page.locator("input#company, input[name*='company'], input[id*='company']")
        if await company_field.count() > 0:
            await company_field.first.fill(PERSONAL_INFO["current_company"])
            print("Filled current company")

        # ---- WEBSITE/GITHUB ----
        website_field = page.locator("input#website, input[name*='website'], input[id*='website']")
        if await website_field.count() > 0:
            await website_field.first.fill(PERSONAL_INFO["github"])
            print("Filled website/GitHub")

        await take_screenshot(page, "03_personal_info_filled")

        # ---- SCROLL DOWN TO SEE MORE FIELDS ----
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
        await page.wait_for_timeout(1000)

        # ---- CUSTOM QUESTIONS ----
        print("Looking for custom questions...")

        # Handle dropdowns and selects
        selects = page.locator("select")
        select_count = await selects.count()
        print(f"Found {select_count} select elements")

        for i in range(select_count):
            sel = selects.nth(i)
            sel_id = await sel.get_attribute("id") or ""
            sel_name = await sel.get_attribute("name") or ""
            print(f"Select {i}: id='{sel_id}', name='{sel_name}'")

            # Work authorization
            if "authorized" in sel_id.lower() or "authorized" in sel_name.lower() or "work" in sel_id.lower():
                await sel.select_option(label="Yes")
                print("Set work authorization to Yes")

            # Visa sponsorship
            if "visa" in sel_id.lower() or "visa" in sel_name.lower() or "sponsor" in sel_id.lower():
                await sel.select_option(label="No")
                print("Set visa sponsorship to No")

            # How did you hear
            if "hear" in sel_id.lower() or "source" in sel_id.lower() or "hear" in sel_name.lower():
                try:
                    await sel.select_option(label="LinkedIn")
                    print("Set referral source to LinkedIn")
                except Exception:
                    try:
                        await sel.select_option(index=1)
                        print("Set referral source to first option")
                    except Exception:
                        pass

        await page.wait_for_timeout(1000)

        # ---- HANDLE REACT SELECT / CUSTOM DROPDOWNS ----
        print("Looking for custom dropdown components...")

        # "Are you open to relocation" dropdown
        page.locator("div:has-text('Are you open to relocation')").locator("..").locator("[class*='select'], [class*='dropdown']")
        page.locator("div[class*='select']").filter(has_text="Select...")

        # Try to find all custom select dropdowns
        all_custom_selects = page.locator("[class*='select__control'], .Select-control, [data-testid*='select']")
        custom_count = await all_custom_selects.count()
        print(f"Found {custom_count} custom select controls")

        # Look for the relocation question specifically
        relocation_label = page.locator("label:has-text('relocation'), span:has-text('relocation')")
        if await relocation_label.count() > 0:
            print("Found relocation question")
            # Click the associated dropdown
            relocation_area = page.locator("div:has-text('Are you open to relocation')").last
            await relocation_area.click()
            await page.wait_for_timeout(500)
            # Select "No" or "Yes" option
            no_option = page.locator("[class*='option']:has-text('No'), [role='option']:has-text('No')")
            if await no_option.count() > 0:
                await no_option.first.click()
                print("Selected 'No' for relocation")
            else:
                # Try clicking anywhere to close
                await page.keyboard.press("Escape")

        # ---- SCROLL TO BOTTOM ----
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)
        await take_screenshot(page, "04_mid_form")

        # ---- HANDLE WORKING ADDRESS FIELD ----
        working_address_inputs = page.locator("input[placeholder*='relocating'], textarea[placeholder*='relocating']")
        if await working_address_inputs.count() > 0:
            await working_address_inputs.first.fill("11909 Glenmore Dr, Coral Springs, FL 33071")
            print("Filled working address")

        # More text inputs check for address field
        address_field = page.locator("input[name*='address'], textarea[name*='address']")
        if await address_field.count() > 0:
            await address_field.first.fill("11909 Glenmore Dr, Coral Springs, FL 33071")
            print("Filled address field")

        # ---- WHY ANTHROPIC - text areas ----
        all_textareas = page.locator("textarea")
        textarea_count = await all_textareas.count()
        print(f"Found {textarea_count} textareas")

        for i in range(textarea_count):
            ta = all_textareas.nth(i)
            ta_id = await ta.get_attribute("id") or ""
            ta_name = await ta.get_attribute("name") or ""
            ta_placeholder = await ta.get_attribute("placeholder") or ""
            current_val = await ta.input_value()

            print(f"Textarea {i}: id='{ta_id}', name='{ta_name}', placeholder='{ta_placeholder[:50]}'")

            if current_val:
                continue  # Already filled

            if "cover" in ta_id.lower() or "cover" in ta_name.lower():
                with open(COVER_LETTER_TXT, 'r') as f:
                    cover_text = f.read()
                await ta.fill(cover_text)
                print(f"Filled cover letter textarea {i}")
            elif any(kw in ta_id.lower() or kw in ta_name.lower() or kw in ta_placeholder.lower()
                     for kw in ["why", "reason", "motivation", "additional", "message"]):
                await ta.fill(WHY_ANTHROPIC)
                print(f"Filled 'why' textarea {i}")

        # ---- HANDLE "Have you ever interviewed at Anthropic" ----
        # Already appears to be set to "No" from previous screenshot
        page.locator("div:has-text('Have you ever interviewed at Anthropic')").locator("..").locator("[class*='select'], [class*='dropdown']")

        await page.wait_for_timeout(1000)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)

        await take_screenshot(page, "05_all_fields_filled")

        print("\n=== FORM FILLING COMPLETE ===")
        print("About to take final screenshot before submission...")
        await take_screenshot(page, "06_pre_submission")

        # ---- SUBMIT THE FORM ----
        print("\nLooking for Submit button...")
        submit_btn = page.locator(
            "input[type='submit'], button[type='submit'], "
            "button:has-text('Submit Application'), button:has-text('Submit'), "
            "input[value='Submit Application'], input[value='Submit']"
        )

        submit_count = await submit_btn.count()
        print(f"Found {submit_count} submit button(s)")

        if submit_count > 0:
            print("Clicking Submit button...")
            await submit_btn.first.click()
            await page.wait_for_load_state("networkidle", timeout=30000)
            print("Form submitted!")
            await page.wait_for_timeout(3000)
        else:
            print("WARNING: No submit button found!")

        # ---- FINAL CONFIRMATION SCREENSHOT ----
        await take_screenshot(page, "07_post_submission")

        # Save the required final screenshot
        await page.screenshot(path=FINAL_SCREENSHOT, full_page=True)
        print(f"\nFinal confirmation screenshot saved: {FINAL_SCREENSHOT}")

        # Report page content
        page_text = await page.inner_text("body")
        print("\n=== PAGE CONTENT AFTER SUBMISSION ===")
        print(page_text[:2000])

        await browser.close()
        print("\nDone!")


if __name__ == "__main__":
    asyncio.run(fill_application())
