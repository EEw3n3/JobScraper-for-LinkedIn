"""Apply to cv.lv vacancies: cv.lv's own three-step form, or the employer's site the ad points to.

The applicant's cv.lv session lives in the same Chrome profile as LinkedIn
(main.py login --site cvlv). Signed-in requests (saved and applied lists, the
date of an earlier application) run inside that browser, so the session never
leaves it. As on LinkedIn, anything unexpected stops the application as
needs_manual instead of guessing.
"""

import json
import re
import time
from datetime import date
from pathlib import Path

import requests
from selenium.common.exceptions import (ElementClickInterceptedException, ElementNotInteractableException,
                                        NoSuchElementException, StaleElementReferenceException, TimeoutException,
                                        WebDriverException)
from selenium.webdriver.common.by import By

from applier import external
from applier.easy_apply import FormFiller, NeedsManual, click, label_of, pause, save_failure, visible
from scraper import cvlv

LOGIN_URL = f"{cvlv.BASE}/lv/login"
FORM = "form.apply-flow"
# The form's buttons by their text. "Saglabāt un turpināt vēlāk" only saves a draft and is never pressed.
SUBMIT = ("pieteikties", "apply", "откликнуться", "подать заявку")
NEXT = ("nākamais", "turpināt", "next", "continue", "далее", "продолжить")
DRAFT = ("saglabāt", "vēlāk", "save", "later", "сохранить", "позже")
WITHOUT_LOGIN = ("bez ielogošanās", "without signing", "without logging", "без входа", "без авторизации")
DONE = ("pieteikums ir nosūtīts", "pieteikums nosūtīts", "application has been sent", "application was sent",
        "отклик отправлен", "заявка отправлена")
ERRORS = ("[class*='error']", "[role='alert']", "[aria-invalid='true'] ~ span")

FETCH = """
const done = arguments[arguments.length - 1];
fetch(arguments[0], {credentials: 'include', headers: {'Accept': 'application/json', 'Accept-Language': 'lv'}})
  .then(response => response.text().then(body => done({status: response.status, body: body})))
  .catch(error => done({status: 0, body: String(error)}));
"""


def api(driver, path):
    """(status, JSON or None) of a cv.lv API request made by the page itself, with the applicant's session."""
    if "cv.lv" not in (driver.current_url or ""):
        driver.get(cvlv.BASE + "/lv")
        pause(2, 3)
    driver.set_script_timeout(40)
    result = driver.execute_async_script(FETCH, cvlv.API + path) or {}
    try:
        body = json.loads(result.get("body") or "null")
    except ValueError:
        body = None
    return result.get("status", 0), body


def signed_in(driver):
    return api(driver, "/vacancies-service/saved?limit=1&offset=0")[0] == 200


def _vacancy_ids(data):
    """Vacancy ids from a list response, whatever its wrapper ({total, vacancies}, {list} or a bare list)."""
    if isinstance(data, dict):
        lists = [value for value in data.values() if isinstance(value, list)]
        data = next((value for value in lists if value and isinstance(value[0], dict)), [])
    found = []
    for item in data or ():
        if isinstance(item, dict):
            number = item.get("vacancyId") or (item.get("vacancy") or {}).get("id") or item.get("id")
            if isinstance(number, int):
                found.append(number)
    return found


def vacancy_ids(driver, kind, page=50):
    """Ids of the applicant's "saved" or "applied" vacancies on cv.lv (empty when signed out)."""
    ids, offset = [], 0
    while True:
        status, data = api(driver, f"/vacancies-service/{kind}?limit={page}&offset={offset}")
        batch = _vacancy_ids(data) if status == 200 else []
        ids += [number for number in batch if number not in ids]
        offset += page
        total = data.get("total") if isinstance(data, dict) else None
        if len(batch) < page or (isinstance(total, int) and offset >= total):
            return ids


def wait_for_login(driver, timeout=600, say=print):
    driver.get(LOGIN_URL)
    say("Sign in to cv.lv in the Chrome window that just opened. "
        f"It closes by itself once you are signed in (waiting up to {timeout // 60} minutes).")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if signed_in(driver):
                return True
        except WebDriverException:
            pass
        time.sleep(3)
    return False


def fetch_vacancy(driver, number, client=None, signed=False):
    """The vacancy as the applicant sees it: signed in, it also tells whether he applied already."""
    if signed:
        status, vacancy = api(driver, f"/vacancies-service/{number}/public")
        if status == 200 and isinstance(vacancy, dict):
            return vacancy
    return (client or cvlv.Client()).vacancy(number)


