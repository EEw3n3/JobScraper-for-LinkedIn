"""Pick suitable scored jobs, tailor the CV, and run Easy Apply with a daily cap and pacing."""

import json
import random
import re
import sqlite3
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from utils import progress
from utils.filters import is_unsuitable_job, is_wanted_language

# Never revisited automatically: the outcome is known or needs a person.
# "hold": the applicant applies to this one themselves (for example with a hand-written letter).
DONE = ("applied", "unconfirmed", "already_applied", "closed", "no_apply_button", "hold", "hidden")
RETRY_ONLY = ("needs_manual", "external")
# A job located in Latvia (English, Latvian or Russian LinkedIn UI) gets the Latvian CV.
LATVIA = re.compile(r"latvi|латви|\bri[gģ]a\b|\bр[иі]га\b", re.I)
INSTRUCTIONS = re.compile(r"include the (word|phrase)|"
                          r"start your (answer|application|message|cover letter) with|mention the (word|phrase)|"
                          r"exact phrase|secret word|keyword in your application", re.I)


class ApplicationLog:
    """One database across all runs, so a job is never applied to twice."""

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS applications (link TEXT PRIMARY KEY, title TEXT, company TEXT, "
                "score INTEGER, status TEXT, reason TEXT, answers TEXT, cv_pdf TEXT, screenshot TEXT, notes TEXT, "
                "updated_at TEXT)")
            existing = {row["name"] for row in self.conn.execute("PRAGMA table_info(applications)")}
            for column in ("apply_url", "ats", "explain", "hidden_at"):
                if column not in existing:
                    self.conn.execute(f"ALTER TABLE applications ADD COLUMN {column} TEXT")

    def status(self, link):
        row = self.conn.execute("SELECT status FROM applications WHERE link=?", (link,)).fetchone()
        return row["status"] if row else None

    def save(self, link, touch=True, **fields):
        """touch=False keeps updated_at, the date the app shows for the application."""
        if touch:
            fields["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        columns = ["link", *fields]
        with self.conn:
            self.conn.execute(
                f"INSERT INTO applications ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
                f"ON CONFLICT(link) DO UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in fields)}",
                (link, *fields.values()))

    def applied_elsewhere(self, title, company):
        """Already applied to the same company and title under another link (reposts, other searches)."""
        key, employer = title_key(title), company_key(company)
        if not key:
            return None
        for row in self.conn.execute("SELECT title, company, link FROM applications WHERE "
                                     "status IN ('applied','unconfirmed','already_applied','hold')"):
            # The same role posted for several cities (Riga, Tallinn, Vilnius) counts as one application.
            same_employer = employer and (company_key(row["company"]) == employer
                                          or employer in title_key(row["title"]) + " " + (row["title"] or "").lower())
            if title_key(row["title"]) == key and same_employer:
                return f"Already applied to this job at {row['link']}"
        return None

    def submitted_last_day(self):
        return self.conn.execute("SELECT COUNT(*) FROM applications WHERE status IN ('applied','unconfirmed') "
                                 "AND coalesce(reason, '') != 'Applied manually' "
                                 "AND updated_at >= datetime('now','-1 day')").fetchone()[0]

    def rows(self):
        return [dict(row) for row in self.conn.execute("SELECT * FROM applications ORDER BY updated_at DESC")]

    def close(self):
        self.conn.close()


def candidates(job_rows, log, min_score, submit, retry=False):
    """Scored, suitable jobs not handled yet. A dry-run 'ready' job is retried when submitting."""
    picked = []
    for row in job_rows:
        if (row.get("parse_status") != "parsed" or row.get("analysis_status") != "analyzed"
                or (row.get("match_score") or 0) < min_score or is_unsuitable_job(row.get("title"))
                or not is_wanted_language(row.get("description"))):
            continue
        status = log.status(row["link"])
        if status in DONE or (status in RETRY_ONLY and not retry) or (status == "ready" and not submit and not retry):
            continue
        picked.append(row)
    # Locations in their SEARCH_LOCATIONS order, remote jobs last; the best score first within each.
    return sorted(picked, key=lambda row: (region_rank(row), -row["match_score"]))


def region_rank(row, locations=None):
    if locations is None:
        from config.settings import SEARCH_LOCATIONS as locations
    order = [location.replace("%20", " ").lower() for location in locations]
    country = (row.get("country") or "").lower()
    return order.index(country) if country in order else len(order)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "job").lower()).strip("-")[:50] or "job"


