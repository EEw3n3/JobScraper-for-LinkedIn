# JobParser Web Panel

Run one web process per results/state directory. The panel serializes its own CLI
tasks; do not run a second panel or external CLI writer against the same databases
while it is working. Deployment files are deliberately outside this component.

## Start

Install `requirements-web.txt` in the application environment, set the password
through the deployment's environment/secret mechanism, then run `python webapp.py`.
There is no default password and no `.env` loading in the web server. Importing
`webapp` does not create directories, start workers, or validate credentials.
`create_app(test_config=None)` constructs the Flask application.

| Environment variable | Default / constraints |
| --- | --- |
| `JOBPARSER_WEB_USER` | `admin`; nonempty, no colon |
| `JOBPARSER_WEB_PASSWORD` | Required; at least 16 characters |
| `JOBPARSER_WEB_HOST` | `127.0.0.1` |
| `JOBPARSER_WEB_PORT` | `8080`; integer 1..65535 |
| `JOBPARSER_RESULTS_DIR` | Project directory + `/results` |
| `JOBPARSER_CV_DIR` | Project directory + `/cv_storage`; read-only from the panel |
| `JOBPARSER_STATE_DIR` | Project directory + `/web_state` |
| `JOBPARSER_TASK_TIMEOUT` | `7200` seconds; integer 1..86400 |
| `JOBPARSER_NOVNC_URL` | Link shown for the separately deployed noVNC display |

For container networking the deployer can set `JOBPARSER_WEB_HOST=0.0.0.0`.
HTTP Basic is **not encryption**: use HTTPS at a trusted reverse proxy or an SSH
tunnel, not plain HTTP on an untrusted LAN or the internet. The proxy must preserve
the original Host and request scheme for same-origin validation. No proxy headers
are trusted by this application. For TLS termination, the deployment must provide
trusted WSGI scheme handling (or use end-to-end HTTPS outside this component);
do not blindly trust client-supplied forwarding headers. A loopback SSH tunnel
needs no proxy integration. All assets are local; there is no CORS support.

## Routes

| Method | Route | Behavior |
| --- | --- | --- |
| GET / HEAD | `/healthz` | Public, only `{"status":"ok"}` |
| GET | `/` | Russian-language dashboard; session CSRF token in a meta element |
| GET | `/static/panel.js`, `/static/panel.css` | Authenticated local assets |
| GET | `/api/state` | Database counts, report/CV filenames, task history |
| GET | `/api/databases/<basename>.db/jobs?page=N` | Read-only job list, 50 rows per page |
| POST | `/api/tasks` | Start an allowlisted CLI command; returns 202 and `task` |
| POST | `/api/tasks/<id>/stop` | Request graceful stop; returns 202 |
| GET | `/api/tasks/<id>/log` | JSON `text`, last at most 65,536 characters |
| GET | `/databases/<basename>.db` | Consistent SQLite backup, attachment |
| GET | `/reports/<basename>` | HTML/CSV/Markdown attachment, sandbox CSP |

Every route except GET/HEAD health requires HTTP Basic authentication. Every POST
also requires a same-origin `Origin` (or `Referer`), the session cookie, and
`X-CSRF-Token` from the dashboard. Errors are JSON `{"error":"message"}` with an
appropriate HTTP status; no tracebacks are returned. Unknown JSON keys are rejected.

Task bodies always include `command`:

| Command | Accepted additional JSON fields |
| --- | --- |
| `scrape` | `roles`, `countries` (newline strings); `lookback`, `limit`, `max_pages`, `browsers`, `ai`, `interactive` |
| `retry` | `db`, `limit`, `max_pages`, `browsers`, `ai`, `refresh_searches`, `interactive` |
| `analyze` | `db`, `limit`, `ai` (must be `true`) |
| `export` | `db`, `format` (`html`, `csv`, `markdown`), optional `min_score` |

Numbers are JSON integers, not strings or booleans. Limit: 1..5000 (default 50);
lookback: 1..720 hours (24); pages: 1..100 (6); browsers: 1..2 (1); score: 0..100.
Role/country lists allow up to 50 nonempty lines, 160 characters per line, 4000
characters total. Blank lists use CLI defaults. `ai`, `interactive`, and
`refresh_searches` are booleans defaulting to false. Scrape/retry use headless mode
unless interactive mode is explicitly selected. CV and output paths are
server-controlled; model/force/custom arguments are not exposed.

Container deployments may set `interactive=true` to omit the headless CLI flag,
force one browser, and use the virtual display exposed separately by noVNC. The
child receives a persistent Chrome profile under the private state directory and
waits on recognized login/verification pages for manual action. The VNC endpoint
has its own password and must remain behind an SSH tunnel or trusted TLS/VPN.

## Lifecycle And Storage

Waitress runs one process with four HTTP threads. CLI tasks run using the current
Python interpreter with unbuffered output and no shell. Logs and atomic task JSON
are persisted under the state directory. Keep state/results private to the service
account; logs may contain job descriptions, error messages, and other private data.
The log API redacts known environment secrets and common credential assignments;
this is not a general-purpose secret scanner, and raw server log files are private.
Web login credentials are removed from the CLI environment.

Exit 0 is `succeeded`, 2 is `partial`, other exits are `failed`. Stops/timeouts are
`stopped`; previously running metadata becomes `interrupted` on restart. PIDs from
disk are never adopted or signaled. New scrape databases are detected by a safe
before/after filename snapshot. The database link appears after task completion.

On Linux, stop/timeout/shutdown sends SIGINT to the task process group, allows five
seconds for cleanup, then SIGKILLs the group including remaining browser children.
The admission slot remains occupied through cleanup. Normal completion also removes
remaining group members. Allow at least 25 seconds of container shutdown grace.
Windows uses a new process group and CTRL_BREAK/taskkill as a best-effort fallback;
the target runtime is Linux. SIGKILL of the web server itself cannot run cleanup:
the container/service supervisor must remove the entire cgroup on forced shutdown.

Reports are never rendered in the panel. Downloaded HTML/CSV should still be treated
as untrusted content when opened locally. Database viewing never invokes migrations;
legacy/missing status columns show unknown counts. Backup downloads include committed
WAL data and are bounded to 15 seconds of backup work. CV contents, uploads, deletion,
and arbitrary filesystem access are not provided.

## Offline Tests

`python -B -m unittest discover -s tests -p test_webapp.py -v`

Tests use temporary directories and mocked subprocesses, not Selenium or AI. Linux
process-group integration and browser behavior require deployment smoke tests.
