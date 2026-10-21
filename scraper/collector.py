import re
from datetime import datetime, timedelta

from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException, WebDriverException
from selenium.webdriver.common.by import By

from config.settings import MAX_PAGES, STOP_LINK_TEXTS
from utils.filters import is_unsuitable_job
from scraper.parser import (
    DescriptionError,
    check_stop,
    interruptible_wait,
    is_rate_limited,
    load_job_page,
    raise_if_fatal_session_error,
    set_rate_limit_cooldown,
    wait_for_manual_access,
    wait_for_navigation_slot,
)


def calculate_absolute_time(relative_str):
    """Convert relative posting times to local timestamps."""
    if not relative_str or relative_str == "Not specified":
        return relative_str
    relative_str = relative_str.lower()
    now = datetime.now()
    match = re.search(r"\d+", relative_str)
    if not match:
        if "now" in relative_str:
            return now.strftime("%Y-%m-%d %H:%M:%S")
        return relative_str
    value = int(match.group())
    for markers, seconds in (
        (("minute", "min"), 60), (("hour",), 3600),
        (("day",), 86400), (("week",), 604800),
        (("month",), 2592000), (("year",), 31536000),
    ):
        if any(marker in relative_str for marker in markers):
            return (now - timedelta(seconds=value * seconds)).strftime("%Y-%m-%d %H:%M:%S")
    return relative_str


def collect_links(driver, site, on_job=None, max_pages=None, limit=None, exclude_links=None):
    """Persist each unique visible job before requesting more results.

    Callback failures propagate unchanged; max_pages bounds scroll rounds.
    """
    jobs = []
    seen = set()
    excluded = set(exclude_links or ())
    in_callback = False
    pages = MAX_PAGES if max_pages is None else max_pages
    check_stop()
    if limit is not None and limit <= 0:
        return jobs

    try:
        load_job_page(driver, site["url"])
        for page in range(max(0, pages) + 1):
            previous_count = len(jobs)
            check_stop()
            if is_rate_limited(driver):
                set_rate_limit_cooldown(0)
                load_job_page(driver, driver.current_url)
            if not wait_for_manual_access(driver):
                raise DescriptionError("auth_required")

            if site["source"] == "LinkedIn":
                cards = driver.find_elements(By.CSS_SELECTOR, "li")
            else:
                cards = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/vacancy/"]')
            for card in cards:
                check_stop()
                try:
                    link = card.find_element(By.TAG_NAME, "a") if site["source"] == "LinkedIn" else card
                    href = link.get_attribute("href")
                    # Some cards put company, place and age into the link text; the title is the first line.
                    title = (link.text.strip().splitlines() or [""])[0].strip()
                    if site["source"] == "LinkedIn" and href and not title:
                        # Card links hide the title from rendered text.
                        title = (link.get_attribute("textContent") or "").strip()
                    if not href or not title:
                        continue
                    if site["source"] == "LinkedIn":
                        # Only vacancy pages; menu links such as /jobs/finance-jobs are not jobs.
                        if "/jobs/view/" not in href or any(text in title.lower() for text in STOP_LINK_TEXTS):
                            continue
                        href = href.split("?")[0]
                        try:
                            posted = card.find_element(By.TAG_NAME, "time").text.strip()
                        except NoSuchElementException:
                            posted = "Not specified"
                    else:
                        if "cv.lv" in title.lower():
                            continue
                        posted = driver.execute_script("""
                            const container = arguments[0].closest('li') || arguments[0].closest('div[class*="item"]');
                            const time = container && container.querySelector('span[class*="time"], div[class*="date"]');
                            return time ? time.innerText.trim() : 'Not specified';
                        """, card)
                except (NoSuchElementException, StaleElementReferenceException):
                    continue

                if href in seen or is_unsuitable_job(title):
                    continue
                seen.add(href)
                if href in excluded:
                    continue
                calculated = calculate_absolute_time(posted)
                job = {
                    "Title": title, "Link": href, "Country": site["country"],
                    "Source": site["source"],
                    "Posted": f"{posted} ({calculated})" if posted != calculated else posted,
                }
                # Keep persistence outside recoverable card-extraction catches.
                if on_job is not None:
                    in_callback = True
                    on_job(job)
                    in_callback = False
                jobs.append(job)
                if limit is not None and len(jobs) >= limit:
                    return jobs

            if page == max(0, pages):
                break
            # Only try load-more when the previous scroll revealed no new jobs.
            buttons = driver.find_elements(By.CSS_SELECTOR, "button.infinite-scroller__show-more-button") if len(jobs) == previous_count else []
            for button in buttons:
                if button.is_displayed():
                    wait_for_navigation_slot()
                    button.click()
                    break
            else:
                wait_for_navigation_slot()
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            interruptible_wait(2)
            if is_rate_limited(driver):
                set_rate_limit_cooldown(0)
                load_job_page(driver, driver.current_url)
        return jobs
    except WebDriverException as error:
        if in_callback:
            raise
        raise_if_fatal_session_error(error)
        raise
