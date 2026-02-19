#!/usr/bin/env python3
"""Debug what's happening with the form submission."""

import asyncio
from playwright.async_api import async_playwright

RESUME_DOCX = "/Users/ganapolsky_i/workspace/git/igor/Resume/applications/anthropic/tailored_resumes/2026-02-17_anthropic_autonomous-agent-infrastructure_resume.docx"
COVER_LETTER_TXT = "/Users/ganapolsky_i/workspace/git/igor/Resume/applications/anthropic/cover_letters/2026-02-17_anthropic_autonomous-agent-infrastructure.txt"
SCREENSHOT_DIR = (
    "/Users/ganapolsky_i/workspace/git/igor/Resume/applications/anthropic/submissions"
)

with open(COVER_LETTER_TXT, "r") as f:
    COVER_LETTER_TEXT = f.read()

WHY_ANTHROPIC = (
    "I've been building production AI agent infrastructure at Subway \u2014 Thompson Sampling RLHF systems, "
    "LanceDB hybrid search, 13 autonomous Claude-powered agents. Anthropic is where that work leads. "
    "The Autonomous Agent Infrastructure role is a direct extension of what I do: designing the systems "
    "that make agents reliable, observable, and safe at scale. I want to work on the hard problems at the frontier."
)


async def greenhouse_select(page, field_id, option_text):
    await page.evaluate(
        f"document.getElementById('{field_id}').scrollIntoView({{block: 'center'}})"
    )
    await page.wait_for_timeout(500)

    coords = await page.evaluate(f"""() => {{
        const inp = document.getElementById('{field_id}');
        if (!inp) return null;
        let el = inp;
        for (let i = 0; i < 10; i++) {{
            el = el.parentElement;
            if (!el) return null;
            if (el.className && el.className.includes('select-shell')) {{
                const ctrl = el.querySelector('[class*="select__control"]');
                if (ctrl) {{
                    const r = ctrl.getBoundingClientRect();
                    return {{x: r.x + r.width/2, y: r.y + r.height/2}};
                }}
            }}
        }}
        return null;
    }}""")

    if not coords:
        print(f"  ERROR: no control for #{field_id}")
        return

    await page.mouse.click(coords["x"], coords["y"])
    await page.wait_for_timeout(800)
    opts = await page.locator("[class*='select__option']").all_text_contents()
    print(f"  #{field_id} options: {opts}")

    target = page.locator("[class*='select__option']").filter(has_text=option_text)
    if await target.count() > 0:
        await target.first.click()
    elif opts:
        await page.locator("[class*='select__option']").last.click()
    await page.wait_for_timeout(400)


