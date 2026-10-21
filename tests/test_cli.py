import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import main
from scraper import parser as scraper_parser
from utils.database import JobDatabase


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job = {"Title": "Engineer", "Link": "https://example.test/jobs/1",
                    "Source": "LinkedIn", "Country": "Test", "Posted": "today"}
        self.detail = {**self.job, "Description": "Build reliable services and maintain data pipelines. " * 10}
        self.addCleanup(scraper_parser.reset_navigation_state)

    def invoke(self, argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), patch("dotenv.load_dotenv"):
            return main.main(argv)

    def test_new_paths_are_unique_and_never_overwrite(self):
        first = main.new_database_path(self.root)
        second = main.new_database_path(self.root)
        self.assertNotEqual(first, second)
        self.assertRegex(first.name, r"^jobs_\d{8}_\d{6}_\d{6}Z\.db$")
        self.assertTrue(first.exists())

    def test_two_scrapes_create_independent_databases_without_ai(self):
        def collect(driver, site, on_job, **kwargs):
            on_job(self.job)
            return [self.job]

        arguments = ["scrape", "--output-dir", str(self.root), "--no-ai", "--role", "Engineer",
                     "--country", "Test", "--browsers", "1", "--max-pages", "0"]
        with patch("scraper.driver.setup_driver", return_value=Mock()), patch("scraper.collector.collect_links", side_effect=collect), patch("scraper.parser.extract_job_details", return_value=self.detail), patch("utils.analyzer.analyze_jobs_with_cv") as analyze:
            self.assertEqual(self.invoke(arguments), 0)
            self.assertEqual(self.invoke(arguments), 0)
            analyze.assert_not_called()
        databases = list(self.root.glob("*.db"))
        self.assertEqual(len(databases), 2)
        for path in databases:
            db = JobDatabase(path)
            try:
                self.assertEqual(db.rows()[0]["parse_status"], "parsed")
                self.assertTrue(path.with_suffix(".html").is_file())
            finally:
                db.close()

    def test_retry_resumes_saved_job_without_new_database(self):
        path = self.root / "run.db"
        db = JobDatabase(path)
        db.discover_job(self.job)
        db.close()
        with patch("scraper.driver.setup_driver", return_value=Mock()), patch("scraper.parser.extract_job_details", return_value=self.detail) as extract:
            self.assertEqual(self.invoke(["retry", "--db", str(path), "--no-ai"]), 0)
            self.assertEqual(extract.call_count, 1)
        self.assertEqual(len(list(self.root.glob("*.db"))), 1)

    def test_parse_error_persists_and_is_attempted_once_per_retry(self):
        path = self.root / "run.db"
        db = JobDatabase(path)
        db.discover_job(self.job)
        db.close()
        with patch("scraper.driver.setup_driver", return_value=Mock()), patch("scraper.parser.extract_job_details", side_effect=scraper_parser.DescriptionError("auth_required")) as extract:
            self.assertEqual(self.invoke(["retry", "--db", str(path), "--no-ai"]), 2)
            self.assertEqual(extract.call_count, 1)
        db = JobDatabase(path)
        try:
            self.assertEqual(db.rows()[0]["parse_status"], "auth_required")
            self.assertEqual(db.rows()[0]["attempts"], 1)
        finally:
            db.close()

    def test_collection_failure_retains_discoveries_and_search(self):
        def collect(driver, site, on_job, **kwargs):
            on_job(self.job)
            raise RuntimeError("Browser crashed")

        with patch("scraper.driver.setup_driver", return_value=Mock()), patch("scraper.collector.collect_links", side_effect=collect):
            with self.assertRaisesRegex(RuntimeError, "Browser crashed"):
                self.invoke(["scrape", "--output-dir", str(self.root), "--role", "Engineer", "--country", "Test", "--no-ai"])
        path = next(self.root.glob("*.db"))
        db = JobDatabase(path)
        try:
            self.assertEqual(len(db.pending_jobs()), 1)
            self.assertEqual(db.conn.execute("SELECT status FROM searches").fetchone()[0], "error")
        finally:
            db.close()

    def test_existing_database_required(self):
        path = self.root / "missing.db"
        with self.assertRaises(SystemExit):
            self.invoke(["export", "--db", str(path)])
        self.assertFalse(path.exists())

    def test_export_existing_database(self):
        path = self.root / "run.db"
        db = JobDatabase(path)
        db.save_job(self.detail)
        db.close()
        self.assertEqual(self.invoke(["export", "--db", str(path), "--format", "csv"]), 0)
        self.assertTrue(path.with_suffix(".csv").exists())


if __name__ == "__main__":
    unittest.main()
