#!/usr/bin/env python3
"""
Anthropic Job Application Automation v5
Uses select-shell structure with proper Playwright clicks
"""

import asyncio
from playwright.async_api import async_playwright

RESUME_DOCX = "/Users/ganapolsky_i/workspace/git/igor/Resume/applications/anthropic/tailored_resumes/2026-02-17_anthropic_autonomous-agent-infrastructure_resume.docx"
COVER_LETTER_TXT = "/Users/ganapolsky_i/workspace/git/igor/Resume/applications/anthropic/cover_letters/2026-02-17_anthropic_autonomous-agent-infrastructure.txt"
SCREENSHOT_DIR = "/Users/ganapolsky_i/workspace/git/igor/Resume/applications/anthropic/submissions"
FINAL_SCREENSHOT = f"{SCREENSHOT_DIR}/2026-02-18_anthropic_submission.png"

with open(COVER_LETTER_TXT, 'r') as f:
    COVER_LETTER_TEXT = f.read()

WHY_ANTHROPIC = (
    "I've been building production AI agent infrastructure at Subway \u2014 Thompson Sampling RLHF systems, "
    "LanceDB hybrid search, 13 autonomous Claude-powered agents. Anthropic is where that work leads. "
    "The Autonomous Agent Infrastructure role is a direct extension of what I do: designing the systems "
    "that make agents reliable, observable, and safe at scale. I want to work on the hard problems at the frontier."
)


async def ss(page, name):
    path = f"{SCREENSHOT_DIR}/2026-02-18_{name}.png"
    await page.screenshot(path=path, full_page=True)
    print(f"  [ss] {name}")


async def greenhouse_select(page, field_id, option_text, timeout_ms=10000):
    """
    Click a Greenhouse React Select dropdown by finding the select__control
    that shares the same select-shell container as the hidden input.
    Then click the matching option.
    """
    print(f"  Selecting '{option_text}' for #{field_id}...")

    # Find the select-shell container that contains our field_id
    # The structure is: select-shell > [accessible text divs, div > select__control, requiredInput(our field)]
    # We need to click the select__control inside the same select-shell

    # Use Playwright to navigate to the control
    # Step 1: get the bounding box of the select__control via JS
    box = await page.evaluate(f"""() => {{
        const inp = document.getElementById('{field_id}');
        if (!inp) return null;

        // Walk up to find select-shell
        let el = inp;
        for (let i = 0; i < 10; i++) {{
            el = el.parentElement;
            if (!el) return null;
            if (el.className && el.className.includes('select-shell')) {{
                // Found the shell, now find the control
                const ctrl = el.querySelector('[class*="select__control"]');
                if (ctrl) {{
                    const rect = ctrl.getBoundingClientRect();
                    return {{x: rect.x + rect.width/2, y: rect.y + rect.height/2, found: true}};
                }}
            }}
        }}
        return null;
    }}""")

    if not box or not box.get('found'):
        print(f"  WARNING: Could not locate select__control for #{field_id}")
        return False

    # Click at the calculated position
    await page.mouse.click(box['x'], box['y'])
    await page.wait_for_timeout(1000)

    # Now the menu should be open - find the option
    # Options render in a menu attached to the select-shell or body
    option = page.locator("[class*='select__option']").filter(has_text=option_text)
    try:
        opt_count = await option.count()
    except Exception:
        opt_count = 0

    if opt_count > 0:
        all_opts = await page.locator("[class*='select__option']").all_text_contents()
        print(f"  Options available: {all_opts}")
        await option.first.click()
        print(f"  Selected '{option_text}'")
        await page.wait_for_timeout(400)
        return True
    else:
        # Menu might still have the phone country dropdown open - escape and retry
        all_opts = await page.locator("[class*='select__option']").all_text_contents()
        print(f"  Options found: {all_opts}")

        if all_opts:
            # Find matching option
            matching = [o for o in all_opts if option_text.lower() in o.lower()]
            if matching:
                opt_el = page.locator("[class*='select__option']").filter(has_text=matching[0])
                await opt_el.first.click()
                print(f"  Selected (fuzzy): '{matching[0]}'")
                return True
            else:
                # Select first option
                await page.locator("[class*='select__option']").first.click()
                print(f"  Selected first option: {all_opts[0][:50]}")
                return True

        print(f"  WARNING: No options available after clicking #{field_id}")
        await page.keyboard.press("Escape")
        return False


