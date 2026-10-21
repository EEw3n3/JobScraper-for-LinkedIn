"""JobScraper desktop app: one window to start the run, watch its progress and handle stopped applications.

The window is HTML (app/ui) shown by pywebview. The work itself runs as
`.venv\\Scripts\\python.exe main.py run-all`, whose "@@{json}" progress lines
the window polls, so the packaged .exe stays small and uses the same code as
the daily task.
"""

import json
import os
import queue
import re
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import webview

FROZEN = getattr(sys, "frozen", False)
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parents[1]
UI = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "ui"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
APPLICANT = ROOT / "config" / "applicant.json"
APPLICATIONS = ROOT / "results" / "applications.db"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Why an application stopped, in plain English, with a short label for the card.
REASONS = [
    (r"No truthful answer for required question: (.*)", "Form question",
     "The form asks: \"{0}\". Your profile and CV have no truthful answer, so the app did not make one up."),
    (r"CAPTCHA", "CAPTCHA", "The site showed a CAPTCHA. The app does not solve CAPTCHAs; submit this one yourself."),
    (r"needs an applicant account", "Account needed", "The company site requires a candidate account."),
    (r"no CV upload found", "No CV field",
     "The page has no CV upload field; it is probably not the application form itself."),
    (r"special application instructions", "Task in the ad",
     "The ad has a special instruction for applicants (for example, start the letter with a given word)."),
    (r"Easy Apply dialog did not open", "Dialog did not open", "The LinkedIn Easy Apply dialog did not open."),
    (r"[Ff]orm errors(?: after Submit)?: (.*)", "Form rejected answers", "The form rejected the answers: {0}"),
    (r"no Submit/Next button", "No submit button", "The app found no submit button on the form."),
    (r"Apply on the company site manually", "Company site only",
     "You can only apply on the company site, and the app did not get there."),
    (r"Apply button (did not open|opened another)", "Site did not open",
     "The apply button did not open the company site."),
    (r"unexpected form layout|Unexpected form layout", "Unusual form",
     "The form is not laid out the way the app expects."),
]


def human_reason(reason, status):
    reason = reason or ""
    for pattern, label, text in REASONS:
        found = re.search(pattern, reason)
        if found:
            return label, text.format(*[g.strip() for g in found.groups() if g is not None])
    if status == "external":
        return "Company site only", "You can only apply on the company site."
    return "Needs a check", reason or "The app stopped at this job."


def read_profile():
    try:
        return json.loads(APPLICANT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def query(sql, args=()):
    if not APPLICATIONS.exists():
        return []
    with closing(sqlite3.connect(APPLICATIONS)) as connection:
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute(sql, args)]
        except sqlite3.OperationalError:
            return []


