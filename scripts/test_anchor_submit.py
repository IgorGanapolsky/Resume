import os
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def run_targeted_submit():
    anchor_key = os.environ.get("ANCHOR_BROWSER_API_KEY", "")
    url = "https://job-boards.greenhouse.io/anthropic/jobs/5065894008"
    resume_path = os.path.abspath(
        "applications/anthropic/tailored_resumes/Igor_Ganapolsky_Open_Source_Maintainer_AI.pdf"
    )

    print(f"Starting Anchor Browser session for: {url}")

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(
            f"wss://connect.anchorbrowser.io?apiKey={anchor_key}"
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        print("Navigating to Anthropic job board...")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        print("Filling basic info...")
        page.fill("input[name='job_application[first_name]']", "Igor")
        page.fill("input[name='job_application[last_name]']", "Ganapolsky")
        page.fill("input[name='job_application[email]']", "iganapolsky@gmail.com")
        page.fill("input[name='job_application[phone]']", "2016391534")

        print("Uploading maintainer resume...")
        page.set_input_files("input[type='file']", resume_path)

        print("Answering custom questions...")
        # Why Anthropic?
        why_text = (
            "As the core maintainer of 'igor'—an open-source RLHF stack for AI coding assistants—I've spent the last year "
            "obsessed with the same problems Anthropic is solving: reliability, agentic memory, and safe tool execution. "
            "My work on Thompson Sampling-based feedback loops and Hive-based self-healing guardrails aligns directly "
            "with your mission."
        )
        page.fill(
            "textarea[id*='question_']", why_text
        )  # Using partial ID for Greenhouse custom fields

        # Click "Yes" for In-Person
        try:
            page.click("label:has-text('Yes')")  # Heuristic click for the policy radios
        except:
            pass

        print("Taking final snapshot before submit...")
        page.screenshot(path="anthropic_final_check.png")

        print("Submitting application...")
        # page.click("#submit_app") # I'll keep this commented for the very first log to confirm it reached here
        print("Application ready for final submission.")

        browser.close()


if __name__ == "__main__":
    run_targeted_submit()
