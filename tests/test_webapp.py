"""Offline panel tests: no CLI, browser, AI or personal files are accessed."""

import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from unittest.mock import patch

from webapp import PROJECT, PanelError, Tasks, create_app, safe_file


PASSWORD = "test-password-123456789"
SIGNAL_PROCESS = Tasks.signal_process


class FakeProcess:
    pid = 12345

    def __init__(self, rc=None):
        self.rc = rc
        self.signals = []

    def poll(self):
        return self.rc

    def wait(self, timeout=None):
        if self.rc is None:
            self.rc = -9
        return self.rc

    def send_signal(self, sig):
        self.signals.append(sig)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = dict(TESTING=True, WEB_USER="admin", WEB_PASSWORD=PASSWORD,
                           RESULTS_DIR=self.root / "results", STATE_DIR=self.root / "state",
                           CV_DIR=self.root / "cvs", TASK_TIMEOUT=30, STOP_GRACE=0.01)
        self.app = create_app(self.config)
        self.manager = self.app.extensions["tasks"]
        self.client = self.app.test_client()
        self.auth = {"Authorization": "Basic " + base64.b64encode(("admin:" + PASSWORD).encode()).decode()}
        self.client.get("/", headers=self.auth)
        with self.client.session_transaction() as session:
            self.token = session["csrf"]
        self.headers = {**self.auth, "Origin": "http://localhost", "X-CSRF-Token": self.token}
        # Always intercept process-group cleanup, including Windows taskkill.
        self.signals = patch.object(Tasks, "signal_process").start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.manager.close)

    def post(self, data, headers=None):
        return self.client.post("/api/tasks", data=json.dumps(data), content_type="application/json",
                                headers=self.headers if headers is None else headers)

    def db(self, name="jobs_test.db"):
        path = self.config["RESULTS_DIR"] / name
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE jobs (parse_status TEXT, analysis_status TEXT)")
            db.executemany("INSERT INTO jobs VALUES (?, ?)", [("parsed", "analyzed"), ("pending", "pending")])
        return path

    def wait(self):
        self.manager.thread.join(3)
        self.assertFalse(self.manager.thread.is_alive())

    def test_password_required_and_no_creation_before_validation(self):
        for password in ("", "short", None):
            with self.assertRaises(ValueError):
                create_app({**self.config, "WEB_PASSWORD": password, "STATE_DIR": self.root / "unused"})
        self.assertFalse((self.root / "unused").exists())

    def test_health_only_public_including_static_and_missing(self):
        self.assertEqual(self.client.get("/healthz").json, {"status": "ok"})
        for path in ("/", "/api/state", "/static/panel.js", "/reports/test.html", "/databases/test.db", "/missing"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 401)
                self.assertIn("Basic", response.headers["WWW-Authenticate"])
        self.assertEqual(self.client.post("/healthz").status_code, 401)

    def test_unicode_credentials(self):
        app = create_app({**self.config, "WEB_USER": "оператор", "WEB_PASSWORD": "пароль-длиннее-16-символов"})
        self.addCleanup(app.extensions["tasks"].close)
        encoded = base64.b64encode("оператор:пароль-длиннее-16-символов".encode()).decode()
        self.assertEqual(app.test_client().get("/api/state", headers={"Authorization": "Basic " + encoded}).status_code, 200)
        self.assertEqual(self.client.get("/", headers={"Authorization": "Basic broken"}).status_code, 401)

    def test_csrf_and_origin_required(self):
        cases = [self.auth, {**self.auth, "Origin": "http://localhost"},
                 {**self.headers, "Origin": "https://evil.test"},
                 {**self.headers, "Origin": "null"},
                 {**self.headers, "X-CSRF-Token": "wrong"},
                 {**self.headers, "Sec-Fetch-Site": "cross-site"}]
        with patch("webapp.subprocess.Popen") as spawn:
            for headers in cases:
                self.assertEqual(self.post({"command": "scrape"}, headers).status_code, 403)
            spawn.assert_not_called()

    def test_security_headers_no_secrets(self):
        response = self.client.get("/", headers=self.auth)
        self.assertNotIn(PASSWORD, response.text)
        self.assertNotIn("WEB_PASSWORD", self.app.config)
        self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        client = self.app.test_client()
        cookie = client.get("/", headers=self.auth).headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_arguments_allowlisted_and_bounded(self):
        invalid = [None, [], {"command": "doctor"}, {"command": ["scrape"]},
                   {"command": "scrape", "args": "--model attacker"},
                   {"command": "scrape", "headless": False},
                   {"command": "scrape", "limit": True},
                   {"command": "scrape", "limit": "50"},
                   {"command": "scrape", "limit": 5001},
                   {"command": "scrape", "max_pages": 0},
                   {"command": "scrape", "browsers": 3},
                   {"command": "scrape", "lookback": -1},
                   {"command": "scrape", "ai": "yes"},
                    {"command": "scrape", "roles": "x\n" * 51},
                   {"command": "scrape", "countries": "x" * 161}]
        with patch("webapp.subprocess.Popen") as spawn:
            for data in invalid:
                with self.subTest(data=data):
                    self.assertEqual(self.post(data).status_code, 400)
            spawn.assert_not_called()

    def test_fixed_paths_headless_and_ai_consent(self):
        args, db = self.manager.command({"command": "scrape", "roles": "--model=not-a-flag\nEngineer"})
        self.assertIsNone(db)
        self.assertEqual(args[1:4], ["-u", str(PROJECT / "main.py"), "scrape"])
        self.assertIn("--role=--model=not-a-flag", args)
        self.assertIn("--headless", args)
        self.assertIn("--no-ai", args)
        self.assertEqual(args[args.index("--browsers") + 1], "1")
        self.assertEqual(args[args.index("--output-dir") + 1], str(self.config["RESULTS_DIR"]))
        self.assertEqual(args[args.index("--cv-dir") + 1], str(self.config["CV_DIR"]))
        db = self.db()
        with self.assertRaises(PanelError):
            self.manager.command({"command": "analyze", "db": db.name})
        args, _ = self.manager.command({"command": "analyze", "db": db.name, "ai": True})
        self.assertEqual(args[args.index("--db") + 1], str(db))
        args, _ = self.manager.command({"command": "retry", "db": db.name})
        self.assertIn("--headless", args)
        self.assertIn("--no-ai", args)
        args, _ = self.manager.command({"command": "scrape", "interactive": True})
        self.assertNotIn("--headless", args)
        with self.assertRaises(PanelError):
            self.manager.command({"command": "scrape", "interactive": True, "browsers": 2})

    def test_interactive_task_controls_browser_environment(self):
        process = FakeProcess(0)
        with patch.dict(os.environ, {"JOBPARSER_VNC_PASSWORD": "private-vnc"}), patch("webapp.subprocess.Popen", return_value=process) as spawn:
            record = self.manager.start({"command": "scrape", "interactive": True})
            self.wait()
        environment = spawn.call_args.kwargs["env"]
        self.assertEqual(environment["JOBPARSER_HEADLESS"], "0")
        self.assertEqual(environment["JOBPARSER_INTERACTIVE"], "1")
        self.assertEqual(environment["CHROME_USER_DATA_DIR"], str(self.config["STATE_DIR"] / "chrome-profile"))
        self.assertNotIn("JOBPARSER_VNC_PASSWORD", environment)
        self.assertTrue(record["interactive"])

    def test_traversal_unknown_files_and_symlinks(self):
        self.db()
        for name in ("../jobs_test.db", "..\\jobs_test.db", "/tmp/jobs.db", "C:\\jobs.db", "jobs_test.db:other", "missing.db"):
            self.assertIn(self.post({"command": "retry", "db": name}).status_code, (400, 404))
        self.assertIn(self.client.get("/reports/..%5Csecret.html", headers=self.auth).status_code, (400, 404))
        outside = self.root / "outside.db"
        outside.touch()
        link = self.config["RESULTS_DIR"] / "link.db"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("Symlink creation requires permission on Windows")
        with self.assertRaises(PanelError):
            safe_file(self.config["RESULTS_DIR"], link.name, {".db"})
        report = self.config["RESULTS_DIR"] / "jobs_test.html"
        report.symlink_to(outside)
        self.assertEqual(self.post({"command": "retry", "db": "jobs_test.db"}).status_code, 400)

    def test_sqlite_listing_does_not_migrate_and_backup_is_valid(self):
        path = self.db()
        before = path.read_bytes()
        state = self.client.get("/api/state", headers=self.auth).json
        self.assertEqual(state["databases"], [{"name": path.name, "total": 2, "parsed": 1, "analyzed": 1}])
        self.assertEqual(path.read_bytes(), before)
        response = self.client.get("/databases/" + path.name, headers=self.auth)
        copy = self.root / "copy.db"
        copy.write_bytes(response.data)
        response.close()
        with closing(sqlite3.connect(copy)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 2)
        self.assertFalse(list(self.config["STATE_DIR"].glob("backup-*")))

    def test_jobs_api_is_authenticated_paginated_and_read_only(self):
        path = self.config["RESULTS_DIR"] / "jobs_view.db"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE jobs (id INTEGER, title TEXT, link TEXT, match_score INTEGER, description TEXT, parse_status TEXT, analysis_status TEXT)")
            db.executemany("INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)", [
                (1, "Lower", "https://example.test/1", 20, "Description one", "parsed", "analyzed"),
                (2, "Higher <unsafe>", "javascript:alert(1)", 90, "Description two", "parsed", "analyzed"),
            ])
        url = "/api/databases/jobs_view.db/jobs?page=1"
        self.assertEqual(self.client.get(url).status_code, 401)
        response = self.client.get(url, headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["total"], 2)
        self.assertEqual(response.json["jobs"][0]["title"], "Higher <unsafe>")
        self.assertEqual(self.client.get("/api/databases/..%5Cjobs_view.db/jobs", headers=self.auth).status_code, 400)
        self.assertEqual(self.client.get("/api/databases/jobs_view.db/jobs?page=0", headers=self.auth).status_code, 400)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual([row[1] for row in db.execute("PRAGMA table_info(jobs)")],
                             ["id", "title", "link", "match_score", "description", "parse_status", "analysis_status"])

    def test_report_attachment_and_restrictive_policy(self):
        (self.config["RESULTS_DIR"] / "jobs_test.html").write_text("<script>alert(1)</script>", encoding="utf-8")
        response = self.client.get("/reports/jobs_test.html", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual(response.mimetype, "application/octet-stream")
        self.assertIn("sandbox", response.headers["Content-Security-Policy"])
        response.close()

    def test_backup_includes_wal_and_legacy_view_does_not_migrate(self):
        path = self.config["RESULTS_DIR"] / "legacy.db"
        with closing(sqlite3.connect(path)) as writer, writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("CREATE TABLE jobs (title TEXT)")
            writer.execute("INSERT INTO jobs VALUES ('Committed in WAL')")
            writer.commit()
            state = self.client.get("/api/state", headers=self.auth).json
            self.assertEqual(state["databases"][0]["total"], 1)
            self.assertIsNone(state["databases"][0]["parsed"])
            self.assertEqual([r[1] for r in writer.execute("PRAGMA table_info(jobs)")], ["title"])
            response = self.client.get("/databases/legacy.db", headers=self.auth)
            snapshot = self.root / "wal-snapshot.db"
            snapshot.write_bytes(response.data)
            response.close()
            with closing(sqlite3.connect(snapshot)) as copy:
                self.assertEqual(copy.execute("SELECT title FROM jobs").fetchone()[0], "Committed in WAL")

    def test_export_args_and_validation(self):
        db = self.db()
        for fmt, suffix in (("html", ".html"), ("csv", ".csv"), ("markdown", ".md")):
            args, _ = self.manager.command({"command": "export", "db": db.name, "format": fmt, "min_score": 60})
            self.assertEqual(args[args.index("--output") + 1], str(db.with_suffix(suffix)))
        for extra in ({"format": "exe"}, {"min_score": 101}, {"output": "outside.html"}):
            self.assertEqual(self.post({"command": "export", "db": db.name, **extra}).status_code, 400)

    def test_single_admission_race_and_stop_cleanup(self):
        process = FakeProcess()
        barrier = threading.Barrier(8)
        def start():
            barrier.wait()
            try:
                return self.manager.start({"command": "scrape"})
            except PanelError as error:
                return error.status
        with patch("webapp.subprocess.Popen", return_value=process) as spawn:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: start(), range(8)))
            self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
            self.assertEqual(results.count(409), 7)
            ident = self.manager.active
            response = self.client.post(f"/api/tasks/{ident}/stop", json={}, headers=self.headers)
            self.assertEqual(response.status_code, 202)
            self.wait()
            self.assertEqual(self.manager.records[ident]["status"], "stopped")
            self.assertIsNone(self.manager.active)
            spawn.assert_called_once()
            options = spawn.call_args.kwargs
            self.assertFalse(options["shell"])
            self.assertEqual(options["cwd"], PROJECT)
            self.assertNotIn("JOBPARSER_WEB_PASSWORD", options["env"])
            self.assertEqual(options["stderr"], -2)
            if os.name == "posix":
                self.assertTrue(options["start_new_session"])
            self.assertTrue(any(call.kwargs.get("force") for call in self.signals.call_args_list))

    def test_return_codes_snapshot_and_persistence(self):
        for rc, status in ((0, "succeeded"), (2, "partial"), (1, "failed")):
            def spawn(*args, **kwargs):
                (self.config["RESULTS_DIR"] / f"jobs_{rc}.db").touch()
                return FakeProcess(rc)
            with patch("webapp.subprocess.Popen", side_effect=spawn):
                result = self.post({"command": "scrape"})
                self.assertEqual(result.status_code, 202)
                self.wait()
            record = self.manager.records[result.json["task"]["id"]]
            self.assertEqual(record["status"], status)
            self.assertEqual(record["db"], f"jobs_{rc}.db")
            stored = json.loads((self.config["STATE_DIR"] / (record["id"] + ".json")).read_text())
            self.assertEqual(stored, record)

    def test_spawn_failure_releases_slot_without_exposing_error(self):
        with patch("webapp.subprocess.Popen", side_effect=OSError("secret-api-key")):
            result = self.post({"command": "scrape"})
            self.wait()
        self.assertIsNone(self.manager.active)
        record = self.manager.records[result.json["task"]["id"]]
        self.assertEqual(record["status"], "failed")
        self.assertNotIn("secret-api-key", json.dumps(record))
        with patch("webapp.subprocess.Popen", return_value=FakeProcess(0)):
            self.assertEqual(self.post({"command": "scrape"}).status_code, 202)
            self.wait()

    def test_thread_start_failure_releases_admission(self):
        with patch("webapp.threading.Thread.start", side_effect=RuntimeError("unavailable")):
            response = self.post({"command": "scrape"})
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(self.manager.active)
        self.assertEqual(self.manager.history()[0]["status"], "failed")

    def test_restart_marks_interrupted_does_not_signal_pid(self):
        ident = "a" * 32
        (self.config["STATE_DIR"] / (ident + ".json")).write_text(json.dumps(
            dict(id=ident, command="scrape", status="running", started=time.time(), pid=99999)))
        app = create_app(self.config)
        manager = app.extensions["tasks"]
        self.addCleanup(manager.close)
        self.assertEqual(manager.records[ident]["status"], "interrupted")
        self.assertIsNone(manager.active)
        self.signals.assert_not_called()
        self.assertEqual(json.loads((self.config["STATE_DIR"] / (ident + ".json")).read_text())["status"], "interrupted")

    def test_timeout_and_shutdown_stop_active(self):
        self.app.config["TASK_TIMEOUT"] = 0.01
        with patch("webapp.subprocess.Popen", return_value=FakeProcess()):
            self.manager.start({"command": "scrape"})
            self.wait()
        self.assertEqual(self.manager.history()[0]["reason"], "timeout")
        self.app.config["TASK_TIMEOUT"] = 30
        with patch("webapp.subprocess.Popen", return_value=FakeProcess()):
            record = self.manager.start({"command": "scrape"})
            self.manager.close()
        self.assertEqual(self.manager.records[record["id"]]["status"], "stopped")
        with self.assertRaises(PanelError):
            self.manager.start({"command": "scrape"})

    def test_linux_signals_target_group_not_individual_pid(self):
        import signal
        with (patch("webapp.os.name", "posix"),
              patch("webapp.signal.SIGKILL", 9, create=True),
              patch("webapp.os.killpg", create=True) as kill_group):
            SIGNAL_PROCESS(FakeProcess())
            SIGNAL_PROCESS(FakeProcess(), force=True)
        self.assertEqual(kill_group.call_args_list[0].args, (12345, signal.SIGINT))
        self.assertEqual(kill_group.call_args_list[1].args, (12345, 9))

    def test_log_is_bounded_redacted_and_authenticated(self):
        with patch("webapp.subprocess.Popen", return_value=FakeProcess(0)):
            record = self.manager.start({"command": "scrape"})
            self.wait()
        path = self.config["STATE_DIR"] / (record["id"] + ".log")
        path.write_text("a" * 80000 + PASSWORD + "\napi_key=hidden-value\n", encoding="utf-8")
        url = f'/api/tasks/{record["id"]}/log'
        self.assertEqual(self.client.get(url).status_code, 401)
        text = self.client.get(url, headers=self.auth).json["text"]
        self.assertLessEqual(len(text), 65536)
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn("hidden-value", text)
        self.assertEqual(self.client.get("/api/tasks/unknown/log", headers=self.auth).status_code, 404)

    def test_cv_names_only_and_static_routes(self):
        self.config["CV_DIR"].mkdir()
        (self.config["CV_DIR"] / "sample.txt").write_text("private cv text")
        response = self.client.get("/api/state", headers=self.auth)
        self.assertEqual(response.json["cvs"], ["sample.txt"])
        self.assertNotIn("private cv text", response.text)
        for path in ("/static/panel.js", "/static/panel.css", "/static/viewer.css"):
            response = self.client.get(path, headers=self.auth)
            self.assertEqual(response.status_code, 200)
            response.close()


if __name__ == "__main__":
    unittest.main()
