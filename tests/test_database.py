import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

from utils.database import JobDatabase


DESCRIPTION = "Build and maintain Python services, design reliable databases, and collaborate with engineers. " * 8


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "jobs.db")
        self.db = JobDatabase(self.path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.db.close)

    def job(self, **changes):
        return {"Title": "Engineer", "Link": "https://example.test/job", "Source": "test", "Description": DESCRIPTION, **changes}

    def test_incremental_upsert_and_invalidation(self):
        self.db.discover_job(self.job())
        self.assertNotIn("Description", self.db.pending_jobs()[0])
        self.assertEqual(self.db.save_job(self.job()), "parsed")
        self.db.update_analysis(self.job()["Link"], 80, "Report", "cv.pdf", "Letter", "key")
        self.db.discover_job(self.job(Description="ignored", Salary="100"))
        self.db.save_job(self.job())
        self.assertEqual(self.db.rows()[0]["analysis_key"], "key")
        self.assertEqual(self.db.rows()[0]["salary"], "100")
        self.db.save_job(self.job(Description=DESCRIPTION + " More duties."))
        row = self.db.rows()[0]
        self.assertIsNone(row["analysis_key"])
        self.assertIsNone(row["match_score"])
        self.assertEqual(row["analysis_status"], "pending")
        self.assertEqual(row["attempts"], 3)
        self.assertEqual(len(self.db.rows()), 1)
        self.assertEqual(self.db.pending_jobs(), [])

    def test_invalid_description_is_pending_and_preserves_parsed_text(self):
        self.db.save_job(self.job())
        self.assertEqual(self.db.save_job(self.job(Description="")), "missing_description")
        self.assertEqual(self.db.rows()[0]["description"], DESCRIPTION)
        self.assertEqual(self.db.analysis_jobs(), [])
        self.assertEqual(len(self.db.pending_jobs()), 1)
        self.assertEqual(self.db.pending_jobs(limit=0), [])
        with self.assertRaises(ValueError):
            self.db.save_job({"Description": DESCRIPTION})

    def test_unknown_updates_fail(self):
        with self.assertRaises(KeyError):
            self.db.update_analysis("unknown", 80, "report", "cv.pdf", "letter")
        with self.assertRaises(KeyError):
            self.db.mark_parse_error("unknown", "error", "failure")
        with self.assertRaises(KeyError):
            self.db.mark_analysis_error("unknown", "failure")

    def test_thread_safe_instance(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: self.db.save_job(self.job(Link=f"https://example.test/{i}")), range(40)))
        self.assertEqual(len(self.db.analysis_jobs()), 40)

    def test_pending_retry_fairness(self):
        for link in ("first", "second", "third"):
            self.db.discover_job(self.job(Link=link))
        for expected in ("first", "second", "third", "first"):
            job = self.db.pending_jobs(limit=1)[0]
            self.assertEqual(job["Link"], expected)
            self.db.mark_parse_error(expected, "error", "temporary failure")

    def test_analysis_attempts_are_fair_and_persisted(self):
        for link in ("first", "second", "third"):
            self.db.save_job(self.job(Link=link))
        self.db.mark_analysis_error("first", "temporary failure")
        self.db.update_analysis("second", 80, "Report", "cv.pdf", "Letter", "key")
        self.assertEqual([job["Link"] for job in self.db.analysis_jobs()], ["third", "first", "second"])
        with closing(JobDatabase(self.path)) as reopened:
            self.assertEqual([row["analysis_attempts"] for row in reopened.rows()], [1, 1, 0])
            self.assertEqual([job["Link"] for job in reopened.analysis_jobs()], ["third", "first", "second"])

    def test_migrate_valid_legacy_analysis_without_cache_key(self):
        for has_status in (False, True):
            with self.subTest(has_status=has_status):
                path = os.path.join(self.tmp.name, f"analysis-{has_status}.db")
                with closing(sqlite3.connect(path)) as conn, conn:
                    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, link TEXT UNIQUE, description TEXT, match_score INTEGER, analysis_report TEXT, recommended_cv TEXT, cover_letter TEXT)")
                    for index, (score, report, cv, letter, text) in enumerate((
                        (80, "Report", "cv.pdf", "Letter", DESCRIPTION),
                        (35, "Report", "cv.pdf", "No Cover Letter", DESCRIPTION),
                        (101, "Report", "cv.pdf", "Letter", DESCRIPTION),
                        (80, " ", "cv.pdf", "Letter", DESCRIPTION),
                        (80, "Report", None, "Letter", DESCRIPTION),
                        (80, "Report", "cv.pdf", "", DESCRIPTION),
                        (80, "Report", "cv.pdf", "Letter", ""),
                    )):
                        conn.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)", (index, str(index), text, score, report, cv, letter))
                    if has_status:
                        conn.execute("ALTER TABLE jobs ADD COLUMN analysis_status TEXT NOT NULL DEFAULT 'pending'")
                with closing(JobDatabase(path)) as db:
                    rows = db.rows()
                    self.assertEqual([row["analysis_status"] for row in rows], ["analyzed", "analyzed"] + ["pending"] * 5)
                    self.assertTrue(all(row["analysis_key"] is None for row in rows))
                    db.create_tables()
                    self.assertEqual(db.rows(), rows)

    def test_migrate_legacy_table(self):
        path = os.path.join(self.tmp.name, "legacy.db")
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, title TEXT, link TEXT UNIQUE, description TEXT, match_score INTEGER)")
            conn.execute("INSERT INTO jobs VALUES (1, 'Old job', 'old', ?, 42)", (DESCRIPTION,))
            conn.execute("INSERT INTO jobs VALUES (2, 'Empty job', 'empty', '', NULL)")
        db = JobDatabase(path)
        try:
            rows = db.rows()
            self.assertEqual(rows[0]["parse_status"], "parsed")
            self.assertEqual(rows[0]["match_score"], 42)
            self.assertTrue(rows[0]["description_hash"])
            self.assertTrue(rows[0]["first_seen_at"])
            self.assertEqual(rows[1]["parse_status"], "missing_description")
            self.assertEqual(db.analysis_jobs()[0]["Title"], "Old job")
            db.create_tables()
            self.assertEqual(db.rows(), rows)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
