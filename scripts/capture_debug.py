from playwright.sync_api import sync_playwright

def capture_debug():
    anchor_key = "sk-e6ad0b592b4b89a083a593ad9923b5d8"
    url = "https://job-boards.greenhouse.io/anthropic/jobs/5065894008"
    
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"wss://connect.anchorbrowser.io?apiKey={anchor_key}")
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(5000)
        page.screenshot(path="anthropic_debug.png", full_page=True)
        browser.close()

if __name__ == "__main__":
    capture_debug()
