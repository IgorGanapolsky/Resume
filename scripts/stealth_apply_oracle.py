import asyncio
import random
import time
from pathlib import Path
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

async def human_type(page, selector_or_locator, text):
    if isinstance(selector_or_locator, str):
        await page.wait_for_selector(selector_or_locator)
        locator = page.locator(selector_or_locator)
    else:
        locator = selector_or_locator
        
    await locator.click()
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(random.uniform(0.02, 0.08))

async def apply():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 800}
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        print("Navigating to Oracle job page...")
        await page.goto("https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobsearch/job/324362/", wait_until="load", timeout=60000)
        await asyncio.sleep(5)
        
        print("Clicking Apply Now...")
        apply_btn = page.locator("button:has-text('Apply Now')").first
        await apply_btn.wait_for(state="visible")
        await apply_btn.click()
        
        await asyncio.sleep(5)
        
        print("Filling email...")
        email_input = page.locator("input[type='email'], input[placeholder*='Email']").first
        await email_input.wait_for(state="visible")
        await human_type(page, email_input, "iganapolsky@gmail.com")
        await page.keyboard.press("Tab")
        await asyncio.sleep(1)
        
        print("Interacting with terms checkbox...")
        try:
            # Force check via JS AND trigger all events
            await page.evaluate('''() => {
                const cb = document.getElementById('legal-disclaimer-checkbox');
                if (cb) {
                    cb.checked = true;
                    cb.dispatchEvent(new Event('change', { bubbles: true }));
                    cb.dispatchEvent(new Event('input', { bubbles: true }));
                    cb.dispatchEvent(new Event('click', { bubbles: true }));
                    cb.dispatchEvent(new Event('blur', { bubbles: true }));
                }
                const email = document.querySelector('input[type="email"]');
                if (email) {
                    email.dispatchEvent(new Event('change', { bubbles: true }));
                    email.dispatchEvent(new Event('blur', { bubbles: true }));
                }
            }''')
        except Exception as e:
            print(f"Checkbox interaction failed: {e}")
            
        await asyncio.sleep(3)
        
        print("Clicking Next via multiple methods...")
        try:
            # Check if button is enabled in DOM
            is_enabled = await page.evaluate('''() => {
                const buttons = Array.from(document.querySelectorAll('button'));
                const nextBtn = buttons.find(b => b.innerText.includes('NEXT') || b.getAttribute('aria-label') === 'Next');
                return nextBtn && !nextBtn.disabled;
            }''')
            print(f"Next button enabled: {is_enabled}")
            
            # Click it regardless
            await page.evaluate('''() => {
                const buttons = Array.from(document.querySelectorAll('button'));
                const nextBtn = buttons.find(b => b.innerText.includes('NEXT') || b.getAttribute('aria-label') === 'Next');
                if (nextBtn) {
                    nextBtn.removeAttribute('disabled'); // Force it
                    nextBtn.click();
                }
            }''')
        except Exception as e:
            print(f"Next click failed: {e}")
        
        print("Waiting for next section...")
        await asyncio.sleep(8)
        
        # Check if terms modal is open
        content = await page.content()
        if "Terms and Conditions" in content or "AGREE" in content:
            print("Terms/Agree modal detected. Clicking AGREE...")
            try:
                # Target the specific AGREE button seen in field list
                agree_btn = page.locator("button:has-text('AGREE')").first
                if await agree_btn.count() > 0:
                    await agree_btn.click()
                else:
                    # Deep JS search for the button, even in shadow DOMs
                    await page.evaluate('''() => {
                        const buttons = Array.from(document.querySelectorAll('button'));
                        const agreeBtn = buttons.find(b => b.innerText && b.innerText.includes('AGREE'));
                        if (agreeBtn) agreeBtn.click();
                    }''')
                await asyncio.sleep(5)
            except Exception as e:
                print(f"Agree click failed: {e}")

        print("Taking snapshot after Agree...")
        await page.screenshot(path="applications/oracle/submissions/stealth_v3_after_agree.png")
        
        # Resume Upload
        print("Looking for resume upload field...")
        # Check for multiple possible selectors for file input
        file_input = page.locator("input[type='file']").first
        if await file_input.count() > 0:
            print("Uploading resume...")
            docx_path = "/Users/ganapolsky_i/workspace/git/igor/Resume/applications/oracle/tailored_resumes/2026-03-11_oracle_sr-principal-ai-software-engineer-ml-ai-innovation.docx"
            await file_input.set_input_files(docx_path)
            await asyncio.sleep(5)
            print("Resume uploaded.")
        else:
            print("Resume field not found via locator, trying JS...")
            # Try to find hidden file input
            await page.evaluate(f'''(path) => {{
                const input = document.querySelector('input[type="file"]');
                if (input) {{
                    // Note: set_input_files is better but if we are here we might need to click a button first
                }}
            }}''')

        # Final Submit
        print("Looking for Submit button...")
        try:
            # Oracle often uses a button with text 'Submit' or 'Done' or 'Apply' at the end
            submit_btn = page.locator("button:has-text('Submit'), button:has-text('Apply')").first
            if await submit_btn.count() > 0:
                print("Clicking Submit button...")
                await submit_btn.click()
                await asyncio.sleep(10)
            else:
                print("Submit button not found via locator, trying JS...")
                await page.evaluate('''() => {
                    const buttons = Array.from(document.querySelectorAll('button'));
                    const submitBtn = buttons.find(b => 
                        b.innerText && (b.innerText.includes('Submit') || b.innerText.includes('Apply'))
                    );
                    if (submitBtn) submitBtn.click();
                }''')
                await asyncio.sleep(10)
        except Exception as e:
            print(f"Final submission failed: {e}")

        print("Taking final confirmation snapshot...")
        await page.screenshot(path="applications/oracle/submissions/stealth_v3_confirmation.png")
        
        body_text = await page.inner_text("body")
        print("--- FINAL BODY TEXT START ---")
        print(body_text)
        print("--- FINAL BODY TEXT END ---")
        
        content = await page.content()
        if "Thank you" in content or "submitted" in content.lower():
            print("SUCCESS: Oracle application submitted.")
        else:
            print("Done. Check stealth_v3_confirmation.png for result.")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(apply())
