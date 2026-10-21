"""Apply on a company's own career site (Lever, Greenhouse, Ashby, Workable, Teamtailor and others).

The LinkedIn "Apply" button leads to the employer's form. The same FormFiller
fills it; a CAPTCHA, a login wall or anything unexpected stops the
application as needs_manual instead of being worked around.
"""

import re
from urllib.parse import urlsplit

from selenium.common.exceptions import (ElementClickInterceptedException, ElementNotInteractableException,
                                        NoSuchElementException, StaleElementReferenceException, TimeoutException,
                                        WebDriverException)
from selenium.webdriver.common.by import By

from applier.easy_apply import FormFiller, NeedsManual, click, label_of, pause, save_failure, visible

ATS = {
    "lever": ("lever.co",),
    "greenhouse": ("greenhouse.io",),
    "ashby": ("ashbyhq.com",),
    "workable": ("workable.com",),
    "teamtailor": ("teamtailor.com",),
    "smartrecruiters": ("smartrecruiters.com",),
    "recruitee": ("recruitee.com",),
    "personio": ("jobs.personio.",),
    "bamboohr": ("bamboohr.com",),
    "workday": ("myworkdayjobs.com", "workday.com"),
    "cvlv": ("cv.lv", "cvmarket.lv"),
}
# These need an applicant account per employer; the program does not create accounts.
ACCOUNT_REQUIRED = ("workday",)
SUBMIT_WORDS = ("submit application", "submit your application", "submit", "send application", "send",
                "apply now", "iesniegt", "nosūtīt", "отправить", "pieteikties")
NEXT_WORDS = ("next", "continue", "turpināt", "tālāk", "далее", "продолжить")
START_WORDS = ("apply for this job", "apply now", "apply", "pieteikties", "откликнуться", "подать заявку")
DONE_WORDS = ("thank you for applying", "thanks for applying", "application has been received", "application received",
              "application was submitted", "application submitted", "we have received your application",
              "successfully submitted", "paldies par pieteikumu", "pieteikums ir saņemts", "спасибо за отклик",
              "заявка отправлена")
LOGIN_WORDS = ("sign in to apply", "create an account", "log in to apply", "create account")
# Cookie banners: only the most privacy-preserving choice is ever clicked.
COOKIE_DECLINE = ("reject all", "decline all", "reject", "decline", "only necessary", "necessary only",
                  "use necessary", "allow necessary", "essential only", "deny", "noraidīt", "atteikties",
                  "tikai nepieciešam", "отклонить", "только необходимые")


def decline_cookies(driver):
    for text, element in action_buttons(driver.find_element(By.TAG_NAME, "body")):
        in_banner = driver.execute_script(
            "let el = arguments[0]; for (let i = 0; i < 6 && el; i++, el = el.parentElement) {"
            " if (/cookie|sīkdatn|куки|consent/i.test((el.getAttribute('aria-label') || '') + ' ' + el.id + ' '"
            " + el.className + ' ' + (i > 1 ? el.innerText.slice(0, 400) : ''))) return true; } return false;", element)
        if in_banner and any(word in text for word in COOKIE_DECLINE):
            try:
                click(driver, element)
                pause(1, 2)
                return True
            except WebDriverException:
                continue
    return False


def detect_ats(url):
    host = urlsplit(url or "").netloc.lower()
    for name, domains in ATS.items():
        if any(domain in host for domain in domains):
            return name
    return "other"


def captcha_challenge(driver):
    """A visible CAPTCHA puzzle. Invisible checks that pass on their own are not reported."""
    for frame in driver.find_elements(By.CSS_SELECTOR, "iframe[src*='hcaptcha'], iframe[src*='recaptcha'], "
                                                       "iframe[src*='challenges.cloudflare'], iframe[title*='captcha' i]"):
        try:
            size = frame.size
        except WebDriverException:
            continue
        if visible(frame) and size.get("height", 0) > 100 and size.get("width", 0) > 100:
            return True
    return False


def form_container(driver):
    """The application form: a form with a file input wins, then the one with most fields, else the page."""
    best, best_rank = None, (0, 0)
    for form in driver.find_elements(By.CSS_SELECTOR, "form"):
        if not visible(form):
            continue
        rank = (len(form.find_elements(By.CSS_SELECTOR, "input[type='file']")),
                len(form.find_elements(By.CSS_SELECTOR, "input:not([type='hidden']), select, textarea")))
        if rank > best_rank:
            best, best_rank = form, rank
    return best if best is not None and best_rank[1] >= 2 else driver.find_element(By.TAG_NAME, "body")


def action_buttons(box):
    found = []
    for element in box.find_elements(By.CSS_SELECTOR, "button, input[type='submit'], a[role='button']"):
        try:
            if visible(element) and element.is_enabled():
                text = " ".join(filter(None, (label_of(element), element.get_attribute("value") or ""))).lower()
                found.append((text, element))
        except StaleElementReferenceException:
            continue
    return found


def form_action(box):
    """The step's main button: ('submit', el), ('next', el) or (None, None)."""
    buttons = action_buttons(box)
    for key, words in (("submit", SUBMIT_WORDS), ("next", NEXT_WORDS)):
        for text, element in buttons:
            if any(word in text for word in words) and "cookie" not in text:
                return key, element
    typed = [element for _, element in buttons if (element.get_attribute("type") or "") == "submit"]
    return ("submit", typed[-1]) if typed else (None, None)


