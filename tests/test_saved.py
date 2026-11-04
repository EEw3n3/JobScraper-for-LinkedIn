import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import main
from applier import easy_apply, pipeline
from utils.database import JobDatabase


class SavedJobsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.log = pipeline.ApplicationLog(self.root / "applications.db")
        self.addCleanup(self.log.close)

    def test_titles_lose_linkedin_badges(self):
        self.assertEqual(easy_apply.saved_job_title("Python Developer, Verified\nAcme\nRiga"), "Python Developer")
        self.assertEqual(easy_apply.saved_job_title("Backend Engineer with verification"), "Backend Engineer")
        self.assertEqual(easy_apply.saved_job_title(""), "")

    def test_saved_jobs_clicks_through_pages(self):
        pages = [[[str(4000000 + n), f"Job {n}"] for n in range(10)] + [["4000000", ""]],
                 [["4000099", "Last job, Verified"]]]
        shown = {"page": 0}

        def script(code, *args):
            if "found.push" in code:
                return pages[min(shown["page"], len(pages) - 1)]
            if "byLabel" in code:
                if args[0] - 1 < len(pages):
                    shown["page"] = args[0] - 1
                    return True
                return False
            return None

        driver = Mock(current_url="https://www.linkedin.com/jobs-tracker/?stage=saved")
        driver.execute_script.side_effect = script
        with patch.object(easy_apply, "pause"):
            jobs = easy_apply.saved_jobs(driver)
        self.assertEqual(len(jobs), 11)
        self.assertEqual(jobs[0], ("https://www.linkedin.com/jobs/view/4000000/", "Job 0"))
        self.assertEqual(jobs[-1], ("https://www.linkedin.com/jobs/view/4000099/", "Last job"))
        driver.get.assert_called_once_with(easy_apply.SAVED_JOBS_URL)

    def test_pin_saved_linkedin_keeps_known_links(self):
        self.log.save("https://lv.linkedin.com/jobs/view/python-developer-at-acme-4482468829", title="Python Developer",
                      status="applied")
        saved = [("https://www.linkedin.com/jobs/view/4482468829/", "Python Developer"),
                 ("https://www.linkedin.com/jobs/view/4511111111/", "QA Engineer")]
        path = self.root / "saved_jobs.db"
        with patch.object(easy_apply, "saved_jobs", return_value=saved):
            links = main.pin_saved_linkedin(Mock(), path, self.log)
        self.assertEqual(links, ["https://lv.linkedin.com/jobs/view/python-developer-at-acme-4482468829",
                                 "https://www.linkedin.com/jobs/view/4511111111/"])
        db = JobDatabase(path)
        try:
            rows = db.rows()
        finally:
            db.close()
        self.assertTrue(all(row["pinned"] for row in rows))
        # Already applied under the other link form: not a candidate again.
        picked = pipeline.candidates(rows, self.log, 70, submit=True)
        self.assertEqual([row["link"] for row in picked], ["https://www.linkedin.com/jobs/view/4511111111/"])

    def test_signed_out_linkedin_is_skipped(self):
        with patch.object(easy_apply, "saved_jobs", side_effect=easy_apply.SessionProblem("sign in")):
            self.assertEqual(main.pin_saved_linkedin(Mock(), self.root / "saved.db", self.log), [])

    def test_saved_linkedin_job_uses_the_page_title_and_description(self):
        row = {"link": "https://www.linkedin.com/jobs/view/4511111111/", "title": "QA Engineer, Verified",
               "parse_status": "pending", "analysis_status": "pending", "match_score": None, "description": None,
               "pinned": 1}
        base_cv = {"name": "Anna Test", "headline": "", "location": "", "email": "", "phone": "", "links": [],
                   "summary": "", "experience": [], "projects": [], "education": [], "skills": [],
                   "languages": [], "certifications": []}
        page = {"title": "QA Engineer", "company": "Acme", "location": "Riga, Latvia",
                "description": "You will test our web services and write automated checks in Python. " * 3}
        llm = Mock(json=Mock(side_effect=RuntimeError("offline")))
        with patch.object(easy_apply, "open_job", return_value=("candidate", Mock())), \
                patch.object(easy_apply, "job_details", return_value=page), \
                patch.object(easy_apply, "run_application", return_value=("applied", [])), \
                patch("applier.cv.html_to_pdf", side_effect=lambda driver, html, pdf: Path(pdf)):
            counts = pipeline.run([row], driver=Mock(), pdf_driver=Mock(), llm=llm,
                                  profile={"first_name": "Anna", "last_name": "Test", "daily_limit": 5},
                                  base_cv=base_cv, out_dir=self.root, log=self.log, submit=True,
                                  sleep=lambda seconds: None, say=lambda line: None)
        self.assertEqual(counts, {"applied": 1})
        saved = self.log.rows()[0]
        self.assertEqual((saved["title"], saved["company"]), ("QA Engineer", "Acme"))


if __name__ == "__main__":
    unittest.main()