def first_line(text):
    return (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""


class Api:
    def __init__(self):
        self.process = None
        self.events = queue.Queue()

    # ---- run -------------------------------------------------------------------------
    def state(self):
        profile = read_profile()
        return {
            "ready": PYTHON.exists() and (ROOT / "main.py").exists(),
            "root": str(ROOT),
            "running": self.process is not None and self.process.poll() is None,
            "auto_submit": bool(profile.get("auto_submit")),
            "daily_limit": profile.get("daily_limit", 10),
            "min_score": profile.get("min_score", 65),
            "apply_external": profile.get("apply_external", True),
            "signed_in": (ROOT / "browser_profile").exists(),
            "gmail": (ROOT / "secrets" / "gmail_token.json").exists(),
        }

    def start(self, dry_run=False, skip_scrape=False):
        if self.process is not None and self.process.poll() is None:
            return {"ok": False, "error": "A run is already in progress"}
        if not PYTHON.exists():
            return {"ok": False, "error": f"Python environment not found: {PYTHON}"}
        argv = [str(PYTHON), "-u", "main.py", "run-all"]
        if dry_run:
            argv.append("--dry-run")
        if skip_scrape:
            argv.append("--skip-scrape")
        env = {**os.environ, "JOBSCRAPER_PROGRESS": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        self.process = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, text=True,
                                        encoding="utf-8", errors="replace", bufsize=1)
        threading.Thread(target=self._read, args=(self.process,), daemon=True).start()
        return {"ok": True}

    def _read(self, process):
        for line in process.stdout:
            line = line.rstrip("\n")
            if line.startswith("@@"):
                try:
                    self.events.put(json.loads(line[2:]))
                    continue
                except ValueError:
                    pass
            if line.strip():
                self.events.put({"event": "log", "text": line})
        self.events.put({"event": "exit", "code": process.wait()})

    def poll(self):
        found = []
        while not self.events.empty() and len(found) < 400:
            found.append(self.events.get_nowait())
        return found

    def stop(self):
        if self.process is None or self.process.poll() is not None:
            return {"ok": True}
        # The run starts Chrome windows of its own; stop the whole process tree.
        subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], creationflags=NO_WINDOW,
                       capture_output=True)
        return {"ok": True}

    # ---- lists ------------------------------------------------------------------------
    def manual_list(self):
        rows = query("SELECT * FROM applications WHERE status IN ('needs_manual', 'external') "
                     "ORDER BY updated_at DESC LIMIT 200")
        items = []
        for row in rows:
            label, text = human_reason(row.get("reason"), row["status"])
            items.append({"link": row["link"], "title": first_line(row["title"]), "company": row.get("company") or "",
                          "score": row.get("score"), "label": label, "reason": text,
                          "apply_url": row.get("apply_url") or "", "ats": row.get("ats") or "",
                          "updated": row.get("updated_at") or "", "cv_pdf": row.get("cv_pdf") or ""})
        return items

    def applied_list(self):
        rows = query("SELECT * FROM applications WHERE status IN ('applied', 'unconfirmed') "
                     "ORDER BY updated_at DESC LIMIT 200")
        return [{"link": r["link"], "title": first_line(r["title"]), "company": r.get("company") or "",
                 "score": r.get("score"), "updated": r.get("updated_at") or "", "ats": r.get("ats") or "",
                 "manual": (r.get("reason") or "") == "Applied manually", "cv_pdf": r.get("cv_pdf") or ""}
                for r in rows]

    def today(self):
        start = datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00:00")
        rows = query("SELECT status, COUNT(*) AS n FROM applications WHERE updated_at >= ? GROUP BY status", (start,))
        return {row["status"]: row["n"] for row in rows}

    # ---- actions ----------------------------------------------------------------------
    def _main(self, *args, timeout=180):
        result = subprocess.run([str(PYTHON), "main.py", *args], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", creationflags=NO_WINDOW, timeout=timeout,
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""

    def explain(self, link):
        try:
            return json.loads(self._main("explain", "--link", link, timeout=240))
        except (ValueError, subprocess.TimeoutExpired) as error:
            return {"summary": "", "todo": [], "note": f"Could not get a summary: {error}"}

    def mark_applied(self, link):
        self._main("mark-applied", link)
        return True

    def hide(self, link):
        self._main("hide", "--link", link)
        return True

    def open_url(self, url):
        if url:
            os.startfile(url)
        return True

    def open_path(self, path):
        target = Path(path)
        if target.exists():
            os.startfile(str(target))
        return True

    def open_report(self):
        report = ROOT / "results" / "applications.html"
        if report.exists():
            os.startfile(str(report))
        return report.exists()

    def set_auto_submit(self, value):
        profile = read_profile()
        if not profile:
            return False
        text = APPLICANT.read_text(encoding="utf-8")
        # Change only that one value, keeping the file's own layout.
        updated, count = re.subn(r'("auto_submit"\s*:\s*)(true|false)', rf"\g<1>{'true' if value else 'false'}", text)
        if count:
            APPLICANT.write_text(updated, encoding="utf-8")
        return bool(count)


def main():
    api = Api()
    webview.create_window("JobScraper", str(UI / "index.html"), js_api=api, width=1280, height=840,
                          min_size=(1000, 680), background_color="#171719")
    webview.start(debug=os.environ.get("JOBSCRAPER_DEBUG") == "1")


if __name__ == "__main__":
    main()
