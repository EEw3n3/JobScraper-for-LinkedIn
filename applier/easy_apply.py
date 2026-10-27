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

from applier.vocabulary import OPTIONAL, REQUIRED

APPLY_BUTTONS =("button.jobs-apply-button, button[data-view-name='job-apply-button'], "
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
    "upload": ("upload", "attach", "augšupielādēt", "augšuplādēt", "pievienot", "загрузить", "прикрепить", "lae üles",
               "įkelti", "hochladen"),
    "apply": ("apply", "подать заявку", "pieteikties"),
    "cover": ("cover", "motivation", "application letter", "сопроводительн", "мотивацион", "письм", "motivācijas",
              "pavadvēstul", "vēstul", "izvēlieties pieteikumu", "kaaskiri", "motyvacin", "anschreiben"),
    "resume": ("resume", "curriculum", "dzīvesgājum", "резюме", "cv"),
    "applied": ("application was sent", "application submitted", "pieteikums nosūtīts", "отклик отправлен",
                "заявка отправлена"),
    "limit": ("easy apply limit", "application limit", "you've reached", "limit for today"),
    "follow": ("follow", "sekot", "отслежива", "подписаться"),
    # LinkedIn's newer markup has generated class names, so the Easy Apply button is found by its label.
    "easy": ("easy apply", "простой подачи", "простая подача", "vienkāršā pieteikšan", "vienkāršo pieteikšan"),
}
# A file picker's button: "Select file" (ReachMee), "Choose file", "Browse".
FILE_CHOOSER = re.compile(r"^(select|choose|add|attach) (a )?(file|document)s?$|^browse( files)?$|"
                          r"^(izvēlēties|izvēlieties|pievienot) (failu|dokumentu)$|^выбрать файл$|^выберите файл$")
SELECT_PLACEHOLDERS = ("select an option", "select...", "please select", "choose...", "izvēlieties", "izvēlies",
                       "выберите", "vali", "pasirinkite", "bitte wählen", "--", "—")


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


# Placeholders that say "type here" instead of naming the field; the label is then the text around the box.
GENERIC_PLACEHOLDER = re.compile(r"^(please )?(enter|type|write|fill( in)?)( here| text| value)?\.{0,3}$|"
                                 r"^(your )?answer( to the question| here)?$|^text$|^atbilde( uz jautājumu)?$|"
                                 r"^ответ( на вопрос)?$|"
                                 r"^(ievadiet|ievadi|rakstiet|введите|напишите)( šeit| tekstu| текст| здесь)?\.{0,3}$|"
                                 r"^\+?\d[\d ()-]{5,}$", re.I)


def question_text(driver, control):
    """Best available label for a form control. A label that only says "Your answer" gives way to the
    question written above the box (cv.lv's screening questions are headings before their text areas)."""
    text = _label_text(driver, control)
    if not text or GENERIC_PLACEHOLDER.search(clean_question(text)):
        above = (driver.execute_script(
            "let el = arguments[0];"
            " for (let i = 0; i < 4 && el; i++, el = el.parentElement) {"
            "   for (let p = el.previousElementSibling; p; p = p.previousElementSibling) {"
            "     const t = (p.innerText || '').trim(); if (t) return t.split('\\n').pop(); } }"
            " return '';", control) or "").strip()
        if above:
            return above
    return text


def _label_text(driver, control):
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
    if text and not GENERIC_PLACEHOLDER.search(text):
        return text
    # Last resort: the first line of text in the nearest container that has some.
    return (driver.execute_script(
        "let el = arguments[0]; for (let i = 0; i < 4 && el; i++) { el = el.parentElement;"
        " const t = el && el.innerText.trim(); if (t) return t.split('\\n')[0]; } return '';", control) or "").strip()


