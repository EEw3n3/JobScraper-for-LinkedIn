"""cv.lv (CV-Online Latvia) vacancies through the site's own JSON API.

Search results and vacancy pages are public, so collecting takes a few HTTP
requests and no browser or account. Some ads are only a picture or a link to the
employer's page; their text comes from that page or from Gemini reading the picture.
The applicant's saved and applied lists need the signed-in browser (applier/cvlv.py).
"""

import html
import random
import re
import time

import requests

from utils.filters import detect_grade, is_unsuitable_job

BASE = "https://www.cv.lv"
API = f"{BASE}/api/v1"
SOURCE = "cv.lv"
LATVIA_ID = 101  # countryId in the API
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/141.0.0.0 Safari/537.36",
    "Accept-Language": "lv,en;q=0.8",
}
# Shorter than this, the ad text is a header only and the full ad is a picture or the employer's page.
SHORT_TEXT = 300
# Lines of cookie banners that employer pages add around the ad.
BANNER = re.compile(r"sīkdatn|cookie|куки|piekrišan\w* (pārvaldī|atsauk)", re.I)


def vacancy_id(link):
    found = re.search(r"cv\.lv/(?:[a-z]{2}/)?vacancy/(\d+)", link or "")
    return int(found.group(1)) if found else None


def vacancy_link(number):
    return f"{BASE}/lv/vacancy/{number}"


def is_cvlv(link):
    return vacancy_id(link) is not None


def html_text(fragment):
    """Readable text of an HTML fragment or page: one line per paragraph or list item."""
    fragment = re.sub(r"(?is)<(script|style|noscript|svg|head|template)\b[^>]*>.*?</\1>", " ", fragment or "")
    fragment = re.sub(r"(?i)<br\s*/?>|</(p|li|div|h\d|tr|section|article|ul|ol)>", "\n", fragment)
    fragment = re.sub(r"(?i)<li\b[^>]*>", "\n- ", fragment)
    plain = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    lines = (" ".join(line.split()) for line in plain.splitlines())
    return "\n".join(line for line in lines if line and line != "-")


def salary_text(highlights):
    low, high = highlights.get("salaryFrom"), highlights.get("salaryTo")
    if not low and not high:
        return "Not specified"
    rate = {"MONTHLY": "month", "HOURLY": "hour", "YEARLY": "year"}.get(highlights.get("ratePer") or "", "")
    low, high = (f"{value:g}" if isinstance(value, (int, float)) else value for value in (low, high))
    amount = f"{low}-{high}" if low and high and low != high else f"{low or high}"
    return f"€ {amount}" + (f" / {rate}" if rate else "")


class Client:
    """Requests to cv.lv one at a time with a short random pause; 429 and server errors are retried."""

    def __init__(self, session=None, delay=(0.8, 1.6), sleep=time.sleep):
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self.delay, self.sleep = delay, sleep

    def get(self, url, accept="application/json", **params):
        url = url if url.startswith("http") else API + url
        response = None
        for attempt in range(4):
            self.sleep(random.uniform(*self.delay))
            response = self.session.get(url, params=params or None, headers={"Accept": accept}, timeout=30)
            if response.status_code != 429 and response.status_code < 500:
                break
            self.sleep(5 * 2 ** attempt)
        response.raise_for_status()
        return response

    def search(self, categories, page_size=100):
        """Every active vacancy in the categories, newest first (summaries without the ad text)."""
        offset = 0
        while True:
            data = self.get("/vacancy-search-service/search", limit=page_size, offset=offset, sorting="LATEST",
                            lang="lv", **{"categories[]": list(categories)}).json()
            batch = data.get("vacancies") or []
            yield from batch
            offset += len(batch)
            if not batch or offset >= (data.get("total") or 0):
                return

    def vacancy(self, number):
        return self.get(f"/vacancies-service/{number}/public").json()

    def file(self, file_id):
        response = self.get(f"/files-service/{file_id}", accept="*/*")
        return response.content, (response.headers.get("content-type") or "").split(";")[0].strip()

    def page(self, url):
        """(text, image bytes or None, content type) of the employer page an ad links to."""
        response = self.get(url, accept="text/html,*/*")
        kind = (response.headers.get("content-type") or "").split(";")[0].strip()
        if kind.startswith("image/") or kind == "application/pdf":
            return "", response.content, kind
        return html_text(response.text), None, kind


