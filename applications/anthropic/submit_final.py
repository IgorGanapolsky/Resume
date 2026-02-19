#!/usr/bin/env python3
"""
Anthropic Job Application - FINAL submission script
Fix: fill Country field (the main blocker)
"""

import asyncio
from playwright.async_api import async_playwright

RESUME_DOCX = "/Users/ganapolsky_i/workspace/git/igor/Resume/applications/anthropic/tailored_resumes/2026-02-17_anthropic_autonomous-agent-infrastructure_resume.docx"
SCREENSHOT_DIR = (
    "/Users/ganapolsky_i/workspace/git/igor/Resume/applications/anthropic/submissions"
)
FINAL_SCREENSHOT = f"{SCREENSHOT_DIR}/2026-02-18_anthropic_submission.png"

WHY_ANTHROPIC = (
    "I've been building production AI agent infrastructure at Subway \u2014 Thompson Sampling RLHF systems, "
    "LanceDB hybrid search, 13 autonomous Claude-powered agents. Anthropic is where that work leads. "
    "The Autonomous Agent Infrastructure role is a direct extension of what I do: designing the systems "
    "that make agents reliable, observable, and safe at scale. I want to work on the hard problems at the frontier."
)


async def ss(page, name, clip=None):
    path = f"{SCREENSHOT_DIR}/2026-02-18_{name}.png"
    if clip:
        await page.screenshot(path=path, clip=clip)
    else:
        await page.screenshot(path=path, full_page=True)
    print(f"  [ss] {name}")


async def select_gh(page, field_id, option_text):
    """Click Greenhouse React Select and pick an option."""
    await page.evaluate(
        f"document.getElementById('{field_id}').scrollIntoView({{block:'center'}})"
    )
    await page.wait_for_timeout(400)

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
        print(f"  SKIP (no coords): #{field_id}")
        return

    await page.mouse.click(coords["x"], coords["y"])
    await page.wait_for_timeout(700)

    opts = await page.locator("[class*='select__option']").all_text_contents()
    target = page.locator("[class*='select__option']").filter(has_text=option_text)
    if await target.count() > 0:
        await target.first.click()
        print(f"  Selected '{option_text}' for #{field_id}")
    elif opts:
        matching = [o for o in opts if option_text.lower() in o.lower()]
        if matching:
            await (
                page.locator("[class*='select__option']")
                .filter(has_text=matching[0])
                .first.click()
            )
            print(f"  Selected (fuzzy) '{matching[0]}' for #{field_id}")
        else:
            await page.locator("[class*='select__option']").first.click()
            print(f"  Selected first option '{opts[0]}' for #{field_id}")
    else:
        print(f"  WARNING: no options for #{field_id}")
        await page.keyboard.press("Escape")

    await page.wait_for_timeout(300)


async def select_country(page):
    """Select United States in the Country field (intl-tel-input-based React Select)."""
    print("  Selecting Country: United States...")

    # The country field id is 'country'
    country_inp = page.locator("#country")
    if await country_inp.count() == 0:
        print("  WARNING: country field not found")
        return

    # It's a React Select too - same select-shell structure
    coords = await page.evaluate("""() => {
        const inp = document.getElementById('country');
        if (!inp) return null;
        let el = inp;
        for (let i = 0; i < 10; i++) {
            el = el.parentElement;
            if (!el) return null;
            if (el.className && (el.className.includes('select-shell') ||
                el.querySelector('[class*="select__control"]'))) {
                const ctrl = el.querySelector('[class*="select__control"]');
                if (ctrl) {
                    const r = ctrl.getBoundingClientRect();
                    return {x: r.x + r.width/2, y: r.y + r.height/2};
                }
            }
        }
        return null;
    }""")

    print(f"  Country control coords: {coords}")

    if coords:
        await page.mouse.click(coords["x"], coords["y"])
        await page.wait_for_timeout(800)

        opts = await page.locator("[class*='select__option']").all_text_contents()
        print(f"  Country options (first 5): {opts[:5]}")

        # Type to filter
        await page.keyboard.type("United States", delay=50)
        await page.wait_for_timeout(500)

        us_option = page.locator("[class*='select__option']").filter(
            has_text="United States"
        )
        if await us_option.count() > 0:
            await us_option.first.click()
            print("  Selected United States")
        else:
            # Try just clicking first option
            opts2 = await page.locator("[class*='select__option']").all_text_contents()
            print(f"  Options after typing: {opts2[:5]}")
            if opts2:
                await page.locator("[class*='select__option']").first.click()
                print(f"  Selected first: {opts2[0]}")
            else:
                await page.keyboard.press("Escape")
    else:
        # Fallback: try clicking on the visible country label area
        print("  Using fallback country selection...")
        country_area = page.locator("div:has(label:has-text('Country'))").first
        ctrl = country_area.locator("[class*='select__control']").first
        if await ctrl.count() > 0:
            await ctrl.click()
            await page.wait_for_timeout(800)
            await page.keyboard.type("United States")
            await page.wait_for_timeout(500)
            us = page.locator("[class*='select__option']").filter(
                has_text="United States"
            )
            if await us.count() > 0:
                await us.first.click()
            else:
                await page.keyboard.press("Escape")

    await page.wait_for_timeout(400)