# What a field holds by its HTML autocomplete token, or by its name/id when the label says nothing.
AUTOCOMPLETE_HINTS = {
    "given-name": "first name", "family-name": "last name", "name": "full name", "email": "email", "tel": "phone",
    "tel-national": "phone", "url": "website", "organization": "current company", "address-level2": "city",
    "country-name": "country", "country": "country",
}
NAME_HINTS = {
    "firstname": "first name", "fname": "first name", "givenname": "first name", "lastname": "last name",
    "lname": "last name", "surname": "last name", "familyname": "last name", "fullname": "full name",
    "name": "full name", "email": "email", "emailaddress": "email", "mail": "email", "phone": "phone",
    "phonenumber": "phone", "tel": "phone", "telephone": "phone", "mobile": "phone", "linkedin": "linkedin",
    "linkedinurl": "linkedin", "github": "github", "city": "city", "country": "country",
}


def with_hint(control, question):
    """The label plus what the field's autocomplete token (or, without any label, its name) says it
    holds: an unlabelled box named "email" becomes "email"."""
    tokens = (control.get_attribute("autocomplete") or "").lower().split()
    hint = next((AUTOCOMPLETE_HINTS[token] for token in reversed(tokens) if token in AUTOCOMPLETE_HINTS), None)
    if hint is None and not question.strip():
        key = re.sub(r"[^a-z]", "", (control.get_attribute("name") or control.get_attribute("id") or "").lower())
        hint = NAME_HINTS.get(key)
    if hint and hint not in question.lower():
        return f"{question} ({hint})" if question else hint
    return question


def clean_question(text):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    # LinkedIn repeats labels for screen readers; keep the first copy.
    return " ".join(dict.fromkeys(lines)).replace("*", "").replace("✱", "").strip()


def trap(driver, control):
    """A honeypot: a box that people cannot see and only bots fill in. Filling it marks the application as spam."""
    names = " ".join(control.get_attribute(attribute) or "" for attribute in ("name", "id", "class")).lower()
    if re.search(r"honeypot|(^|[\s_-])hp[_-]|bot[_-]?field|leave[_-]?(this[_-]?)?(blank|empty)", names):
        return True
    return bool(driver.execute_script(
        "const el = arguments[0], r = el.getBoundingClientRect(), s = getComputedStyle(el);"
        " return r.right + scrollX <= 0 || r.bottom + scrollY <= 0 || r.left + scrollX >= innerWidth + 1000"
        " || parseFloat(s.opacity) === 0"
        " || el.getAttribute('aria-hidden') === 'true';", control))


def is_required(driver, control, question):
    lowered = (question or "").lower()
    if any(word in lowered for word in OPTIONAL):
        return False
    return bool(control.get_attribute("required") or control.get_attribute("aria-required") == "true"
                or any(word in lowered for word in REQUIRED)
                or driver.execute_script("const g = arguments[0].closest('fieldset, .fb-dash-form-element, "
                                         ".jobs-easy-apply-form-section__grouping, label, .application-question');"
                                         " return !!(g && /[*✱]/.test(g.innerText));", control))


