from playwright.sync_api import sync_playwright
import os

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(f"file://{os.path.abspath('resumes/Igor_Ganapolsky_Open_Source_Maintainer_AI.html')}")
    page.pdf(path="applications/anthropic/tailored_resumes/Igor_Ganapolsky_Open_Source_Maintainer_AI.pdf", format="A4")
    browser.close()