def write_report(log, path):
    cards = []
    for row in log.rows():
        answers = "".join(f"<tr><td>{escape(str(a['question']))}</td><td>{escape(str(a['answer']))}</td>"
                          f"<td>{escape(str(a['source']))}</td></tr>" for a in json.loads(row["answers"] or "[]"))
        pdf = Path(row["cv_pdf"]).as_uri() if row["cv_pdf"] and Path(row["cv_pdf"]).exists() else ""
        shot = Path(row["screenshot"]).as_uri() if row["screenshot"] and Path(row["screenshot"]).exists() else ""
        cards.append(
            f"<section><h3><a href='{escape(row['link'])}'>{escape(row['title'] or '')}</a> · {escape(row['company'] or '')}</h3>"
            f"<p><b>{escape(row['status'] or '')}</b> · score {row['score']} · {escape(row['updated_at'] or '')} UTC"
            + (f" · {escape(row.get('ats') or '')}" if row.get("ats") else "")
            + (f" · <a href='{escape(row['apply_url'])}'>company form</a>" if row.get("apply_url") else "")
            + (f" · <a href='{pdf}'>CV PDF</a>" if pdf else "") + "</p>"
            + (f"<p>{escape(row['reason'])}</p>" if row["reason"] else "")
            + (f"<p class='notes'>{escape(row['notes'])}</p>" if row["notes"] else "")
            + (f"<table><tr><th>Question</th><th>Answer</th><th>Source</th></tr>{answers}</table>" if answers else "")
            + (f"<img src='{shot}' alt='Review step'>" if shot else "") + "</section>")
    Path(path).write_text(
        "<!doctype html><meta charset='utf-8'><title>Applications</title><style>body{font-family:Arial;max-width:980px;"
        "margin:20px auto;padding:0 16px}section{border:1px solid #ccc;border-radius:8px;padding:12px;margin:14px 0}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:4px;font-size:13px;text-align:left}"
        "img{max-width:100%;margin-top:8px;border:1px solid #ddd}.notes{color:#666;font-size:12px}</style>"
        "<h1>Applications</h1>" + ("".join(cards) or "<p>No applications yet.</p>"), encoding="utf-8")
    return Path(path)


def title_key(title):
    """A job title without gender markers, location suffixes and punctuation, for duplicate checks."""
    title = (title or "").strip().splitlines()[0] if (title or "").strip() else ""
    title = re.sub(r"\((m|w|f|d|x|h)(/(m|w|f|d|x|h))+\)", " ", title.lower())
    title = re.sub(r"[‐‑‒–—―−]", "-", title)
    title = re.split(r"\s[|-]\s", title)[0]
    return " ".join(re.findall(r"\w+", title))


def company_key(company):
    """The first word of the company name: "Nordbank Eesti", "Nordbank Lietuvoje" and "Nordbank banka" are one employer."""
    words = re.findall(r"\w+", (company or "").lower())
    return words[0] if words else ""


def in_latvia(row, details):
    return row.get("country") == "Latvia" or bool(LATVIA.search(details.get("location") or ""))


def applied_in_mail(title, company, mail_texts):
    """A confirmation email that names both the company and the job title."""
    company_key = " ".join(re.findall(r"\w+", (company or "").lower().replace(" sia", "").replace(" as", "")))
    key = title_key(title)
    if not company_key or not key:
        return None
    for text in mail_texts or ():
        flat = " ".join(re.findall(r"\w+", text.lower()))
        if company_key in flat and key in flat:
            return "Gmail has an application confirmation for this job"
    return None


