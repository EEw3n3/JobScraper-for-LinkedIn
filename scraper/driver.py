import os

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


def setup_driver(headless=False):
    options = Options()
    options.add_argument("--window-size=1440,1000")
    if headless or os.environ.get("JOBPARSER_HEADLESS", "").strip().lower() in ("1", "true", "yes", "on"):
        options.add_argument("--headless=new")
    if os.environ.get("CHROME_BINARY"):
        options.binary_location = os.environ["CHROME_BINARY"]
    if os.environ.get("CHROME_USER_DATA_DIR"):
        options.add_argument(f"--user-data-dir={os.environ['CHROME_USER_DATA_DIR']}")
    if os.environ.get("JOBPARSER_CHROME_NO_SANDBOX", "").strip().lower() in ("1", "true", "yes", "on"):
        options.add_argument("--no-sandbox")

    service = Service(os.environ.get("CHROMEDRIVER_PATH") or ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    try:
        driver.set_page_load_timeout(60)
        driver.set_script_timeout(30)
    except BaseException:
        try:
            driver.quit()
        except Exception:
            pass  # Preserve the configuration error if browser cleanup also fails.
        raise
    return driver
