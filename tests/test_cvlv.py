import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from applier import cvlv as cvlv_form, easy_apply, pipeline
from scraper import cvlv
from utils.database import JobDatabase

TEXT = "Mēs meklējam Python izstrādātāju, kurš veidos mūsu servisus un strādās komandā ar datiem. " * 4


def vacancy(number, title="Python izstrādātājs/-a", **extra):
    return {"id": number, "position": title, "employerName": "Example SIA", "status": "PUBLISHED",
            "highlights": {"salaryFrom": 2000, "salaryTo": 3000, "ratePer": "MONTHLY",
                           "location": {"countryId": 101}},
            "details": {"standardDetails": [{"title": "Pienākumi", "content": f"<ul><li>{TEXT}</li></ul>"}],
                        "urlDetails": None, "fileDetails": None},
            "settings": {"applyingUrl": None, "dateTo": "2999-01-01"}, **extra}


class FakeClient:
    def __init__(self, summaries=(), vacancies=None, page=("", None, ""), picture=(b"", "")):
        self.summaries, self.vacancies = list(summaries), vacancies or {}
        self.page_result, self.picture = page, picture

    def search(self, categories):
        return iter(self.summaries)

    def vacancy(self, number):
        return self.vacancies[number]

    def page(self, url):
        return self.page_result

    def file(self, file_id):
        return self.picture


class TextTests(unittest.TestCase):
    def test_links(self):
        self.assertEqual(cvlv.vacancy_id("https://www.cv.lv/lv/vacancy/1664802/labmedtech/data"), 1664802)
        self.assertEqual(cvlv.vacancy_id("https://cv.lv/en/vacancy/1401474?framed=true"), 1401474)
        self.assertIsNone(cvlv.vacancy_id("https://www.linkedin.com/jobs/view/4479533480"))
        self.assertEqual(cvlv.vacancy_link(5), "https://www.cv.lv/lv/vacancy/5")

    def test_html_text_keeps_lines(self):
        text = cvlv.html_text("<div><strong>Tev uzticēsim:</strong></div><ul><li>Python &amp; SQL</li>"
                              "<li>Docker</li></ul><script>var x = 1;</script>")
        self.assertEqual(text, "Tev uzticēsim:\n- Python & SQL\n- Docker")

    def test_salary(self):
        self.assertEqual(cvlv.salary_text({"salaryFrom": 3200, "salaryTo": 4000, "ratePer": "MONTHLY"}),
                         "€ 3200-4000 / month")
        self.assertEqual(cvlv.salary_text({"salaryFrom": 15, "ratePer": "HOURLY"}), "€ 15 / hour")
        self.assertEqual(cvlv.salary_text({}), "Not specified")

    def test_titles(self):
        skip = ["pārdošan", "designer"]
        self.assertTrue(cvlv.wanted_title("Python izstrādātājs/-a", skip))
        self.assertFalse(cvlv.wanted_title("Vecākais Python izstrādātājs", skip))
        self.assertFalse(cvlv.wanted_title("IT pārdošanas speciālists", skip))
        self.assertFalse(cvlv.wanted_title("Product Designer", skip))


class DescriptionTests(unittest.TestCase):
    def test_own_text(self):
        text = cvlv.description(vacancy(1), FakeClient())
        self.assertTrue(text.startswith("Pienākumi\n- Mēs meklējam"))

    def test_employer_page_without_cookie_lines(self):
        ad = vacancy(1, details={"standardDetails": [], "urlDetails": "https://karjera.example.lv/jobs/1"})
        page = "Šī vietne izmanto sīkdatnes\n" + TEXT
        self.assertEqual(cvlv.description(ad, FakeClient(page=(page, None, "text/html"))), TEXT.strip())

    def test_picture_is_read_only_with_ai(self):
        ad = vacancy(1, details={"standardDetails": [], "fileDetails": {"fileId": "abc"}})
        client = FakeClient(picture=(b"png", "image/png"))
        self.assertEqual(cvlv.description(ad, client), "")
        read = Mock(return_value=TEXT)
        self.assertEqual(cvlv.description(ad, client, read), TEXT.strip())
        read.assert_called_once_with(b"png", "image/png")


