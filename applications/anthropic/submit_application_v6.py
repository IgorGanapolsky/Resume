#!/usr/bin/env python3
"""
Anthropic Job Application Automation v6
Properly handles all Greenhouse React dropdowns.
Key insight: the select-shell contains the control and hidden input.
Some dropdowns may be below viewport - must scroll to them first.
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
    "I've been building production AI agent infrastructure at Subway \u2014 Thompson Sampling RLHF systems, "
    "LanceDB hybrid search, 13 autonomous Claude-powered agents. Anthropic is where that work leads. "
    "The Autonomous Agent Infrastructure role is a direct extension of what I do: designing the systems "
    "that make agents reliable, observable, and safe at scale. I want to work on the hard problems at the frontier."
)


async def ss(page, name):
    path = f"{SCREENSHOT_DIR}/2026-02-18_{name}.png"
    await page.screenshot(path=path, full_page=True)
    print(f"  [ss] {name}")


async def greenhouse_select(page, field_id, option_text):
    """
    Select an option in a Greenhouse React Select dropdown.
    Scrolls the control into view, clicks it, then clicks the option.
    """
    print(f"  Selecting '{option_text}' for #{field_id}...")

    # Scroll the hidden input into view (this scrolls the select-shell into view)
    await page.evaluate(f"""() => {{
        const el = document.getElementById('{field_id}');
        if (el) el.scrollIntoView({{block: 'center', behavior: 'smooth'}});
    }}""")
    await page.wait_for_timeout(500)

    # Get bounding rect of the select__control inside the same select-shell as our field
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
                    if (r.height > 0) {{
                        return {{x: r.x + r.width/2, y: r.y + r.height/2, w: r.width, h: r.height}};
                    }}
                }}
            }}
        }}
        return null;
    }}""")

    if not coords:
        print(f"  ERROR: Could not find select__control for #{field_id}")
        return False

    if coords["y"] < 0 or coords["y"] > 900:
        print(f"  Control not in viewport (y={coords['y']}), scrolling more...")
        await page.evaluate(f"window.scrollBy(0, {coords['y'] - 400})")
        await page.wait_for_timeout(500)
        # Re-get coords
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

    print(f"  Clicking control at ({coords['x']:.0f}, {coords['y']:.0f})")
    await page.mouse.click(coords["x"], coords["y"])
    await page.wait_for_timeout(800)

    # Check for open options
    open_options = page.locator("[class*='select__option']")
    opt_count = await open_options.count()
    all_opts = await open_options.all_text_contents() if opt_count > 0 else []
    print(f"  Menu options: {all_opts}")

    if not all_opts:
        print("  WARNING: No options appeared. Trying again...")
        await page.mouse.click(coords["x"], coords["y"])
        await page.wait_for_timeout(1000)
        all_opts = await page.locator("[class*='select__option']").all_text_contents()
        print(f"  Second attempt options: {all_opts}")

    # Click the target option
    target_opt = page.locator("[class*='select__option']").filter(has_text=option_text)
    count = await target_opt.count()

    if count > 0:
        await target_opt.first.click()
        print(f"  Clicked '{option_text}'")
        await page.wait_for_timeout(400)
        return True
    elif all_opts:
        # Fallback: click first matching or first option
        matching = [o for o in all_opts if option_text.lower() in o.lower()]
        if matching:
            await (
                page.locator("[class*='select__option']")
                .filter(has_text=matching[0])
                .first.click()
            )
            print(f"  Clicked fuzzy match: '{matching[0]}'")
        else:
            await page.locator("[class*='select__option']").first.click()
            print(f"  Clicked first option: '{all_opts[0]}'")
        await page.wait_for_timeout(400)
        return True
    else:
        print(f"  FAILED: No options for #{field_id}")
        await page.keyboard.press("Escape")
        return False


async def check_dropdown_selected(page, field_id):
    """Check if a dropdown has a selected value by looking at the single-value container."""
    val = await page.evaluate(f"""() => {{
        const inp = document.getElementById('{field_id}');
        if (!inp) return '';
        let el = inp;
        for (let i = 0; i < 10; i++) {{
            el = el.parentElement;
            if (!el) return '';
            if (el.className && el.className.includes('select-shell')) {{
                const sv = el.querySelector('[class*="select__single-value"]');
                return sv ? sv.textContent.trim() : '';
            }}
        }}
        return inp.value;
    }}""")
    return val


