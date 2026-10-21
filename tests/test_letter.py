import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from applier import letter

CV = {"name": "Anna Test", "headline": "Developer | Python, FastAPI", "location": "Riga", "email": "anna@example.test",
      "phone": "", "links": [], "summary": "Developer building Python services.",
      "experience": [{"title": "Software Developer", "org": "Example SIA", "location": "", "period": "2023",
                      "bullets": ["Built a REST API with FastAPI for 47 stores."]}],
      "projects": [], "education": [], "skills": [{"name": "Backend", "keywords": ["Python", "FastAPI"]}],
      "languages": [], "certifications": []}
PROFILE = {"first_name": "Anna", "last_name": "Test", "email": "anna@example.test"}
JOB = "We use Java, Spring Boot and Angular at ACME. You will build REST services."
GOOD = ("Dear ACME Hiring Team,\n\nI am a Software Developer from Riga building Python services.\n\n"
        "I found the Backend Developer role on LinkedIn. At Example SIA I built a REST API with FastAPI for 47 stores. "
        "I have not yet worked with Java, and I am motivated to learn it.\n\nKind regards,\nAnna Test")


class LetterTests(unittest.TestCase):
    def problems(self, text):
        return letter.letter_problems(text, CV, PROFILE, "Backend Developer", "ACME", JOB)

    def test_honest_letter_passes(self):
        self.assertEqual(self.problems(GOOD), [])

    def test_invented_facts_and_style_are_rejected(self):
        self.assertTrue(self.problems(GOOD.replace("FastAPI for 47 stores", "Java for 5000 stores")))
        self.assertTrue(self.problems(GOOD + "\nI have 5 years of Kubernetes experience."))
        level = "states a language level (only \"native\" is allowed)"
        self.assertIn(level, self.problems(GOOD + "\nI speak professional English and Latvian (B2)."))
        self.assertNotIn(level, self.problems(GOOD + "\nI speak English, Latvian and native Russian."))
        self.assertIn("buzzword 'scalable'", self.problems(GOOD.replace("Python services", "scalable services")))
        self.assertTrue(self.problems(""))

    def test_rewrite_once_then_give_up(self):
        def reply(text):
            *paragraphs, signoff = text.split("\n\n")
            return {"paragraphs": paragraphs, "signoff": signoff.split("\n")}

        bad = GOOD.replace("FastAPI", "Kubernetes")
        llm = SimpleNamespace(json=Mock(side_effect=[reply(bad), reply(GOOD)]))
        text, notes = letter.write_letter(CV, PROFILE, "Backend Developer", "ACME", JOB, "Example", llm)
        self.assertEqual((text, notes), (GOOD, []))
        self.assertIn("Kubernetes", llm.json.call_args_list[1].args[0])
        llm = SimpleNamespace(json=Mock(return_value=reply(bad)))
        text, notes = letter.write_letter(CV, PROFILE, "Backend Developer", "ACME", JOB, "Example", llm)
        self.assertIsNone(text)
        self.assertIn("rejected", notes[0])
        self.assertEqual(llm.json.call_count, 3)


if __name__ == "__main__":
    unittest.main()
