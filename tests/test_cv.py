import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from applier import cv

# A fictional CV in the Reactive Resume JSON layout.
RESUME = {
    "basics": {"name": "Anna Test", "headline": "Developer | Python, FastAPI", "email": "anna@example.test",
               "phone": "", "location": "Riga, Latvia"},
    "summary": {"content": "<p>Developer building Python services and automation.</p>"},
    "sections": {
        "profiles": {"items": [{"hidden": False, "website": {"url": "https://github.com/anna"}}]},
        "experience": {"items": [{"hidden": False, "company": "Example SIA", "position": "Software Developer",
                                  "location": "Riga", "period": "2023 – Present",
                                  "description": "<ul><li><p>Built a REST API with FastAPI.</p></li>"
                                                 "<li><p>Automated reports with Python &amp; SQL.</p></li></ul>"}]},
        "projects": {"items": [{"hidden": False, "name": "Price Monitor", "period": "2025",
                                "description": "<ul><li><p>Scraped 5 stores.</p></li><li><p>Ran Docker on Linux.</p></li></ul>"}]},
        "education": {"items": [{"hidden": False, "school": "Tech School", "degree": "Programmer", "area": "",
                                 "grade": "9/10", "location": "Riga", "period": "2022 – 2026"}]},
        "skills": {"items": [{"hidden": False, "name": "Backend", "keywords": ["Python", "FastAPI", "SQL"]},
                             {"hidden": False, "name": "DevOps", "keywords": ["Docker", "Linux"]},
                             {"hidden": True, "name": "Hidden", "keywords": ["Secret"]}]},
        "languages": {"items": [{"hidden": False, "language": "English", "fluency": "Fluent"}]},
        "certifications": {"items": []},
    },
}


class CvTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "cv.json"
        path.write_text(json.dumps(RESUME), encoding="utf-8")
        self.cv = cv.load_base_cv(path)

    def test_load_reads_visible_sections(self):
        self.assertEqual(self.cv["experience"][0]["bullets"], ["Built a REST API with FastAPI.", "Automated reports with Python & SQL."])
        self.assertEqual([group["name"] for group in self.cv["skills"]], ["Backend", "DevOps"])
        self.assertEqual(self.cv["links"], ["https://github.com/anna"])

    def test_unsupported_terms(self):
        base = cv.cv_plain_text(self.cv)
        self.assertEqual(cv.unsupported_terms("Python developer using FastAPI and Docker.", base), [])
        self.assertEqual(cv.unsupported_terms("Builds Java services on AWS with Kubernetes", base), ["Java", "AWS", "Kubernetes"])
        self.assertEqual(cv.unsupported_terms("Scraped 7 stores", base), ["7"])
        self.assertTrue(cv.unsupported_terms("5 years of Python experience", base))

    def test_tailoring_reorders_but_never_adds(self):
        result = {"headline": "Python Backend Developer | FastAPI, SQL",
                  "summary": "Junior developer with Go and Rust.",
                  "skill_groups": [{"name": "DevOps", "keywords": ["Linux", "Kubernetes"]},
                                   {"name": "Invented", "keywords": ["Go"]}],
                  "experience_order": [[1, 0]], "project_order": [[0, 0]]}
        reordered, _ = cv.apply_tailoring(self.cv, {**result, "project_order": [[1, 0]]})
        self.assertEqual(reordered["projects"][0]["bullets"], ["Ran Docker on Linux.", "Scraped 5 stores."])
        tailored, notes = cv.apply_tailoring(self.cv, result)
        self.assertEqual(tailored["headline"], self.cv["headline"])
        self.assertEqual(tailored["summary"], self.cv["summary"])
        self.assertTrue(any("summary" in note for note in notes))
        self.assertEqual(tailored["skills"], [{"name": "DevOps", "keywords": ["Linux", "Docker"]},
                                              {"name": "Backend", "keywords": ["Python", "FastAPI", "SQL"]}])
        # The first experience bullet is pinned; only the rest may move.
        self.assertEqual(tailored["experience"][0]["bullets"][0], "Built a REST API with FastAPI.")
        # An invalid order (repeated index) keeps the original order.
        self.assertEqual(tailored["projects"][0]["bullets"], self.cv["projects"][0]["bullets"])

    def test_summary_must_keep_the_plain_voice(self):
        self.assertEqual(cv.summary_problems("Developer building Python services and automation."), [])
        self.assertIn("robust", cv.summary_problems("Builds robust Python services."))
        self.assertIn("first-person pronoun", cv.summary_problems("I am a developer building Python services."))
        self.assertTrue(cv.summary_problems("word " * 80))
        self.assertEqual(cv.unsourced_sentences("Built a REST API with FastAPI. Follows modern development practices.",
                                                self.cv), ["Follows modern development practices."])
        result = {"headline": "", "summary": "Proven track record building Python services.", "skill_groups": [],
                  "experience_order": [], "project_order": []}
        tailored, notes = cv.apply_tailoring(self.cv, result)
        self.assertEqual(tailored["summary"], self.cv["summary"])
        self.assertTrue(any("proven track record" in note for note in notes))

    def test_tailor_cv_falls_back_when_the_model_fails(self):
        llm = SimpleNamespace(json=Mock(side_effect=RuntimeError("quota")))
        tailored, notes = cv.tailor_cv(self.cv, "Python Developer", "Python job", llm)
        self.assertEqual(tailored, self.cv)
        self.assertIn("quota", notes[0])

    def test_house_rules(self):
        self.assertEqual(cv.clean("Oct 2025 — Present · Riga – Latvia"), "Oct 2025 - Present, Riga - Latvia")
        self.assertEqual(cv.phone_text(29000000, "Latvia (+371)"), "+371 29000000")
        self.assertEqual(cv.phone_text("+44 7000"), "+44 7000")
        self.assertIn("lists target roles", cv.summary_problems("Open to Python and System Administration roles."))
        self.assertIn("mentions user numbers", cv.summary_problems("Ran a platform for 47 users."))
        personal = {**self.cv, "summary": "20-year-old developer building Python services. Always ready to learn."}
        self.assertIn("closing sentence changed", cv.summary_problems("20-year-old developer building Python "
                                                                      "services.", personal))
        html = cv.render_html({**self.cv, "headline": "Shown nowhere", "experience": [
            {**self.cv["experience"][0], "period": "2023 – Present"}]}, phone="20000000", country="Latvia (+371)")
        self.assertNotIn("Shown nowhere", html)
        self.assertNotIn("·", html)
        self.assertNotIn("–", html)
        self.assertIn("+371 20000000", html)

    def test_latvian_cv_labels_and_voice(self):
        latvian = {**self.cv, "language": "lv", "summary": "Izstrādāju Python servisus."}
        self.assertIn("Darba pieredze", cv.render_html(latvian))
        self.assertNotIn("first-person pronoun", cv.summary_problems("Izstrādāju Python servisus.", latvian))

    def test_period_drops_the_project_note(self):
        self.assertEqual(cv._period("Oct 2025 – Present · Qualification project"), "Oct 2025 - Present")

    def test_render_escapes_text(self):
        changed = {**self.cv, "summary": "<script>alert(1)</script>"}
        html = cv.render_html(changed, phone="+371 20000000")
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("+371 20000000", html)
        self.assertIn("Python &amp; SQL", html)
        self.assertIn("&lt;b&gt;", cv.render_letter_html("Dear team,\n\n<b>Hi</b>", "Anna"))


if __name__ == "__main__":
    unittest.main()
