import json
import sqlite3
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from utils.analyzer import analyze_jobs_with_cv
from utils.database import JobDatabase


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.db = JobDatabase(":memory:")
        self.addCleanup(self.db.close)
        self.job = {"Title": "Engineer", "Link": "test", "Description": "Design and build Python services and maintain reliable databases. " * 12}
        self.db.save_job(self.job)
        self.client = SimpleNamespace(models=Mock())
        self.result = {"score": 80, "cv": "cv.pdf", "report": "Relevant experience", "letter": "Dear hiring team"}
        self.client.models.generate_content.return_value = SimpleNamespace(text=json.dumps(self.result))
        patcher = patch("utils.analyzer.get_all_cvs_text", return_value=("Fictional CV text", ["cv.pdf"]))
        self.cvs = patcher.start()
        self.addCleanup(patcher.stop)

    def run_analysis(self, **kwargs):
        return analyze_jobs_with_cv(self.db, "unused", model="offline-model", client=self.client, **kwargs)

    def test_cache_force_and_changed_inputs(self):
        self.assertEqual(self.run_analysis(), {"analyzed": 1, "skipped": 0, "failed": 0})
        self.assertEqual(self.run_analysis()["skipped"], 1)
        self.assertEqual(self.client.models.generate_content.call_count, 1)
        self.assertEqual(self.run_analysis(force=True)["analyzed"], 1)
        self.cvs.return_value = ("Different fictional CV", ["cv.pdf"])
        self.assertEqual(self.run_analysis()["analyzed"], 1)
        self.db.save_job({**self.job, "Title": "Backend Engineer"})
        self.assertEqual(self.run_analysis()["analyzed"], 1)

    def test_invalid_results_are_not_saved(self):
        for text in ("not JSON", "[]", json.dumps({**self.result, "score": True}), json.dumps({**self.result, "score": 101}), json.dumps({**self.result, "cv": "unknown.pdf"}), json.dumps({**self.result, "report": ""}), json.dumps({**self.result, "letter": None})):
            with self.subTest(text=text):
                self.client.models.generate_content.return_value = SimpleNamespace(text=text)
                self.assertEqual(self.run_analysis()["failed"], 1)
                row = self.db.rows()[0]
                self.assertEqual(row["analysis_status"], "error")
                self.assertIsNone(row["analysis_key"])
                self.assertIsNone(row["match_score"])

    def test_database_errors_propagate(self):
        with patch.object(self.db, "update_analysis", side_effect=sqlite3.OperationalError("write failed")):
            with self.assertRaises(sqlite3.OperationalError):
                self.run_analysis()
        self.client.models.generate_content.side_effect = ValueError("bad request")
        with patch.object(self.db, "mark_analysis_error", side_effect=sqlite3.OperationalError("write failed")):
            with self.assertRaises(sqlite3.OperationalError):
                self.run_analysis()

    def test_readiness_and_limit(self):
        self.assertEqual(self.run_analysis(limit=0), {"analyzed": 0, "skipped": 0, "failed": 0})
        self.client.models.generate_content.assert_not_called()

    def test_bounded_retry_for_sdk_api_errors(self):
        class APIError(Exception):
            def __init__(self, code):
                super().__init__(f"HTTP {code}")
                self.code = code

        errors = ModuleType("google.genai.errors")
        errors.APIError = APIError
        response = SimpleNamespace(text=json.dumps(self.result))
        with patch.dict("sys.modules", {"google.genai.errors": errors}), patch("utils.analyzer.time.sleep") as sleep:
            self.client.models.generate_content.side_effect = [APIError(429), APIError(503), response]
            self.assertEqual(self.run_analysis()["analyzed"], 1)
            self.assertEqual(sleep.call_count, 2)
            self.client.models.generate_content.reset_mock()
            self.client.models.generate_content.side_effect = APIError(503)
            self.assertEqual(self.run_analysis(force=True)["failed"], 1)
            self.assertEqual(self.client.models.generate_content.call_count, 3)
            self.client.models.generate_content.reset_mock()
            self.client.models.generate_content.side_effect = APIError(400)
            self.assertEqual(self.run_analysis()["failed"], 1)
            self.assertEqual(self.client.models.generate_content.call_count, 1)

    def test_empty_cvs_fail_before_requests(self):
        self.cvs.return_value = ("", [])
        with self.assertRaises(RuntimeError):
            self.run_analysis()
        self.client.models.generate_content.assert_not_called()

    def test_real_sdk_with_offline_transport(self):
        try:
            import httpx
            from google import genai
        except ImportError:
            self.skipTest("Optional Gemini SDK is not installed")

        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"candidates": [{
                "content": {"role": "model", "parts": [{"text": json.dumps(self.result)}]},
                "finishReason": "STOP",
            }]})

        with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
            with genai.Client(api_key="offline-test-key", http_options={
                "httpx_client": transport, "timeout": 60000,
                "retry_options": {"attempts": 1},
            }) as client:
                counts = analyze_jobs_with_cv(self.db, "unused", model="gemini-test", client=client)
        self.assertEqual(counts["analyzed"], 1)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["generationConfig"]["responseJsonSchema"]["properties"]["cv"]["enum"], ["cv.pdf"])

    def test_limited_failures_do_not_starve_later_jobs(self):
        self.db.save_job({**self.job, "Link": "second"})
        self.client.models.generate_content.return_value = SimpleNamespace(text="invalid")
        self.assertEqual(self.run_analysis(limit=1)["failed"], 1)
        self.client.models.generate_content.return_value = SimpleNamespace(text=json.dumps(self.result))
        self.assertEqual(self.run_analysis(limit=1)["analyzed"], 1)
        rows = self.db.rows()
        self.assertEqual([row["analysis_status"] for row in rows], ["error", "analyzed"])
        self.assertEqual([row["analysis_attempts"] for row in rows], [1, 1])

    def test_low_score_letter_threshold(self):
        for score in (0, 35, 36):
            with self.subTest(score=score):
                self.client.models.generate_content.return_value = SimpleNamespace(text=json.dumps({**self.result, "score": score}))
                self.assertEqual(self.run_analysis(force=True)["analyzed"], 1)
                self.assertEqual(self.db.rows()[0]["cover_letter"], "No Cover Letter" if score <= 35 else self.result["letter"])

    def test_schema_and_request_timeout(self):
        self.run_analysis()
        config = self.client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(config["http_options"], {"timeout": 60000, "retry_options": {"attempts": 1}})
        schema = config["response_json_schema"]
        self.assertEqual(schema["required"], ["score", "cv", "report", "letter"])
        self.assertEqual(schema["properties"]["score"], {"type": "integer", "minimum": 0, "maximum": 100})
        self.assertEqual(schema["properties"]["cv"]["enum"], ["cv.pdf"])
        self.assertEqual(schema["properties"]["report"]["type"], "string")
        self.assertEqual(schema["properties"]["letter"]["type"], "string")

    def test_owned_client_timeout_and_cleanup(self):
        google = ModuleType("google")
        genai = ModuleType("google.genai")
        errors = ModuleType("google.genai.errors")
        errors.APIError = type("APIError", (Exception,), {})
        owned = SimpleNamespace(models=self.client.models, close=Mock())
        genai.Client = Mock(return_value=owned)
        google.genai = genai
        with patch.dict("sys.modules", {"google": google, "google.genai": genai, "google.genai.errors": errors}), patch("utils.analyzer.os.getenv", side_effect=lambda name: {"GEMINI_API_KEY": "fake-key"}.get(name)):
            self.assertEqual(analyze_jobs_with_cv(self.db, "unused", model="offline-model")["analyzed"], 1)
        genai.Client.assert_called_once_with(api_key="fake-key", http_options={"timeout": 60000, "retry_options": {"attempts": 1}})
        owned.close.assert_called_once_with()

    def test_transport_timeout_retries_are_bounded(self):
        httpx = ModuleType("httpx")
        httpx.TransportError = type("TransportError", (Exception,), {})
        timeout = type("ReadTimeout", (httpx.TransportError,), {})("timed out")
        with patch.dict("sys.modules", {"httpx": httpx, "google.genai.errors": None}), patch("utils.analyzer.time.sleep") as sleep:
            self.client.models.generate_content.side_effect = [timeout, SimpleNamespace(text=json.dumps(self.result))]
            self.assertEqual(self.run_analysis()["analyzed"], 1)
            sleep.assert_called_once_with(5)
            self.client.models.generate_content.reset_mock()
            sleep.reset_mock()
            self.client.models.generate_content.side_effect = timeout
            self.assertEqual(self.run_analysis(force=True)["failed"], 1)
            self.assertEqual(self.client.models.generate_content.call_count, 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(self.db.rows()[0]["analysis_attempts"], 2)

    def test_injected_client_without_optional_dependencies(self):
        with patch.dict("sys.modules", {"httpx": None, "google.genai.errors": None}):
            self.assertEqual(self.run_analysis()["analyzed"], 1)


if __name__ == "__main__":
    unittest.main()
