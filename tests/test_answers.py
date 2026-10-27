import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from applier import answers

PROFILE = {
    "first_name": "Anna", "last_name": "Test", "email": "anna@example.test", "phone_country": "Latvia (+371)",
    "phone": "20000000", "city": "Riga, Latvia", "years_experience_total": 3,
    "skills_years": {"Python": 3, "SQL": 2, "Kubernetes": None}, "eu_work_authorization": True,
    "needs_visa_sponsorship": False, "willing_to_relocate": False, "salary_eur_month_gross": 1800,
    "salary_eur_year_gross": 21600, "languages": {"English": "Professional working"},
    "accept_application_consents": False, "custom_answers": {"Why do you want to join us?": "I like your product."},
}


def llm_returning(**result):
    return SimpleNamespace(json=Mock(return_value=result))


class AnswerTests(unittest.TestCase):
    def resolve(self, question, kind="text", options=None, profile=PROFILE, llm=None):
        return answers.resolve(question, kind, options, profile, "CV text", "Python Developer", llm)

    def test_profile_rules(self):
        self.assertEqual(self.resolve("Are you legally authorized to work in Latvia?", "choice", ["Yes", "No"]), ("Yes", "rule"))
        self.assertEqual(self.resolve("Will you now or in the future require visa sponsorship?", "choice", ["Yes", "No"]), ("No", "rule"))
        self.assertEqual(self.resolve("Phone country code", "choice", ["Estonia (+372)", "Latvia (+371)"]), ("Latvia (+371)", "rule"))
        self.assertEqual(self.resolve("Mobile phone number"), ("20000000", "rule"))
        self.assertEqual(self.resolve("How many years of work experience do you have with Python?", "number"), ("3", "rule"))
        self.assertEqual(self.resolve("Expected monthly salary in EUR", "number"), ("1800", "rule"))
        self.assertEqual(self.resolve("What is your expected annual salary?", "number"), ("21600", "rule"))
        self.assertEqual(self.resolve("What is your level of proficiency in English?", "choice",
                                      ["Native", "Professional working", "Limited"]), ("Professional working", "rule"))
        self.assertEqual(self.resolve("Why do you want to join us?"), ("I like your product.", "rule"))

    def test_unknown_facts_are_not_invented(self):
        self.assertEqual(self.resolve("How many years of experience do you have with Kubernetes?", "number"), (None, None))
        llm = llm_returning(known=False, answer="5")
        self.assertEqual(self.resolve("Do you hold an AWS certification?", "choice", ["Yes", "No"], llm=llm), (None, None))

    def test_ai_answer_must_match_an_option(self):
        llm = llm_returning(known=True, answer="Maybe")
        self.assertEqual(self.resolve("Can you work weekends?", "choice", ["Yes", "No"], llm=llm), (None, None))
        llm = llm_returning(known=True, answer="yes")
        self.assertEqual(self.resolve("Can you work weekends?", "choice", ["Yes", "No"], llm=llm), ("Yes", "ai"))
        llm = llm_returning(known=True, answer="about 2 years")
        self.assertEqual(self.resolve("Years with Django?", "number", llm=llm), ("2", "ai"))

    def test_ai_free_text_is_checked_against_the_cv(self):
        llm = llm_returning(known=True, answer="I build scalable Kubernetes platforms.")
        self.assertEqual(answers.resolve("Describe your experience", "long_text", None, PROFILE,
                                         "Python developer. Built services.", "Dev", llm), (None, None))
        llm = llm_returning(known=True, answer="I built Python services.")
        self.assertEqual(answers.resolve("Describe your experience", "long_text", None, PROFILE,
                                         "Python developer. Built services.", "Dev", llm), ("I built Python services.", "ai"))

    def test_consents_follow_the_profile_and_never_the_ai(self):
        llm = llm_returning(known=True, answer="Yes")
        self.assertEqual(self.resolve("I agree to the privacy policy", "choice", ["Yes", "No"], llm=llm), (None, None))
        llm.json.assert_not_called()
        accepting = {**PROFILE, "accept_application_consents": True}
        self.assertEqual(self.resolve("I consent to the processing of my personal data", "choice", ["Yes", "No"],
                                      profile=accepting), ("Yes", "profile"))
        self.assertEqual(self.resolve("Please review our privacy notice and provide your consent.", "choice",
                                      ["I do not consent", "I consent"], profile=accepting), ("I consent", "profile"))
        # "agree to relocate" is not a consent question.
        self.assertEqual(self.resolve("Do you agree to relocate to Berlin?", "choice", ["Yes", "No"]), ("No", "rule"))

    def test_latvian_and_russian_fields(self):
        cases = {
            "Vārds": "Anna", "Uzvārds": "Test", "Vārds Uzvārds": "Anna Test", "Vārds, uzvārds": "Anna Test",
            "E-pasta adrese": "anna@example.test", "E-PASTA ADRESE": "anna@example.test", "Tālruņa numurs": "20000000",
            "Telefona numurs": "20000000", "Имя": "Anna", "Фамилия": "Test", "Имя и фамилия": "Anna Test",
            "Электронная почта": "anna@example.test", "Номер телефона": "20000000", "Pilsēta": "Riga, Latvia",
            "First name (As written in your passport)": "Anna", "Valsts": "Latvia",
            "Vēlamais atalgojums (bruto, EUR)": 1800, "Ожидаемая зарплата": 1800,
        }
        for question, expected in cases.items():
            with self.subTest(question):
                self.assertEqual(self.resolve(question)[0], str(expected) if isinstance(expected, int) else expected)

    def test_long_questions_are_not_contact_fields(self):
        self.assertEqual(self.resolve("Do you have experience in mobile development for our clients?"), (None, None))
        self.assertEqual(self.resolve("Phone number (with country code)")[0], "20000000")

    def test_job_source_and_country_lists(self):
        profile = {**PROFILE, "job_source": "cv.lv"}
        self.assertEqual(self.resolve("Kā jūs uzzinājāt par vakanci?", profile=profile), ("cv.lv", "rule"))
        self.assertEqual(self.resolve("How did you hear about us?", "choice", ["LinkedIn", "CV.lv", "Other"],
                                      profile=profile), ("CV.lv", "rule"))
        self.assertEqual(self.resolve("Country", "choice", ["Igaunija", "Latvija", "Lietuva"]), ("Latvija", "rule"))
        self.assertEqual(self.resolve("Pilsēta", "choice", ["Daugavpils", "Rīga"]), ("Rīga", "rule"))
        self.assertEqual(self.resolve("Phone country code", "choice", ["+372 Igaunija", "+371 Latvija"]),
                         ("+371 Latvija", "rule"))

    def test_language_levels_in_other_languages(self):
        profile = {**PROFILE, "languages": {"English": "C1", "Latvian": "B2", "Russian": "Native"}}
        self.assertEqual(self.resolve("Angļu valodas zināšanu līmenis", profile=profile)[0], "C1")
        self.assertEqual(self.resolve("Уровень владения латышским языком", profile=profile)[0], "B2")
        self.assertIsNone(self.resolve("What is your level of language skills?", profile=profile)[0])

    def test_consent_scope_and_talent_pool(self):
        accepting = {**PROFILE, "accept_application_consents": True}
        options = ["I agree for my personal data to be used for this position only",
                   "I agree for my personal data to be used for all suitable positions"]
        self.assertEqual(self.resolve("Privacy", "choice", options, profile=accepting), (options[0], "profile"))
        self.assertEqual(self.resolve("Vai vēlaties, lai jūsu dati tiek saglabāti nākotnes vakancēm?", "choice",
                                      ["Jā", "Nē"], profile=accepting), ("Nē", "profile"))
        self.assertEqual(self.resolve("Piekrītu personas datu apstrādei", "choice", ["Piekrītu", "Nepiekrītu"],
                                      profile=accepting), ("Piekrītu", "profile"))

    def test_workday_phone_lists_and_work_time(self):
        self.assertEqual(self.resolve("Phone Device Type", "choice", ["Landline", "Mobile"]), ("Mobile", "rule"))
        self.assertEqual(self.resolve("Country Phone Code", "choice", ["Lithuania (+370)", "Latvia (+371)"]),
                         ("Latvia (+371)", "rule"))
        self.assertEqual(self.resolve("+371"), ("20000000", "rule"))
        self.assertEqual(self.resolve("Would you like to work full time or part time?", "choice",
                                      ["Full time", "Part time"]), ("Full time", "rule"))

    def test_match_option(self):
        self.assertEqual(answers.match_option("yes", ["Yes, I am", "No"]), "Yes, I am")
        self.assertEqual(answers.match_option("Jā", ["Jā", "Nē"]), "Jā")
        self.assertIsNone(answers.match_option("Riga, Latvia", ["Yes", "No"]))
        self.assertEqual(answers.match_option("Latvia (+371)", ["Литва (+370)", "Латвия (+371)"]), "Латвия (+371)")
        self.assertIsNone(answers.match_option("Canada (+1)", ["Канада (+1)", "Соединенные Штаты (+1)"]))


if __name__ == "__main__":
    unittest.main()
