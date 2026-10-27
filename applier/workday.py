"""Apply on Workday career sites (company.wd3.myworkdayjobs.com).

Workday asks for an applicant account on every employer's site. The program never creates accounts or
types passwords: when the site shows its sign-in step, the application stops as needs_manual, or, with
login_wait (python main.py apply --workday), waits in the open Chrome window until the applicant has
signed in. After that the form is the same on every Workday site, and it is filled step by step:
My Information, My Experience (the tailored CV), Application Questions, Voluntary Disclosures, Review.
"""

import re
import time

from selenium.common.exceptions import (ElementClickInterceptedException, ElementNotInteractableException,
                                        NoSuchElementException, StaleElementReferenceException, TimeoutException,
                                        WebDriverException)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from applier import vocabulary
from applier.easy_apply import (FormFiller, NeedsManual, clean_question, click, is_required, pause, question_text,
                                save_failure, visible)

SIGN_IN = ("[data-automation-id='signInContent'], [data-automation-id='createAccountContent'], "
           "[data-automation-id='signInFormo'], [data-automation-id='SignInWithEmailButton'], "
           "[data-automation-id='createAccountLink']")
NEXT_BUTTON = "[data-automation-id='pageFooterNextButton'], [data-automation-id='bottom-navigation-next-button']"
ERRORS = ("[data-automation-id='errorMessage'], [data-automation-id='errorBanner'] li, "
          "[data-automation-id='inputAlert'], [data-automation-id='errorHeading']")
# "Select One" on a list that has nothing chosen yet, in the languages Workday sites use here.
PLACEHOLDER = re.compile(r"^(select one|select|choose one|atlasiet vienu|atlasiet|izvēlieties|выберите( одно| один)?|"
                         r"valige üks|pasirinkite|auswählen|bitte auswählen)?$", re.I)
SOURCE_QUESTION = re.compile(r"hear about|how did you (find|learn)|source|uzzināj|узнал|источник")
SOURCE_GROUP = re.compile(r"job (board|site|portal|posting)|social|internet|online|website|darba portāl|sociāl|"
                          r"интернет|сайт|соц")
SIGN_IN_STEP = re.compile(r"sign in|create account|pierakstī|reģistr|войти|вход|созда\w* (учетн|аккаунт)", re.I)


def is_workday(url):
    return "myworkdayjobs.com" in (url or "").lower() or "myworkdaysite.com" in (url or "").lower()


def shown(driver, selector):
    return [element for element in driver.find_elements(By.CSS_SELECTOR, selector) if visible(element)]


def active_step(driver):
    steps = shown(driver, "[data-automation-id='progressBarActiveStep']")
    return clean_question(steps[0].text) if steps else ""


def needs_sign_in(driver):
    return bool(shown(driver, SIGN_IN)) or bool(SIGN_IN_STEP.search(active_step(driver)))


def open_application(driver, url):
    """Open the job's application flow (Apply -> Apply Manually); the sign-in step may come first."""
    driver.get(url)
    pause(4, 6)
    for button in shown(driver, "[data-automation-id='legalNoticeDeclineButton']"):
        click(driver, button)  # Only the most private cookie choice is ever clicked.
        pause(1, 2)
    if not shown(driver, "[data-automation-id='applyFlowPage']"):
        start = shown(driver, "[data-automation-id='adventureButton']")
        if not start:
            raise NeedsManual("workday: no Apply button on the job page (the job may be closed)")
        click(driver, start[0])
        pause(2, 4)
        # The tailored CV is uploaded by the program, so neither "Autofill with Resume" nor the
        # LinkedIn import is used.
        manual = shown(driver, "[data-automation-id='applyManually']")
        if manual:
            click(driver, manual[0])
            pause(3, 5)


def wait_for_sign_in(driver, timeout, say=print):
    """Wait while the applicant signs in (or creates the account) in the open window."""
    if not needs_sign_in(driver):
        return True
    if timeout <= 0:
        return False
    say(f"   Workday: sign in to this employer's site in the Chrome window (waiting up to {timeout // 60} min). "
        "Use 'Sign in with email'; Google sign-in is usually blocked in a program-controlled browser.")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(3)
        try:
            if not needs_sign_in(driver) and shown(driver, NEXT_BUTTON):
                pause(2, 3)
                return True
        except WebDriverException:
            continue
    return False