def open_form(driver, url):
    """Navigate to the application form of a posting; return the ATS name."""
    ats = detect_ats(url)
    if ats == "lever" and not urlsplit(url).path.rstrip("/").endswith("/apply"):
        url = url.split("?")[0].rstrip("/") + "/apply"
    elif ats == "ashby" and "/application" not in url:
        url = url.split("?")[0].rstrip("/") + "/application"
    elif ats == "teamtailor" and re.search(r"/jobs/\d+[^/]*/?$", urlsplit(url).path):
        url = url.split("?")[0].rstrip("/") + "/applications/new"
    driver.get(url)
    pause(3, 5)
    decline_cookies(driver)
    page = driver.find_element(By.TAG_NAME, "body").text.lower()
    if ats in ACCOUNT_REQUIRED or any(word in page for word in LOGIN_WORDS):
        raise NeedsManual(f"{ats}: this site needs an applicant account; apply yourself")
    # On a company's own page the Greenhouse form can sit in an embedded iframe.
    if not form_has_fields(driver):
        frames = [frame for frame in driver.find_elements(By.CSS_SELECTOR, "iframe#grnhse_iframe, iframe[src*='greenhouse.io']")
                  if "recaptcha" not in (frame.get_attribute("src") or "")]
        if frames:
            driver.switch_to.frame(frames[0])
            return "greenhouse"
    if not form_has_fields(driver):
        for text, element in action_buttons(driver.find_element(By.TAG_NAME, "body")):
            if any(text.strip().startswith(word) for word in START_WORDS):
                element.click()
                pause(2, 4)
                break
    return ats


def form_has_fields(driver):
    return len([e for e in driver.find_elements(By.CSS_SELECTOR, "input[type='email'], input[type='file'], textarea")
                if visible(e) or e.get_attribute("type") == "file"]) >= 1


def run_external(driver, url, answer_fn, files, submit, screenshot_path, max_steps=8):
    """Fill the employer's form; return (status, answers, ats) like run_application."""
    ats = open_form(driver, url)
    filler = FormFiller(driver, answer_fn, files)
    last_errors = None
    try:
        for _ in range(max_steps):
            if captcha_challenge(driver):
                raise NeedsManual(f"{ats}: CAPTCHA on the form; finish this application yourself", filler.log)
            filler.fill(form_container(driver))
            # React forms (Greenhouse) re-render after an upload, so look the form up again.
            action, target = form_action(form_container(driver))
            if action is None:
                pause(2, 3)
                action, target = form_action(form_container(driver))
            if action is None:
                raise NeedsManual(f"{ats}: no Submit/Next button found", filler.log)
            if action == "submit":
                if "cv" not in filler.uploaded:
                    # Every real application takes a CV; without that field this is some other form.
                    raise NeedsManual(f"{ats}: no CV upload found, so this may not be the application form",
                                      filler.log)
                driver.save_screenshot(str(screenshot_path))
                if not submit:
                    return "ready", filler.log, ats
                start = driver.current_url
                target.click()
                pause(4, 6)
                if captcha_challenge(driver):
                    raise NeedsManual(f"{ats}: CAPTCHA after Submit; finish this application yourself", filler.log)
                page = driver.find_element(By.TAG_NAME, "body").text.lower()
                errors = visible_errors(driver)
                if any(word in page for word in DONE_WORDS) or (driver.current_url != start and not errors):
                    return "applied", filler.log, ats
                if errors:
                    raise NeedsManual(f"{ats}: form errors after Submit: " + "; ".join(errors), filler.log)
                return "unconfirmed", filler.log, ats
            target.click()
            pause(2, 3)
            errors = visible_errors(driver)
            if errors:
                if errors == last_errors:
                    raise NeedsManual(f"{ats}: form errors: " + "; ".join(errors), filler.log)
                last_errors = errors
        raise NeedsManual(f"{ats}: too many form steps", filler.log)
    except (NoSuchElementException, TimeoutException, StaleElementReferenceException,
            ElementNotInteractableException, ElementClickInterceptedException, KeyError) as error:
        save_failure(driver, screenshot_path)
        raise NeedsManual(f"{ats}: unexpected form layout: {type(error).__name__}: {str(error)[:200]}",
                          filler.log) from error
    except NeedsManual:
        save_failure(driver, screenshot_path)
        raise


ERROR_SELECTORS = ("[role='alert']", ".error", ".error-message", ".field-error", ".invalid-feedback",
                   "[aria-live='assertive']", ".application-error")


def visible_errors(driver):
    found = []
    for selector in ERROR_SELECTORS:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            text = (element.text or "").strip()
            if text and visible(element) and len(text) < 200 and text not in found:
                found.append(text)
    return found


def follow_apply_button(driver, button):
    """Click LinkedIn's external Apply button and return the employer URL, leaving its tab open.

    Returns (url, original_window). The caller closes the new tab with close_extra_tabs.
    """
    original = driver.current_window_handle
    windows = set(driver.window_handles)
    button.click()
    pause(3, 5)
    opened = [handle for handle in driver.window_handles if handle not in windows]
    if opened:
        driver.switch_to.window(opened[0])
    else:
        # Sometimes LinkedIn first shows a "continue to the company site" dialog.
        for text, element in action_buttons(driver.find_element(By.TAG_NAME, "body")):
            if re.search(r"continue|продолжить|turpināt", text):
                element.click()
                pause(3, 5)
                break
        opened = [handle for handle in driver.window_handles if handle not in windows]
        if opened:
            driver.switch_to.window(opened[0])
    url = driver.current_url
    if "linkedin.com" in urlsplit(url).netloc:
        raise NeedsManual("The Apply button did not open the company site")
    return url, original


def close_extra_tabs(driver, original):
    try:
        driver.switch_to.default_content()
        for handle in driver.window_handles:
            if handle != original:
                driver.switch_to.window(handle)
                driver.close()
        driver.switch_to.window(original)
    except WebDriverException:
        pass
