"""Drive LinkedIn's Easy Apply dialog in the applicant's own logged-in Chrome profile.

LinkedIn changes its markup often, so every lookup has several fallbacks and
the flow stops (status needs_manual) instead of guessing when something is
unexpected. Text matching covers English, Latvian and Russian UI labels.
"""

import random
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from selenium.common.exceptions import (ElementClickInterceptedException, ElementNotInteractableException,
                                        NoSuchElementException, StaleElementReferenceException, TimeoutException,
                                        WebDriverException)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select

APPLY_BUTTONS = ("button.jobs-apply-button, button[data-view-name='job-apply-button'], "
                 "a[data-view-name='job-apply-button'], a.jobs-apply-button")
# 2026 LinkedIn renders a native <dialog data-testid="dialog">; older layouts use the div variants.
DIALOG = ("dialog[data-testid='dialog'], dialog[open], div.jobs-easy-apply-modal, "
          "div[data-test-modal][role='dialog'], div[role='dialog']")
ERRORS = ".artdeco-inline-feedback--error, [data-test-form-element-error-messages], .fb-dash-form-element-error"
WORDS = {
    "submit": ("submit application", "submit", "iesniegt", "отправить"),
    "review": ("review", "pārskatīt", "проверить"),
    "next": ("continue to next step", "next", "continue", "turpināt", "nākamais", "далее", "продолжить"),
    "discard": ("discard", "atmest", "отменить", "не сохранять", "удалить"),
    "dismiss": ("dismiss", "aizvērt", "закрыть", "пропустить"),
    "upload": ("upload", "augšupielādēt", "загрузить"),
    "apply": ("apply", "подать заявку", "pieteikties"),
    "cover": ("cover", "сопроводительн", "motivācijas", "pavadvēstul"),
    "resume": ("resume", "curriculum", "dzīvesgājum", "резюме"),
    "applied": ("application was sent", "application submitted", "pieteikums nosūtīts", "отклик отправлен",
                "заявка отправлена"),
    "limit": ("easy apply limit", "application limit", "you've reached", "limit for today"),
    "follow": ("follow", "sekot", "отслежива", "подписаться"),
    # LinkedIn's newer markup has generated class names, so the Easy Apply button is found by its label.
    "easy": ("easy apply", "простой подачи", "простая подача", "vienkāršā pieteikšan", "vienkāršo pieteikšan"),
}
SELECT_PLACEHOLDERS = ("select an option", "izvēlieties", "выберите")


class SessionProblem(RuntimeError):
    """Login wall, security check or daily limit: stop the whole run."""


class NeedsManual(RuntimeError):
    def __init__(self, reason, answers=None):
        super().__init__(reason)
        self.answers = answers or []


class External(RuntimeError):
    """The apply button leads to the company's own site."""


def pause(low=0.6, high=1.6):
    time.sleep(random.uniform(low, high))


def has_words(text, key):
    text = (text or "").lower()
    return any(word in text for word in WORDS[key])


def label_of(element):
    return " ".join(filter(None, ((element.get_attribute("aria-label") or ""), (element.text or "")))).strip()


def click(driver, element):
    """Click like a person; if an overlay covers the element, click it through the DOM."""
    try:
        element.click()
    except (ElementClickInterceptedException, ElementNotInteractableException):
        driver.execute_script("arguments[0].click();", element)


def visible(element):
    try:
        return element.is_displayed()
    except StaleElementReferenceException:
        return False


def check_session(driver):
    url = (driver.current_url or "").lower()
    if any(marker in url for marker in ("/login", "/checkpoint", "/authwall", "/uas/")):
        raise SessionProblem(f"LinkedIn needs you to sign in or verify ({driver.current_url}). Run: python main.py login")


def job_details(driver):
    def first_text(selectors):
        for selector in selectors:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                text = (element.text or "").strip()
                if text:
                    return text
        return ""

    return {
        "company": first_text((".job-details-jobs-unified-top-card__company-name", ".jobs-unified-top-card__company-name",
                               "div[data-view-name='job-details-company-name']", "a[href*='/company/']")),
        "description": first_text(("#job-details", ".jobs-description__content", ".jobs-box__html-content")),
        # "Riga, Latvia · 2 days ago · ..." under the title; picks the CV language.
        "location": first_text((".job-details-jobs-unified-top-card__primary-description-container",
                                ".job-details-jobs-unified-top-card__tertiary-description-container",
                                ".jobs-unified-top-card__primary-description",
                                "div[data-view-name='job-details-top-card'] span")),
    }


