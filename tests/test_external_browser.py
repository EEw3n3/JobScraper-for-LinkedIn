"""Company career-site forms (Lever and Greenhouse imitations) in headless Chrome.

Needs Chrome, so it only runs when JOBPARSER_BROWSER_TESTS=1.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from applier import answers, easy_apply, external

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = {"first_name": "Anna", "last_name": "Test", "email": "anna@example.test", "phone": "20000000",
           "linkedin": "https://www.linkedin.com/in/anna", "github": "https://github.com/anna",
           "eu_work_authorization": True, "needs_visa_sponsorship": False, "eeo_answer": "Prefer not to say",
           "custom_answers": {}}


@unittest.skipUnless(os.environ.get("JOBPARSER_BROWSER_TESTS") == "1", "set JOBPARSER_BROWSER_TESTS=1 to run")
class ExternalBrowserTests(unittest.TestCase):
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
        for module in (easy_apply, external):
            patcher = patch.object(module, "pause")
            patcher.start()
            self.addCleanup(patcher.stop)

    def apply(self, fixture, submit):
        def answer_fn(question, kind, options, required=True):
            return answers.resolve(question, kind, options, PROFILE)

        return external.run_external(self.driver, (FIXTURES / fixture).as_uri(), answer_fn, {"cv": self.cv},
                                     submit, self.root / "review.png")

    def test_lever_dry_run_and_submit(self):
        status, log, _ = self.apply("lever_apply.html", submit=False)
        self.assertEqual(status, "ready")
        self.assertTrue((self.root / "review.png").exists())
        self.assertIsNone(self.driver.execute_script("return window.submitted"))
        given = {entry["question"]: entry["answer"] for entry in log}
        self.assertEqual(given["Resume/CV"], "Anna_Test_CV.pdf")
        self.assertEqual(given["Full name"], "Anna Test")
        self.assertEqual(given["Are you legally authorized to work in the EU?"], "Yes")

        status, _, _ = self.apply("lever_apply.html", submit=True)
        self.assertEqual(status, "applied")
        sent = self.driver.execute_script("return window.submitted")
        self.assertEqual(sent, {"name": "Anna Test", "email": "anna@example.test", "phone": "20000000", "org": "",
                                "linkedin": "https://www.linkedin.com/in/anna", "github": "https://github.com/anna",
                                "auth": "Yes", "heard": "LinkedIn", "comments": "", "resume": "Anna_Test_CV.pdf"})

    def test_greenhouse_comboboxes(self):
        status, _, _ = self.apply("greenhouse_apply.html", submit=True)
        self.assertEqual(status, "applied")
        sent = self.driver.execute_script("return window.submitted")
        self.assertEqual(sent["question_2"], "Yes")
        self.assertEqual(sent["question_3"], "No")
        self.assertEqual(sent["gender"], "Decline To Self Identify")
        self.assertEqual(sent["resume"], "Anna_Test_CV.pdf")
        self.assertEqual((sent["first"], sent["last"], sent["heard"]), ("Anna", "Test", "LinkedIn"))

    def test_missing_required_answer_stops(self):
        profile = {**PROFILE, "eu_work_authorization": None}
        with patch.dict(PROFILE, profile), self.assertRaises(easy_apply.NeedsManual) as caught:
            self.apply("greenhouse_apply.html", submit=True)
        self.assertIn("authorized", str(caught.exception))
        self.assertIsNone(self.driver.execute_script("return window.submitted"))


class AtsTests(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(external.detect_ats("https://jobs.eu.lever.co/acme/123/apply"), "lever")
        self.assertEqual(external.detect_ats("https://job-boards.greenhouse.io/ubiquiti/jobs/1"), "greenhouse")
        self.assertEqual(external.detect_ats("https://acme.wd3.myworkdayjobs.com/x"), "workday")
        self.assertEqual(external.detect_ats("https://mintos.teamtailor.com/jobs/1"), "teamtailor")
        self.assertEqual(external.detect_ats("https://example.com/careers"), "other")


if __name__ == "__main__":
    unittest.main()
