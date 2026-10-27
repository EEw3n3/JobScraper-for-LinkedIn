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

from applier import vocabulary
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
ACCOUNT_REQUIRED = ("workday", "successfactors", "taleo", "icims", "njoyn")
# Career sites on the employer's own domain still show their ATS in the page source.
PAGE_ATS = {"successfactors": ("successfactors.com", "successfactors.eu", "sapsf"),
            "taleo": ("taleo.net",), "icims": ("icims.com",), "njoyn": ("njoyn.com",),
            "teamtailor": ("teamtailor-cdn", "teamtailor.com"), "recruitee": ("recruitee.com",),
            "greenhouse": ("boards.greenhouse.io", "job-boards.greenhouse.io"), "lever": ("jobs.lever.co",)}
# The words in every language live in applier/vocabulary.py.
SUBMIT_WORDS = vocabulary.SUBMIT
NEXT_WORDS = vocabulary.NEXT
START_WORDS = vocabulary.APPLY
DONE_WORDS = vocabulary.DONE
LOGIN_WORDS = vocabulary.LOGIN
COOKIE_DECLINE = vocabulary.COOKIE_DECLINE


def decline_cookies(driver):
    for text, element in action_buttons(driver.find_element(By.TAG_NAME, "body")):
        in_banner = driver.execute_script(
            "let el = arguments[0]; for (let i = 0; i < 6 && el; i++, el = el.parentElement) {"
            " if (/cookie|sīkdatn|sīkfail|куки|cookie-файл|consent|küpsis|slapuk/i.test((el.getAttribute('aria-label') || '') + ' ' + el.id + ' '"
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


ROBOT_CHECK = re.compile(r"not a robot|verify (that )?you are (a )?human|are you a robot|nevis robots|neesat robots|"
                         r"pārliecināti, ka tas esat jūs|не робот|подтвердите, что вы человек|ei ole robot", re.I)


def captcha_challenge(driver):
    """A visible CAPTCHA puzzle or a bot-check page. Invisible checks that pass on their own are not reported."""
    for frame in driver.find_elements(By.CSS_SELECTOR, "iframe[src*='hcaptcha'], iframe[src*='recaptcha'], "
                                                       "iframe[src*='challenges.cloudflare'], iframe[title*='captcha' i], "
                                                       "iframe[src*='captcha-delivery'], iframe[src*='datadome']"):
        try:
            size = frame.size
        except WebDriverException:
            continue
        if visible(frame) and size.get("height", 0) > 100 and size.get("width", 0) > 100:
            return True
    if not form_has_fields(driver):
        # A full-page check ("we want to be sure you are not a robot") instead of the form.
        return bool(ROBOT_CHECK.search(driver.find_element(By.TAG_NAME, "body").text[:3000]))
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


def action_buttons(box, include_disabled=False):
    """(text, element) of the visible buttons; include_disabled also keeps buttons that unlock once the
    form is filled (the form check looks at a form it does not fill)."""
    found = []
    # PeopleHR and other older forms make their Next button a styled link.
    for element in box.find_elements(By.CSS_SELECTOR, "button, input[type='submit'], input[type='button'], "
                                                      "a[role='button'], a.button, a.btn"):
        try:
            if visible(element) and (include_disabled or element.is_enabled()):
                text = " ".join(filter(None, (label_of(element), element.get_attribute("value") or ""))).lower()
                found.append((text, element))
        except StaleElementReferenceException:
            continue
    return found


def clickable(text):
    """Not a draft, sign-in, social-apply or back button, which the applier never presses."""
    return not any(word in text for word in vocabulary.NEVER_CLICK)


def form_action(box, include_disabled=False):
    """The step's main button: ('submit', el), ('next', el) or (None, None).

    Words are tried in their list order, so "Submit application" wins over a "Register" elsewhere.
    """
    buttons = [(text, element) for text, element in action_buttons(box, include_disabled)
               if clickable(text) and "cookie" not in text]
    for key, words in (("submit", SUBMIT_WORDS), ("next", NEXT_WORDS)):
        for word in words:
            for text, element in buttons:
                if word in text:
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
    if ats == "other":
        source = driver.page_source.lower()
        ats = next((name for name, markers in PAGE_ATS.items() if any(marker in source for marker in markers)), ats)
    if ats in ACCOUNT_REQUIRED:
        raise NeedsManual(f"{ats}: this site needs an applicant account; apply yourself")
    # On a company's own page the Greenhouse form can sit in an embedded iframe.
    if not form_has_fields(driver):
        frames = [frame for frame in driver.find_elements(By.CSS_SELECTOR, "iframe#grnhse_iframe, iframe[src*='greenhouse.io']")
                  if "recaptcha" not in (frame.get_attribute("src") or "")]
        if frames:
            driver.switch_to.frame(frames[0])
            return "greenhouse"
    if not form_has_fields(driver):
        for text, element in start_buttons(driver):
            if clickable(text) and any(text.strip().startswith(word) for word in START_WORDS):
                windows = set(driver.window_handles)
                click(driver, element)
                pause(2, 4)
                opened = [handle for handle in driver.window_handles if handle not in windows]
                if opened:
                    # Some job pages open the form in a new tab; close_extra_tabs closes it afterwards.
                    driver.switch_to.window(opened[0])
                    pause(2, 3)
                decline_cookies(driver)
                break
    if not form_has_fields(driver):
        switch_to_form_frame(driver)
    if not form_has_fields(driver):
        # Only a page without a form counts: many career sites show "Create account" in the header.
        page = driver.find_element(By.TAG_NAME, "body").text.lower()
        if any(word in page for word in LOGIN_WORDS):
            raise NeedsManual(f"{ats}: this site needs an applicant account; apply yourself")
    return ats


def start_buttons(driver):
    """Buttons and plain links of a job page, as (text, element); career sites often make "Apply" a link.

    The site's header, menu and footer are left out: their "Apply" usually leads to a general sign-up.
    """
    found = action_buttons(driver.find_element(By.TAG_NAME, "body"))
    # TalentLyft's "Apply" is a link without an address that only runs a script.
    for element in driver.find_elements(By.CSS_SELECTOR, "a[href], a[onclick]"):
        try:
            href = (element.get_attribute("href") or "").lower()
            if visible(element) and not href.startswith(("mailto:", "tel:")):
                text = " ".join(filter(None, (label_of(element), element.get_attribute("title") or ""))).lower()
                if text:
                    found.append((text, element))
        except StaleElementReferenceException:
            continue
    inside = [(text, element) for text, element in found if not driver.execute_script(
        "return !!arguments[0].closest('header, nav, footer, [role=navigation], [role=banner], [role=contentinfo]')",
        element)]
    # Main content first; the header's buttons stay as a last choice for pages that have no other.
    return inside + [pair for pair in found if pair not in inside]


EMAIL_ONLY = re.compile(r"(send|e-?mail|submit) (us )?(your )?(cv|resume|application)[^.]{0,40}\b(to|at)\b[^.]{0,40}@|"
                        r"sūt\w* (savu |jūsu |tavu )?(cv|pieteikumu)[^.]{0,40}e-?past|"
                        r"отправ\w* (ваше |своё )?(резюме|cv)[^.]{0,40}(почт|e-?mail)", re.I)


def no_form_reason(driver):
    """Why a page without an application form has none: "closed" (the job is filled or expired),
    "email" (the employer asks for the CV by e-mail), else None."""
    if form_has_fields(driver):
        return None
    page = driver.find_element(By.TAG_NAME, "body").text
    if any(word in page.lower() for word in vocabulary.CLOSED):
        return "closed"
    return "email" if EMAIL_ONLY.search(page) else None


def account_form(driver):
    """A visible password box: the site wants a sign-in or a new account, which the applier never makes."""
    return any(visible(element) for element in driver.find_elements(By.CSS_SELECTOR, "input[type='password']"))


def switch_to_form_frame(driver):
    """Enter an embedded application form (an iframe from the employer's ATS), if the page has one."""
    for frame in driver.find_elements(By.CSS_SELECTOR, "iframe"):
        source = (frame.get_attribute("src") or "").lower()
        if not visible(frame) or re.search(r"captcha|challenges\.cloudflare|youtube|vimeo|google\.com/maps|facebook",
                                           source):
            continue
        # A lazy frame (workingday.lv) or a form loaded by a script (Teamdash on pasts.lv) shows its fields
        # only once it is scrolled into view and has had a moment to load.
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", frame)
        for attempt in range(2):
            if attempt or frame.get_attribute("loading") == "lazy":
                pause(2, 3)
            try:
                driver.switch_to.frame(frame)
            except WebDriverException:
                break
            if form_has_fields(driver):
                return True
            driver.switch_to.default_content()
    return False


def form_has_fields(driver):
    """An application form is showing: a visible e-mail box, text area or upload (an upload's own input is
    often hidden behind a button, so its container counts). Newsletter boxes and site headers do not count."""
    for element in driver.find_elements(By.CSS_SELECTOR, "input[type='email'], input[type='file'], textarea"):
        try:
            shown = visible(element) or (element.get_attribute("type") == "file"
                                         and visible(element.find_element(By.XPATH, "..")))
            if shown and not driver.execute_script(
                    "const el = arguments[0];"
                    " if (el.closest('header, nav, footer, [role=banner], [role=contentinfo]')) return true;"
                    # Dropzone's spare upload box sits hidden at the end of the page, outside any form.
                    " if (el.type === 'file' && (el.parentElement === document.body"
                    "     || el.classList.contains('dz-hidden-input'))) return true;"
                    " const f = el.closest('form') || el.parentElement;"
                    # A form kept hidden until "Apply" is pressed (TalentLyft) is not showing yet.
                    " if (f && !f.getClientRects().length) return true;"
                    " return /subscri|newsletter|search|jaunum|рассылк/i.test("
                    "   [el.name, el.id, f && f.id, f && f.className, f && f.getAttribute('action')].join(' '));",
                    element):
                return True
        except (StaleElementReferenceException, NoSuchElementException):
            continue
    return False


def run_external(driver, url, answer_fn, files, submit, screenshot_path, max_steps=8, login_wait=0, say=print):
    """Fill the employer's form; return (status, answers, ats) like run_application.

    login_wait: seconds to wait for the applicant to sign in on a Workday site (0 stops at its sign-in step).
    """
    from applier import workday

    if workday.is_workday(url):
        return workday.run_workday(driver, url, answer_fn, files, submit, screenshot_path, login_wait, say=say)
    ats = open_form(driver, url)
    reason = no_form_reason(driver)
    if reason == "closed":
        return "closed", [], ats
    if reason == "email":
        # Sending e-mails on the applicant's behalf is left to the applicant.
        raise NeedsManual(f"{ats}: the employer takes applications by e-mail only; send your CV yourself")
    filler = FormFiller(driver, answer_fn, files)
    last_errors = None
    try:
        for _ in range(max_steps):
            if captcha_challenge(driver):
                raise NeedsManual(f"{ats}: CAPTCHA on the form; finish this application yourself", filler.log)
            page = driver.find_element(By.TAG_NAME, "body").text.lower()
            if any(word in page for word in vocabulary.EMAIL_CODE):
                raise NeedsManual(f"{ats}: the site first sends a code to your e-mail; finish this application yourself",
                                  filler.log)
            if account_form(driver):
                raise NeedsManual(f"{ats}: this site needs an applicant account; apply yourself", filler.log)
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