def description(vacancy, client, read_image=None):
    """The ad text: cv.lv's own text blocks, else the employer's page, else the ad picture read by Gemini.

    read_image(data, mime_type) -> text, or None when no AI is configured.
    """
    details = vacancy.get("details") or {}
    parts = []
    for block in details.get("standardDetails") or ():
        parts += [block.get("title") or "", html_text(block.get("content") or "")]
    text = "\n".join(part for part in parts if part.strip())
    pictures = []
    if len(text) < SHORT_TEXT and details.get("urlDetails"):
        try:
            page, picture, kind = client.page(details["urlDetails"])
        except requests.RequestException:
            page, picture, kind = "", None, ""
        text = "\n".join(filter(None, (text, "\n".join(line for line in page.splitlines()
                                                         if not BANNER.search(line)))))
        if picture:
            pictures.append((picture, kind))
    file_id = (details.get("fileDetails") or {}).get("fileId")
    if len(text) < SHORT_TEXT and file_id:
        try:
            data, kind = client.file(file_id)
            if kind.startswith("image/") or kind == "application/pdf":
                pictures.append((data, kind))
        except requests.RequestException:
            pass
    for data, kind in pictures:
        if read_image is None or len(text) >= SHORT_TEXT:
            break
        try:
            text = "\n".join(filter(None, (text, read_image(data, kind) or "")))
        except Exception:
            continue
    return text.strip()


def job_row(summary, vacancy, text):
    """A jobs-table row for one vacancy."""
    highlights = vacancy.get("highlights") or {}
    location = highlights.get("location") or {}
    title = vacancy.get("position") or summary.get("positionTitle") or ""
    country = location.get("countryId") or summary.get("countryId")
    posted = (summary.get("renewedDate") or summary.get("publishDate") or vacancy.get("firstPublishDate") or "")
    return {
        "Title": title,
        "Grade": detect_grade(title, text),
        "Salary": salary_text(highlights or summary),
        "Country": "Latvia" if country == LATVIA_ID else "Abroad",
        "Posted": posted[:10] or "Not specified",
        "Link": vacancy_link(vacancy.get("id") or summary["id"]),
        "Source": SOURCE,
        "Description": text,
    }


def wanted_title(title, skip_words=()):
    """Senior roles and non-engineering IT roles (sales, design, HR...) are not worth an AI request.

    skip_words match at the start of a word, so a stem like "pārdošan" covers every ending.
    """
    lowered = (title or "").lower()
    return not is_unsuitable_job(title) and not any(
        re.search(rf"(?<!\w){re.escape(word.lower())}", lowered) for word in skip_words)


def collect(save, *, client, categories, skip_words=(), known=(), read_image=None, limit=None,
            on_step=None, say=print):
    """Save every new suitable vacancy in the categories; return how many were saved.

    save(row) stores one jobs-table row. known: links already in the run's database.
    on_step(done, total, title) reports progress.
    """
    known = set(known)
    summaries = [summary for summary in client.search(categories)
                 if wanted_title(summary.get("positionTitle"), skip_words)
                 and vacancy_link(summary["id"]) not in known]
    if limit is not None:
        summaries = summaries[:limit]
    saved = 0
    for number, summary in enumerate(summaries, 1):
        if on_step:
            on_step(number, len(summaries), summary.get("positionTitle") or "")
        try:
            vacancy = client.vacancy(summary["id"])
        except requests.RequestException as error:
            say(f"cv.lv: could not read vacancy {summary['id']}: {error}")
            continue
        save(job_row(summary, vacancy, description(vacancy, client, read_image)))
        saved += 1
    return saved
