"""Runs the Easy Apply flow against an offline imitation of the dialog in headless Chrome.

Needs Chrome, so it only runs when JOBPARSER_BROWSER_TESTS=1.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from applier import answers, easy_apply

FIXTURE = Path(__file__).parent / "fixtures" / "easy_apply.html"
FIXTURE_2026 = Path(__file__).parent / "fixtures" / "easy_apply_2026.html"
PROFILE = {"phone_country": "Latvia (+371)", "phone": "20000000", "skills_years": {"Python": 3},
           "eu_work_authorization": True, "languages": {"English": "Professional working"},
           "accept_application_consents": True, "custom_answers": {}}


@unittest.skipUnless(os.environ.get("JOBPARSER_BROWSER_TESTS") == "1", "set JOBPARSER_BROWSER_TESTS=1 to run")
class EasyApplyBrowserTests(unittest.TestCase):
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
        patcher = patch.object(easy_apply, "pause")
        patcher.start()
        self.addCleanup(patcher.stop)

    def apply(self, profile, submit):
        state, button = easy_apply.open_job(self.driver, FIXTURE.as_uri())
        self.assertEqual(state, "candidate")
        self.assertEqual(easy_apply.job_details(self.driver)["company"], "Example Company")

        def answer_fn(question, kind, options, required=True):
            return answers.resolve(question, kind, options, profile)

        return easy_apply.run_application(self.driver, button, answer_fn,
                                          {"cv": self.cv, "cover_letter_text": "Dear Example team"},
                                          submit, self.root / "review.png")

    def test_dry_run_fills_every_step_and_discards(self):
        status, log = self.apply(PROFILE, submit=False)
        self.assertEqual(status, "ready")
        self.assertTrue((self.root / "review.png").exists())
        given = {entry["question"]: entry["answer"] for entry in log}
        self.assertEqual(given["Mobile phone number"], "20000000")
        self.assertEqual(given["Phone country code"], "Latvia (+371)")
        self.assertEqual(given["Upload resume"], "Anna_Test_CV.pdf")
        self.assertEqual(given["How many years of work experience do you have with Python?"], "3")
        self.assertEqual(given["Are you legally authorized to work in Latvia?"], "Yes")
        self.assertEqual(given["What is your level of proficiency in English?"], "Professional working")
        self.assertEqual(given["Cover letter"], "Dear Example team")
        self.assertIsNone(self.driver.execute_script("return window.submitted"))
        self.assertTrue(self.driver.execute_script("return window.discarded"))

    def test_submit_sends_the_filled_form_without_following(self):
        status, _ = self.apply(PROFILE, submit=True)
        self.assertEqual(status, "applied")
        sent = self.driver.execute_script("return window.submitted")
        self.assertEqual(sent["phone"], "20000000")
        self.assertEqual(sent["auth"], "Yes")
        self.assertEqual(sent["resume"], "Anna_Test_CV.pdf")
        self.assertFalse(sent["follow"])

    def test_2026_dialog_in_russian(self):
        for submit in (False, True):
            with self.subTest(submit=submit):
                state, button = easy_apply.open_job(self.driver, FIXTURE_2026.as_uri())
                self.assertEqual(state, "candidate")

                def answer_fn(question, kind, options, required=True):
                    return answers.resolve(question, kind, options, PROFILE)

                status, log = easy_apply.run_application(self.driver, button, answer_fn, {"cv": self.cv},
                                                         submit, self.root / "review.png")
                given = {entry["question"]: entry["answer"] for entry in log}
                self.assertEqual(given["Телефонный код страны"], "Латвия (+371)")
                self.assertEqual(given["Phone"], "20000000")
                self.assertEqual(given["Загрузить резюме"], "Anna_Test_CV.pdf")
                if submit:
                    self.assertEqual(status, "applied")
                    sent = self.driver.execute_script("return window.submitted")
                    self.assertEqual(sent, {"phone": "20000000", "code": "lv", "resume": "Anna_Test_CV.pdf",
                                            "follow": False})
                else:
                    self.assertEqual(status, "ready")
                    self.assertIsNone(self.driver.execute_script("return window.submitted"))
                    self.assertTrue(self.driver.execute_script("return window.discarded"))

    def test_unknown_required_answer_stops_without_submitting(self):
        profile = {**PROFILE, "accept_application_consents": False}
        with self.assertRaises(easy_apply.NeedsManual) as caught:
            self.apply(profile, submit=True)
        self.assertIn("privacy policy", str(caught.exception))
        self.assertIsNone(self.driver.execute_script("return window.submitted"))
        self.assertTrue(self.driver.execute_script("return window.discarded"))


if __name__ == "__main__":
    unittest.main()