async def check_selected(page, field_id):
    return await page.evaluate(f"""() => {{
        const inp = document.getElementById('{field_id}');
        if (!inp) return '';
        let el = inp;
        for (let i = 0; i < 10; i++) {{
            el = el.parentElement;
            if (!el) return '';
            if (el.className && el.className.includes('select-shell')) {{
                const sv = el.querySelector('[class*="select__single-value"]');
                return sv ? sv.textContent.trim() : 'NO_SINGLE_VALUE';
            }}
        }}
        return inp.value;
    }}""")


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=100)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        await page.goto(
            "https://job-boards.greenhouse.io/anthropic/jobs/5065894008",
            wait_until="networkidle",
        )
        apply = page.locator("button:has-text('Apply')")
        if await apply.count() > 0:
            await apply.first.click()
            await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)

        # Fill basics
        await page.locator("#first_name").fill("Igor")
        await page.locator("#last_name").fill("Ganapolsky")
        await page.locator("#email").fill("iganapolsky@gmail.com")
        await page.locator("#phone").fill("2016391534")

        # Resume
        await page.locator("#resume").set_input_files(RESUME_DOCX)
        await page.wait_for_timeout(3000)

        # Text fields
        await page.locator("#question_14439953008").fill(
            "https://github.com/IgorGanapolsky"
        )
        await page.locator("#question_14439955008").fill("Immediately")
        await page.locator("#question_14439956008").fill("No specific deadline")
        await page.locator("#question_14439958008").fill(WHY_ANTHROPIC)
        await page.locator("#question_14439961008").fill(
            "Open to SF, NYC, or Seattle. GitHub: https://github.com/IgorGanapolsky"
        )
        await page.locator("#question_14439962008").fill(
            "https://www.linkedin.com/in/igor-ganapolsky-859317343/"
        )
        await page.locator("#question_14439964008").fill(
            "11909 Glenmore Dr, Coral Springs, FL 33071"
        )

        # Dropdowns
        await greenhouse_select(page, "question_14439954008", "Yes")
        await greenhouse_select(page, "question_14439957008", "Yes")
        await greenhouse_select(page, "question_14439959008", "No")
        await greenhouse_select(page, "question_14439960008", "No")
        await greenhouse_select(page, "question_14439963008", "No")
        await greenhouse_select(page, "question_14439965008", "No")

        # Verify all
        print("\nDropdown values:")
        for fid in [
            "question_14439954008",
            "question_14439957008",
            "question_14439959008",
            "question_14439960008",
            "question_14439963008",
            "question_14439965008",
        ]:
            val = await check_selected(page, fid)
            print(f"  #{fid} = '{val}'")

        # Now try to submit and capture the response/errors
        submit = page.locator("input[type='submit'], button[type='submit']")
        await submit.last.scroll_into_view_if_needed()

        # Take screenshot of form right before submit
        form_area = await page.evaluate("""() => {
            const form = document.querySelector('form');
            if (form) {
                const rect = form.getBoundingClientRect();
                return {x: rect.x, y: rect.y, w: rect.width, h: rect.height};
            }
            return null;
        }""")
        print(f"\nForm area: {form_area}")

        # Capture just the bottom of the form
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.6)")
        await page.wait_for_timeout(500)
        await page.screenshot(path=f"{SCREENSHOT_DIR}/2026-02-18_DEBUG_form_mid.png")
        print("Mid screenshot taken")

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(500)
        await page.screenshot(path=f"{SCREENSHOT_DIR}/2026-02-18_DEBUG_form_bottom.png")
        print("Bottom screenshot taken")

        # Click submit and listen for network requests
        print("\nSubmitting...")

        # Intercept the submission
        responses = []

        async def handle_response(response):
            if "greenhouse" in response.url or "application" in response.url:
                responses.append({"url": response.url, "status": response.status})

        page.on("response", handle_response)

        await submit.last.click()
        await page.wait_for_timeout(5000)

        print(f"\nNetwork responses after submit: {responses}")
        print(f"Current URL: {page.url}")

        # Check for errors in DOM after submit attempt
        error_report = await page.evaluate("""() => {
            const errors = [];
            // Check for error classes
            document.querySelectorAll('[class*="error"], .field--error').forEach(el => {
                if (el.textContent.trim()) {
                    errors.push({class: el.className.substring(0, 50), text: el.textContent.trim().substring(0, 100)});
                }
            });
            // Check aria-describedby error messages
            document.querySelectorAll('[aria-invalid="true"]').forEach(el => {
                errors.push({type: 'aria-invalid', id: el.id, label: el.labels ? (el.labels[0]?.textContent||'') : ''});
            });
            return errors;
        }""")
        print(f"\nDOM errors after submit: {error_report}")

        # Check for reCAPTCHA
        recaptcha_info = await page.evaluate("""() => {
            const rc = document.querySelector('.g-recaptcha, iframe[src*="recaptcha"]');
            return {
                found: !!rc,
                element: rc ? rc.tagName : null,
                src: rc && rc.src ? rc.src.substring(0, 80) : null
            };
        }""")
        print(f"\nreCAPTCHA info: {recaptcha_info}")

        # Take screenshot of page after attempt
        await page.screenshot(
            path=f"{SCREENSHOT_DIR}/2026-02-18_DEBUG_after_submit.png", full_page=True
        )
        print("After-submit screenshot taken")

        # Get the full page text around the form
        form_text = await page.evaluate("""() => {
            const form = document.querySelector('form#application-form, form.s-form, form[data-form="true"], form');
            return form ? form.innerText.substring(0, 3000) : document.body.innerText.substring(0, 3000);
        }""")
        print(f"\nForm text:\n{form_text[:2000]}")

        await browser.close()


asyncio.run(run())
