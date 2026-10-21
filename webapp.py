"""Authenticated, single-process operations panel. Run with python webapp.py."""

import atexit
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, render_template, request, send_file, session
from werkzeug.exceptions import HTTPException


PROJECT = Path(__file__).resolve().parent
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")
TASK_ID = re.compile(r"[0-9a-f]{32}\Z")


class PanelError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def safe_file(root, name, suffixes, exists=True):
    if not isinstance(name, str) or not NAME.fullmatch(name) or ".." in name:
        raise PanelError("Invalid filename")
    path = root / name
    if path.suffix not in suffixes or path.is_symlink() or path.resolve().parent != root:
        raise PanelError("Invalid filename")
    if exists and not path.is_file():
        raise PanelError("File not found", 404)
    return path


def integer(data, key, default, low, high):
    value = data.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise PanelError(f"{key} must be an integer in {low}..{high}")
    return value


def boolean(data, key, default=False):
    value = data.get(key, default)
    if type(value) is not bool:
        raise PanelError(f"{key} must be a boolean")
    return value


class Tasks:
    def __init__(self, app):
        self.app = app
        self.root = app.config["RESULTS_DIR"]
        self.state = app.config["STATE_DIR"]
        self.lock = threading.RLock()
        self.records = {}
        self.active = None
        self.thread = None
        self.closing = False
        self.stop_event = threading.Event()
        for path in self.state.glob("*.json"):
            if not TASK_ID.fullmatch(path.stem) or path.is_symlink():
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(record, dict) or record.get("id") != path.stem:
                    continue
                if (record.get("command") not in ("scrape", "retry", "analyze", "export")
                        or record.get("status") not in ("running", "succeeded", "partial", "failed", "stopped", "interrupted")
                        or type(record.get("started")) not in (int, float)):
                    continue
                record = {key: record.get(key) for key in
                          ("id", "command", "db", "interactive", "status", "started", "ended", "returncode", "reason")}
                # Never signal or adopt a PID loaded from disk.
                if record.get("status") == "running":
                    record.update(status="interrupted", ended=time.time())
                    self.save(record)
                self.records[path.stem] = record
            except (OSError, ValueError):
                continue

    def save(self, record):
        target = self.state / (record["id"] + ".json")
        temporary = self.state / (record["id"] + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)

    def command(self, data):
        if not isinstance(data, dict):
            raise PanelError("Expected a JSON object")
        kind = data.get("command")
        fields = {
            "scrape": {"roles", "countries", "lookback", "limit", "max_pages", "browsers", "ai", "interactive"},
            "retry": {"db", "limit", "max_pages", "browsers", "ai", "refresh_searches", "interactive"},
            "analyze": {"db", "limit", "ai"},
            "export": {"db", "format", "min_score"},
        }
        if not isinstance(kind, str) or kind not in fields or set(data) - fields[kind] - {"command"}:
            raise PanelError("Unsupported command or argument")
        args = [sys.executable, "-u", str(PROJECT / "main.py"), kind]
        db = None
        if kind == "scrape":
            args += ["--output-dir", str(self.root)]
            for key, flag in (("roles", "--role"), ("countries", "--country")):
                text = data.get(key, "")
                if not isinstance(text, str) or len(text) > 4000:
                    raise PanelError(f"{key} must contain at most 4000 characters")
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                if len(lines) > 50:
                    raise PanelError(f"{key} must contain at most 50 nonempty lines")
                if any(len(line) > 160 or any(ord(c) < 32 for c in line) for line in lines):
                    raise PanelError(f"Each {key} line must contain at most 160 printable characters")
                for line in lines:
                    args.append(flag + "=" + line)
            args += ["--lookback-hours", str(integer(data, "lookback", 24, 1, 720))]
        else:
            db = safe_file(self.root, data.get("db"), {".db"})
            args += ["--db", str(db)]
            if kind != "export":
                safe_file(self.root, db.stem + ".html", {".html"}, exists=False)
        if kind != "export":
            args += ["--limit", str(integer(data, "limit", 50, 1, 5000)),
                     "--cv-dir", str(self.app.config["CV_DIR"])]
            ai = boolean(data, "ai")
            if kind == "analyze" and not ai:
                raise PanelError("Explicit paid AI consent required")
        if kind in ("scrape", "retry"):
            interactive = boolean(data, "interactive")
            browsers = integer(data, "browsers", 1, 1, 2)
            if interactive and browsers != 1:
                raise PanelError("Interactive mode supports one browser")
            if not interactive:
                args.append("--headless")
            args += ["--browsers", str(browsers),
                      "--max-pages", str(integer(data, "max_pages", 6, 1, 100))]
            if not ai:
                args.append("--no-ai")
            if kind == "retry" and boolean(data, "refresh_searches"):
                args.append("--refresh-searches")
        if kind == "export":
            fmt = data.get("format", "html")
            if fmt not in ("html", "csv", "markdown"):
                raise PanelError("Invalid export format")
            suffix = {"html": ".html", "csv": ".csv", "markdown": ".md"}[fmt]
            output = safe_file(self.root, db.stem + suffix, {suffix}, exists=False)
            args += ["--format", fmt, "--output", str(output)]
            if "min_score" in data:
                args += ["--min-score", str(integer(data, "min_score", 0, 0, 100))]
        return args, db.name if db else None

    def start(self, data):
        with self.lock:
            if self.closing or self.active:
                raise PanelError("Another task is running or the server is stopping", 409)
            args, db = self.command(data)
            before = {p.name for p in self.root.glob("*.db")}
            ident = secrets.token_hex(16)
            interactive = boolean(data, "interactive") if data["command"] in ("scrape", "retry") else False
            record = dict(id=ident, command=data["command"], db=db, interactive=interactive, status="running",
                           started=time.time(), ended=None, returncode=None)
            self.save(record)
            self.records[ident] = record
            self.active = ident
            self.stop_event.clear()
            try:
                self.thread = threading.Thread(target=self.run, args=(record, args, before, interactive), daemon=True)
                self.thread.start()
            except Exception:
                record.update(status="failed", ended=time.time())
                self.active = None
                self.save(record)
                raise PanelError("Could not start task", 503)
            return dict(record)

    @staticmethod
    def signal_process(process, force=False):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL if force else signal.SIGINT)
            elif force:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            else:
                process.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, subprocess.SubprocessError):
            pass

    def run(self, record, args, before, interactive=False):
        process = None
        status, rc, reason = "failed", None, None
        try:
            with (self.state / (record["id"] + ".log")).open("xb") as log:
                environment = {k: v for k, v in os.environ.items()
                               if k not in ("JOBPARSER_WEB_PASSWORD", "JOBPARSER_WEB_USER", "JOBPARSER_VNC_PASSWORD")}
                environment["JOBPARSER_HEADLESS"] = "0" if interactive else "1"
                environment["JOBPARSER_INTERACTIVE"] = "1" if interactive else "0"
                if interactive:
                    profile = self.state / "chrome-profile"
                    profile.mkdir(mode=0o700, exist_ok=True)
                    environment["CHROME_USER_DATA_DIR"] = str(profile)
                else:
                    environment.pop("CHROME_USER_DATA_DIR", None)
                options = {"start_new_session": True} if os.name == "posix" else {
                    "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                process = subprocess.Popen(args, cwd=PROJECT, env=environment, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT, shell=False, **options)
                deadline = time.monotonic() + self.app.config["TASK_TIMEOUT"]
                while process.poll() is None:
                    if self.stop_event.wait(0.2) or time.monotonic() >= deadline:
                        reason = "stop requested" if self.stop_event.is_set() else "timeout"
                        self.signal_process(process)
                        # Retain the process group ID until children have had their grace period,
                        # even if the CLI exits before its browsers do.
                        until = time.monotonic() + self.app.config["STOP_GRACE"]
                        while time.monotonic() < until:
                            time.sleep(min(0.1, max(0, until - time.monotonic())))
                        self.signal_process(process, force=True)
                        break
                rc = process.wait(timeout=5)
                status = "stopped" if reason else {0: "succeeded", 2: "partial"}.get(rc, "failed")
        except Exception:
            reason = "Task execution failed"
        finally:
            if process is not None:
                # Also remove orphaned browsers after a normal CLI exit.
                self.signal_process(process, force=True)
                try:
                    process.wait(timeout=5)
                except (OSError, subprocess.SubprocessError):
                    pass
            with self.lock:
                try:
                    if record["command"] == "scrape":
                        candidates = sorted(p.name for p in self.root.glob("*.db") if p.name not in before)
                        for name in candidates:
                            try:
                                record["db"] = safe_file(self.root, name, {".db"}).name
                                break
                            except PanelError:
                                continue
                    record.update(status=status, returncode=rc, ended=time.time(), reason=reason)
                    self.save(record)
                except OSError:
                    record.update(status="failed", ended=time.time(), reason="Could not persist task completion")
                finally:
                    self.active = None

    def stop(self, ident):
        with self.lock:
            if self.active != ident:
                raise PanelError("Task is not running", 409)
            self.stop_event.set()

    def close(self):
        with self.lock:
            self.closing = True
            self.stop_event.set()
            thread = self.thread
        if thread and thread.is_alive():
            thread.join(self.app.config["STOP_GRACE"] + 16)

    def history(self):
        with self.lock:
            return [dict(r) for r in sorted(self.records.values(), key=lambda r: r.get("started", 0), reverse=True)]


def create_app(test_config=None):
    app = Flask(__name__, template_folder="web", static_folder="web/static", static_url_path="/static")
    app.config.update(
        SECRET_KEY=secrets.token_bytes(32), MAX_CONTENT_LENGTH=16384,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
        RESULTS_DIR=os.environ.get("JOBPARSER_RESULTS_DIR", PROJECT / "results"),
        CV_DIR=os.environ.get("JOBPARSER_CV_DIR", PROJECT / "cv_storage"),
        STATE_DIR=os.environ.get("JOBPARSER_STATE_DIR", PROJECT / "web_state"),
        WEB_USER=os.environ.get("JOBPARSER_WEB_USER", "admin"),
        WEB_PASSWORD=os.environ.get("JOBPARSER_WEB_PASSWORD", ""),
        NOVNC_URL=os.environ.get("JOBPARSER_NOVNC_URL", "http://127.0.0.1:16080/vnc.html?autoconnect=true&resize=scale"),
        TASK_TIMEOUT=os.environ.get("JOBPARSER_TASK_TIMEOUT", "7200"), STOP_GRACE=5,
    )
    if test_config:
        app.config.update(test_config)
    password = app.config["WEB_PASSWORD"]
    if not isinstance(password, str) or len(password) < 16:
        raise ValueError("JOBPARSER_WEB_PASSWORD must contain at least 16 characters")
    if not isinstance(app.config["WEB_USER"], str) or not app.config["WEB_USER"] or ":" in app.config["WEB_USER"]:
        raise ValueError("Invalid JOBPARSER_WEB_USER")
    novnc = urlsplit(app.config["NOVNC_URL"])
    if novnc.scheme not in ("http", "https") or not novnc.netloc:
        raise ValueError("JOBPARSER_NOVNC_URL must be an absolute HTTP(S) URL")
    try:
        app.config["TASK_TIMEOUT"] = int(app.config["TASK_TIMEOUT"])
        if not 1 <= app.config["TASK_TIMEOUT"] <= 86400:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("JOBPARSER_TASK_TIMEOUT must be 1..86400 seconds") from None
    for key in ("RESULTS_DIR", "CV_DIR", "STATE_DIR"):
        app.config[key] = Path(app.config[key]).resolve()
        if key != "CV_DIR":
            app.config[key].mkdir(parents=True, exist_ok=True)
    manager = Tasks(app)
    app.extensions["tasks"] = manager
    user_digest = hashlib.sha256(app.config["WEB_USER"].encode("utf-8")).digest()
    password_digest = hashlib.sha256(password.encode("utf-8")).digest()
    sensitive = [v for k, v in os.environ.items() if len(v) >= 8 and
                 any(word in k.upper() for word in ("PASSWORD", "SECRET", "TOKEN", "API_KEY"))]
    sensitive.append(password)
    app.config.pop("WEB_PASSWORD", None)

    @app.before_request
    def protect():
        if request.path == "/healthz" and request.method in ("GET", "HEAD"):
            return None
        auth = request.authorization
        user = auth.username if auth and auth.type == "basic" else ""
        supplied = auth.password if auth and auth.type == "basic" else ""
        valid_user = hmac.compare_digest(hashlib.sha256((user or "").encode("utf-8")).digest(), user_digest)
        valid_password = hmac.compare_digest(hashlib.sha256((supplied or "").encode("utf-8")).digest(), password_digest)
        if not (valid_user & valid_password):
            return jsonify(error="Authentication required"), 401, {"WWW-Authenticate": 'Basic realm="JobParser", charset="UTF-8"'}
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            referer = request.headers.get("Referer")
            expected = urlsplit(request.host_url)
            source = urlsplit(origin or referer or "")
            if ((source.scheme, source.netloc) != (expected.scheme, expected.netloc)
                    or request.headers.get("Sec-Fetch-Site") not in (None, "same-origin")):
                raise PanelError("Same-origin request required", 403)
            token = request.headers.get("X-CSRF-Token", "")
            if not token or not hmac.compare_digest(token.encode(), session.get("csrf", "").encode()):
                raise PanelError("Invalid CSRF token", 403)

    @app.after_request
    def headers(response):
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    @app.errorhandler(PanelError)
    def panel_error(error):
        return jsonify(error=error.message), error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.name), error.code

    @app.errorhandler(Exception)
    def unexpected_error(error):
        return jsonify(error="Internal operation failed"), 500

    @app.get("/healthz")
    def health():
        return jsonify(status="ok")

    @app.get("/")
    def index():
        session.setdefault("csrf", secrets.token_urlsafe(32))
        return render_template("index.html", csrf=session["csrf"], novnc_url=app.config["NOVNC_URL"])

    @app.get("/api/state")
    def state():
        databases, reports = [], []
        for path in sorted(app.config["RESULTS_DIR"].iterdir(), reverse=True):
            try:
                safe_file(app.config["RESULTS_DIR"], path.name, {".db", ".html", ".csv", ".md"})
            except PanelError:
                continue
            if path.suffix != ".db":
                reports.append(path.name)
                continue
            item = dict(name=path.name, total=None, parsed=None, analyzed=None)
            try:
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
                    db.execute("PRAGMA query_only=ON")
                    deadline = time.monotonic() + 2
                    db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
                    columns = {r[1] for r in db.execute("PRAGMA table_info(jobs)")}
                    item["total"] = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                    if "parse_status" in columns:
                        item["parsed"] = db.execute("SELECT COUNT(*) FROM jobs WHERE parse_status='parsed'").fetchone()[0]
                    if "analysis_status" in columns:
                        item["analyzed"] = db.execute("SELECT COUNT(*) FROM jobs WHERE analysis_status='analyzed'").fetchone()[0]
            except sqlite3.Error:
                item["unavailable"] = True
            databases.append(item)
        cv = app.config["CV_DIR"]
        names = sorted(p.name for p in cv.iterdir() if p.is_file() and not p.is_symlink()) if cv.is_dir() else []
        return jsonify(databases=databases, reports=reports, cvs=names, tasks=manager.history())

    @app.get("/api/databases/<name>/jobs")
    def jobs(name):
        path = safe_file(app.config["RESULTS_DIR"], name, {".db"})
        try:
            page = int(request.args.get("page", "1"))
        except ValueError:
            raise PanelError("page must be an integer", 400) from None
        if not 1 <= page <= 100000:
            raise PanelError("page must be in 1..100000", 400)
        limit = 50
        offset = (page - 1) * limit
        wanted = (
            "id", "title", "link", "country", "grade", "salary", "posted", "source",
            "description", "parse_status", "analysis_status", "match_score",
            "analysis_report", "recommended_cv", "cover_letter", "last_error",
            "first_seen_at", "last_seen_at",
        )
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                deadline = time.monotonic() + 2
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
                available = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
                if not available:
                    raise PanelError("Invalid job database", 400)
                fields = [field for field in wanted if field in available]
                total = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                order = "match_score IS NULL, match_score DESC, id DESC" if {"match_score", "id"} <= available else "rowid DESC"
                rows = [dict(row) for row in db.execute(
                    f"SELECT {','.join(fields)} FROM jobs ORDER BY {order} LIMIT ? OFFSET ?",
                    (limit, offset),
                )]
        except sqlite3.Error:
            raise PanelError("Database is unavailable", 503) from None
        return jsonify(database=path.name, page=page, page_size=limit, total=total, jobs=rows)

    @app.post("/api/tasks")
    def start():
        return jsonify(task=manager.start(request.get_json())), 202

    @app.post("/api/tasks/<ident>/stop")
    def stop(ident):
        manager.stop(ident)
        return jsonify(status="stopping"), 202

    @app.get("/api/tasks/<ident>/log")
    def log(ident):
        if not TASK_ID.fullmatch(ident) or ident not in manager.records:
            raise PanelError("Task not found", 404)
        path = safe_file(manager.state, ident + ".log", {".log"}, exists=False)
        text = ""
        if path.is_file():
            with path.open("rb") as stream:
                stream.seek(0, 2)
                window = 65536 + max(len(value.encode("utf-8")) for value in sensitive)
                stream.seek(max(0, stream.tell() - window))
                text = stream.read(window).decode("utf-8", errors="replace")
        for value in sensitive:
            text = text.replace(value, "[redacted]")
        text = re.sub(r"(?i)((?:api[_ -]?key|token|password|secret)\s*[:=]\s*)\S+", r"\1[redacted]", text)
        return jsonify(text=text[-65536:])

    @app.get("/reports/<name>")
    def report(name):
        path = safe_file(app.config["RESULTS_DIR"], name, {".html", ".csv", ".md"})
        response = send_file(path, as_attachment=True, download_name=path.name, mimetype="application/octet-stream")
        response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; frame-ancestors 'none'"
        return response

    @app.get("/databases/<name>")
    def database(name):
        path = safe_file(app.config["RESULTS_DIR"], name, {".db"})
        temporary = tempfile.TemporaryDirectory(prefix="backup-", dir=manager.state)
        output = Path(temporary.name) / "snapshot.db"
        try:
            deadline = time.monotonic() + 15
            def progress(status, remaining, total):
                if time.monotonic() > deadline:
                    raise PanelError("Database is busy; retry download", 503)
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as source:
                with closing(sqlite3.connect(output)) as target:
                    source.backup(target, pages=256, progress=progress, sleep=0.05)
            stream = output.open("rb")
        except Exception:
            temporary.cleanup()
            raise
        def chunks():
            try:
                while chunk := stream.read(65536):
                    yield chunk
            finally:
                stream.close()
                temporary.cleanup()
        response = Response(chunks(), mimetype="application/octet-stream")
        response.headers["Content-Disposition"] = f'attachment; filename="{name}"'
        def cleanup():
            stream.close()
            temporary.cleanup()
        response.call_on_close(cleanup)
        return response

    return app


def main():
    from waitress import create_server
    app = create_app()
    manager = app.extensions["tasks"]
    host = os.environ.get("JOBPARSER_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("JOBPARSER_WEB_PORT", "8080"))
    if not 1 <= port <= 65535:
        raise ValueError("JOBPARSER_WEB_PORT must be 1..65535")
    server = create_server(app, host=host, port=port, threads=4)
    atexit.register(manager.close)
    def shutdown(signum, frame):
        raise KeyboardInterrupt
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, shutdown)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        # Prevent repeated signals from interrupting child cleanup.
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_IGN)
        manager.close()
        server.close()
        atexit.unregister(manager.close)


if __name__ == "__main__":
    main()