async def fill_and_submit():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=100)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        print("Loading application...")
        await page.goto(
            "https://job-boards.greenhouse.io/anthropic/jobs/5065894008",
            wait_until="networkidle",
        )

        apply = page.locator("button:has-text('Apply')")
        if await apply.count() > 0:
            await apply.first.click()
            await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)
        await ss(page, "FINAL_01_form")

        # Basic fields
        await page.locator("#first_name").fill("Igor")
        await page.locator("#last_name").fill("Ganapolsky")
        await page.locator("#email").fill("iganapolsky@gmail.com")
        await page.locator("#phone").fill("2016391534")
        print("Filled: first_name, last_name, email, phone")

        # COUNTRY - select United States
        await page.evaluate(
            "document.getElementById('country').scrollIntoView({block:'center'})"
        )
        await page.wait_for_timeout(300)
        await select_country(page)

        # Resume upload
        await page.locator("#resume").set_input_files(RESUME_DOCX)
        await page.wait_for_timeout(3000)
        print("Resume uploaded")
        await ss(page, "FINAL_02_basics")

        # Text question fields
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
        print("Filled all text fields")
        await ss(page, "FINAL_03_texts")

        # Dropdown fields
        print("Filling dropdowns...")
        await select_gh(page, "question_14439954008", "Yes")  # In-person 25%
        await select_gh(page, "question_14439957008", "Yes")  # AI Policy
        await select_gh(page, "question_14439959008", "No")  # Visa sponsorship
        await select_gh(page, "question_14439960008", "No")  # Future visa
        await select_gh(page, "question_14439963008", "No")  # Relocation
        await select_gh(page, "question_14439965008", "No")  # Interviewed at Anthropic
        await ss(page, "FINAL_04_dropdowns")

        # Verify country
        country_val = await page.evaluate("""() => {
            const inp = document.getElementById('country');
            if (!inp) return 'NOT FOUND';
            let el = inp;
            for (let i = 0; i < 10; i++) {
                el = el.parentElement;
                if (!el) return 'NO PARENT';
                const sv = el.querySelector('[class*="select__single-value"]');
                if (sv) return sv.textContent.trim();
            }
            return inp.value || 'EMPTY';
        }""")
        print(f"Country value: '{country_val}'")

        # Scroll to bottom to see the full form
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)
        await ss(page, "FINAL_05_bottom")

        # Find and click submit
        submit = page.locator("input[type='submit'], button[type='submit']")
        sub_count = await submit.count()
        print(f"Submit buttons: {sub_count}")

        if sub_count > 0:
            await submit.last.scroll_into_view_if_needed()
            submit_text = await page.evaluate("""() => {
                const b = document.querySelector('input[type=submit], button[type=submit]');
                return b ? (b.value || b.textContent.trim()) : '';
            }""")
            print(f"Submit button text: '{submit_text}'")
            await ss(page, "FINAL_06_pre_submit")

            print("SUBMITTING APPLICATION...")
            await submit.last.click()

            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            await page.wait_for_timeout(5000)

        # Results
        final_url = page.url
        body = await page.inner_text("body")
        title = await page.title()

        print(f"\nURL: {final_url}")
        print(f"Title: {title}")
        print(f"Body (first 800):\n{body[:800]}")

        # Check for remaining errors
        dom_errors = await page.evaluate("""() => {
            const errs = [];
            document.querySelectorAll('[class*="helper-text--error"], [class*="field--error"] .helper-text').forEach(e => {
                if (e.textContent.trim()) errs.push(e.textContent.trim());
            });
            return errs;
        }""")
        print(f"\nDOM errors: {dom_errors}")

        success = any(
            k in body.lower()
            for k in [
                "thank you for applying",
                "application received",
                "your application",
                "successfully submitted",
            ]
        )
        print(f"\nSuccess: {success}")

        await ss(page, "FINAL_07_result")
        await page.screenshot(path=FINAL_SCREENSHOT, full_page=True)
        print(f"Final screenshot: {FINAL_SCREENSHOT}")

        await browser.close()
        return success, final_url, dom_errors


if __name__ == "__main__":
    s, u, errs = asyncio.run(fill_and_submit())
    print(f"\n=== DONE === success={s} url={u} remaining_errors={errs}")