class CollectTests(unittest.TestCase):
    def test_new_suitable_vacancies_are_saved(self):
        summaries = [{"id": 1, "positionTitle": "Python izstrādātājs/-a", "publishDate": "2026-10-24T03:58:11Z"},
                     {"id": 2, "positionTitle": "Senior Python Developer"},
                     {"id": 3, "positionTitle": "IT pārdošanas speciālists"},
                     {"id": 4, "positionTitle": "Backend Developer"}]
        client = FakeClient(summaries, {1: vacancy(1), 4: vacancy(4, "Backend Developer")})
        saved, steps = [], []
        count = cvlv.collect(saved.append, client=client, categories=["INFORMATION_TECHNOLOGY"],
                             skip_words=["pārdošan"], known={"https://www.cv.lv/lv/vacancy/4"},
                             on_step=lambda done, total, title: steps.append((done, total)))
        self.assertEqual(count, 1)
        row = saved[0]
        self.assertEqual((row["Link"], row["Source"], row["Country"], row["Posted"]),
                         ("https://www.cv.lv/lv/vacancy/1", "cv.lv", "Latvia", "2026-10-24"))
        self.assertEqual(row["Salary"], "€ 2000-3000 / month")
        self.assertIn("Python izstrādātāju", row["Description"])
        self.assertEqual(steps, [(1, 1)])

    def test_unreadable_vacancy_is_skipped(self):
        class Broken(FakeClient):
            def vacancy(self, number):
                raise requests.ConnectionError("down")

        saved = []
        count = cvlv.collect(saved.append, client=Broken([{"id": 1, "positionTitle": "Developer"}]),
                             categories=["INFORMATION_TECHNOLOGY"], say=lambda line: None)
        self.assertEqual((count, saved), (0, []))


class AdoptAnalysisTests(unittest.TestCase):
    def test_unchanged_jobs_keep_their_score(self):
        with tempfile.TemporaryDirectory() as temp:
            old_path, new_path = Path(temp) / "jobs_1.db", Path(temp) / "jobs_2.db"
            job = {"Title": "Developer", "Link": "https://www.cv.lv/lv/vacancy/1", "Source": "cv.lv",
                   "Description": TEXT}
            changed = {**job, "Link": "https://www.cv.lv/lv/vacancy/2"}
            old = JobDatabase(old_path)
            for row in (job, changed):
                old.save_job(row)
                old.update_analysis(row["Link"], 77, "report", "cv.pdf", "letter", analysis_key="key")
            old.close()
            new = JobDatabase(new_path)
            new.save_job(job)
            new.save_job({**changed, "Description": TEXT + " Changed."})
            self.assertEqual(new.adopt_analysis(old_path), 1)
            rows = {row["link"]: row for row in new.rows()}
            self.assertEqual((rows[job["Link"]]["match_score"], rows[job["Link"]]["analysis_key"]), (77, "key"))
            self.assertEqual(rows[changed["Link"]]["analysis_status"], "pending")
            self.assertEqual(new.adopt_analysis(Path(temp) / "missing.db"), 0)
            new.close()


class ListTests(unittest.TestCase):
    def test_vacancy_ids_from_any_wrapper(self):
        self.assertEqual(cvlv_form._vacancy_ids({"total": 2, "vacancies": [{"id": 5}, {"vacancyId": 6}]}), [5, 6])
        self.assertEqual(cvlv_form._vacancy_ids([{"vacancy": {"id": 7}}]), [7])
        self.assertEqual(cvlv_form._vacancy_ids({"errorType": "NOT_AUTHENTICATED"}), [])

    def test_open_job_states(self):
        driver = Mock()
        client = FakeClient(vacancies={
            1: vacancy(1),
            2: vacancy(2, settings={"applyingUrl": " https://acme.recruitee.com/o/dev/c/new ", "dateTo": "2999-01-01"}),
            3: vacancy(3, dateUserLastApplied="2026-10-20"),
            4: vacancy(4, settings={"dateTo": "2020-01-01"})})
        self.assertEqual(cvlv_form.open_job(driver, cvlv.vacancy_link(1), client)[:2], ("cvlv", None))
        self.assertEqual(cvlv_form.open_job(driver, cvlv.vacancy_link(2), client)[:2],
                         ("external", "https://acme.recruitee.com/o/dev/c/new"))
        self.assertEqual(cvlv_form.open_job(driver, cvlv.vacancy_link(3), client)[0], "already_applied")
        self.assertEqual(cvlv_form.open_job(driver, cvlv.vacancy_link(4), client)[0], "closed")

        class Gone(FakeClient):
            def vacancy(self, number):
                response = SimpleNamespace(status_code=404 if number == 5 else 503)
                raise requests.HTTPError(response=response)

        self.assertEqual(cvlv_form.open_job(driver, cvlv.vacancy_link(5), Gone())[0], "closed")
        self.assertEqual(cvlv_form.open_job(driver, cvlv.vacancy_link(6), Gone())[0], "unavailable")


