import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("form_lab", Path(__file__).parents[1] / "tools" / "form_lab.py")
form_lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(form_lab)


class SampleTests(unittest.TestCase):
    def test_sites_take_turns_and_carry_their_share(self):
        items = [{"url": f"https://a.example/{n}"} for n in range(5)] + [{"url": "https://b.example/1"}]
        picked = form_lab.sample(items, per_host=2)
        self.assertEqual([item["url"] for item in picked],
                         ["https://a.example/0", "https://b.example/1", "https://a.example/1"])
        self.assertEqual([item["weight"] for item in picked], [2.5, 1.0, 2.5])
        self.assertEqual(len(form_lab.sample(items, per_host=0)), 6)

    def test_score_weights_each_form_by_its_ads(self):
        results = [{"status": "ready", "weight": 3}, {"status": "ready_step1", "weight": 1},
                   {"status": "account", "weight": 4}]
        self.assertEqual(form_lab.score(results), 50)
        self.assertEqual(form_lab.score(results + [{"status": "closed", "weight": 5}]), 50)

    def test_email_only_pages(self):
        from applier.external import EMAIL_ONLY

        self.assertTrue(EMAIL_ONLY.search("Lūdzu, sūtiet savu pieteikumu uz e-pastu personals@example.lv"))
        self.assertTrue(EMAIL_ONLY.search("Please send your CV to jobs@example.com"))
        self.assertIsNone(EMAIL_ONLY.search("Questions? Write to hr@example.com. Apply with the form below."))


if __name__ == "__main__":
    unittest.main()
