# JobScraper for LinkedIn

A Windows desktop tool for a job search on LinkedIn. It collects fresh vacancies for the roles you
choose, scores each one against your CV with Google Gemini, tailors your CV and a cover letter to the
best matches without inventing anything, and fills in LinkedIn Easy Apply forms and common employer
application forms (Lever, Greenhouse, Teamtailor and others). Optionally it sorts replies from
employers in Gmail into labels.

Everything runs on your own computer. Your CV, profile, results and LinkedIn session stay in local
folders that are excluded from git.

> **Read this before you use it**
>
> - **LinkedIn's User Agreement prohibits automated access and automated actions.** Using this tool
>   can get your LinkedIn account restricted or banned. The tool paces itself (one browser, 1.5 to 4
>   minute pauses between applications, a daily limit), but that does not make it allowed. You use it
>   at your own risk.
> - **Start with dry runs.** `auto_submit` is off by default: the tool fills in the forms and stops
>   before Submit until you turn it on. Read the tailored CVs and letters in `results/` first.
> - **Your data goes to Google.** CV text and job descriptions are sent to the Gemini API. On the free
>   tier Google may use that data to improve its products. Use a paid key if that matters to you.
> - The tool never answers a question it has no truthful answer for, never solves CAPTCHAs, never
>   creates accounts on employer sites, and only ever declines cookie banners. Those applications are
>   left for you in the app's "Needs you" list.

