import random
import os
import threading
import time

from selenium.common.exceptions import (
    InvalidSessionIdException,
    NoSuchElementException,
    NoSuchWindowException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from utils.filters import detect_grade
from utils.validation import description_status


RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BASE_DELAY = 30
MIN_NAVIGATION_INTERVAL = 1.0

_rate_limit_lock = threading.Lock()
_rate_limit_until = 0.0
_navigation_lock = threading.Lock()
_next_navigation_at = 0.0
stop_event = threading.Event()


def is_access_blocked(driver):
    url = (driver.current_url or "").lower()
    title = (driver.title or "").lower()
    return (
        any(marker in url for marker in ("authwall", "auth_wall", "/login", "/signin", "checkpoint"))
        or any(marker in title for marker in ("sign in", "log in", "security verification", "verify you are human"))
    )


def wait_for_manual_access(driver):
    """Allow an interactive noVNC user to complete login or verification."""
    if not is_access_blocked(driver):
        return True
    interactive = os.environ.get("JOBPARSER_INTERACTIVE", "").strip().lower() in ("1", "true", "yes", "on")
    if not interactive:
        return False
    try:
        timeout = min(1800, max(1, int(os.environ.get("JOBPARSER_INTERACTIVE_WAIT", "300"))))
    except ValueError:
        timeout = 300
    print(f"   -> Browser needs manual action. Use noVNC; waiting up to {timeout} seconds...", flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        interruptible_wait(min(2, deadline - time.monotonic()))
        try:
            if not is_access_blocked(driver):
                print("   -> Manual browser action completed; continuing.", flush=True)
                return True
        except Exception as error:
            raise_if_fatal_session_error(error)
    return False


def reset_navigation_state():
    """Reset shared state before starting a run, after old workers have exited."""
    global _rate_limit_until, _next_navigation_at
    with _navigation_lock, _rate_limit_lock:
        _rate_limit_until = 0.0
        _next_navigation_at = 0.0
        stop_event.clear()


def check_stop():
    if stop_event.is_set():
        raise RateLimitError("Scraping stopped by the shared stop event")


def interruptible_wait(seconds):
    if stop_event.wait(max(0, seconds)):
        check_stop()


class RateLimitError(RuntimeError):
    pass


class BrowserSessionError(WebDriverException):
    pass


class DescriptionError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Job description unavailable: {status}")


def raise_if_fatal_session_error(error):
    fatal_markers = (
        "invalid session id",
        "no such window",
        "chrome not reachable",
        "not connected to devtools",
        "target window already closed",
        "connection refused",
        "connection reset",
    )
    is_fatal = isinstance(
        error, (InvalidSessionIdException, NoSuchWindowException)
    ) or any(marker in str(error).lower() for marker in fatal_markers)

    if is_fatal:
        raise BrowserSessionError(str(error)) from error


def is_rate_limited(driver):
    page_parts = []

    check_stop()
    page_parts.extend((driver.current_url, driver.title))

    try:
        page_parts.append(driver.find_element(By.TAG_NAME, "body").text[:500])
    except NoSuchElementException:
        pass

    normalized_parts = [part.lower() for part in page_parts if part]
    markers = (
        "too many requests",
        "rate limit exceeded",
        "temporarily rate limited",
        "error 429",
        "http 429",
        "status=429",
    )
    return any(
        part.strip().startswith("429")
        or any(marker in part for marker in markers)
        for part in normalized_parts
    )


def wait_for_rate_limit_cooldown():
    while True:
        check_stop()
        with _rate_limit_lock:
            remaining = _rate_limit_until - time.monotonic()

        if remaining <= 0:
            return

        interruptible_wait(remaining)


def set_rate_limit_cooldown(retry_number):
    global _rate_limit_until

    delay = RATE_LIMIT_BASE_DELAY * (2 ** retry_number) + random.uniform(0, 10)
    with _rate_limit_lock:
        now = time.monotonic()
        _rate_limit_until = max(_rate_limit_until, now + delay)
        return _rate_limit_until - now


def wait_for_navigation_slot():
    global _next_navigation_at

    # Serialize only navigation starts; both browsers still parse pages in parallel.
    with _navigation_lock:
        wait_for_rate_limit_cooldown()
        remaining = _next_navigation_at - time.monotonic()
        if remaining > 0:
            interruptible_wait(remaining)

        wait_for_rate_limit_cooldown()
        check_stop()

        _next_navigation_at = (
            time.monotonic()
            + MIN_NAVIGATION_INTERVAL
            + random.uniform(0, 0.25)
        )


def load_job_page(driver, url):
    for retry_number in range(RATE_LIMIT_RETRIES + 1):
        wait_for_navigation_slot()
        try:
            driver.get(url)
            interruptible_wait(random.uniform(2, 4))
            limited = is_rate_limited(driver)
        except Exception as error:
            raise_if_fatal_session_error(error)
            raise

        if not limited:
            return

        cooldown = set_rate_limit_cooldown(retry_number)
        if retry_number == RATE_LIMIT_RETRIES:
            stop_event.set()
            raise RateLimitError(
                f"HTTP 429 persisted after {RATE_LIMIT_RETRIES + 1} attempts; "
                "all workers have been stopped"
            )

        print(
            f"   -> HTTP 429 detected. Both browsers will wait "
            f"about {cooldown:.0f} seconds before retrying."
        )


def extract_job_details(driver, job):
    load_job_page(driver, job["Link"])

    if not wait_for_manual_access(driver):
        raise DescriptionError("auth_required")

    description = "Could not extract"
    salary = "Not specified"

    try:
        if job["Source"] == "LinkedIn":

            try:
                wait_for_navigation_slot()
                driver.execute_script("""
                    var btn = document.querySelector('.show-more-less-html__button');
                    if (btn) { btn.click(); }

                """)
                interruptible_wait(1)
                if is_rate_limited(driver):
                    set_rate_limit_cooldown(0)
                    load_job_page(driver, driver.current_url)
            except Exception as error:
                raise_if_fatal_session_error(error)
                raise


            try:
                description = driver.execute_script("""
                    var el = document.querySelector('.show-more-less-html__markup, .jobs-description-content__text');
                    return el ? el.textContent : null;
                """)

                if not description or len(description.strip()) < 10:
                    desc_el = driver.find_element(By.CSS_SELECTOR,
                                                  ".show-more-less-html__markup, .jobs-description-content__text")
                    description = desc_el.text
            except NoSuchElementException:
                if "auth_wall" in driver.current_url or "sign in" in driver.title.lower():
                    description = "LinkedIn requires authorization to view (Auth wall hard block)"

            try:
                salary = driver.execute_script("""
                    var el = document.querySelector('.compensation__salary-tooltip-text');
                    return el ? el.textContent : 'Not specified';
                """)
                if salary == 'Not specified' or not salary:
                    sal_el = driver.find_element(By.CSS_SELECTOR, ".compensation__salary-tooltip-text")
                    salary = sal_el.text
            except NoSuchElementException:
                pass

        else:
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            iframe_found = False
            for iframe in iframes:
                if "cv.lv" in (iframe.get_attribute("src") or "") or "description" in (iframe.get_attribute("id") or "").lower():
                    driver.switch_to.frame(iframe)
                    try:
                        try:
                            description = driver.find_element(By.TAG_NAME, "body").text
                        except NoSuchElementException:
                            description = None
                    finally:
                        driver.switch_to.default_content()
                    iframe_found = True
                    break

            if not iframe_found:
                try:
                    desc_el = driver.find_element(By.CSS_SELECTOR, ".vacancy-description, .job-description, article")
                    description = desc_el.text
                except NoSuchElementException:
                    description = None

            try:
                sal_elements = driver.find_elements(By.XPATH, "//*[contains(text(), '€')]")
                for el in sal_elements:
                    text = el.text.strip()
                    if len(text) < 30 and any(char.isdigit() for char in text):
                        salary = text
                        break
            except Exception as error:
                raise_if_fatal_session_error(error)
                raise

    except Exception as error:
        raise_if_fatal_session_error(error)
        raise

    check_stop()
    status = description_status(description)
    if status != "parsed":
        raise DescriptionError(status)

    if description:
        job["Description"] = description.replace("\n", " | ").strip()
    else:
        job["Description"] = "Could not extract"

    if salary:
        job["Salary"] = salary.strip()
    else:
        job["Salary"] = "Not specified"

    job["Grade"] = detect_grade(job["Title"], job["Description"])

    return job