def expired(vacancy):
    until = ((vacancy.get("settings") or {}).get("dateTo") or "")[:10]
    return bool(until) and until < date.today().isoformat()


def open_job(driver, link, client=None, signed=False):
    """(state, apply_url, vacancy). state: cvlv (cv.lv's own form), external, already_applied, closed or
    unavailable (cv.lv did not answer; the job is tried again next run)."""
    try:
        vacancy = fetch_vacancy(driver, cvlv.vacancy_id(link), client, signed)
    except requests.HTTPError as error:
        gone = error.response is not None and error.response.status_code in (404, 410)
        return ("closed" if gone else "unavailable"), None, {}
    except requests.RequestException:
        return "unavailable", None, {}
    if vacancy.get("dateUserLastApplied"):
        return "already_applied", None, vacancy
    if vacancy.get("status") not in (None, "PUBLISHED") or expired(vacancy):
        return "closed", None, vacancy
    url = ((vacancy.get("settings") or {}).get("applyingUrl") or "").strip()
    return ("external", url, vacancy) if url else ("cvlv", None, vacancy)


def details(vacancy):
    location = ((vacancy.get("highlights") or {}).get("location") or {})
    return {"company": vacancy.get("employerName") or "", "description": "",
            "location": "Latvia" if location.get("countryId") == cvlv.LATVIA_ID else ""}


def form(driver):
    for element in driver.find_elements(By.CSS_SELECTOR, FORM):
        if visible(element):
            return element
    return None


def open_form(driver, link):
    """The vacancy page with its application form shown (the form opens from the "Pieteikties" button)."""
    driver.get(link)
    pause(3, 5)
    external.decline_cookies(driver)
    if form(driver) is None:
        for button in driver.find_elements(By.CSS_SELECTOR, "button"):
            if visible(button) and (button.text or "").strip().lower() == "pieteikties":
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                pause(0.5, 1)
                click(driver, button)
                pause(2, 3)
                break
    box = form(driver)
    if box is None:
        raise NeedsManual("cv.lv: the application form did not open")
    return box


def action(box):
    """The step's button: ('submit', el), ('next', el), ('login', el) or (None, None). Drafts are never saved."""
    buttons = []
    for element in box.find_elements(By.CSS_SELECTOR, "button, a[role='button']"):
        try:
            if visible(element) and element.is_enabled():
                buttons.append((label_of(element).lower(), element))
        except StaleElementReferenceException:
            continue
    for text, element in buttons:
        if any(word in text for word in WITHOUT_LOGIN):
            return "login", element
    buttons = [(text, element) for text, element in buttons if not any(word in text for word in DRAFT)]
    for key, words in (("submit", SUBMIT), ("next", NEXT)):
        for text, element in buttons:
            if any(text.startswith(word) for word in words):
                return key, element
    return None, None


def open_letter_section(driver, box, files):
    """Unfold "Izvēlieties pieteikumu" (the optional application letter), so the letter file can be attached."""
    if not files.get("cover_letter"):
        return
    for button in box.find_elements(By.CSS_SELECTOR, ".toggler > button"):
        text = (button.text or "").lower()
        if visible(button) and re.search(r"pieteikum|application letter|сопроводительн|письм", text) and \
                not button.find_elements(By.XPATH, "./following-sibling::div[.//input or .//button]"):
            click(driver, button)
            pause(1, 1.5)


# The radio lists of files in the form: the account's CVs and its application letters.
FILE_CHOICES = {"cv": "cv", "cover": "applicationLetter"}


def choices(box, name):
    return box.find_elements(By.CSS_SELECTOR, f"input[type='radio'][name='{name}']")


def select_uploaded(driver, box, name, path, before):
    """After an upload, cv.lv lists the new file among the account's files: make sure it is the chosen one.

    before: the values of the choices before the upload; the new file is the choice that was not there
    (earlier uploads can carry the same file name).
    """
    radios = choices(box, name)
    if not radios:
        return True
    new = [radio for radio in radios if radio.get_attribute("value") not in before]
    if not new:
        new = [radio for radio in radios if Path(path).name.lower()
               in (radio.find_element(By.XPATH, "./ancestor::label[1]").text or "").lower()][-1:]
    if not new:
        return False
    if not new[0].is_selected():
        click(driver, new[0].find_element(By.XPATH, "./ancestor::label[1]"))
        pause(0.5, 1)
    return True


