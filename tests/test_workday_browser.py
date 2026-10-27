"""Workday applications on an imitation of a Workday career site, in headless Chrome.

Needs Chrome, so it only runs when JOBPARSER_BROWSER_TESTS=1.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from applier import answers, easy_apply, workday

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = {"first_name": "Anna", "last_name": "Test", "email": "anna@example.test", "phone": "20000000",
           "phone_country": "Latvia (+371)", "city": "Riga, Latvia", "needs_visa_sponsorship": False,
           "eeo_answer": "Prefer not to say", "custom_answers": {}}


@unittest.skipUnless(os.environ.get("JOBPARSER_BROWSER_TESTS") == "1", "set JOBPARSER_BROWSER_TESTS=1 to run")
class WorkdayBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from applier.browser import pdf_driver

        cls.driver = pdf_driver()

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cv = self.root / "Anna_Test_CV.pdf"
        self.cv.write_bytes(b"%PDF-1.4 test")
        for module in (easy_apply, workday):
            patcher = patch.object(module, "pause")
            patcher.start()
            self.addCleanup(patcher.stop)

    def apply(self, submit, signed=True, login_wait=0):
        def answer_fn(question, kind, options, required=True):
            return answers.resolve(question, kind, options, PROFILE)

        url = (FIXTURES / "workday_apply.html").as_uri() + ("#signed" if signed else "")
        self.driver.get("about:blank")
        return workday.run_workday(self.driver, url, answer_fn, {"cv": self.cv}, submit, self.root / "review.png",
                                   login_wait=login_wait, say=lambda *_: None)

    def test_sign_in_step_stops_without_waiting(self):
        with self.assertRaises(easy_apply.NeedsManual) as caught:
            self.apply(submit=False, signed=False)
        self.assertIn("sign in", str(caught.exception))
        self.assertIsNone(self.driver.execute_script("return window.submitted"))

    def test_dry_run_stops_at_review(self):
        status, log, ats = self.apply(submit=False)
        self.assertEqual((status, ats), ("ready", "workday"))
        self.assertIsNone(self.driver.execute_script("return window.submitted"))
        self.assertTrue((self.root / "review.png").exists())
        given = {entry["question"]: entry["answer"] for entry in log}
        self.assertEqual(given["Phone Device Type"], "Mobile")

    def test_submit_fills_every_step(self):
        status, _, _ = self.apply(submit=True)
        self.assertEqual(status, "applied")
        self.assertEqual(self.driver.execute_script("return window.submitted"), {
            "source": "LinkedIn", "first": "Anna", "last": "Test", "phoneType": "Mobile", "code": "Latvia (+371)",
            "phone": "20000000", "resume": "Anna_Test_CV.pdf", "visa": "No", "gender": "I do not wish to answer"})


if __name__ == "__main__":
    unittest.main()
