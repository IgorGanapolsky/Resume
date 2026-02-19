#!/usr/bin/env python3
"""
Anthropic Job Application Automation v2
Job: Senior/Staff+ Software Engineer, Autonomous Agent Infrastructure
URL: https://job-boards.greenhouse.io/anthropic/jobs/5065894008
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


async def take_screenshot(page, name):
    path = f"{SCREENSHOT_DIR}/2026-02-18_{name}.png"
    await page.screenshot(path=path, full_page=True)
    print(f"  [screenshot] {path}")
    return path


async def inspect_form(page):
    """Inspect and print all form fields."""
    print("\n=== INSPECTING FORM FIELDS ===")

    # All inputs
    inputs = await page.evaluate("""() => {
        const inputs = document.querySelectorAll('input, textarea, select');
        return Array.from(inputs).map(el => ({
            tag: el.tagName,
            type: el.type || '',
            id: el.id || '',
            name: el.name || '',
            placeholder: el.placeholder || '',
            className: el.className || '',
            value: el.value || '',
            labels: Array.from(el.labels || []).map(l => l.textContent.trim())
        }));
    }""")

    for i, inp in enumerate(inputs):
        print(
            f"  [{i}] {inp['tag']} type={inp['type']} id={inp['id']} name={inp['name']} placeholder={inp['placeholder'][:40]} labels={inp['labels']}"
        )

    # Custom dropdowns
    print("\n  Custom dropdowns:")
    dropdowns = await page.evaluate("""() => {
        const containers = document.querySelectorAll('[class*="select__container"], [class*="select-container"]');
        return Array.from(containers).map(c => ({
            id: c.id || '',
            className: c.className,
            text: c.textContent.trim().substring(0, 100)
        }));
    }""")
    for d in dropdowns:
        print(f"  Dropdown: id={d['id']} text={d['text'][:80]}")

    return inputs


async def select_react_dropdown(page, label_text, option_text, timeout=5000):
    """Select an option in a React Select dropdown by finding its label."""
    print(f"  Selecting '{option_text}' for '{label_text}'...")

    try:
        # Find the field group containing the label
        field_group = page.locator(f"div:has(label:has-text('{label_text}'))").last
        if await field_group.count() == 0:
            field_group = page.locator(f"div:has(span:has-text('{label_text}'))").last

        # Click the dropdown control within this group
        control = field_group.locator(
            "[class*='select__control'], [class*='Select-control']"
        ).first
        if await control.count() > 0:
            await control.click()
            await page.wait_for_timeout(500)
        else:
            # Try clicking the container itself
            await field_group.click()
            await page.wait_for_timeout(500)

        # Look for the option in the dropdown menu
        option = page.locator(
            f"[class*='select__option']:has-text('{option_text}')"
        ).first
        if await option.count() > 0:
            await option.click(timeout=timeout)
            print(f"  Selected '{option_text}'")
            return True

        # Try role=option
        option2 = page.locator(f"[role='option']:has-text('{option_text}')").last
        if await option2.count() > 0:
            await option2.click(timeout=timeout)
            print(f"  Selected '{option_text}' via role=option")
            return True

    except Exception as e:
        print(f"  Warning: Could not select '{option_text}' for '{label_text}': {e}")
        await page.keyboard.press("Escape")
        return False


async def fill_application():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=300)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        print("Step 1: Navigate to job page")
        await page.goto(
            "https://job-boards.greenhouse.io/anthropic/jobs/5065894008",
            wait_until="networkidle",
        )
        await take_screenshot(page, "01_job_page")

        print("Step 2: Click Apply button")
        apply_selectors = [
            "a:has-text('Apply for this Job')",
            "a:has-text('Apply Now')",
            "button:has-text('Apply')",
            "#apply-button",
            ".apply-button",
        ]
        clicked = False
        for sel in apply_selectors:
            btn = page.locator(sel)
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_load_state("networkidle")
                clicked = True
                print(f"  Clicked: {sel}")
                break

        if not clicked:
            print("  No apply button found, checking if already on form...")

        await page.wait_for_timeout(2000)
        await take_screenshot(page, "02_after_apply_click")

        print("Step 3: Inspect form structure")
        await inspect_form(page)

        print("\nStep 4: Fill basic personal info")

        # First name
        fn = page.locator("#first_name")
        if await fn.count() > 0:
            await fn.fill("Igor")
            print("  Filled first_name")

        # Last name
        ln = page.locator("#last_name")
        if await ln.count() > 0:
            await ln.fill("Ganapolsky")
            print("  Filled last_name")

        # Email
        em = page.locator("#email")
        if await em.count() > 0:
            await em.fill("iganapolsky@gmail.com")
            print("  Filled email")

        # Phone - need to handle the intl phone input
        phone_input = page.locator("#phone")
        if await phone_input.count() > 0:
            await phone_input.fill("2016391534")
            print("  Filled phone")

        # Location / City
        loc = page.locator("#job_application_location, #location")
        if await loc.count() > 0:
            await loc.first.fill("Coral Springs, FL, USA")
            await page.wait_for_timeout(500)
            # Pick first autocomplete suggestion if any
            suggestion = page.locator(".pac-item, [role='option']").first
            if await suggestion.count() > 0:
                await suggestion.click()
            print("  Filled location")

        # Current company
        company = page.locator("#company")
        if await company.count() > 0:
            await company.fill("Subway")
            print("  Filled company")

        # Resume upload
        print("\nStep 5: Upload resume")
        # Wait for any file input to appear
        resume_input = page.locator("input[type='file']").first
        if await resume_input.count() > 0:
            await resume_input.set_input_files(RESUME_DOCX)
            await page.wait_for_timeout(2000)
            print(f"  Uploaded: {RESUME_DOCX}")
        else:
            print("  WARNING: No file input found for resume!")

        await take_screenshot(page, "03_basic_info_filled")

        print("\nStep 6: Scroll down to find more fields")
        await page.evaluate("window.scrollBy(0, 400)")
        await page.wait_for_timeout(1000)
        await inspect_form(page)

        # LinkedIn field
        linkedin_inputs = page.locator(
            "input[id*='linkedin'], input[name*='linkedin'], input[placeholder*='linkedin' i]"
        )
        if await linkedin_inputs.count() > 0:
            await linkedin_inputs.first.fill(
                "https://www.linkedin.com/in/igor-ganapolsky-859317343/"
            )
            print("  Filled LinkedIn")

        # Website/Github field
        website_inputs = page.locator(
            "input[id*='website'], input[name*='website'], input[placeholder*='website' i], input[placeholder*='github' i]"
        )
        if await website_inputs.count() > 0:
            await website_inputs.first.fill("https://github.com/IgorGanapolsky")
            print("  Filled Website/GitHub")

        # Cover letter textarea (if exists as text)
        cover_textarea = page.locator("textarea[id*='cover'], textarea[name*='cover']")
        if await cover_textarea.count() > 0:
            await cover_textarea.first.fill(COVER_LETTER_TEXT)
            print("  Filled cover letter textarea")

        # Additional info / message textarea
        additional_inputs = page.locator(
            "textarea:not([id*='cover']):not([name*='cover'])"
        )
        add_count = await additional_inputs.count()
        for i in range(add_count):
            ta = additional_inputs.nth(i)
            val = await ta.input_value()
            if not val:
                ta_id = await ta.get_attribute("id") or ""
                ta_name = await ta.get_attribute("name") or ""
                ta_placeholder = await ta.get_attribute("placeholder") or ""
                print(
                    f"  Found empty textarea: id={ta_id} name={ta_name} placeholder={ta_placeholder[:50]}"
                )
                if any(
                    kw in (ta_id + ta_name + ta_placeholder).lower()
                    for kw in [
                        "why",
                        "reason",
                        "tell",
                        "additional",
                        "info",
                        "message",
                        "note",
                    ]
                ):
                    await ta.fill(WHY_ANTHROPIC)
                    print("  Filled textarea with WHY_ANTHROPIC text")

        await take_screenshot(page, "04_more_fields")

        print("\nStep 7: Handle custom dropdowns")
        await page.evaluate("window.scrollBy(0, 400)")
        await page.wait_for_timeout(500)

        # Get all the question labels visible on page
        questions = await page.evaluate("""() => {
            const labels = document.querySelectorAll('label, .field-label, .question-label');
            return Array.from(labels).map(l => l.textContent.trim()).filter(t => t.length > 3);
        }""")
        print("  Questions/labels found:", questions[:30])

        # Handle relocation question
        relocation_q = page.locator(
            "div.field:has(label:has-text('relocation')), div.field:has(span:has-text('relocation'))"
        )
        if await relocation_q.count() > 0:
            print("  Found relocation question div")
            reloc_control = relocation_q.locator("[class*='select__control']").first
            if await reloc_control.count() > 0:
                await reloc_control.click()
                await page.wait_for_timeout(500)
                # Look for options
                options = await page.locator(
                    "[class*='select__option']"
                ).all_text_contents()
                print(f"  Relocation options: {options}")
                no_opt = page.locator("[class*='select__option']").filter(has_text="No")
                if await no_opt.count() > 0:
                    await no_opt.first.click()
                    print("  Selected 'No' for relocation")
                else:
                    await page.keyboard.press("Escape")

        # Handle "Have you interviewed at Anthropic" question
        anthropic_q = page.locator(
            "div.field:has(label:has-text('interviewed at Anthropic')), div.field:has(span:has-text('interviewed at Anthropic'))"
        )
        if await anthropic_q.count() > 0:
            print("  Found interviewed-at-Anthropic question")
            anth_control = anthropic_q.locator("[class*='select__control']").first
            if await anth_control.count() > 0:
                anth_val = await anth_control.text_content()
                print(f"  Current value: {anth_val}")
                if "No" not in (anth_val or ""):
                    await anth_control.click()
                    await page.wait_for_timeout(500)
                    no_opt = (
                        page.locator("[class*='select__option']")
                        .filter(has_text="No")
                        .first
                    )
                    if await no_opt.count() > 0:
                        await no_opt.click()
                        print("  Selected 'No' for Anthropic interview question")
                    else:
                        await page.keyboard.press("Escape")

        # Handle "working address" question
        work_addr_field = page.locator(
            "input[placeholder*='relocating'], textarea[placeholder*='relocating']"
        )
        if await work_addr_field.count() > 0:
            await work_addr_field.first.fill(
                "11909 Glenmore Dr, Coral Springs, FL 33071"
            )
            print("  Filled working address")

        await take_screenshot(page, "05_dropdowns_handled")

        print("\nStep 8: Scroll to see all remaining fields")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
        await page.wait_for_timeout(1000)

        # Check for "how did you hear about us"
        hear_selects = await page.evaluate("""() => {
            const selects = document.querySelectorAll('select');
            return Array.from(selects).map(s => ({
                id: s.id,
                name: s.name,
                options: Array.from(s.options).map(o => o.text)
            }));
        }""")
        print("  Native selects:", hear_selects)

        # Handle native selects
        for sel_info in hear_selects:
            sel_id = sel_info["id"]
            sel_name = sel_info["name"]
            options = sel_info["options"]

            if any(
                kw in (sel_id + sel_name).lower()
                for kw in ["hear", "source", "referral"]
            ):
                sel_el = (
                    page.locator(f"select#{sel_id}")
                    if sel_id
                    else page.locator(f"select[name='{sel_name}']")
                )
                if "LinkedIn" in options:
                    await sel_el.select_option(label="LinkedIn")
                    print(f"  Set {sel_id or sel_name} to LinkedIn")
                elif len(options) > 1:
                    await sel_el.select_option(index=1)
                    print(
                        f"  Set {sel_id or sel_name} to first available option: {options[1]}"
                    )

            if any(
                kw in (sel_id + sel_name).lower()
                for kw in ["authorized", "work_auth", "legal"]
            ):
                sel_el = (
                    page.locator(f"select#{sel_id}")
                    if sel_id
                    else page.locator(f"select[name='{sel_name}']")
                )
                if "Yes" in options:
                    await sel_el.select_option(label="Yes")
                    print("  Set work authorization to Yes")

            if any(kw in (sel_id + sel_name).lower() for kw in ["visa", "sponsor"]):
                sel_el = (
                    page.locator(f"select#{sel_id}")
                    if sel_id
                    else page.locator(f"select[name='{sel_name}']")
                )
                if "No" in options:
                    await sel_el.select_option(label="No")
                    print("  Set visa sponsorship to No")

        await take_screenshot(page, "06_additional_fields")

        print("\nStep 9: Scroll to bottom and handle EEO / demographic fields")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(2000)
        await take_screenshot(page, "07_bottom_of_form")

        print("\nStep 10: Final review - find and click Submit")
        submit_btn = page.locator(
            "input[type='submit'][value*='Submit'], "
            "button[type='submit']:has-text('Submit'), "
            "input[value='Submit Application'], "
            "button:has-text('Submit Application')"
        )

        submit_count = await submit_btn.count()
        print(f"  Found {submit_count} submit button(s)")

        if submit_count > 0:
            btn_text = (
                await submit_btn.first.text_content()
                or await submit_btn.first.get_attribute("value")
            )
            print(f"  Submit button text: {btn_text}")

            # Scroll to submit button
            await submit_btn.first.scroll_into_view_if_needed()
            await page.wait_for_timeout(500)
            await take_screenshot(page, "08_pre_submit_scroll")

            print("  SUBMITTING APPLICATION...")
            await submit_btn.first.click()

            # Wait for response
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                await page.wait_for_timeout(5000)

            print("  Form submitted (or attempted)")
        else:
            # Try JS click on any submit-looking button
            print("  Trying JS approach to find submit button...")
            all_btns = await page.evaluate("""() => {
                const btns = document.querySelectorAll('input[type=submit], button[type=submit]');
                return Array.from(btns).map(b => ({
                    text: b.value || b.textContent,
                    id: b.id,
                    disabled: b.disabled
                }));
            }""")
            print(f"  All submit buttons via JS: {all_btns}")

            if all_btns:
                await page.evaluate(
                    "document.querySelector('input[type=submit], button[type=submit]').click()"
                )
                await page.wait_for_timeout(5000)
                print("  Clicked submit via JS")

        print("\nStep 11: Capture confirmation")
        await page.wait_for_timeout(3000)

        # Check for confirmation
        page_url = page.url
        page_title = await page.title()
        body_text = await page.inner_text("body")

        print(f"\n  URL after submit: {page_url}")
        print(f"  Page title: {page_title}")
        print(f"  Body text (first 500 chars): {body_text[:500]}")

        # Check for success indicators
        success_keywords = [
            "thank you",
            "application received",
            "submitted",
            "confirmation",
            "success",
        ]
        is_success = any(kw.lower() in body_text.lower() for kw in success_keywords)
        print(f"\n  SUCCESS INDICATORS FOUND: {is_success}")

        await take_screenshot(page, "09_after_submit")
        await page.screenshot(path=FINAL_SCREENSHOT, full_page=True)
        print(f"\n  FINAL SCREENSHOT saved: {FINAL_SCREENSHOT}")

        await browser.close()
        return is_success, page_url, body_text[:1000]


if __name__ == "__main__":
    result = asyncio.run(fill_application())
    print(f"\n=== RESULT: success={result[0]}, url={result[1]} ===")
    print(f"Page content summary: {result[2]}")