def errors_in(box):
    found = []
    for selector in ERRORS:
        for element in box.find_elements(By.CSS_SELECTOR, selector):
            text = (element.text or "").strip()
            if text and visible(element) and len(text) < 200 and text not in found:
                found.append(text)
    return found


def run_application(driver, link, answer_fn, files, submit, screenshot_path, signed=False, max_steps=6):
    """Fill cv.lv's own form; return (status, answers) like easy_apply.run_application."""
    filler = FormFiller(driver, answer_fn, files)
    last_errors = None
    try:
        box = open_form(driver, link)
        for _ in range(max_steps):
            if external.captcha_challenge(driver):
                raise NeedsManual("cv.lv: CAPTCHA on the form; finish this application yourself", filler.log)
            key, target = action(box)
            if key == "login":
                # The e-mail has a cv.lv account but this browser is signed out: the form allows going on as a guest.
                click(driver, target)
                pause(1.5, 2.5)
                box = form(driver) or box
                continue
            open_letter_section(driver, box, files)
            box = form(driver) or box
            for _ in range(4):
                # An upload redraws the form, so filling repeats until a pass uploads nothing new.
                uploaded_before = set(filler.uploaded)
                before = {kind: {radio.get_attribute("value") for radio in choices(box, name)}
                          for kind, name in FILE_CHOICES.items()}
                try:
                    filler.fill(box)
                    redrawn = False
                except StaleElementReferenceException:
                    redrawn = True
                new = [kind for kind in FILE_CHOICES if kind in filler.uploaded and kind not in uploaded_before]
                for kind in new:
                    pause(2, 3)  # cv.lv uploads the file and adds it to the account's list of files
                    box = form(driver) or box
                    path = files["cv" if kind == "cv" else "cover_letter"]
                    if not select_uploaded(driver, box, FILE_CHOICES[kind], path, before[kind]):
                        raise NeedsManual(f"cv.lv: the uploaded {Path(path).name} did not appear in the list",
                                          filler.log)
                box = form(driver) or box
                if not new and not redrawn:
                    break
            key, target = action(form(driver) or box)
            if key is None:
                raise NeedsManual("cv.lv: no Next or Apply button found", filler.log)
            if key == "submit":
                if "cv" not in filler.uploaded:
                    raise NeedsManual("cv.lv: the CV could not be attached", filler.log)
                driver.save_screenshot(str(screenshot_path))
                if not submit:
                    return "ready", filler.log
                click(driver, target)
                pause(4, 6)
                page = driver.find_element(By.TAG_NAME, "body").text.lower()
                if any(word in page for word in DONE) or (
                        signed and fetch_vacancy(driver, cvlv.vacancy_id(link), signed=True).get("dateUserLastApplied")):
                    return "applied", filler.log
                errors = errors_in(form(driver) or driver.find_element(By.TAG_NAME, "body"))
                if errors:
                    raise NeedsManual("cv.lv: form errors after Submit: " + "; ".join(errors), filler.log)
                return "unconfirmed", filler.log
            click(driver, target)
            pause(1.5, 2.5)
            box = form(driver)
            if box is None:
                raise NeedsManual("cv.lv: the form closed after Next", filler.log)
            errors = errors_in(box)
            if errors:
                if errors == last_errors:
                    raise NeedsManual("cv.lv: form errors: " + "; ".join(errors), filler.log)
                last_errors = errors
        raise NeedsManual("cv.lv: too many form steps", filler.log)
    except (NoSuchElementException, TimeoutException, StaleElementReferenceException,
            ElementNotInteractableException, ElementClickInterceptedException, KeyError) as error:
        keep_form(driver, screenshot_path)
        raise NeedsManual(f"cv.lv: unexpected form layout: {type(error).__name__}: {str(error)[:200]}",
                          filler.log) from error
    except NeedsManual:
        keep_form(driver, screenshot_path)
        raise


def keep_form(driver, screenshot_path):
    """The screenshot and HTML of the step that stopped, to adjust the form handling."""
    save_failure(driver, screenshot_path)
    try:
        box = form(driver)
        if box is not None:
            (Path(screenshot_path).parent / "stopped_form.html").write_text(box.get_attribute("outerHTML"),
                                                                           encoding="utf-8")
    except (WebDriverException, OSError):
        pass
