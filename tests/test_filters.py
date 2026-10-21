import unittest

from utils.filters import description_language, detect_grade, is_unsuitable_job


class GradeTests(unittest.TestCase):
    def test_title_decides_before_description(self):
        self.assertTrue(detect_grade("Senior DevOps Engineer", "We also hire juniors").startswith("Senior"))
        self.assertTrue(detect_grade("Junior Java developer", "Report to the team lead").startswith("Junior"))

    def test_substrings_do_not_count(self):
        self.assertEqual(detect_grade("Automation Engineer", "ISG International, internal tools"), "Not Specified")

    def test_description_is_fallback(self):
        self.assertEqual(detect_grade("Python Developer", "Entry level role, internship possible"), "Junior / Intern")

    def test_senior_titles_are_unsuitable(self):
        for title in ("Senior DevOps Engineer", "Mid/Senior Data Engineer", "Tech Lead", "Sr. Python Developer", "Principal Engineer"):
            self.assertTrue(is_unsuitable_job(title), title)
        for title in ("Junior Java developer", "Python Developer", "Leading-edge Automation Engineer"):
            self.assertFalse(is_unsuitable_job(title), title)

    def test_description_language(self):
        samples = {
            "en": "We are looking for a Python developer to join our team. You will work with the cloud and our data.",
            "de": "Wir suchen einen Systemadministrator für die IT und das Team. Sie arbeiten mit der Infrastruktur.",
            "fr": "Nous recherchons un administrateur pour les serveurs et la sécurité. Vous travaillerez dans une équipe.",
            "lv": "Mēs meklējam sistēmu administratoru. Darba pienākumi un prasības: pieredze ar Linux un tīkliem.",
            "ru": "Мы ищем разработчика Python в нашу команду. " * 5,
        }
        for code, text in samples.items():
            self.assertEqual(description_language(text), code, code)
        self.assertEqual(description_language("Python Docker Kubernetes AWS Terraform"), "other")


if __name__ == "__main__":
    unittest.main()