def job(link, score=80, title="Python Developer", **extra):
    return {"link": link, "title": title, "parse_status": "parsed", "analysis_status": "analyzed",
            "match_score": score, "description": TEXT, "country": "Latvia", **extra}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.log = pipeline.ApplicationLog(self.root / "applications.db")
        self.addCleanup(self.log.close)
        self.profile = {"first_name": "Anna", "last_name": "Test", "phone": "", "daily_limit": 5, "min_score": 60}
        self.base_cv = {"name": "Anna Test", "headline": "", "location": "", "email": "", "phone": "", "links": [],
                        "summary": "", "experience": [], "projects": [], "education": [], "skills": [],
                        "languages": [], "certifications": []}

    def test_pinned_jobs_come_first_whatever_their_score(self):
        rows = [job("a", 90), job("pinned", None, title="Senior Developer", analysis_status="pending", pinned=1),
                job("low", 30)]
        picked = pipeline.candidates(rows, self.log, 60, submit=True)
        self.assertEqual([row["link"] for row in picked], ["pinned", "a"])

    def run_pipeline(self, rows, open_results):
        llm = SimpleNamespace(json=Mock(side_effect=RuntimeError("offline")))
        with patch.object(cvlv_form, "signed_in", return_value=True), \
                patch.object(cvlv_form, "open_job", side_effect=open_results), \
                patch.object(cvlv_form, "run_application", return_value=("applied", [])) as own_form, \
                patch("applier.external.run_external", return_value=("applied", [], "recruitee")) as employer, \
                patch("applier.external.follow_apply_button") as follow, \
                patch("applier.external.close_extra_tabs"), \
                patch("applier.cv.html_to_pdf", side_effect=lambda driver, html, pdf: Path(pdf)):
            counts = pipeline.run(rows, driver=Mock(), pdf_driver=Mock(), llm=llm, profile=self.profile,
                                  base_cv=self.base_cv, out_dir=self.root, log=self.log, submit=True,
                                  sleep=lambda seconds: None, say=lambda line: None)
        return counts, own_form, employer, follow

    def test_cvlv_jobs_use_the_cvlv_form_or_the_employer_form(self):
        rows = [job(cvlv.vacancy_link(1)), job(cvlv.vacancy_link(2), 70, title="Backend Developer"),
                job(cvlv.vacancy_link(3), 65, title="Automation Engineer")]
        ad = {"employerName": "Example SIA", "highlights": {"location": {"countryId": 101}}}
        counts, own_form, employer, follow = self.run_pipeline(rows, [
            ("cvlv", None, ad), ("external", "https://acme.recruitee.com/o/dev/c/new", ad), ("closed", None, ad)])
        self.assertEqual(counts, {"applied": 2, "closed": 1})
        own_form.assert_called_once()
        self.assertEqual(employer.call_args.args[1], "https://acme.recruitee.com/o/dev/c/new")
        follow.assert_not_called()
        statuses = {row["link"]: (row["status"], row["ats"], row["company"]) for row in self.log.rows()}
        self.assertEqual(statuses[cvlv.vacancy_link(1)], ("applied", "cvlv", "Example SIA"))
        self.assertEqual(statuses[cvlv.vacancy_link(2)], ("applied", "recruitee", "Example SIA"))
        self.assertEqual(statuses[cvlv.vacancy_link(3)][0], "closed")

    def test_linkedin_limit_does_not_stop_cvlv(self):
        rows = [job("https://www.linkedin.com/jobs/view/1", 95), job(cvlv.vacancy_link(1), 70)]
        ad = {"employerName": "Example SIA", "highlights": {"location": {"countryId": 101}}}
        with patch.object(easy_apply, "open_job", side_effect=easy_apply.SessionProblem("limit")):
            with self.assertRaises(easy_apply.SessionProblem):
                self.run_pipeline(rows, [("cvlv", None, ad)])
        self.assertEqual(self.log.status(cvlv.vacancy_link(1)), "applied")
        self.assertIsNone(self.log.status("https://www.linkedin.com/jobs/view/1"))


if __name__ == "__main__":
    unittest.main()
