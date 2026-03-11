from playwright.sync_api import sync_playwright
import os


def final_submit():
    anchor_key = "sk-e6ad0b592b4b89a083a593ad9923b5d8"
    url = "https://job-boards.greenhouse.io/anthropic/jobs/5065894008"
    resume_path = os.path.abspath(
        "applications/anthropic/tailored_resumes/Igor_Ganapolsky_Open_Source_Maintainer_AI.pdf"
    )

    print(f"Starting FINAL Anchor session for: {url}")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(
            f"wss://connect.anchorbrowser.io?apiKey={anchor_key}"
        )
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")

        print("Filling First/Last Name...")
        page.fill("#first_name", "Igor")
        page.fill("#last_name", "Ganapolsky")
        page.fill("#email", "iganapolsky@gmail.com")
        page.fill("#phone", "2016391534")

        print("Uploading Resume...")
        page.set_input_files("input[type='file']", resume_path)

        print("Filling 'Why Anthropic'...")
        why_text = (
            "As the core maintainer of 'igor'—an open-source RLHF stack for AI coding assistants—I've spent the last year "
            "obsessed with the same problems Anthropic is solving: reliability, agentic memory, and safe tool execution. "
            "My work on Thompson Sampling-based feedback loops and Hive-based self-healing guardrails aligns directly "
            "with your mission of building steerable, reliable AI."
        )
        # Find the textarea for "Why Anthropic" - usually has a label with 'Why Anthropic'
        page.get_by_label("Why Anthropic?", exact=False).fill(why_text)

        print("Handling Radio Buttons/Selects...")
        # In-person policy
        page.get_by_role("combobox").filter(has_text="Select...").nth(0).click()
        page.get_by_role("option", name="Yes").click()

        # AI Policy
        page.get_by_role("combobox").filter(has_text="Select...").nth(1).click()
        page.get_by_role("option", name="Yes").click()

        # Visa Sponsorship
        page.get_by_role("combobox").filter(has_text="Select...").nth(2).click()
        page.get_by_role("option", name="No").click()

        # Future Visa
        page.get_by_role("combobox").filter(has_text="Select...").nth(3).click()
        page.get_by_role("option", name="No").click()

        # Relocation
        page.get_by_role("combobox").filter(has_text="Select...").nth(4).click()
        page.get_by_role("option", name="No").click()

        # Interviewed before
        page.get_by_role("combobox").filter(has_text="Select...").nth(5).click()
        page.get_by_role("option", name="No").click()

        print("Finalizing submission...")
        page.screenshot(path="anthropic_pre_submit.png")

        # Click Submit
        page.click("#submit_app")

        print("Waiting for confirmation text...")
        page.wait_for_timeout(10000)
        page.screenshot(path="anthropic_post_submit.png")

        if "Thank you" in page.content() or "received" in page.content():
            print("SUCCESS: Application submitted to Anthropic!")
        else:
            print(
                "WARNING: Submit clicked, but confirmation text not seen. Check anthropic_post_submit.png"
            )

        browser.close()


if __name__ == "__main__":
    final_submit()