async def fill_application():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=100)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        print("Navigating...")
        await page.goto("https://job-boards.greenhouse.io/anthropic/jobs/5065894008", wait_until="networkidle")
        await ss(page, "30_start")

        # Click Apply
        apply = page.locator("button:has-text('Apply')")
        if await apply.count() > 0:
            await apply.first.click()
            await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)
        await ss(page, "31_form")
        print("On application form")

        # ---- BASIC FIELDS ----
        await page.locator("#first_name").fill("Igor")
        await page.locator("#last_name").fill("Ganapolsky")
        await page.locator("#email").fill("iganapolsky@gmail.com")
        await page.locator("#phone").fill("2016391534")
        print("Filled name/email/phone")

        # ---- RESUME ----
        resume_inp = page.locator("#resume")
        if await resume_inp.count() == 0:
            resume_inp = page.locator("input[type='file']").first
        await resume_inp.set_input_files(RESUME_DOCX)
        await page.wait_for_timeout(3000)
        print("Uploaded resume")
        await ss(page, "32_resume")

        # ---- TEXT FIELDS ----
        await page.locator("#question_14439953008").fill("https://github.com/IgorGanapolsky")
        await page.locator("#question_14439955008").fill("Immediately")
        await page.locator("#question_14439956008").fill("No specific deadline")
        await page.locator("#question_14439958008").fill(WHY_ANTHROPIC)
        await page.locator("#question_14439961008").fill(
            "Open to SF, NYC, or Seattle. LinkedIn: https://www.linkedin.com/in/igor-ganapolsky-859317343/"
        )
        await page.locator("#question_14439962008").fill("https://www.linkedin.com/in/igor-ganapolsky-859317343/")
        await page.locator("#question_14439964008").fill("11909 Glenmore Dr, Coral Springs, FL 33071")
        print("Filled all text fields")
        await ss(page, "33_texts")

        # ---- DROPDOWNS ----
        # Scroll down to make sure dropdowns are visible before clicking
        print("\nHandling dropdowns...")

        # 1. "Are you open to working in-person 25%?" -> Yes
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(300)
        await greenhouse_select(page, "question_14439954008", "Yes")

        # 2. AI Policy
        await greenhouse_select(page, "question_14439957008", "I confirm")

        # 3. Visa sponsorship -> No
        await greenhouse_select(page, "question_14439959008", "No")

        # 4. Future visa -> No
        await greenhouse_select(page, "question_14439960008", "No")

        # 5. Relocation -> No
        await greenhouse_select(page, "question_14439963008", "No")

        # 6. Interviewed at Anthropic -> No
        await greenhouse_select(page, "question_14439965008", "No")

        await ss(page, "34_dropdowns")

        # ---- VERIFY ----
        print("\nVerifying required fields...")
        check = await page.evaluate("""() => {
            const res = [];
            document.querySelectorAll('input[id], textarea[id]').forEach(el => {
                const lbl = el.labels && el.labels[0] ? el.labels[0].textContent.trim() : '';
                if (lbl.includes('*')) {
                    res.push({id: el.id, val: el.value, lbl: lbl.substring(0,60)});
                }
            });
            return res;
        }""")

        missing = []
        for f in check:
            status = "OK" if f['val'] else "MISSING"
            print(f"  {status}: #{f['id']} [{f['lbl']}] = '{f['val'][:40]}'")
            if not f['val']:
                missing.append(f['id'])

        print(f"\n  Missing required fields: {missing}")

        # ---- SCROLL AND SUBMIT ----
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)
        await ss(page, "35_bottom")

        submit = page.locator("input[type='submit'], button[type='submit']")
        sub_count = await submit.count()
        print(f"\nSubmit buttons: {sub_count}")

        if sub_count > 0:
            await submit.last.scroll_into_view_if_needed()
            await ss(page, "36_pre_submit")

            print("SUBMITTING APPLICATION...")
            await submit.last.click()

            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            await page.wait_for_timeout(4000)

        # Result
        url = page.url
        body = await page.inner_text("body")
        title = await page.title()

        print(f"\nResult URL: {url}")
        print(f"Title: {title}")
        print(f"Body (500): {body[:500]}")

        success = any(k in body.lower() for k in [
            "thank you for applying", "application received", "your application",
            "we've received", "successfully submitted"
        ])
        errors = any(k in body.lower() for k in ["can't be blank", "is required", "please fill"])

        print(f"\nSuccess: {success}, Errors: {errors}")

        await ss(page, "37_result")
        await page.screenshot(path=FINAL_SCREENSHOT, full_page=True)
        print(f"Final screenshot saved: {FINAL_SCREENSHOT}")

        await browser.close()
        return success, errors, url


if __name__ == "__main__":
    s, e, u = asyncio.run(fill_application())
    print(f"\n=== DONE === success={s} errors={e} url={u}")