class WorkdayFiller(FormFiller):
    """FormFiller plus Workday's own widgets: button lists ("Select One") and searchable lists."""

    def fill(self, box):
        self._multiselects(box)
        self._listboxes(box)
        super().fill(box)

    def skip_text(self, control):
        return bool(self.driver.execute_script(
            "return !!arguments[0].closest(\"[data-automation-id='multiselectInputContainer'], "
            "[data-uxi-widget-type='multiselect'], [data-automation-id='searchBox']\")"
            " || arguments[0].getAttribute('data-automation-id') === 'searchBox'"
            " || /^dateSection/.test(arguments[0].getAttribute('data-automation-id') || '');", control))

    def field_question(self, control):
        """The field's label without Workday's "Select One" and "Required" additions."""
        raw = question_text(self.driver, control) or control.get_attribute("aria-label") or ""
        text = re.sub(r"\b(select one|required)\b", " ", clean_question(raw), flags=re.I)
        return " ".join(text.split()), raw

    def field_required(self, control, question, raw):
        return (is_required(self.driver, control, question) or "*" in raw
                or "required" in (control.get_attribute("aria-label") or "").lower()
                or bool(self.driver.execute_script(
                    "const g = arguments[0].closest(\"[data-automation-id^='formField']\");"
                    " return !!(g && /\\*/.test((g.querySelector('label') || {}).innerText || ''));", control)))

    def _options(self, selector="[role='option']"):
        return [option for option in shown(self.driver, f"[role='listbox'] {selector}, {selector}")
                if option.text.strip()]

    def _listboxes(self, box):
        for button in box.find_elements(By.CSS_SELECTOR, "button[aria-haspopup='listbox']"):
            try:
                if not visible(button) or not PLACEHOLDER.match((button.text or "").strip()):
                    continue  # already chosen (by the applicant's account or an earlier pass)
                question, raw = self.field_question(button)
                required = self.field_required(button, question, raw)
                click(self.driver, button)
                pause(0.8, 1.4)
                options = self._options()
                texts = [option.text.strip() for option in options]
                value, source = self.answer(question, "choice", texts, required) if texts else (None, None)
                if value is None or self.plan:
                    button.send_keys(Keys.ESCAPE)
                    if value is None and required:
                        self.unanswered(question or "a list without a readable question")
                    elif value is not None:
                        self.record(question, value, source)
                    continue
                click(self.driver, options[texts.index(value)])
                self.record(question, value, source)
                pause(0.5, 1)
            except StaleElementReferenceException:
                continue

    def _multiselects(self, box):
        """Searchable lists ("How Did You Hear About Us?", "Country Phone Code"), some with sub-lists."""
        for container in box.find_elements(By.CSS_SELECTOR, "[data-automation-id='multiselectInputContainer']"):
            try:
                if not visible(container) or container.find_elements(By.CSS_SELECTOR,
                                                                     "[data-automation-id='selectedItem']"):
                    continue
                search = container.find_elements(By.CSS_SELECTOR, "input")
                if not search:
                    continue
                question, raw = self.field_question(search[0])
                required = self.field_required(search[0], question, raw)
                click(self.driver, search[0])
                pause(1, 1.6)
                chosen = None
                for _ in range(3):  # a category, then its entries ("Job Board" -> "LinkedIn")
                    options = self._options("[data-automation-id='promptOption']") or self._options()
                    texts = [option.text.strip() for option in options]
                    if not texts:
                        break
                    value, source = self.answer(question, "choice", texts, required)
                    if value is None and SOURCE_QUESTION.search(question.lower()):
                        # "LinkedIn" usually sits inside a category such as "Job Board" or "Social Media".
                        value = next((text for text in texts if SOURCE_GROUP.search(text.lower())), None)
                        source = "rule"
                    if value is None:
                        break
                    chosen = (value, source)
                    if self.plan:
                        break
                    click(self.driver, options[texts.index(value)])
                    pause(1, 1.6)
                    if container.find_elements(By.CSS_SELECTOR, "[data-automation-id='selectedItem']"):
                        break
                if chosen is None or self.plan:
                    search[0].send_keys(Keys.ESCAPE)
                if chosen is None:
                    if required:
                        self.unanswered(question or "a list without a readable question")
                    continue
                self.record(question, *chosen)
            except StaleElementReferenceException:
                continue


def visible_errors(driver):
    found = []
    for element in shown(driver, ERRORS):
        text = (element.text or "").strip()
        if text and len(text) < 300 and text not in found:
            found.append(text)
    return found


def run_workday(driver, url, answer_fn, files, submit, screenshot_path, login_wait=0, max_steps=10, say=print):
    """Fill a Workday application; return (status, answers, "workday") like external.run_external."""
    open_application(driver, url)
    if not wait_for_sign_in(driver, login_wait, say):
        raise NeedsManual("workday: sign in on this employer's Workday site first (python main.py apply --workday "
                          "opens it and waits for you), or apply yourself")
    filler = WorkdayFiller(driver, answer_fn, files)
    last_errors = None
    try:
        for _ in range(max_steps):
            pause(1.5, 2.5)
            step = active_step(driver)
            page = driver.find_element(By.TAG_NAME, "body").text.lower()
            if any(word in page for word in vocabulary.DONE):
                return "applied", filler.log, "workday"
            box = (shown(driver, "[data-automation-id='applyFlowPage']") or [driver.find_element(By.TAG_NAME, "body")])[0]
            filler.fill(box)
            buttons = shown(driver, NEXT_BUTTON)
            if not buttons:
                raise NeedsManual(f"workday: no Next/Submit button on the step '{step}'", filler.log)
            button = buttons[-1]
            if re.search(r"submit|iesniegt|отправить|esita|pateikti|absenden", (button.text or "").lower()):
                if "cv" not in filler.uploaded:
                    raise NeedsManual("workday: the tailored CV was not uploaded on any step", filler.log)
                driver.save_screenshot(str(screenshot_path))
                if not submit:
                    return "ready", filler.log, "workday"
                click(driver, button)
                pause(5, 7)
                errors = visible_errors(driver)
                if errors:
                    raise NeedsManual("workday: form errors after Submit: " + "; ".join(errors), filler.log)
                page = driver.find_element(By.TAG_NAME, "body").text.lower()
                return ("applied" if any(word in page for word in vocabulary.DONE) or "submitted" in page
                        else "unconfirmed"), filler.log, "workday"
            click(driver, button)
            pause(3, 5)
            errors = visible_errors(driver)
            if errors:
                if errors == last_errors:
                    raise NeedsManual(f"workday: form errors on '{step}': " + "; ".join(errors), filler.log)
                last_errors = errors
        raise NeedsManual("workday: too many form steps", filler.log)
    except (NoSuchElementException, TimeoutException, ElementNotInteractableException,
            ElementClickInterceptedException, KeyError, ValueError) as error:
        save_failure(driver, screenshot_path)
        raise NeedsManual(f"workday: unexpected form layout: {type(error).__name__}: {str(error)[:200]}",
                          filler.log) from error
    except NeedsManual:
        save_failure(driver, screenshot_path)
        raise