def run(job_rows, *, driver, pdf_driver, llm, profile, base_cv, out_dir, log, submit, limit=None,
        min_score=None, retry=False, example_letter=None, mail_texts=(), base_cv_lv=None, sleep=time.sleep,
        say=print):
    """Process candidates up to the daily cap. Returns a count per status.

    base_cv_lv: the Latvian CV, used for jobs in Latvia (the English base_cv otherwise).
    example_letter: the applicant's own cover letter, used as the model for one letter per job.
    mail_texts: subjects and snippets of application confirmation emails, to skip jobs already applied to.
    """
    from applier import answers, cv as cvlib, easy_apply, external, letter as letter_lib

    min_score = profile.get("min_score", 65) if min_score is None else min_score
    cap = max(0, int(profile.get("daily_limit", 10)) - log.submitted_last_day()) if submit else 5
    if limit is not None:
        cap = min(cap, limit)
    picked = candidates(job_rows, log, min_score, submit, retry)
    say(f"Candidates: {len(picked)}; this run handles up to {cap} "
        f"({'SUBMITS applications' if submit else 'dry run, stops before Submit'})")
    counts = {}
    progress.stage("apply", "start", total=min(cap, len(picked)), candidates=len(picked), submit=submit)

    def report(status, row, company=""):
        counts[status] = counts.get(status, 0) + 1
        progress.emit("result", status=status, title=row["title"], company=company, link=row["link"],
                      score=row.get("match_score"))

    cv_text = cvlib.cv_plain_text(base_cv)
    name = re.sub(r"\W+", "_", f"{profile.get('first_name', '')} {profile.get('last_name', '')}").strip("_") or "CV"
    handled = 0
    for row in picked:
        if handled >= cap:
            break
        link, title = row["link"], row["title"]
        say(f"-> {title} (score {row['match_score']})")
        progress.step("apply", handled, min(cap, len(picked)), f"Opening the job: {title}")
        if INSTRUCTIONS.search(f"{title}\n{row.get('description') or ''}"):
            # Ads that test whether the applicant read them need the applicant.
            log.save(link, title=title, score=row["match_score"], status="needs_manual",
                     reason="The ad has special application instructions; read it and apply yourself")
            report("needs_manual", row)
            say("   needs_manual (special instructions)")
            continue
        state, button = easy_apply.open_job(driver, link)
        details = easy_apply.job_details(driver)
        reason = None
        if state == "external" and not profile.get("apply_external", True):
            state, reason = "external_skipped", "Apply on the company site manually"
        if state in ("candidate", "external"):
            reason = log.applied_elsewhere(title, details["company"]) or applied_in_mail(
                title, details["company"], mail_texts)
            if reason:
                state = "already_applied"
        if state not in ("candidate", "external"):
            log.save(link, title=title, company=details["company"], score=row["match_score"],
                     status="external" if state == "external_skipped" else state, reason=reason)
            report(state, row, details["company"])
            say(f"   {state}")
            sleep(random.uniform(4, 9))
            continue

        company = details["company"]
        progress.step("apply", handled, min(cap, len(picked)), f"Tailoring the CV for {company}: {title}")
        folder = Path(out_dir) / f"{datetime.now().strftime('%Y%m%d')}-{slug(details['company'])}-{slug(title)}"
        folder.mkdir(parents=True, exist_ok=True)
        job_cv = base_cv_lv if base_cv_lv and in_latvia(row, details) else base_cv
        tailored, notes = cvlib.tailor_cv(job_cv, title, row.get("description") or details["description"], llm)
        pdf_path, pdf_notes = cvlib.render_cv_pdf(pdf_driver, tailored, folder, f"{name}_CV.pdf",
                                                  profile.get("phone", ""), profile.get("phone_country", ""))
        notes += pdf_notes
        letter = None
        if example_letter and profile.get("generate_cover_letter", True):
            progress.step("apply", handled, min(cap, len(picked)), f"Writing a cover letter for {company}")
            letter, letter_notes = letter_lib.write_letter(job_cv, profile, title, details["company"],
                                                           row.get("description") or details["description"],
                                                           example_letter, llm)
            notes += letter_notes
        files = {"cv": pdf_path, "cover_letter_text": letter}
        if letter:
            (folder / "cover_letter.txt").write_text(letter, encoding="utf-8")
            letter_html = folder / "cover_letter.html"
            letter_html.write_text(cvlib.render_letter_html(letter, tailored["name"]), encoding="utf-8")
            files["cover_letter"] = cvlib.html_to_pdf(pdf_driver, letter_html, folder / f"{name}_Cover_Letter.pdf")
        screenshot = folder / "review.png"

        def answer_fn(question, kind, options, required=True):
            # Optional fields get only the applicant's own data, never AI-written text.
            return answers.resolve(question, kind, options, profile, cv_text, title, llm if required else None)

        base = dict(title=title, company=details["company"], score=row["match_score"], cv_pdf=str(pdf_path),
                    notes="; ".join(notes) or None)
        original = driver.current_window_handle
        progress.step("apply", handled, min(cap, len(picked)),
                      f"{'Filling in the company form' if state == 'external' else 'Filling in Easy Apply'}: {company}")
        try:
            if state == "external":
                url, original = external.follow_apply_button(driver, button)
                base.update(apply_url=url, ats=external.detect_ats(url))
                status, given, ats = external.run_external(driver, url, answer_fn, files, submit, screenshot)
            else:
                base.update(ats="linkedin")
                status, given = easy_apply.run_application(driver, button, answer_fn, files, submit, screenshot)
            log.save(link, status=status, reason=None, answers=json.dumps(given, ensure_ascii=False),
                     screenshot=str(screenshot), **base)
        except easy_apply.External:
            status = "external"
            log.save(link, status=status, reason="Apply on the company site manually", answers=None,
                     screenshot=None, **base)
        except easy_apply.NeedsManual as problem:
            status = "needs_manual"
            log.save(link, status=status, reason=str(problem), answers=json.dumps(problem.answers, ensure_ascii=False),
                     screenshot=None, **base)
        finally:
            if state == "external":
                external.close_extra_tabs(driver, original)
        report(status, row, company)
        say(f"   {status}")
        handled += 1
        progress.step("apply", handled, min(cap, len(picked)), f"Done: {title} · {company}")
        if handled < cap:
            pause = random.uniform(90, 240) if submit else random.uniform(20, 45)
            progress.step("apply", handled, min(cap, len(picked)),
                          f"Pausing {int(pause)} s before the next job so LinkedIn does not take this for a bot")
            sleep(pause)
    progress.stage("apply", "done", **counts)
    return counts
