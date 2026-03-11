import os
from playwright.sync_api import sync_playwright


def capture_debug():
    anchor_key = os.environ.get("ANCHOR_BROWSER_API_KEY", "")
    url = "https://job-boards.greenhouse.io/anthropic/jobs/5065894008"

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(
            f"wss://connect.anchorbrowser.io?apiKey={anchor_key}"
        )
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(5000)
        page.screenshot(path="anthropic_debug.png", full_page=True)
        browser.close()


if __name__ == "__main__":
    capture_debug()
