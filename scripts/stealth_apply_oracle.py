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
        await page.goto("https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobsearch/job/324362/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_load_state("networkidle")
        
        print("Clicking Apply Now...")
        apply_btn = page.get_by_role("button", name="Apply Now").first
        await apply_btn.wait_for()
        await apply_btn.click()
        
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(5)
        
        print("Filling email...")
        email_input = page.locator("input[type='email'], input[placeholder*='Email']").first
        await email_input.wait_for()
        await human_type(page, email_input, "iganapolsky@gmail.com")
        
        print("Interacting with terms checkbox...")
        try:
            # Oracle hidden checkbox hack: click the label which is actually visible
            label = page.locator("label[for='legal-disclaimer-checkbox']")
            await label.scroll_into_view_if_needed()
            box = await label.bounding_box()
            if box:
                # Click center of label
                await page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
            else:
                # Fallback to direct JS check and click
                await page.evaluate('''
                    const cb = document.getElementById('legal-disclaimer-checkbox');
                    cb.checked = true;
                    cb.dispatchEvent(new Event('change', { bubbles: true }));
                    cb.dispatchEvent(new Event('click', { bubbles: true }));
                ''')
        except Exception as e:
            print(f"Checkbox interaction failed: {e}")
            
        await asyncio.sleep(3)
        
        print("Clicking Next via JS to bypass overlay...")
        try:
            # Directly trigger the click via JS since standard click is intercepted
            await page.evaluate('''
                const buttons = Array.from(document.querySelectorAll('button'));
                const nextBtn = buttons.find(b => b.innerText.includes('Next') || b.getAttribute('aria-label') === 'Next');
                if (nextBtn) {
                    nextBtn.click();
                } else {
                    throw new Error("Next button not found in JS");
                }
            ''')
        except Exception as e:
            print(f"JS Click failed: {e}")
            # Fallback to standard but with force
            await page.locator("button:has-text('Next')").first.click(force=True)
        
        print("Waiting for next section (networkidle)...")
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except:
            print("Network idle timed out, proceeding anyway...")
            
        await asyncio.sleep(5)
        
        # Check if terms modal is open
        content = await page.content()
        if "Terms and Conditions" in content:
            print("Terms modal detected. Deep searching for Accept button...")
            try:
                # Deep JS search for the button, even in shadow DOMs
                await page.evaluate('''
                    function findAndClickAccept() {
                        const allElements = document.querySelectorAll('*');
                        for (let el of allElements) {
                            if (el.shadowRoot) {
                                const btn = el.shadowRoot.querySelector('button');
                                if (btn && btn.textContent.includes('Accept')) {
                                    btn.click();
                                    return true;
                                }
                            }
                            if (el.tagName === 'BUTTON' && el.textContent.includes('Accept')) {
                                el.click();
                                return true;
                            }
                        }
                        // Also try common Oracle Recruiting classes/ids
                        const oracleAccept = document.querySelector('button[title*="Accept"], .oj-button-button');
                        if (oracleAccept && oracleAccept.textContent.includes('Accept')) {
                            oracleAccept.click();
                            return true;
                        }
                        return false;
                    }
                    findAndClickAccept();
                ''')
                await asyncio.sleep(5)
            except Exception as e:
                print(f"Deep Accept click failed: {e}")

        print("Final form state reached. Taking snapshot...")
        await page.screenshot(path="applications/oracle/submissions/stealth_v2_final_form.png")
        
        # Output current page text to help identify fields
        body_text = await page.inner_text("body")
        print("--- BODY TEXT START ---")
        print(body_text[:2000])
        print("--- BODY TEXT END ---")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(apply())