def find_apply_button(driver):
    for element in driver.find_elements(By.CSS_SELECTOR, APPLY_BUTTONS):
        if visible(element):
            return element
    for element in driver.find_elements(By.CSS_SELECTOR, "button"):
        if visible(element) and has_words(label_of(element), "easy"):
            return element
    return None


def open_job(driver, url):
    driver.get(url)
    pause(3, 5)
    check_session(driver)
    button = find_apply_button(driver)
    if button is None:
        page = driver.find_element(By.TAG_NAME, "body").text.lower()
        if re.search(r"\bapplied\b|pieteicāties|вы откликнулись|вы подали заявку|заявка подана", page):
            return "already_applied", None
        if "no longer accepting" in page or "vairs nepieņem" in page or "больше не принимает" in page:
            return "closed", None
        for element in driver.find_elements(By.CSS_SELECTOR, "a, button"):
            if visible(element) and has_words(label_of(element), "apply"):
                return "external", element
        return "no_apply_button", None
    # The label depends on the account's language, so the link shape decides; clicking confirms it.
    external = (button.tag_name == "a"
                or bool(button.find_elements(By.CSS_SELECTOR, "[data-test-icon*='link-external'], li-icon[type*='link-external']")))
    return ("external", button) if external else ("candidate", button)


def dialog(driver, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for element in driver.find_elements(By.CSS_SELECTOR, DIALOG):
            try:
                if element.is_displayed() and element.find_elements(By.CSS_SELECTOR, "button"):
                    return element
            except StaleElementReferenceException:
                continue
        time.sleep(0.3)
    raise NeedsManual("Easy Apply dialog did not open")


def open_dialog_present(driver):
    return any(visible(element) for element in driver.find_elements(By.CSS_SELECTOR, DIALOG))


def question_text(driver, control):
    """Best available label for a form control."""
    control_id = control.get_attribute("id")
    if control_id:
        labels = driver.find_elements(By.CSS_SELECTOR, f"label[for='{control_id}']")
        if labels and labels[0].text.strip():
            return labels[0].text.strip()
    for script in (
        # Workable and other ARIA-labelled forms name the question by id.
        "const el = arguments[0]; const owner = el.getAttribute('aria-labelledby') ? el :"
        " el.closest('[role=radiogroup][aria-labelledby], fieldset[aria-labelledby], [role=group][aria-labelledby]');"
        " if (!owner) return ''; return owner.getAttribute('aria-labelledby').split(/\\s+/)"
        ".map(id => document.getElementById(id)).filter(Boolean).map(e => e.innerText).join(' ');",
        "const f = arguments[0].closest('fieldset'); const l = f && f.querySelector('legend'); return l ? l.innerText : '';",
        "const g = arguments[0].closest('.jobs-easy-apply-form-section__grouping, .fb-dash-form-element, [data-test-form-element]');"
        " const l = g && g.querySelector('label, legend, span[aria-hidden=true]'); return l ? l.innerText : '';",
        # Company career sites (Lever and others) wrap the field in its label.
        "const l = arguments[0].closest('label'); return l ? l.innerText.split('\\n')[0] : '';",
    ):
        text = (driver.execute_script(script, control) or "").strip()
        if text:
            return text
    text = (control.get_attribute("aria-label") or control.get_attribute("placeholder") or "").strip()
    if text:
        return text
    # Last resort: the first line of text in the nearest container that has some.
    return (driver.execute_script(
        "let el = arguments[0]; for (let i = 0; i < 4 && el; i++) { el = el.parentElement;"
        " const t = el && el.innerText.trim(); if (t) return t.split('\\n')[0]; } return '';", control) or "").strip()


def clean_question(text):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    # LinkedIn repeats labels for screen readers; keep the first copy.
    return " ".join(dict.fromkeys(lines)).replace("*", "").replace("✱", "").strip()


def is_required(driver, control, question):
    return bool(control.get_attribute("required") or control.get_attribute("aria-required") == "true"
                or "required" in (question or "").lower()
                or driver.execute_script("const g = arguments[0].closest('fieldset, .fb-dash-form-element, "
                                         ".jobs-easy-apply-form-section__grouping, label, .application-question');"
                                         " return !!(g && /[*✱]/.test(g.innerText));", control))


class FormFiller:
    """Fills the visible step of a form.

    answer_fn(question, kind, options, required) -> (value, source); optional questions are answered
    only from the applicant's own data, never written by the AI.
    """

    def __init__(self, driver, answer_fn, files):
        self.driver, self.answer_fn, self.files = driver, answer_fn, files
        self.log = []
        self.uploaded = set()

    def option_label(self, radio):
        """(visible text, element to click) for one radio option."""
        label = self.label_for(radio) if radio.get_attribute("id") else None
        if label is None or not label.text.strip():
            found = radio.find_elements(By.XPATH, "./ancestor::label[1]")
            label = found[0] if found else label
        text = (label.text if label is not None else "").strip() or (radio.get_attribute("value") or "").strip()
        return text, (label if label is not None and visible(label) else radio)

    def answer(self, question, kind, options, required):
        if not question.strip():
            return None, None  # A field nobody can read is never guessed at.
        return self.answer_fn(question, kind, options, required)

    def record(self, question, value, source):
        self.log.append({"question": question, "answer": value, "source": source})

    def unanswered(self, question):
        raise NeedsManual(f"No truthful answer for required question: {question}", self.log)

    def label_for(self, control):
        found = self.driver.find_elements(By.CSS_SELECTOR, f"label[for='{control.get_attribute('id')}']")
        return found[0] if found else None

    def fill(self, box):
        self._files(box)
        self._radios(box)
        self._selects(box)
        self._texts(box)
        self._checkboxes(box)

    def _files(self, box):
        if "cv" not in self.uploaded and not box.find_elements(By.CSS_SELECTOR, "input[type='file']"):
            # The 2026 dialog has no file input until its upload button creates one.
            for button in box.find_elements(By.CSS_SELECTOR, "button"):
                label = label_of(button)
                if visible(button) and has_words(label, "upload") and not has_words(label, "cover"):
                    if upload_via_button(self.driver, button, self.files["cv"]):
                        self.uploaded.add("cv")
                        self.record(label, Path(self.files["cv"]).name, "file")
                        pause(2, 3)
                    break
        if self.files.get("cover_letter") and "cover" not in self.uploaded \
                and not box.find_elements(By.CSS_SELECTOR, "input[type='file']"):
            for button in box.find_elements(By.CSS_SELECTOR, "button"):
                label = label_of(button)
                if visible(button) and has_words(label, "upload") and has_words(label, "cover"):
                    if upload_via_button(self.driver, button, self.files["cover_letter"]):
                        self.uploaded.add("cover")
                        self.record(label, Path(self.files["cover_letter"]).name, "file")
                        pause(2, 3)
                    break
        for control in box.find_elements(By.CSS_SELECTOR, "input[type='file']"):
            # LinkedIn hides the file input itself behind an upload button, so check its container.
            key = control.get_attribute("id") or control.get_attribute("name") or "file"
            label = self.label_for(control) if control.get_attribute("id") else None
            shown = (visible(control) or visible(control.find_element(By.XPATH, ".."))
                     or (label is not None and visible(label)))
            if key in self.uploaded or not shown:
                continue
            question = clean_question(question_text(self.driver, control)) or "file upload"
            if has_words(question + " " + key, "cover"):
                path = self.files.get("cover_letter")
                if not path:
                    if is_required(self.driver, control, question):
                        self.unanswered(question)
                    continue
            elif "cv" in self.uploaded:
                continue
            else:
                path = self.files["cv"]
                self.uploaded.add("cv")
            control.send_keys(str(Path(path).resolve()))
            self.uploaded.add(key)
            self.record(question, Path(path).name, "file")
            pause(2, 3)

    def _radios(self, box):
        self._radio_fieldsets(box)
        self._radio_groups(box)

    def _radio_groups(self, box):
        """Radio buttons outside a fieldset, grouped by name (Lever and other career sites)."""
        groups = {}
        for radio in box.find_elements(By.CSS_SELECTOR, "input[type='radio']"):
            if self.driver.execute_script("return !!arguments[0].closest('fieldset')", radio):
                continue
            groups.setdefault(radio.get_attribute("name") or radio.get_attribute("id"), []).append(radio)
        for radios in groups.values():
            if any(radio.is_selected() for radio in radios):
                continue
            choices = dict(self.option_label(radio) for radio in radios)
            if not any(visible(target) for target in choices.values()):
                continue
            # The question is the group's container text before its first option.
            raw = (self.driver.execute_script(
                "const radios = arguments[0]; let el = radios[0];"
                " while (el.parentElement && !radios.every(r => el.contains(r))) el = el.parentElement;"
                " for (let i = 0; i < 3 && el.parentElement; i++) {"
                "   el = el.parentElement; const lines = el.innerText.split('\\n').map(s => s.trim()).filter(Boolean);"
                "   const first = lines.find(line => !arguments[1].includes(line)); if (first) return first; }"
                " return '';", radios, list(choices)) or "")
            question = clean_question(raw)
            required = any(radio.get_attribute("required") for radio in radios) or bool(re.search(r"[*✱]", raw))
            if not question or question in choices:
                if required:
                    self.unanswered(question or "a choice without a readable question")
                continue
            value, source = self.answer(question, "choice", list(choices), required)
            if value is None:
                if required:
                    self.unanswered(question)
                continue
            click(self.driver, choices[value])
            self.record(question, value, source)

    def _radio_fieldsets(self, box):
        for fieldset in box.find_elements(By.CSS_SELECTOR, "fieldset"):
            radios = fieldset.find_elements(By.CSS_SELECTOR, "input[type='radio']")
            if not radios or not visible(fieldset) or any(radio.is_selected() for radio in radios):
                continue
            legend = fieldset.find_elements(By.CSS_SELECTOR, "legend")
            question = clean_question(legend[0].text if legend else question_text(self.driver, radios[0]))
            if has_words(question, "resume") or fieldset.find_elements(By.CSS_SELECTOR, "input[type='file']") or any(
                    has_words(label_of(button), "upload") for button in fieldset.find_elements(By.CSS_SELECTOR, "button")):
                continue  # The list of uploaded CVs; the upload above selects the tailored one.
            choices = dict(self.option_label(radio) for radio in radios)
            required = not re.search(r"optional|neobligāt|необязательн", question.lower())
            if not question or question in choices:
                if required:
                    self.unanswered(question or "a choice without a readable question")
                continue
            value, source = self.answer(question, "choice", list(choices), required)
            if value is None:
                if required:
                    self.unanswered(question)
                continue
            click(self.driver, choices[value])
            self.record(question, value, source)

    def _selects(self, box):
        for control in box.find_elements(By.CSS_SELECTOR, "select"):
            if not visible(control):
                continue
            select = Select(control)
            current = (select.first_selected_option.text or "").strip().lower()
            if current and not current.startswith(SELECT_PLACEHOLDERS):
                continue
            question = clean_question(question_text(self.driver, control))
            options = [o.text.strip() for o in select.options
                       if o.text.strip() and not o.text.strip().lower().startswith(SELECT_PLACEHOLDERS)]
            required = is_required(self.driver, control, question)
            value, source = self.answer(question, "choice", options, required)
            if value is None:
                if required:
                    self.unanswered(question)
                continue
            select.select_by_visible_text(value)
            self.record(question, value, source)

    def _texts(self, box):
        selector = ("input:not([type='hidden']):not([type='radio']):not([type='checkbox']):not([type='file'])"
                    ":not([type='submit']):not([type='button']), textarea")
        for control in box.find_elements(By.CSS_SELECTOR, selector):
            if not visible(control) or (control.get_attribute("value") or "").strip():
                continue
            question = clean_question(question_text(self.driver, control))
            if control.get_attribute("role") == "combobox" and control.get_attribute("aria-autocomplete") != "none" \
                    and self._combobox(control, question):
                continue
            kind = "number" if (control.get_attribute("type") == "number"
                                or "numeric" in (control.get_attribute("id") or "")) else (
                "long_text" if control.tag_name == "textarea" else "text")
            required = is_required(self.driver, control, question)
            if has_words(question, "cover") or (kind == "long_text" and re.search(
                    r"motivation|why (do you want|are you interested)|tell us about yourself|message to|"
                    r"anything else|additional information", question.lower()) and self.files.get("cover_letter_text")):
                # The checked, job-specific letter goes into every cover-letter style field.
                value, source = self.files.get("cover_letter_text"), "cover_letter"
            else:
                value, source = self.answer(question, kind, None, required)
            if value is None:
                if required:
                    self.unanswered(question)
                continue
            control.clear()
            control.send_keys(str(value))
            if control.get_attribute("role") == "combobox":
                # A location typeahead: pick the suggestion that matches, else the first one.
                pause(1.5, 2.5)
                options = self.visible_options(control)
                wanted = str(value).split(",")[0].strip().lower()
                matching = [o for o in options if wanted and wanted in o.text.lower()] or options
                if matching:
                    click(self.driver, matching[0])
                else:
                    control.send_keys(Keys.ARROW_DOWN, Keys.ENTER)
            self.record(question, value, source)
            pause(0.3, 0.8)

    def visible_options(self, control):
        listbox_id = control.get_attribute("aria-controls") or control.get_attribute("aria-owns")
        scope = self.driver.find_elements(By.ID, listbox_id) if listbox_id else []
        options = (scope[0] if scope else self.driver).find_elements(By.CSS_SELECTOR, "[role='option']")
        return [option for option in options if visible(option) and option.text.strip()]

    def _combobox(self, control, question):
        """Dropdowns built from a text input (react-select on Greenhouse and similar).

        Returns True when handled. A combobox that shows no list stays a plain typeahead.
        """
        key = control.get_attribute("id") or question
        if key in self.uploaded:
            return True
        try:
            control.click()
        except (ElementNotInteractableException, WebDriverException):
            return False
        pause(0.8, 1.4)
        options = self.visible_options(control)
        if not options or len(options) > 60:
            control.send_keys(Keys.ESCAPE)
            return False
        texts = [option.text.strip() for option in options]
        required = is_required(self.driver, control, question)
        value, source = self.answer(question, "choice", texts, required)
        if value is None:
            control.send_keys(Keys.ESCAPE)
            if required:
                self.unanswered(question)
            self.uploaded.add(key)
            return True
        click(self.driver, options[texts.index(value)])
        self.uploaded.add(key)
        self.record(question, value, source)
        pause(0.3, 0.8)
        return True

    def toggle(self, control, label):
        """Click a checkbox; custom-styled ones may ignore real clicks, so fall back to a DOM click."""
        before = control.is_selected()
        for target in (label, control):
            if target is not None and visible(target):
                try:
                    target.click()
                except (ElementNotInteractableException, WebDriverException):
                    continue
                if control.is_selected() != before:
                    return
        self.driver.execute_script("arguments[0].click();", control)

    def _checkboxes(self, box):
        for control in box.find_elements(By.CSS_SELECTOR, "input[type='checkbox']"):
            label = self.label_for(control)
            # The 2026 dialog leaves the checkbox label empty and puts its text in a neighbouring element.
            text = ((label.get_attribute("textContent") or "").strip() if label is not None else "") \
                or question_text(self.driver, control) or self.driver.execute_script(
                    "let el = arguments[0]; for (let i = 0; i < 4 && el; i++) { el = el.parentElement;"
                    " if (el && el.innerText.trim()) return el.innerText.slice(0, 300); } return '';", control)
            question = clean_question(text)
            if has_words(question, "follow"):
                # Following the company is not part of applying.
                if control.is_selected():
                    self.toggle(control, label)
                    self.record(question, "unchecked" if not control.is_selected() else "still checked", "rule")
                continue
            if not (visible(control) or (label is not None and visible(label))):
                continue
            if control.is_selected() or not is_required(self.driver, control, question):
                continue
            value, source = self.answer(question, "choice", ["Yes", "No"], True)
            if value != "Yes":
                self.unanswered(question)
            self.toggle(control, label)
            self.record(question, "checked", source)


def footer_action(box):
    """The step's main button and what it does: submit, review or next."""
    buttons = [b for b in box.find_elements(By.CSS_SELECTOR, "button") if visible(b) and b.is_enabled()]
    for key in ("submit", "review", "next"):
        for button in buttons:
            if has_words(label_of(button), key) and not has_words(label_of(button), "dismiss"):
                return key, button
    primary = [b for b in buttons if "artdeco-button--primary" in (b.get_attribute("class") or "")]
    return ("next", primary[-1]) if primary else (None, None)


UPLOAD_HOOK = """
window.__jobscraperFileInputs = [];
if (!HTMLInputElement.prototype.__jobscraperHooked) {
  const original = HTMLInputElement.prototype.click;
  HTMLInputElement.prototype.click = function () {
    if (this.type === 'file') {
      // Keep the input instead of opening the system file dialog; the program sets the file itself.
      if (!this.isConnected) { this.style.display = 'none'; document.body.appendChild(this); }
      window.__jobscraperFileInputs.push(this);
      return;
    }
    return original.apply(this, arguments);
  };
  HTMLInputElement.prototype.__jobscraperHooked = true;
}
"""


def upload_via_button(driver, button, path):
    """Click an upload button that creates its file input on demand, and give that input the file."""
    driver.execute_script(UPLOAD_HOOK)
    before = set(driver.find_elements(By.CSS_SELECTOR, "input[type='file']"))
    button.click()
    pause(1, 2)
    element = driver.execute_script("return window.__jobscraperFileInputs.pop() || null")
    if element is None:
        created = [e for e in driver.find_elements(By.CSS_SELECTOR, "input[type='file']") if e not in before]
        element = created[0] if created else None
    if element is None:
        return False
    element.send_keys(str(Path(path).resolve()))
    return True


def close_dialog(driver):
    """Leave the application without saving it as a draft."""
    boxes = [box for box in driver.find_elements(By.CSS_SELECTOR, DIALOG) if visible(box)]
    candidates = [button for button in (boxes[0].find_elements(By.CSS_SELECTOR, "button") if boxes else [])
                  if visible(button) and has_words(label_of(button), "dismiss")]
    for selector in ("button[aria-label*='Dismiss']", "button.artdeco-modal__dismiss", "button[data-test-modal-close-btn]"):
        candidates += [button for button in driver.find_elements(By.CSS_SELECTOR, selector) if visible(button)]
    if candidates:
        candidates[0].click()
        pause()
    else:
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        pause()
    for button in driver.find_elements(By.CSS_SELECTOR, "button"):
        try:
            if visible(button) and has_words(label_of(button), "discard"):
                button.click()
                pause()
                return
        except (StaleElementReferenceException, ElementNotInteractableException):
            continue


def save_failure(driver, screenshot_path):
    """Keep the step that stopped the application, to adjust the selectors or answers."""
    try:
        folder = Path(screenshot_path).parent
        driver.save_screenshot(str(folder / "stopped.png"))
        boxes = [element for element in driver.find_elements(By.CSS_SELECTOR, DIALOG) if visible(element)]
        if boxes:
            (folder / "stopped_dialog.html").write_text(boxes[0].get_attribute("outerHTML"), encoding="utf-8")
    except (WebDriverException, OSError):
        pass


def run_application(driver, button, answer_fn, files, submit, screenshot_path, max_steps=15):
    """Return (status, answers): 'ready' when stopped before Submit, 'applied' or 'unconfirmed' after it."""
    windows, start = set(driver.window_handles), urlsplit(driver.current_url or "").netloc
    button.click()
    pause(1.5, 2.5)
    opened = [handle for handle in driver.window_handles if handle not in windows]
    if opened:
        for handle in opened:
            driver.switch_to.window(handle)
            driver.close()
        driver.switch_to.window(next(iter(windows)))
        raise External("The apply button opened another site")
    if not open_dialog_present(driver) and urlsplit(driver.current_url or "").netloc != start:
        raise External("The apply button opened another site")
    filler = FormFiller(driver, answer_fn, files)
    last_errors = None
    try:
        for _ in range(max_steps):
            check_session(driver)
            box = dialog(driver)
            if has_words(box.text, "limit"):
                raise SessionProblem("LinkedIn reports the Easy Apply limit for today")
            filler.fill(box)
            action, target = footer_action(box)
            if action is None:
                raise NeedsManual("No Continue/Review/Submit button found", filler.log)
            if action == "submit":
                box.screenshot(str(screenshot_path))
                if not submit:
                    return "ready", filler.log
                target.click()
                pause(3, 5)
                page = driver.find_element(By.TAG_NAME, "body").text
                if has_words(page, "limit"):
                    raise SessionProblem("LinkedIn reports the Easy Apply limit for today")
                return ("applied" if has_words(page, "applied") else "unconfirmed"), filler.log
            target.click()
            pause(1.2, 2.2)
            errors = [e.text.strip() for e in dialog(driver).find_elements(By.CSS_SELECTOR, ERRORS) if e.text.strip()]
            if errors:
                if errors == last_errors:
                    raise NeedsManual("Form errors: " + "; ".join(errors), filler.log)
                last_errors = errors
        raise NeedsManual("Too many form steps", filler.log)
    except (NoSuchElementException, TimeoutException, StaleElementReferenceException,
            ElementNotInteractableException, ElementClickInterceptedException, KeyError) as error:
        save_failure(driver, screenshot_path)
        raise NeedsManual(f"Unexpected form layout: {type(error).__name__}: {str(error)[:200]}", filler.log) from error
    except NeedsManual:
        save_failure(driver, screenshot_path)
        raise
    finally:
        try:
            if open_dialog_present(driver):
                close_dialog(driver)
        except WebDriverException:
            pass
