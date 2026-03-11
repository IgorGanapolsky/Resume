import asyncio
import random
from playwright.async_api import async_playwright
from playwright_stealth import Stealth


async def human_type(page, selector, text):
    await page.wait_for_selector(selector)
    await page.click(selector)
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(random.uniform(0.02, 0.10))


async def human_mouse_move(page):
    viewport = page.viewport_size or {"width": 1280, "height": 800}
    for _ in range(5):
        x = random.randint(0, viewport["width"])
        y = random.randint(0, viewport["height"])
        await page.mouse.move(x, y, steps=10)
        await asyncio.sleep(random.uniform(0.1, 0.5))


async def apply():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            device_scale_factor=2,
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        print("Building trust: Navigating to Perplexity home page...")
        await page.goto("https://www.perplexity.ai/", wait_until="load")
        await asyncio.sleep(random.uniform(3, 6))

        print("Navigating to Perplexity jobs board...")
        await page.goto("https://jobs.ashbyhq.com/perplexity", wait_until="load")
        await asyncio.sleep(random.uniform(2, 4))

        print("Navigating to specific job...")
        await page.goto(
            "https://jobs.ashbyhq.com/perplexity/bc1a6878-8de9-48c2-a791-95b2f8f27261/application",
            wait_until="networkidle",
        )

        await human_mouse_move(page)

        print("Filling personal info...")
        await human_type(
            page,
            'div[role="textbox"]:has-text("Name"), textbox[name*="name"], [placeholder*="Type here"]',
            "Igor Ganapolsky",
        )
        await asyncio.sleep(random.uniform(1, 2))
        await human_type(
            page,
            'div[role="textbox"]:has-text("Email"), textbox[name*="email"], [placeholder*="hello@example.com"]',
            "iganapolsky@gmail.com",
        )
        await asyncio.sleep(random.uniform(1, 2))
        await human_type(
            page,
            'div[role="textbox"]:has-text("Phone"), [placeholder*="1-415-555-1234"]',
            "2016391534",
        )

        # Location
        await human_type(
            page, "input[placeholder*='Start typing']", "Coral Springs, FL"
        )
        await asyncio.sleep(2)
        await page.keyboard.press("Enter")

        # Sponsorship: No
        await page.click("button:has-text('No')")

        # In-office: Yes
        # Note: We need to find which 'Yes' belongs to which question.
        # Based on snapshot, e14 is Sponsorship Yes, e15 is Sponsorship No
        # e16 is In-office Yes, e17 is In-office No
        await page.click("button:has-text('Yes') >> nth=1")

        print("Filling custom questions...")
        why = "I'm excited about the Comet ecosystem and Perplexity Computer—building generalized frontier intelligence that faithfully actualizes user intent. The challenge of training action/decision models that navigate the digital world based on multimodal states is exactly where I want to be. I'm particularly interested in designing optimal data representations for agent-environment interaction and scaling these capabilities for millions of users while maintaining a high craft bar."
        how_ai = "I use AI (Claude, Windsurf, Cursor) for everything from architecting multi-agent systems to automating high-volume job submissions. Underappreciated benefit: 'Rubber-ducking' complex system designs where the AI can simulate edge cases I haven't considered. Pain point: Context drift in long-running autonomous sessions and the 'wall' of advanced bot detection (like Ashby/Cloudflare) which requires sophisticated stealth bypasses—ironically, the very thing I'm building right now."
        shared_url = "https://www.perplexity.ai/search/explain-why-igor-ganapolsky-is-a-strong-candidate-for-perplexity-ai-agents-team-vBVSJMaRSxacql0MB11BWg"

        await human_type(page, "textarea[name*='782bb650']", why)
        await human_type(page, "textarea[name*='07734bac']", how_ai)
        await human_type(page, "input[name*='77deaf2d']", shared_url)

        print("Uploading resume...")
        resume_path = "/Users/ganapolsky_i/workspace/git/igor/Resume/applications/perplexity/tailored_resumes/2026-03-11_perplexity_ai-software-engineer-agents.docx"
        file_inputs = await page.query_selector_all("input[type='file']")
        if len(file_inputs) >= 2:
            await file_inputs[1].set_input_files(resume_path)
        else:
            await page.set_input_files("input[type='file']", resume_path)

        await asyncio.sleep(random.uniform(5, 8))

        # Consent Checkbox
        print("Checking consent checkbox...")
        try:
            await page.click("text=I agree", timeout=5000)
        except Exception:
            await page.click('div:has-text("I agree")', force=True)

        print("Taking pre-submit screenshot...")
        await page.screenshot(
            path="applications/perplexity/submissions/stealth_pre_submit.png"
        )

        print("Submitting...")
        submit_btn = page.get_by_role("button", name="Submit Application").first
        await submit_btn.click()

        print("Waiting for response...")
        for i in range(20):
            await asyncio.sleep(1)
            content = await page.content()
            if "Thank you" in content or "submitted" in content.lower():
                print("SUCCESS: Application submitted.")
                break
            if "spam" in content.lower():
                print("FAILURE: Still flagged as spam.")
                break

        await page.screenshot(
            path="applications/perplexity/submissions/stealth_post_submit.png"
        )
        await browser.close()


if __name__ == "__main__":
    asyncio.run(apply())
