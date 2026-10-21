"""Chrome sessions for applying: the applicant's saved LinkedIn profile, and a plain one for PDFs."""

import os
import time
from pathlib import Path

LOGIN_URL = "https://www.linkedin.com/login"


def _chrome(options):
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    if os.environ.get("CHROME_BINARY"):
        options.binary_location = os.environ["CHROME_BINARY"]
    service = Service(os.environ.get("CHROMEDRIVER_PATH") or ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(60)
    return driver


def profile_driver(profile_dir, headless=False):
    """Chrome with its own user-data folder, so the LinkedIn sign-in is remembered.

    The password is typed by the applicant in this window once and never stored by the program.
    """
    from selenium.webdriver.chrome.options import Options

    options = Options()
    options.add_argument(f"--user-data-dir={Path(profile_dir).resolve()}")
    options.add_argument("--window-size=1440,1000")
    if headless:
        options.add_argument("--headless=new")
    return _chrome(options)


def pdf_driver():
    from selenium.webdriver.chrome.options import Options

    options = Options()
    options.add_argument("--headless=new")
    return _chrome(options)


def is_logged_in(driver):
    url = (driver.current_url or "").lower()
    if any(marker in url for marker in ("/login", "/checkpoint", "/authwall", "/uas/", "/signup")):
        return False
    return any(cookie.get("name") == "li_at" for cookie in driver.get_cookies())


def wait_for_login(driver, timeout=600, say=print):
    driver.get(LOGIN_URL)
    say("Sign in to LinkedIn in the Chrome window that just opened. "
        f"It closes by itself once you are signed in (waiting up to {timeout // 60} minutes).")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_logged_in(driver):
            return True
        time.sleep(2)
    return False