The idea comes from [JobParser With Descriptions](https://github.com/Oldoge/JobParser-with-descriptions)
by [Oldoge](https://github.com/Oldoge), which this project grew out of (job collection, resumable
runs, Gemini scoring, reports and the home lab web panel). This version adds the desktop app, CV and
cover letter tailoring, Easy Apply and employer-site applications, and Gmail sorting.

## Contents

- [What you need](#what-you-need)
- [Setup on Windows](#setup-on-windows)
- [Gemini API key: free, paid or another model](#gemini-api-key-free-paid-or-another-model)
- [Preparing your CV and profile](#preparing-your-cv-and-profile)
- [Where your files go](#where-your-files-go)
- [Choosing roles and locations](#choosing-roles-and-locations)
- [Daily use](#daily-use)
- [Gmail sorting (optional)](#gmail-sorting-optional)
- [Keeping your data out of git](#keeping-your-data-out-of-git)
- [Command reference](#command-reference)

## What you need

- Windows 10 or 11
- [Python 3.11 or newer](https://www.python.org/downloads/windows/) (tick "Add python.exe to PATH" in
  the installer)
- Google Chrome
- [Git](https://git-scm.com/download/win) (or download the repository as a ZIP)
- A LinkedIn account and a Google account for the Gemini API key

## Setup on Windows

Open PowerShell and run:

```powershell
git clone https://github.com/EEw3n3/JobScraper-for-LinkedIn.git
cd JobScraper-for-LinkedIn
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-app.txt
```

`requirements-app.txt` installs everything: the scraper, Gemini, the desktop window and PyInstaller.
Add `-r requirements-gmail.txt` if you want Gmail sorting.

Then create your own copies of the templates:

```powershell
Copy-Item .env.example .env
Copy-Item config\applicant.example.json config\applicant.json
Copy-Item config\cover_letter_example.example.md config\cover_letter_example.md
Copy-Item cv_storage\base_cv.example.json cv_storage\base_cv.json
```

Fill them in as described below, then check the setup:

```powershell
.\.venv\Scripts\python.exe main.py doctor
```

`doctor` reports which files and keys are found without printing any secret.

Sign in to LinkedIn once in the tool's own Chrome profile (a separate profile in `browser_profile/`,
not your everyday Chrome). You type the password yourself; the tool never stores it.

```powershell
.\.venv\Scripts\python.exe main.py login
```

Build the desktop app (optional; you can use the command line instead):

```powershell
.\build_app.ps1
```

This creates `JobScraper.exe` in the project folder. Keep it there: it runs the code from `.venv`,
so code changes do not need a rebuild, only changes in `app\` do.

## Gemini API key: free, paid or another model

**Free key.** Open [Google AI Studio](https://aistudio.google.com/apikey), sign in, click
"Create API key" and put it in `.env`:

```
GEMINI_API_KEY=your-key-here
```

The default model is `gemini-3.5-flash-lite`. The free tier allows about 15 requests per minute per
model, so the tool starts requests at least 4 seconds apart (`GEMINI_MIN_INTERVAL=4.0`). A run that
scores 150 jobs therefore takes about 10 minutes of AI time. Free-tier limits change; see
[Gemini API rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).

**Paid key.** Turn on billing for the Google Cloud project that owns the key (AI Studio shows a
"Set up billing" link next to the key). The key itself stays the same. Then you can raise the pace
and pick a stronger model in `.env`:

```
GEMINI_MODEL=gemini-3.5-flash
GEMINI_MIN_INTERVAL=0.5
```

You can also pass `--model` to `scrape`, `analyze` and `apply` for a single run. Changing the model
re-scores every job once, because the model is part of the analysis cache key.

**Another provider.** All AI calls go through two places: `applier/llm.py` (CV tailoring, cover
letters, form answers) and `utils/analyzer.py` (job scoring). Both use the `google-genai` client and
ask for JSON that matches a schema. To use OpenAI, Anthropic or a local model, replace the client
call in these two files with one that returns the same JSON; the checks on the output stay the same.

## Preparing your CV and profile

The tool uses your CV in two forms.

**1. PDF CVs for scoring: `cv_storage\*.pdf`.** Put one or more text-based PDF CVs here (not scans).
Each job is scored against all of them and the best one is noted in the report.

**2. Structured CV for tailoring: `cv_storage\base_cv.json`.** This is a
[Reactive Resume](https://rxresu.me) JSON export. The easiest way is to build your CV in Reactive
Resume (free) and use "Export > JSON". You can also edit `base_cv.example.json` by hand: it is a
fictional CV that shows every field the tool reads (name, contacts, summary, profiles, experience,
education, projects, skills, languages, certifications). Bullets are HTML `<li>` items.

How tailoring works, so you can write the base CV for it:

- The model may only reword the summary from sentences already in your CV and reorder skills and
  bullets. Any new technology, number, employer or buzzword makes the tool keep your original text.
- The last sentence of your summary is always kept word for word.
- The first bullet of every job always stays first: put your main point there.
- The PDF is shrunk until it fits on one A4 page.
- Optional: `cv_storage\base_cv_lv.json`, a Latvian version of the same CV, is used for jobs located
  in Latvia (and the cover letter is then written in Latvian too).

**Your answers for application forms: `config\applicant.json`.** Copy it from
`applicant.example.json` and fill in every `null`. The forms are answered from these fields first;
the model only answers required questions that your CV can answer truthfully.

| Field | What it is for |
|---|---|
| `first_name`, `last_name`, `email`, `phone`, `phone_country`, `city`, `linkedin`, `github`, `website` | Contact fields. `phone_country` is the option text LinkedIn shows, for example `"Latvia (+371)"`; its code is added to the phone on the CV |
| `years_experience_total`, `skills_years` | "How many years of experience with X" questions. A skill missing here is never guessed |
| `eu_work_authorization`, `needs_visa_sponsorship`, `willing_to_relocate`, `remote_ok`, `hybrid_ok`, `onsite_ok` | Yes/no questions |
| `notice_period`, `earliest_start`, `salary_eur_month_gross`, `salary_eur_year_gross` | Availability and salary questions |
| `languages`, `highest_education`, `has_bachelor_degree`, `driving_licence` | Language, education and licence questions |
| `eeo_answer` | Diversity questions (default "Prefer not to say") |
| `accept_application_consents` | Whether privacy or terms checkboxes on forms may be ticked. Never decided by the model |
| `extra_facts`, `custom_answers` | Extra true facts, and fixed answers for exact question texts |
| `apply_external` | Also apply on employer sites, not only with Easy Apply |
| `auto_submit` | `false`: fill in and stop before Submit. `true`: submit |
| `daily_limit`, `min_score` | At most this many applications per 24 hours, only to jobs scored at least `min_score` |

**Your cover letter example: `config\cover_letter_example.md`.** One real letter you wrote yourself,
in English. Each new letter follows its structure and voice, uses only facts from your CV and
profile, and may mention a technology you lack only in an honest "not used yet, motivated to learn"
sentence. Language levels are left out of letters (the CV carries them).

## Where your files go

| Path | Contents | In git? |
|---|---|---|
| `.env` | Gemini key and model settings | No |
| `config\applicant.json` | Your form answers | No |
| `config\cover_letter_example.md` | Your example letter | No |
| `cv_storage\*.pdf`, `cv_storage\base_cv.json`, `cv_storage\base_cv_lv.json` | Your CVs | No |
| `browser_profile\` | The LinkedIn session of the tool's Chrome | No |
| `secrets\` | Gmail OAuth client and token | No |
| `results\` | Job databases, reports, tailored CVs and letters, screenshots, logs | No |
| `config\settings.py` | Roles, locations and filters to search | Yes (no personal data) |
| `*.example.*` files | Templates | Yes |

## Choosing roles and locations

Edit `config\settings.py`:

- `SEARCH_ROLES`: LinkedIn search phrases, URL-encoded (`%20` for a space), for example
  `"Python%20Developer"`.
- `SEARCH_LOCATIONS`: LinkedIn locations in order of priority, for example
  `["Latvia", "Estonia", "Lithuania"]`. Jobs from the first location are applied to first, then the
  next one. A remote search across Europe is always added and only fills the rest of the daily limit.
- `LOCAL_LOOKBACK_HOURS`: how far back the location searches look (72 hours by default, since local
  markets post few jobs a day). The remote search uses `--lookback-hours` (24 by default).
- `STOP_SENIOR_WORDS`: titles with these words are skipped (senior, lead, manager and so on).

You can also pass `--role` and `--country` to `scrape` for a single run.

## Daily use

**Desktop app.** Open `JobScraper.exe`, keep "Dry run" ticked for the first runs and click Start.
A full run collects jobs, reads descriptions, scores them, sorts Gmail (if connected) and applies.
"Send applications" skips collection and applies to the newest collected jobs. "Check mail" only sorts
Gmail replies (offers, rejections, confirmations) and sends nothing. Jobs the tool could not finish appear under "Needs you" with
the reason, an AI summary of the job and what to do; mark them "I applied myself" when done. The
"Auto-submit" switch writes `auto_submit` to `config\applicant.json`.

**Command line.** The same steps one by one:

```powershell
.\.venv\Scripts\python.exe main.py scrape --headless --browsers 1
.\.venv\Scripts\python.exe main.py apply --dry-run --limit 3
.\.venv\Scripts\python.exe main.py apply
.\.venv\Scripts\python.exe main.py mark-applied <job id>
```

`run_daily.ps1` runs scrape, mail and apply in a row and can be scheduled with Windows Task
Scheduler. Results are in `results\applications.html`; `results\applications.db` remembers every job
so none is applied to twice.

## Gmail sorting (optional)

`main.py mail` labels recent replies under `JobScraper/Offers and interviews`,
`JobScraper/Rejections` and `JobScraper/Applications`, and can add each run's job list as a message
under `JobScraper/Jobs`. It uses only the `gmail.modify` scope: nothing is sent or deleted.

1. In [Google Cloud Console](https://console.cloud.google.com/) create a project, enable the Gmail
   API, configure the OAuth consent screen (External, add yourself as a test user) and create an
   OAuth client of type "Desktop app".
2. Download its JSON to `secrets\gmail_credentials.json`.
3. Run `.\.venv\Scripts\python.exe main.py mail --dry-run` and allow access in the browser. The token
   is saved next to the credentials.

## Keeping your data out of git

`.gitignore` already excludes `.env`, `secrets\`, `browser_profile\`, `results\`, your CVs, profile
and letter. Before you push a fork, check that nothing personal is staged:

```powershell
git status --ignored
git ls-files
```

Only the `*.example.*` templates should appear among config and CV files. If a key ever lands in a
commit, revoke it in AI Studio first; removing the file later does not remove it from git history.

## Command reference

The sections below describe each command in detail.

### New Run

```powershell
python main.py scrape --no-ai --limit 50
python main.py scrape --role "DevOps Engineer" --country Latvia --lookback-hours 24 --browsers 1 --headless --no-ai
```

Every `scrape` invocation creates a **new, independent database**, for example
`results/jobs_20260907_142530_123456Z.db`. The timestamp is UTC with microseconds.
Existing databases are never reused by a new scrape. `python main.py` is shorthand
for `scrape`, with AI enabled. Use `--no-ai` to avoid all CV reads and AI requests.

The database path is printed before Chrome starts. An HTML report with the same
basename is generated after normal or handled partial completion. Defaults for
results and CVs are relative to the project root, not the shell's directory.
Explicit relative paths are relative to the shell's directory.

- Repeat `--role` and `--country` to configure multiple searches.
- Without overrides, configured roles and countries plus Europe Remote are used.
- `--lookback-hours` defaults to 24; it is the source's rolling search window.
- `--max-pages` defaults to six scroll rounds per search; zero collects the initial viewport.
- `--limit` independently caps new discoveries, parse jobs, and AI jobs per invocation. It is not a browser-request limit.
- A search cut short by the job limit remains pending. Known links do not consume the discovery limit when resuming.
- The page budget defines the extent of a completed search, not proof that the source has no more results.

### Resume

```powershell
python main.py retry --db "results/jobs_20260907_142530_123456Z.db" --no-ai
python main.py retry --db "results/jobs_20260907_142530_123456Z.db" --refresh-searches --max-pages 12 --no-ai
```

`retry` uses the selected existing database; it does not create another run.
Search configurations, discovered jobs, parse outcomes, attempt counts, and
errors are persisted. Each discovered job and successful parse is committed
immediately. Retry resumes unfinished searches and non-parsed jobs, prioritizing
less-attempted work. `--refresh-searches` also reopens completed searches, for
example when increasing the page budget. It does not reparse successful jobs.

Two browsers share throttling and cancellation. Persistent HTTP 429 stops new
work; in-flight browser calls may still need to finish or time out. One failed
browser session can be replaced once per worker. Ctrl+C preserves committed
progress. Resume requires another explicit command; blocks are not bypassed.

Descriptions shorter than 100 normalized characters, extraction placeholders,
and recognized authentication walls are rejected. These heuristics are not a
guarantee of content correctness. Auth-blocked or expired pages may remain pending.

### Analyze

Place text-readable PDF CVs in `cv_storage`, or pass a different `--cv-dir`.

```powershell
python main.py analyze --db "results/jobs_20260907_142530_123456Z.db" --limit 20
python main.py analyze --db "results/jobs_20260907_142530_123456Z.db" --cv-dir "C:\CVs" --force-reanalyze
python run_analysis.py --db "results/jobs_20260907_142530_123456Z.db"
```

Only valid parsed descriptions are analyzed. Responses use a JSON schema and
local validation, including scores from 0 to 100 and an actual supplied CV name.
Scores at or below 35 do not retain a generated cover letter. Empty or unreadable
PDFs are excluded; image-only PDFs need OCR outside this tool.

Within one database, unchanged vacancy/CV/model/prompt inputs reuse the saved
analysis. `--force-reanalyze` bypasses this cache. **Separate timestamp databases
do not share an AI cache**, so a new scrape can incur fresh analysis charges for
the same vacancy. Use `--no-ai`, followed by selective analysis, to control usage.

Missing AI dependencies or credentials do not prevent collection. The run keeps
its data and reports the AI configuration error. HTTP 429, server errors, and
transport failures have bounded retries; invalid responses remain failed jobs.

AI mode sends the extracted CV text and job description to Google. Check the
provider's current privacy and retention terms, remove unnecessary personal data,
and manually review all evaluations and letters. No applications are sent.

### Export

```powershell
python main.py export --db "results/jobs_20260907_142530_123456Z.db"
python main.py export --db "results/jobs_20260907_142530_123456Z.db" --format csv
python main.py export --db "results/jobs_20260907_142530_123456Z.db" --format markdown --min-score 60
```

Exports overwrite the selected report rather than append duplicates. Use
`--output PATH` for a custom destination. Reports are sorted by descending score
and first-seen time, include descriptions and failures, and contain job links,
CV choices, analysis, and letters. `--min-score` includes only completed analyses.
HTML works offline and uses a mobile-readable layout. Database paths cannot be
used as report destinations. Export does not import browser or AI packages.

Existing databases with the old `jobs` schema are migrated on opening; valid old
analyses remain visible, but cannot reuse a cache key whose inputs were not stored.
Back up important old databases before using the updated application.

### Gmail

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-gmail.txt
python main.py mail --dry-run
python main.py mail --move --digest-db "results/jobs_20260907_142530_123456Z.db"
```

`mail` labels recent inbox replies as offers/interview invitations, rejections or
application confirmations (English, Latvian and Russian keywords) under
`JobScraper/...` labels, and `--digest-db` inserts the run's parsed jobs into the
owner's own inbox. It uses only the `gmail.modify` scope: nothing is sent or deleted.
`--move` also removes rejections and confirmations from the inbox; offers always stay.
Put a Desktop OAuth client at `secrets/gmail_credentials.json`; the token is stored
next to it. `run_daily.ps1` runs a scrape followed by `mail` for Task Scheduler.

### Easy Apply

```powershell
python main.py login
python main.py apply --dry-run --limit 3
python main.py apply
```

`login` opens Chrome with its own `browser_profile/` so you sign in to LinkedIn once; the
password is typed by you and never stored by the program. `apply` takes scored jobs at or above
`min_score` (no senior titles, English/Latvian/Russian ads only), tailors the CV from
`cv_storage/base_cv.json` (Reactive Resume JSON) to each job, renders it to PDF and fills the
Easy Apply form from `config/applicant.json` (copy `config/applicant.example.json`), falling back
to Gemini for questions the CV answers. Tailoring may only reorder skills and bullets and reword the
headline and summary from sentences already in the CV; any unsupported term, years-of-experience
claim or buzzword keeps the original text. A question without a truthful answer stops that
application as `needs_manual` rather than guessing. Privacy and terms consents are answered only
from `accept_application_consents`, never by the model.

`--dry-run` stops before Submit and discards the draft. Without it, `apply` submits only when
`--submit` is given or `auto_submit` is true in the profile, at most `daily_limit` per 24 hours with
1.5–4 minute pauses. Results, answers and review screenshots go to `results/applications.html`;
`results/applications.db` remembers every job so none is applied to twice. LinkedIn's User
Agreement prohibits automated activity, so use this at your own risk to your account.

Jobs whose Apply button leads to the employer's site are applied to there as well (set
`apply_external` to false to only list them). The employer form is detected by URL (Lever,
Greenhouse, Ashby, Workable, Teamtailor, SmartRecruiters, Recruitee, Personio, BambooHR, others
generically) and filled with the same rules. Optional fields only get the applicant's own data; the
model answers required questions only, and its free text must pass the same unsupported-term and
buzzword checks as the CV summary. Cookie banners are only ever declined. A form without a CV upload
is never submitted. Workday and other account-based sites, CAPTCHA challenges, and questions without a
truthful answer stop as `needs_manual` for the applicant to finish. `apply --link <job id>` handles
one job.

Set `JOBPARSER_BROWSER_TESTS=1` to also run the Easy Apply flows (2024 and 2026 LinkedIn dialogs) and the
Lever and Greenhouse imitations in `tests/fixtures` in headless Chrome.

### Diagnostics And Tests

```powershell
python main.py doctor
python main.py --help
python -B -m unittest discover -s tests -v
```

`doctor` checks imports and whether an API key is configured without exposing it.
It does not launch Chrome, read CV contents, or test model access. Tests use mocks
and temporary synthetic databases; no browser, real CV, or paid API is needed.

Exit codes: `0` completed, `1` operational/AI failure, `2` partial collection or
invalid CLI arguments, `130` interrupted. A completed collection means processing
within configured search/page limits, not guaranteed coverage of LinkedIn.
Observe the source's access rules; HTML changes and account restrictions can
require parser updates.

### Proxmox Home Lab

For an Ubuntu Server VM without a desktop, the project includes `Dockerfile`,
`compose.yaml`, and an authenticated web panel. The panel starts headless searches,
resumes runs, performs explicitly approved AI analysis, shows jobs and logs, and
downloads reports or consistent SQLite snapshots. Only one browser task runs at a
time. Follow `docs/HOMELAB.md`; by default the service is reachable only through an
SSH tunnel to the VM's loopback interface. An optional noVNC mode lets you watch
and click the real Chromium window, including completing sign-in screens manually.
Do not expose BasicAuth or noVNC over plain HTTP to the LAN or Internet.
