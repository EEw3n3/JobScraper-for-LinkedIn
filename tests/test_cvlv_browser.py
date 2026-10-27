"""cv.lv's own apply form (an imitation of the signed-in flow) in headless Chrome.

Needs Chrome, so it only runs when JOBPARSER_BROWSER_TESTS=1.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from applier import answers, cvlv, easy_apply, external

FIXTURE = (Path(__file__).parent / "fixtures" / "cvlv_apply.html").as_uri()
PROFILE = {"first_name": "Anna", "last_name": "Test", "email": "anna@example.test", "phone": "20000000",
           "skills_years": {"Python": 3}, "onsite_ok": True, "custom_answers": {}}


@unittest.skipUnless(os.environ.get("JOBPARSER_BROWSER_TESTS") == "1", "set JOBPARSER_BROWSER_TESTS=1 to run")
class CvlvFormTests(unittest.TestCase):
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
        self.cv.write_bytes(b"%PDF-1.4 cv")
        self.letter = self.root / "Anna_Test_Cover_Letter.pdf"
        self.letter.write_bytes(b"%PDF-1.4 letter")
        for module in (easy_apply, external, cvlv):
            patcher = patch.object(module, "pause")
            patcher.start()
            self.addCleanup(patcher.stop)

    def apply(self, submit, letter=True):
        def answer_fn(question, kind, options, required=True):
            return answers.resolve(question, kind, options, PROFILE)

        files = {"cv": self.cv, "cover_letter": self.letter if letter else None, "cover_letter_text": None}
        return cvlv.run_application(self.driver, FIXTURE, answer_fn, files, submit, self.root / "review.png")

    def test_dry_run_stops_before_apply(self):
        status, log = self.apply(submit=False)
        self.assertEqual(status, "ready")
        self.assertIsNone(self.driver.execute_script("return window.submitted"))
        self.assertFalse(self.driver.execute_script("return window.draftSaved"))
        given = {entry["question"]: entry["answer"] for entry in log}
        self.assertEqual(given["Pievienot CV: pārlūkot"], "Anna_Test_CV.pdf")
        self.assertEqual(given["Izvēlieties pieteikumu: pārlūkot"], "Anna_Test_Cover_Letter.pdf")
        self.assertEqual(given["How many years of experience do you have with Python?"], "3")

    def test_submit_sends_the_new_cv_letter_and_answers(self):
        status, _ = self.apply(submit=True)
        self.assertEqual(status, "applied")
        sent = self.driver.execute_script("return window.submitted")
        self.assertEqual(sent, {"cv": "Anna_Test_CV.pdf", "letter": "Anna_Test_Cover_Letter.pdf", "answers": ["3", "Yes"]})
        self.assertFalse(self.driver.execute_script("return window.draftSaved"))

    def test_without_a_letter_the_letter_section_stays_closed(self):
        status, _ = self.apply(submit=True, letter=False)
        self.assertEqual(status, "applied")
        self.assertEqual(self.driver.execute_script("return window.submitted")["letter"],
                         "Turpināt bez pieteikuma vēstules")


if __name__ == "__main__":
    unittest.main()
