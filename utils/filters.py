import re
from collections import Counter

from config.settings import CLOUD_SIGNALS, STOP_SENIOR_WORDS


def _has_word(text, words):
    # Whole words only: "intern" must not match "international".
    return any(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) for word in words)


def detect_grade(title, description):
    text = f"{title} {description}".lower()
    grade = "Not Specified"
    junior = ["junior", "intern", "internship", "trainee", "без опыта", "entry level", "entry-level"]
    middle = ["middle", "mid-level", "mid", "2 years", "3 years", "2 gadi", "3 gadi", "2 года", "3 года"]

    # The title is the most reliable signal; the description is only a fallback.
    for source in (title.lower(), text):
        if _has_word(source, STOP_SENIOR_WORDS):
            grade = "Senior / Lead"
        elif _has_word(source, junior):
            grade = "Junior / Intern"
        elif _has_word(source, middle):
            grade = "Middle"
        else:
            continue
        break

    if any(signal in text for signal in CLOUD_SIGNALS):
        if grade == "Not Specified":
            grade = "Cloud/Infra Priority"
        else:
            grade += " | Cloud/Infra Priority"

    return grade


def is_unsuitable_job(title, description=""):
    """Senior, lead and management roles are out of scope; only the title decides."""
    return _has_word((title or "").lower(), STOP_SENIOR_WORDS + ["sr", "sr."])


def is_matching_target(title, description):
    return True


# Frequent function words per language; the share of them identifies the text.
LANGUAGE_WORDS = {
    "en": "the and you with we our for are will to of in your experience".split(),
    "lv": "un ar ir jūs mēs uz par vai darba pieredze kā lai".split(),
    "de": "und die der wir mit sie für ist das auf ein eine bei zu".split(),
    "fr": "et les des vous nous pour une est sur dans avec le la".split(),
    "es": "y los las de para con una por que el en nuestro".split(),
    "nl": "en de het van wij je een voor met zijn naar".split(),
    "it": "e il di per con una che della nel sono".split(),
    "pl": "i w z na do się oraz dla jest jak".split(),
    "pt": "e os as de para com uma em que no na".split(),
}
WANTED_LANGUAGES = ("en", "lv", "ru")


def description_language(text):
    """Best-guess language code of a job description, or "other"."""
    text = (text or "").lower()
    if len(re.findall(r"[а-яё]", text)) > 50:
        return "ru"
    words = re.findall(r"[a-zāčēģīķļņšūžäöüßéèàçñ]+", text)
    counts = Counter(words)
    shares = {code: sum(counts[w] for w in vocabulary) / max(1, len(words))
              for code, vocabulary in LANGUAGE_WORDS.items()}
    best = max(shares, key=shares.get)
    return best if shares[best] >= 0.03 else "other"


def is_wanted_language(description):
    return description_language(description) in WANTED_LANGUAGES
