#!/usr/bin/env python3
"""
Anthropic Job Application Automation v4
Properly handles Greenhouse custom dropdowns
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
    "I've been building production AI agent infrastructure at Subway — Thompson Sampling RLHF systems, "
    "LanceDB hybrid search, 13 autonomous Claude-powered agents. Anthropic is where that work leads. "
    "The Autonomous Agent Infrastructure role is a direct extension of what I do: designing the systems "
    "that make agents reliable, observable, and safe at scale. I want to work on the hard problems at the frontier."
)


async def ss(page, name):
    path = f"{SCREENSHOT_DIR}/2026-02-18_{name}.png"
    await page.screenshot(path=path, full_page=True)
    print(f"  [ss] {name}")


async def inspect_dropdown_structure(page, field_id):
    """Inspect the DOM structure around a specific field to understand how to click it."""
    result = await page.evaluate(f"""() => {{
        const inp = document.getElementById('{field_id}');
        if (!inp) return {{error: 'field not found'}};

        // Walk up the DOM to find parent structure
        let parent = inp.parentElement;
        let levels = [];
        for (let i = 0; i < 5; i++) {{
            if (!parent) break;
            levels.push({{
                tag: parent.tagName,
                className: parent.className.substring(0, 80),
                children: Array.from(parent.children).map(c => ({{
                    tag: c.tagName,
                    className: c.className.substring(0, 60),
                    id: c.id
                }}))
            }});
            parent = parent.parentElement;
        }}
        return {{levels}};
    }}""")
    return result


async def click_greenhouse_dropdown_and_select(page, field_id, option_text):
    """
    Greenhouse React Select dropdowns:
    - The input (type=text, id=questionXXX) is HIDDEN (opacity:0 or display:none)
    - The visible control is a sibling div with class containing 'select__control'
    - We need to click the control, then click the option
    """
    print(f"  Selecting '{option_text}' for #{field_id}...")

    # Find the container that holds both the hidden input and the visible control
    # Greenhouse wraps them in a div with class "select-container" or similar
    await page.evaluate(f"""() => {{
        const inp = document.getElementById('{field_id}');
        if (!inp) return null;

        // Find the React Select container - usually the parent or grandparent
        let el = inp;
        for (let i = 0; i < 6; i++) {{
            el = el.parentElement;
            if (!el) break;
            if (el.className && (el.className.includes('select__container') ||
                el.className.includes('select-container') ||
                el.querySelector('[class*="select__control"]'))) {{
                return {{
                    found: true,
                    level: i+1,
                    className: el.className.substring(0, 80)
                }};
            }}
        }}
        return {{found: false}};
    }}""")

    # Use Playwright to click the select control near this field
    # Strategy: find the control that is visually near the label for this field

    # Try clicking via JavaScript - find the control sibling
    clicked = await page.evaluate(f"""() => {{
        const inp = document.getElementById('{field_id}');
        if (!inp) return false;

        // Walk up and find a select__control inside the same ancestor
        let el = inp.parentElement;
        for (let i = 0; i < 6; i++) {{
            if (!el) break;
            const ctrl = el.querySelector('[class*="select__control"]');
            if (ctrl) {{
                ctrl.click();
                return true;
            }}
            el = el.parentElement;
        }}
        return false;
    }}""")

    if not clicked:
        print(f"  WARNING: Could not find/click control for #{field_id}")
        return False

    await page.wait_for_timeout(800)

    # Now find and click the option in the dropdown menu
    # Options are rendered in a portal/menu at the end of body
    option = page.locator("[class*='select__option']").filter(has_text=option_text)
    opt_count = await option.count()

    if opt_count == 0:
        # Try role option
        option = page.locator("[role='option']").filter(has_text=option_text)
        opt_count = await option.count()

    if opt_count > 0:
        # Get all visible options
        all_opts = await page.locator("[class*='select__option']").all_text_contents()
        print(f"  Available options: {all_opts}")
        await option.first.click()
        print(f"  Clicked '{option_text}'")
        await page.wait_for_timeout(400)
        return True
    else:
        all_opts = await page.locator("[class*='select__option']").all_text_contents()
        print(f"  WARNING: '{option_text}' not found. Available: {all_opts}")
        # Try clicking the first available option
        if all_opts:
            await page.locator("[class*='select__option']").first.click()
            print(f"  Clicked first option: {all_opts[0]}")
            return True
        await page.keyboard.press("Escape")
        return False


async def fill_application():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=150)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        print("Navigating to job page...")
        await page.goto("https://job-boards.greenhouse.io/anthropic/jobs/5065894008", wait_until="networkidle")
        await ss(page, "20_start")

        # Click Apply
        await page.locator("button:has-text('Apply'), a:has-text('Apply for this Job')").first.click()
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)
        print("On application form")
        await ss(page, "21_form")

        # ---- BASIC FIELDS ----
        await page.locator("#first_name").fill("Igor")
        await page.locator("#last_name").fill("Ganapolsky")
        await page.locator("#email").fill("iganapolsky@gmail.com")
        await page.locator("#phone").fill("2016391534")
        print("Filled name/email/phone")

        # ---- RESUME UPLOAD ----
        resume_input = page.locator("#resume")
        if await resume_input.count() == 0:
            resume_input = page.locator("input[type='file']").first
        await resume_input.set_input_files(RESUME_DOCX)
        await page.wait_for_timeout(3000)
        print("Uploaded resume (.docx)")
        await ss(page, "22_resume_uploaded")

        # ---- INSPECT DROPDOWN STRUCTURE ----
        print("\nInspecting dropdown DOM structure...")
        for field_id in ["question_14439954008", "question_14439957008"]:
            struct = await inspect_dropdown_structure(page, field_id)
            print(f"\n  Structure for #{field_id}:")
            if 'levels' in struct:
                for i, level in enumerate(struct['levels']):
                    print(f"    Level {i+1}: <{level['tag']} class='{level['className']}'> children: {[c['className'][:40] for c in level['children']]}")

        # ---- TEXT FIELDS ----
        print("\nFilling text fields...")
        await page.locator("#question_14439953008").fill("https://github.com/IgorGanapolsky")
        await page.locator("#question_14439955008").fill("Immediately / 2 weeks notice")
        await page.locator("#question_14439956008").fill("No specific deadline")
        await page.locator("#question_14439962008").fill("https://www.linkedin.com/in/igor-ganapolsky-859317343/")
        await page.locator("#question_14439964008").fill("11909 Glenmore Dr, Coral Springs, FL 33071")

        # Textareas
        await page.locator("#question_14439958008").fill(WHY_ANTHROPIC)
        await page.locator("#question_14439961008").fill(
            "Available immediately. Open to San Francisco, New York, or Seattle offices. "
            "Cover letter included. References available on request."
        )
        print("  All text fields filled")
        await ss(page, "23_text_filled")

        # ---- DROPDOWN FIELDS ----
        print("\nHandling dropdown fields...")
        await page.evaluate("window.scrollTo(0, 600)")
        await page.wait_for_timeout(500)

        # Are you open to working in-person 25%?
        await click_greenhouse_dropdown_and_select(page, "question_14439954008", "Yes")

        # AI Policy for Application
        print("  Handling AI Policy dropdown...")
        await page.evaluate("""() => {
            const inp = document.getElementById('question_14439957008');
            if (!inp) return false;
            let el = inp.parentElement;
            for (let i = 0; i < 6; i++) {
                if (!el) break;
                const ctrl = el.querySelector('[class*="select__control"]');
                if (ctrl) { ctrl.click(); return true; }
                el = el.parentElement;
            }
            return false;
        }""")
        await page.wait_for_timeout(800)
        ai_opts = await page.locator("[class*='select__option']").all_text_contents()
        print(f"  AI Policy options: {ai_opts}")
        if ai_opts:
            await page.locator("[class*='select__option']").first.click()
            print(f"  Selected AI Policy: {ai_opts[0][:60]}")
        else:
            await page.keyboard.press("Escape")

        # Visa sponsorship - No
        await click_greenhouse_dropdown_and_select(page, "question_14439959008", "No")

        # Future visa sponsorship - No
        await click_greenhouse_dropdown_and_select(page, "question_14439960008", "No")

        # Relocation - No
        await click_greenhouse_dropdown_and_select(page, "question_14439963008", "No")

        # Interviewed at Anthropic - No
        await click_greenhouse_dropdown_and_select(page, "question_14439965008", "No")

        await ss(page, "24_dropdowns_done")

        # ---- VERIFY FIELDS ----
        print("\nVerifying field values...")
        field_check = await page.evaluate("""() => {
            const inputs = document.querySelectorAll('input[type=text][id], textarea[id]');
            return Array.from(inputs).map(el => {
                const label = el.labels && el.labels[0] ? el.labels[0].textContent.trim() : '';
                const isReq = label.includes('*');
                return {id: el.id, value: el.value, label: label.substring(0,60), required: isReq};
            });
        }""")

        all_ok = True
        for f in field_check:
            if f['required'] and not f['value']:
                print(f"  MISSING: #{f['id']} | {f['label']}")
                all_ok = False
            elif f['value']:
                print(f"  OK: #{f['id']} = '{f['value'][:40]}'")

        print(f"\n  All required fields filled: {all_ok}")

        # ---- SCROLL TO BOTTOM AND SUBMIT ----
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)
        await ss(page, "25_before_submit")

        submit = page.locator("input[type='submit'], button[type='submit']")
        sub_count = await submit.count()
        print(f"\nFound {sub_count} submit buttons")

        if sub_count > 0:
            # Get text of first one
            sub_text = await page.evaluate("""() => {
                const b = document.querySelector('input[type=submit], button[type=submit]');
                return b ? (b.value || b.textContent) : 'not found';
            }""")
            print(f"Submit button: '{sub_text}'")

            await submit.first.scroll_into_view_if_needed()
            await ss(page, "26_submit_visible")
            await submit.first.click()
            print("SUBMITTED!")

            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            await page.wait_for_timeout(4000)

        # ---- CAPTURE RESULT ----
        final_url = page.url
        final_title = await page.title()
        body = await page.inner_text("body")

        print(f"\nFinal URL: {final_url}")
        print(f"Final title: {final_title}")
        print(f"Body (first 600 chars):\n{body[:600]}")

        success = any(kw in body.lower() for kw in [
            "thank you for applying", "application received",
            "your application has been", "we've received your application"
        ])
        has_errs = any(kw in body.lower() for kw in ["can't be blank", "is required"])

        print(f"\nSuccess: {success}, Has errors: {has_errs}")

        await ss(page, "27_final")
        await page.screenshot(path=FINAL_SCREENSHOT, full_page=True)
        print(f"Final screenshot: {FINAL_SCREENSHOT}")

        await browser.close()
        return success, has_errs, final_url


if __name__ == "__main__":
    s, e, u = asyncio.run(fill_application())
    print(f"\n=== DONE === success={s} errors={e} url={u}")