async def fill_application():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=100)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        print("Navigating to Anthropic job...")
        await page.goto(
            "https://job-boards.greenhouse.io/anthropic/jobs/5065894008",
            wait_until="networkidle",
        )
        await ss(page, "40_start")

        # Click Apply
        apply = page.locator("button:has-text('Apply')")
        count = await apply.count()
        if count > 0:
            await apply.first.click()
            await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)
        print("On application form")
        await ss(page, "41_form")

        # ---- BASIC FIELDS ----
        print("\n--- Filling basic fields ---")
        await page.locator("#first_name").fill("Igor")
        await page.locator("#last_name").fill("Ganapolsky")
        await page.locator("#email").fill("iganapolsky@gmail.com")
        await page.locator("#phone").click()
        await page.locator("#phone").fill("")
        await page.locator("#phone").type("2016391534", delay=50)
        print("  first_name, last_name, email, phone filled")

        # ---- RESUME UPLOAD ----
        print("\n--- Uploading resume ---")
        resume_inp = page.locator("#resume")
        if await resume_inp.count() == 0:
            resume_inp = page.locator("input[type='file']").first
        await resume_inp.set_input_files(RESUME_DOCX)
        await page.wait_for_timeout(3000)
        print("  Resume uploaded")
        await ss(page, "42_resume")

        # ---- SCROLL AND FILL TEXT FIELDS ----
        print("\n--- Filling text fields ---")
        await page.locator("#question_14439953008").fill(
            "https://github.com/IgorGanapolsky"
        )
        print("  Website filled")
        await page.locator("#question_14439955008").fill("Immediately")
        print("  Start date filled")
        await page.locator("#question_14439956008").fill("No specific deadline")
        print("  Timeline filled")
        await page.locator("#question_14439958008").fill(WHY_ANTHROPIC)
        print("  Why Anthropic filled")
        await page.locator("#question_14439961008").fill(
            "Open to relocating to SF, NYC, or Seattle. "
            "Active GitHub: https://github.com/IgorGanapolsky"
        )
        print("  Additional info filled")
        await page.locator("#question_14439962008").fill(
            "https://www.linkedin.com/in/igor-ganapolsky-859317343/"
        )
        print("  LinkedIn filled")
        await page.locator("#question_14439964008").fill(
            "11909 Glenmore Dr, Coral Springs, FL 33071"
        )
        print("  Working address filled")
        await ss(page, "43_texts_filled")

        # ---- DROPDOWNS ----
        print("\n--- Handling dropdowns ---")

        # Clear any open phone dropdown first
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)

        # Dropdown 1: In-person 25%? -> Yes
        await greenhouse_select(page, "question_14439954008", "Yes")
        val1 = await check_dropdown_selected(page, "question_14439954008")
        print(f"  [verify] question_14439954008 = '{val1}'")

        # Dropdown 2: AI Policy -> first option (usually "I have read..." or "Yes")
        await greenhouse_select(page, "question_14439957008", "Yes")
        val2 = await check_dropdown_selected(page, "question_14439957008")
        print(f"  [verify] question_14439957008 = '{val2}'")

        # Dropdown 3: Visa sponsorship? -> No
        await greenhouse_select(page, "question_14439959008", "No")
        val3 = await check_dropdown_selected(page, "question_14439959008")
        print(f"  [verify] question_14439959008 = '{val3}'")

        # Dropdown 4: Future visa? -> No
        await greenhouse_select(page, "question_14439960008", "No")
        val4 = await check_dropdown_selected(page, "question_14439960008")
        print(f"  [verify] question_14439960008 = '{val4}'")

        # Dropdown 5: Relocation? -> No
        await greenhouse_select(page, "question_14439963008", "No")
        val5 = await check_dropdown_selected(page, "question_14439963008")
        print(f"  [verify] question_14439963008 = '{val5}'")

        # Dropdown 6: Interviewed at Anthropic? -> No
        await greenhouse_select(page, "question_14439965008", "No")
        val6 = await check_dropdown_selected(page, "question_14439965008")
        print(f"  [verify] question_14439965008 = '{val6}'")

        await ss(page, "44_dropdowns")

        # ---- FULL VERIFICATION ----
        print("\n--- Verifying all fields ---")
        # Check required fields via visible single-value text
        dropdown_ids = {
            "question_14439954008": "In-person 25%",
            "question_14439957008": "AI Policy",
            "question_14439959008": "Visa sponsorship",
            "question_14439960008": "Future visa",
            "question_14439963008": "Relocation",
            "question_14439965008": "Interviewed Anthropic",
        }

        missing_dropdowns = []
        for fid, name in dropdown_ids.items():
            val = await check_dropdown_selected(page, fid)
            status = "OK" if val else "MISSING"
            print(f"  {status}: {name} = '{val}'")
            if not val:
                missing_dropdowns.append((fid, name))

        if missing_dropdowns:
            print(
                f"\n  RETRYING missing dropdowns: {[n for _, n in missing_dropdowns]}"
            )
            for fid, name in missing_dropdowns:
                print(f"\n  Retrying {name} (#{fid})...")
                # Try clicking directly on the select control using Playwright's locator
                await page.evaluate(
                    f"document.getElementById('{fid}').scrollIntoView({{block:'center'}})"
                )
                await page.wait_for_timeout(500)

                # Get all select-shell containers in order
                shells_count = await page.evaluate("""() => {
                    return document.querySelectorAll('[class*="select-shell"]').length;
                }""")

                # Find which shell index contains our field
                shell_idx = await page.evaluate(f"""() => {{
                    const inp = document.getElementById('{fid}');
                    if (!inp) return -1;
                    const shells = Array.from(document.querySelectorAll('[class*="select-shell"]'));
                    return shells.findIndex(s => s.contains(inp));
                }}""")

                print(f"  Shell index: {shell_idx} of {shells_count}")

                if shell_idx >= 0:
                    # Click the control in this specific shell
                    f"[class*='select-shell']:nth-of-type({shell_idx + 1}) [class*='select__control']"
                    # Use nth locator
                    ctrl = (
                        page.locator("[class*='select-shell']")
                        .nth(shell_idx)
                        .locator("[class*='select__control']")
                    )
                    ctrl_count = await ctrl.count()
                    print(f"  Control count in shell {shell_idx}: {ctrl_count}")

                    if ctrl_count > 0:
                        await ctrl.first.scroll_into_view_if_needed()
                        await ctrl.first.click()
                        await page.wait_for_timeout(800)
                        opts = await page.locator(
                            "[class*='select__option']"
                        ).all_text_contents()
                        print(f"  Options: {opts}")
                        no_opt = page.locator("[class*='select__option']").filter(
                            has_text="No"
                        )
                        if await no_opt.count() > 0:
                            await no_opt.first.click()
                        elif opts:
                            await page.locator("[class*='select__option']").last.click()
                        await page.wait_for_timeout(400)

                        new_val = await check_dropdown_selected(page, fid)
                        print(f"  After retry: '{new_val}'")

        await ss(page, "45_after_retry")

        # ---- SCROLL TO BOTTOM AND SUBMIT ----
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)
        await ss(page, "46_bottom")

        print("\n--- Submitting ---")
        submit = page.locator("input[type='submit'], button[type='submit']")
        sub_n = await submit.count()
        print(f"  Submit buttons: {sub_n}")

        if sub_n > 0:
            await submit.last.scroll_into_view_if_needed()
            await ss(page, "47_pre_submit")
            await submit.last.click()
            print("  Clicked Submit!")
            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            await page.wait_for_timeout(5000)

        # ---- RESULTS ----
        url = page.url
        body = await page.inner_text("body")
        title = await page.title()

        print(f"\nURL: {url}")
        print(f"Title: {title}")
        print(f"Body preview: {body[:600]}")

        success = any(
            k in body.lower()
            for k in [
                "thank you for applying",
                "application received",
                "your application",
                "we've received",
                "successfully submitted",
                "application has been",
            ]
        )
        errors_found = any(k in body.lower() for k in ["can't be blank", "is required"])

        print(f"\nSuccess: {success}, Errors: {errors_found}")

        await ss(page, "48_final")
        await page.screenshot(path=FINAL_SCREENSHOT, full_page=True)
        print(f"Final screenshot: {FINAL_SCREENSHOT}")

        await browser.close()
        return success, errors_found, url


if __name__ == "__main__":
    s, e, u = asyncio.run(fill_application())
    print(f"\n=== DONE === success={s} errors={e} url={u}")