class FormFiller:
    """Fills the visible step of a form.

    answer_fn(question, kind, options, required) -> (value, source); optional questions are answered
    only from the applicant's own data, never written by the AI.
    plan=True only decides what every field would get, without typing, clicking or uploading
    (the form check in tools/form_lab.py); unanswered required questions are collected in missing.
    """

    def __init__(self, driver, answer_fn, files, plan=False):
        self.driver, self.answer_fn, self.files, self.plan = driver, answer_fn, files, plan
        self.log = []
        self.uploaded = set()
        self.missing = []

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
        if self.plan:
            self.missing.append(question)
            return
        raise NeedsManual(f"No truthful answer for required question: {question}", self.log)

    def label_for(self, control):
        found = self.driver.find_elements(By.CSS_SELECTOR, f"label[for='{control.get_attribute('id')}']")
        return found[0] if found else None

    def fill(self, box):
        self._files(box)
        self._radios(box)
        self._selects(box)
        self._texts(box)
        self._checkboxes(box, self._checkbox_groups(box))

    def _files(self, box):
        if not box.find_elements(By.CSS_SELECTOR, "input[type='file']"):
            # The 2026 dialog (and ReachMee) has no file input in the form: its upload button creates or
            # opens one elsewhere on the page.
            for kind, key in (("cv", "cv"), ("cover", "cover_letter")):
                if kind in self.uploaded or not self.files.get(key):
                    continue
                for button in box.find_elements(By.CSS_SELECTOR, "button"):
                    label, context = self.upload_button(button)
                    if label is None or (kind == "cover") != (has_words(context, "cover")
                                                              or bool(re.search(r"pldocument|personal letter",
                                                                                context))):
                        continue
                    if self.plan or upload_via_button(self.driver, button, self.files[key]):
                        self.uploaded.add(kind)
                        self.record(label, Path(self.files[key]).name, "file")
                        pause(2, 3)
                    break
        for control in box.find_elements(By.CSS_SELECTOR, "input[type='file']"):
            try:
                self._file_input(control)
            except StaleElementReferenceException:
                continue  # The form redrew itself after an upload; the next pass finds the new input.

    def upload_button(self, button):
        """(label, context) of a visible button that uploads a document, else (None, None). The context adds
        the heading the button names in aria-labelledby and its id ("button-prof_cvdocument")."""
        if not visible(button):
            return None, None
        label = label_of(button)
        context = " ".join(filter(None, (label, button.get_attribute("id") or "", self.driver.execute_script(
            "return (arguments[0].getAttribute('aria-labelledby') || '').split(/\\s+/)"
            ".map(id => document.getElementById(id)).filter(Boolean).map(e => e.innerText).join(' ');",
            button) or ""))).lower()
        if not (has_words(label, "upload") or FILE_CHOOSER.search(label.lower())) \
                or re.search(r"photo|foto|фото|picture|attēl|avatar", context):
            return None, None
        return label, context

    def _file_input(self, control):
        # The element itself is the key: cv.lv gives the CV and the letter inputs the same id and name.
        key = control.id
        if key in self.uploaded:
            return
        # LinkedIn hides the file input itself behind an upload button, so check its container.
        label = self.label_for(control) if control.get_attribute("id") else None
        shown = (visible(control) or visible(control.find_element(By.XPATH, ".."))
                 or (label is not None and visible(label)))
        question = clean_question(question_text(self.driver, control)) or "file upload"
        # A drop zone's own label says only "browse"; the heading of its section says what goes there.
        section = self.driver.execute_script(
            # Only a heading written before the upload counts: a later one belongs to the next question.
            "const input = arguments[0]; let el = input;"
            " for (let i = 0; i < 14 && el && el.tagName !== 'FORM'; i++, el = el.parentElement) {"
            "  const before = [...el.querySelectorAll('h1, h2, h3, h4, h5, h6, legend')].filter(h => h.innerText.trim()"
            "   && (h.contains(input) || (h.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING)));"
            "  if (before.length) return before[before.length - 1].innerText.trim(); } return '';", control) or ""
        if section and section.lower() not in question.lower():
            question = f"{clean_question(section)}: {question}"
        accept = (control.get_attribute("accept") or "").lower()
        names = f"{control.get_attribute('id') or ''} {control.get_attribute('name') or ''}"
        if not shown and not re.search(r"resume|\bcv\b|curriculum|cover|letter|attach|document|pdf|\.doc|"
                                       r"dzīvesgāj|резюме|vēstul|письм", f"{names} {accept} {question}".lower()):
            # Career sites hide a document input behind their own button (Breezy's "cResume"); Chrome
            # still takes a file for it. Other hidden inputs are left alone.
            return
        if (accept and all(kind.strip().startswith("image") for kind in accept.split(","))) or re.search(
                r"photo|foto|фото|picture|attēl|avatar", f"{question} {names}".lower()):
            return  # A profile photo: the CV never goes there, and no photo is sent.
        kind = "cover" if has_words(question + " " + names, "cover") else "cv"
        path = self.files.get("cover_letter" if kind == "cover" else "cv")
        if kind in self.uploaded or not path:
            if not path and is_required(self.driver, control, question):
                self.unanswered(question)
            return
        if not self.plan:
            control.send_keys(str(Path(path).resolve()))
            pause(2, 3)
        self.uploaded.update((kind, key))
        self.record(question, Path(path).name, "file")

    def _radios(self, box):
        self._radio_fieldsets(box)
        self._radio_groups(box)

    def group_question(self, controls, choices):
        """The question of a group of options: the nearest text above its first option, looking in the
        smallest container of the group and then a few containers further out."""
        return (self.driver.execute_script(
            "const inputs = arguments[0], options = arguments[1]; let el = inputs[0];"
            " while (el.parentElement && !inputs.every(r => el.contains(r))) el = el.parentElement;"
            " let fallback = '';"
            " for (let i = 0; i < 4 && el; i++, el = el.parentElement) {"
            "   const lines = el.innerText.split('\\n').map(s => s.trim()).filter(Boolean);"
            "   const first = lines.findIndex(line => options.includes(line));"
            "   if (first > 0) return lines[first - 1];"
            "   if (!fallback && first < 0) fallback = lines.find(line => !options.includes(line)) || ''; }"
            " return fallback;", controls, list(choices)) or "")

    def _checkbox_groups(self, box):
        """Checkbox lists of one question ("Which countries can you work in?"): ticks the option the answer names.

        Returns the names of the groups, so that _checkboxes leaves their boxes alone.
        """
        groups = {}
        for control in box.find_elements(By.CSS_SELECTOR, "input[type='checkbox']"):
            if control.get_attribute("name"):
                groups.setdefault(control.get_attribute("name"), []).append(control)
        handled = set()
        for name, controls in groups.items():
            if len(controls) < 2:
                continue
            handled.add(name)
            if any(control.is_selected() for control in controls):
                continue
            choices = dict(self.option_label(control) for control in controls)
            if not any(visible(target) for target in choices.values()):
                continue
            raw = self.group_question(controls, choices)
            question = clean_question(raw)
            required = any(control.get_attribute("required") for control in controls) or bool(re.search(r"[*✱]", raw))
            if not question or question in choices:
                if required:
                    self.unanswered(question or "a list of options without a readable question")
                continue
            value, source = self.answer(question, "choice", list(choices), required)
            if value is None:
                if required:
                    self.unanswered(question)
                continue
            if not self.plan:
                click(self.driver, choices[value])
            self.record(question, value, source)
        return handled

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
            raw = self.group_question(radios, choices)
            question = clean_question(raw)
            if has_words(question, "resume") or (radios[0].get_attribute("name") or "").lower() in ("cv", "resume"):
                continue  # A list of CVs saved on the site; the tailored CV is uploaded instead.
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
            if not self.plan:
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
            required = not any(word in question.lower() for word in OPTIONAL)
            if not question or question in choices:
                if required:
                    self.unanswered(question or "a choice without a readable question")
                continue
            value, source = self.answer(question, "choice", list(choices), required)
            if value is None:
                if required:
                    self.unanswered(question)
                continue
            if not self.plan:
                click(self.driver, choices[value])
            self.record(question, value, source)

    def _selects(self, box):
        for control in box.find_elements(By.CSS_SELECTOR, "select"):
            if not visible(control):
                continue
            select = Select(control)

            def placeholder(option):
                # "Select...", "-- Izvēlieties --" or an option without a value stands for "nothing chosen".
                text = (option.text or "").strip().lower()
                return not text or text.startswith(SELECT_PLACEHOLDERS) or option.get_attribute("value") == ""

            try:
                chosen = select.first_selected_option
            except NoSuchElementException:
                chosen = None  # nothing selected yet (some lists start without even a placeholder)
            if chosen is not None and not placeholder(chosen):
                continue
            raw = question_text(self.driver, control)
            question = with_hint(control, clean_question(raw))
            options = [o.text.strip() for o in select.options if not placeholder(o)]
            if not question and sum(bool(re.search(r"\+\d{1,3}\b", option)) for option in options) > len(options) / 2:
                question = "Phone country code"  # an unlabelled list of dialling codes next to the phone box
            required = is_required(self.driver, control, question) or bool(re.search(r"[*✱]", raw))
            value, source = self.answer(question, "choice", options, required)
            if value is None:
                if required:
                    self.unanswered(question)
                continue
            if not self.plan:
                select.select_by_visible_text(value)
            self.record(question, value, source)

    def _texts(self, box):
        selector = ("input:not([type='hidden']):not([type='radio']):not([type='checkbox']):not([type='file'])"
                    ":not([type='submit']):not([type='button']), textarea")
        for control in box.find_elements(By.CSS_SELECTOR, selector):
            if not visible(control) or (control.get_attribute("value") or "").strip() or trap(self.driver, control) \
                    or self.skip_text(control):
                continue
            raw = question_text(self.driver, control)
            question = with_hint(control, clean_question(raw))
            if control.get_attribute("role") == "combobox" and control.get_attribute("aria-autocomplete") != "none" \
                    and self._combobox(control, question):
                continue
            kind = "number" if (control.get_attribute("type") == "number"
                                or "numeric" in (control.get_attribute("id") or "")) else (
                "long_text" if control.tag_name == "textarea" else "text")
            required = is_required(self.driver, control, question) or bool(re.search(r"[*✱]", raw))
            if has_words(question, "cover") or (kind == "long_text" and re.search(
                    r"motivation|why (do you want|are you interested|you would be|you are)|(perfect|good|great) fit|"
                    r"tell us about yourself|message to|"
                    r"anything else|additional information|kāpēc|motivāc|papildu informāc|komentār|ziņ[au]|"
                    r"par sevi|почему|мотивац|дополнительн|комментар|сообщени|о себе|miks|kodėl",
                    question.lower()) and self.files.get("cover_letter_text")):
                # The checked, job-specific letter goes into every cover-letter style field.
                value, source = self.files.get("cover_letter_text"), "cover_letter"
            else:
                value, source = self.answer(question, kind, None, required)
            if value is None:
                if required:
                    self.unanswered(question)
                continue
            if self.plan:
                self.record(question, value, source)
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

    def skip_text(self, control):
        """A text box that a site-specific filler handles itself (Workday's search lists)."""
        return False

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
        if value is None or self.plan:
            control.send_keys(Keys.ESCAPE)
            if value is None and required:
                self.unanswered(question)
            self.uploaded.add(key)
            if value is not None:
                self.record(question, value, source)
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

    def _checkboxes(self, box, grouped=()):
        for control in box.find_elements(By.CSS_SELECTOR, "input[type='checkbox']"):
            if control.get_attribute("name") in grouped:
                continue
            label = self.label_for(control)
            # The 2026 dialog leaves the checkbox label empty and puts its text in a neighbouring element.
            text = ((label.get_attribute("textContent") or "").strip() if label is not None else "") \
                or question_text(self.driver, control) or self.driver.execute_script(
                    "let el = arguments[0]; for (let i = 0; i < 4 && el; i++) { el = el.parentElement;"
                    " if (el && el.innerText.trim()) return el.innerText.slice(0, 300); } return '';", control)
            question = clean_question(text)
            if has_words(question, "follow"):
                # Following the company is not part of applying.
                if control.is_selected() and not self.plan:
                    self.toggle(control, label)
                    self.record(question, "unchecked" if not control.is_selected() else "still checked", "rule")
                continue
            if not (visible(control) or (label is not None and visible(label))):
                continue
            if control.is_selected() or not is_required(self.driver, control, question):
                continue
            value, source = self.answer(question, "checkbox", ["Yes", "No"], True)
            if value != "Yes":
                self.unanswered(question)
                continue
            if not self.plan:
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
