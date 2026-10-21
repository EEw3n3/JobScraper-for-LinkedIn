import hashlib
import os
import sqlite3
import threading

from utils.validation import description_status


class JobDatabase:
    FIELDS = ("Title", "Grade", "Salary", "Country", "Posted", "Link", "Source")

    def __init__(self, db_path):
        self.path = os.fspath(db_path)
        parent = os.path.dirname(os.fspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        try:
            self.create_tables()
        except Exception:
            self.conn.close()
            raise

    def create_tables(self):
        columns = {
            **{name.lower(): "TEXT" for name in self.FIELDS},
            "description": "TEXT", "match_score": "INTEGER",
            "analysis_report": "TEXT", "recommended_cv": "TEXT", "cover_letter": "TEXT",
            "parse_status": "TEXT NOT NULL DEFAULT 'pending'",
            "analysis_status": "TEXT NOT NULL DEFAULT 'pending'",
            "attempts": "INTEGER NOT NULL DEFAULT 0", "last_error": "TEXT",
            "analysis_attempts": "INTEGER NOT NULL DEFAULT 0",
            "first_seen_at": "TEXT", "last_seen_at": "TEXT",
            "description_hash": "TEXT", "analysis_key": "TEXT",
        }
        with self._lock, self.conn:
            self.conn.execute("CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, link TEXT UNIQUE)")
            existing = {row["name"] for row in self.conn.execute("PRAGMA table_info(jobs)")}
            for name, declaration in columns.items():
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS jobs_link_unique ON jobs(link)")
            self.conn.execute("UPDATE jobs SET first_seen_at=COALESCE(first_seen_at, CURRENT_TIMESTAMP), last_seen_at=COALESCE(last_seen_at, CURRENT_TIMESTAMP)")
            if "parse_status" not in existing:
                for row in self.conn.execute("SELECT link, description FROM jobs").fetchall():
                    status = description_status(row["description"])
                    digest = self._hash(row["description"]) if status == "parsed" else None
                    self.conn.execute("UPDATE jobs SET parse_status=?, description_hash=? WHERE link=?", (status, digest, row["link"]))
            if "analysis_status" not in existing or "analysis_attempts" not in existing:
                for row in self.conn.execute("SELECT * FROM jobs WHERE analysis_key IS NULL AND analysis_status='pending' AND parse_status='parsed'").fetchall():
                    if (type(row["match_score"]) is int and 0 <= row["match_score"] <= 100
                            and description_status(row["description"]) == "parsed"
                            and all(isinstance(row[name], str) and row[name].strip()
                                    for name in ("analysis_report", "recommended_cv", "cover_letter"))):
                        self.conn.execute("UPDATE jobs SET analysis_status='analyzed' WHERE id=?", (row["id"],))

    @staticmethod
    def _hash(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _link(job):
        link = job.get("Link")
        if not isinstance(link, str) or not link.strip():
            raise ValueError("Job requires a nonempty Link")
        return link

    def _discover(self, job):
        self._link(job)
        names = [name.lower() for name in self.FIELDS]
        updates = ", ".join(f"{name}=COALESCE(excluded.{name},jobs.{name})" for name in names if name != "link")
        self.conn.execute(
            f"INSERT INTO jobs ({','.join(names)},first_seen_at,last_seen_at) VALUES ({','.join('?' for _ in names)},CURRENT_TIMESTAMP,CURRENT_TIMESTAMP) "
            f"ON CONFLICT(link) DO UPDATE SET {updates}, last_seen_at=CURRENT_TIMESTAMP",
            tuple(job.get(name) for name in self.FIELDS),
        )

    def discover_job(self, job):
        with self._lock, self.conn:
            previous = self.conn.execute("SELECT title FROM jobs WHERE link=?", (self._link(job),)).fetchone()
            self._discover(job)
            if previous and job.get("Title") is not None and previous["title"] != job["Title"]:
                self._invalidate(job["Link"])

    def _invalidate(self, link):
        self.conn.execute("UPDATE jobs SET analysis_status='pending', analysis_key=NULL, match_score=NULL, analysis_report=NULL, recommended_cv=NULL, cover_letter=NULL WHERE link=?", (link,))

    def save_job(self, job):
        status = description_status(job.get("Description"))
        with self._lock, self.conn:
            link = self._link(job)
            previous = self.conn.execute("SELECT title, description_hash FROM jobs WHERE link=?", (link,)).fetchone()
            self._discover(job)
            if previous and job.get("Title") is not None and previous["title"] != job["Title"]:
                self._invalidate(link)
            if status != "parsed":
                self.conn.execute("UPDATE jobs SET parse_status=?, attempts=attempts+1, last_error=? WHERE link=?", (status, status, link))
                return status
            digest = self._hash(job["Description"])
            if not previous or previous["description_hash"] != digest or (job.get("Title") is not None and previous["title"] != job["Title"]):
                self._invalidate(link)
            self.conn.execute("UPDATE jobs SET description=?, description_hash=?, parse_status='parsed', attempts=attempts+1, last_error=NULL WHERE link=?", (job["Description"], digest, link))
        return status

    def _update_known(self, sql, parameters):
        cursor = self.conn.execute(sql, parameters)
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown job: {parameters[-1]}")

    def mark_parse_error(self, link, status, error):
        if status == "parsed" or not isinstance(status, str) or not status:
            raise ValueError("Expected a parse failure status")
        with self._lock, self.conn:
            self._update_known("UPDATE jobs SET parse_status=?, attempts=attempts+1, last_error=?, last_seen_at=CURRENT_TIMESTAMP WHERE link=?", (status, str(error), link))

    def rows(self):
        with self._lock:
            return [dict(row) for row in self.conn.execute("SELECT * FROM jobs ORDER BY id")]

    def pending_jobs(self, limit=None):
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("limit must be a nonnegative integer")
        ordered = sorted(self.rows(), key=lambda row: (row["attempts"], row["id"]))
        jobs = [{name: row[name.lower()] for name in self.FIELDS} for row in ordered if row["parse_status"] != "parsed"]
        return jobs if limit is None else jobs[:limit]

    def analysis_jobs(self):
        ordered = sorted(self.rows(), key=lambda row: (row["analysis_attempts"], row["id"]))
        return [{**{name: row[name.lower()] for name in self.FIELDS}, "Description": row["description"], "analysis_key": row["analysis_key"]} for row in ordered if row["parse_status"] == "parsed" and description_status(row["description"]) == "parsed"]

    def update_analysis(self, link, score, report, best_cv, cover_letter, analysis_key=None):
        if type(score) is not int or not 0 <= score <= 100:
            raise ValueError("score must be an integer from 0 to 100")
        if any(not isinstance(value, str) or not value.strip() for value in (report, best_cv, cover_letter)):
            raise ValueError("Analysis fields must be nonempty strings")
        with self._lock, self.conn:
            self._update_known("UPDATE jobs SET match_score=?, analysis_report=?, recommended_cv=?, cover_letter=?, analysis_key=?, analysis_status='analyzed', analysis_attempts=analysis_attempts+1, last_error=NULL WHERE link=?", (score, report, best_cv, cover_letter, analysis_key, link))

    def mark_analysis_error(self, link, error):
        with self._lock, self.conn:
            self._update_known("UPDATE jobs SET analysis_status='error', analysis_attempts=analysis_attempts+1, analysis_key=NULL, match_score=NULL, analysis_report=NULL, recommended_cv=NULL, cover_letter=NULL, last_error=? WHERE link=?", (str(error), link))

    def close(self):
        with self._lock:
            self.conn.close()
