"""Check how far the applier gets on real application forms, without sending anything.

Each form is opened in a throwaway headless Chrome (no cookies, no sign-ins), and the
FormFiller decides in plan mode what every field would get: nothing is typed, uploaded
or submitted. A form is "ready" when it has a CV field, a Submit or Next button and an
answer for every required question; otherwise the report says why not.

    python tools/form_lab.py --cvlv                  employer forms linked from cv.lv IT ads
    python tools/form_lab.py --cvlv --host sage.hr   only forms on matching hosts
    python tools/form_lab.py URL [URL ...]
    python tools/form_lab.py --cvlv --ai             let Gemini answer required questions, as a real run does

Results: results/form_lab/report.html and results.json (one folder per form with a
screenshot and the form's HTML, for adjusting the applier).
"""

import argparse
import json
import re
import sys
import tempfile
import time
from collections import Counter
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from selenium.common.exceptions import WebDriverException  # noqa: E402
from selenium.webdriver.common.by import By  # noqa: E402

from applier import answers, cv as cvlib, external, vocabulary  # noqa: E402
from applier.easy_apply import FormFiller, NeedsManual  # noqa: E402

OUT = ROOT / "results" / "form_lab"
CAPTCHA_SCRIPTS = (("recaptcha", "recaptcha"), ("hcaptcha", "hcaptcha"), ("turnstile", "challenges.cloudflare.com"),
                   ("friendlycaptcha", "friendlycaptcha"))


def cvlv_urls(categories=("INFORMATION_TECHNOLOGY",)):
    """Employer form URLs of the current cv.lv ads (cached for a day in results/form_lab/urls.json)."""
    from scraper import cvlv

    cache = OUT / "urls.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
        return json.loads(cache.read_text(encoding="utf-8"))
    client = cvlv.Client()
    found = []
    for summary in client.search(categories):
        try:
            vacancy = client.vacancy(summary["id"])
        except Exception as error:  # noqa: BLE001 - one unreadable ad does not stop the check
            print(f"skip {summary['id']}: {error}")
            continue
        url = ((vacancy.get("settings") or {}).get("applyingUrl") or "").strip()
        if url:
            found.append({"url": url, "title": vacancy.get("position"), "employer": vacancy.get("employerName"),
                          "vacancy": summary["id"]})
    OUT.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    return found


def sample(items, per_host):
    """Up to per_host forms of each site, taking turns between sites so that no site gets visits back to back.

    Forms of one site share their layout, and fewer visits keep bot protection from blocking the
    applicant's own address. Each kept item carries "weight": how many ads it stands for.
    """
    by_host = {}
    for item in items:
        by_host.setdefault(urlsplit(item["url"]).netloc.lower(), []).append(item)
    rounds = []
    for host_items in by_host.values():
        kept = host_items if per_host <= 0 else host_items[:per_host]
        for index, item in enumerate(kept):
            share = len(host_items) / len(kept)
            while len(rounds) <= index:
                rounds.append([])
            rounds[index].append({**item, "weight": share})
    return [item for round_items in rounds for item in round_items]


def lab_driver(lang):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1440,1000")
    options.add_argument(f"--lang={lang}")
    # Real applications run in the applicant's normal, visible Chrome; the check presents the same browser
    # name, since some career sites turn away anything that calls itself HeadlessChrome.
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
    options.add_argument(f"--user-data-dir={tempfile.mkdtemp(prefix='form_lab_')}")
    options.add_experimental_option("prefs", {"intl.accept_languages": lang})
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(45)
    return driver


def captcha_scripts(driver):
    sources = " ".join((element.get_attribute("src") or "") for element in
                       driver.find_elements(By.CSS_SELECTOR, "script[src], iframe[src]")).lower()
    return [name for name, marker in CAPTCHA_SCRIPTS if marker in sources]


def check(driver, item, answer_fn, files, folder):
    """Open one form and plan its filling; returns the result record."""
    url = item["url"]
    result = {**item, "host": urlsplit(url).netloc.lower(), "ats": external.detect_ats(url)}
    try:
        result["ats"] = external.open_form(driver, url)
    except NeedsManual as problem:
        return {**result, "status": "account", "reason": str(problem)}
    except WebDriverException as error:
        return {**result, "status": "error", "reason": f"{type(error).__name__}: {str(error)[:160]}"}
    result["final_url"] = driver.current_url
    result["captcha_scripts"] = captcha_scripts(driver)
    try:
        page = driver.find_element(By.TAG_NAME, "body").text.lower()
        if external.captcha_challenge(driver):
            result.update(status="captcha", reason="visible CAPTCHA or bot check before the form")
        elif any(word in page for word in vocabulary.EMAIL_CODE):
            result.update(status="account", reason="a code sent by e-mail comes first")
        elif external.account_form(driver):
            result.update(status="account", reason="a password box: sign-in or a new account first")
        else:
            filler = FormFiller(driver, answer_fn, files, plan=True)
            box = external.form_container(driver)
            filler.fill(box)
            action, target = external.form_action(external.form_container(driver), include_disabled=True)
            result.update(fields=filler.log, missing=filler.missing, cv="cv" in filler.uploaded, action=action,
                          button=(target.text or target.get_attribute("value") or "").strip()[:60] if target else "")
            reason = external.no_form_reason(driver) if not filler.log else None
            if reason == "closed":
                result.update(status="closed", reason="the job is closed")
            elif reason == "email":
                result.update(status="email", reason="applications by e-mail only")
            elif not filler.log and action is None:
                result.update(status="no_form", reason="no fields and no button")
            elif action is None:
                result.update(status="no_button")
            elif "cv" not in filler.uploaded and action == "submit":
                result.update(status="no_cv_field")
            elif filler.missing:
                result.update(status="missing_answers")
            else:
                # A first step with Next: the CV field may come on a later step, which the check cannot see.
                result.update(status="ready" if action == "submit" else "ready_step1")
    except NeedsManual as problem:
        result.update(status="error", reason=str(problem))
    except WebDriverException as error:
        result.update(status="error", reason=f"{type(error).__name__}: {str(error)[:160]}")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(folder / "page.png"))
        (folder / "form.html").write_text(external.form_container(driver).get_attribute("outerHTML"),
                                          encoding="utf-8")
    except (WebDriverException, OSError):
        pass
    return result


READY = ("ready", "ready_step1")
BROKEN_BROWSER = re.compile(r"invalid session id|disconnected|chrome not reachable|session deleted", re.I)


def browser_broken(result):
    return result["status"] == "error" and bool(BROKEN_BROWSER.search(result.get("reason") or ""))


def score(results):
    """Share of ads (each checked form weighted by the ads of its site) whose form is ready, in percent.

    Closed jobs have no form to check, so they are left out.
    """
    results = [result for result in results if result["status"] != "closed"]
    total = sum(result.get("weight", 1) for result in results)
    return 100 * sum(result.get("weight", 1) for result in results if result["status"] in READY) / max(1, total)


def write_report(results, path):
    counts = Counter(result["status"] for result in results)
    by_host = {}
    for result in results:
        by_host.setdefault(result.get("ats") if result.get("ats") not in (None, "other") else result["host"],
                           Counter())[result["status"]] += 1
    missing = Counter(question for result in results for question in result.get("missing") or ())
    good = counts["ready"] + counts["ready_step1"]
    weighted = score(results)

    def planned(result):
        return " | ".join(f"{field['question']} = {field['answer']}" for field in result.get("fields") or [])[:700]

    rows = "".join(
        f"<tr class='{escape(r['status'])}'><td>{escape(r['status'])}</td><td>{escape(r.get('ats') or '')}</td>"
        f"<td><a href='{escape(r['url'])}'>{escape(r['host'])}</a><br><small>{escape(r.get('title') or '')}</small></td>"
        f"<td>{escape(r.get('button') or '')}</td><td>{escape('; '.join(r.get('missing') or []) or r.get('reason') or '')}</td>"
        f"<td>{escape(', '.join(r.get('captcha_scripts') or []))}</td><td>{escape(planned(r))}</td></tr>"
        for r in sorted(results, key=lambda r: (r["status"], r["host"])))
    path.write_text(
        "<!doctype html><meta charset='utf-8'><title>Form lab</title><style>body{font-family:Arial;margin:20px}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:4px;font-size:12px;"
        "vertical-align:top}tr.ready td:first-child,tr.ready_step1 td:first-child{background:#d8f5d8}"
        "tr.missing_answers td:first-child{background:#fff2c2}</style>"
        f"<h1>Form lab: {good} of {len(results)} checked forms ready; {weighted:.0f}% of all ads</h1>"
        f"<p>{escape(', '.join(f'{k}: {v}' for k, v in counts.most_common()))}</p>"
        "<h2>By platform</h2><ul>" + "".join(
            f"<li>{escape(name)}: {escape(', '.join(f'{k} {v}' for k, v in c.most_common()))}</li>"
            for name, c in sorted(by_host.items(), key=lambda kv: -sum(kv[1].values()))) + "</ul>"
        "<h2>Unanswered required questions</h2><ul>" + "".join(
            f"<li>{n} × {escape(q)}</li>" for q, n in missing.most_common(60)) + "</ul>"
        "<h2>Forms</h2><table><tr><th>Status</th><th>ATS</th><th>Site</th><th>Button</th><th>Missing / reason</th>"
        "<th>CAPTCHA script</th><th>Planned answers</th></tr>" + rows + "</table>", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("urls", nargs="*")
    parser.add_argument("--cvlv", action="store_true", help="Employer forms linked from cv.lv IT ads")
    parser.add_argument("--host", help="Only URLs whose host contains this text")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--recheck", type=Path,
                        help="An earlier results.json: forms it found behind an account or a CAPTCHA keep that "
                             "result without another visit (fewer visits, less bot protection)")
    parser.add_argument("--keep-ready", action="store_true",
                        help="With --recheck, also keep the forms it found ready: only the rest are visited again")
    parser.add_argument("--per-host", type=int, default=2,
                        help="Forms checked per site (each stands for all of its site's ads in the weighted score); "
                             "0 checks every form")
    parser.add_argument("--lang", default="lv", help="Browser language (lv, en, ru)")
    parser.add_argument("--ai", action="store_true", help="Gemini answers required questions, as in a real run")
    parser.add_argument("--applicant", type=Path, default=ROOT / "config" / "applicant.json")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    items = [{"url": url} for url in args.urls] + (cvlv_urls() if args.cvlv else [])
    if args.host:
        items = [item for item in items if args.host.lower() in urlsplit(item["url"]).netloc.lower()]
    items = sample(items, args.per_host)
    items = items[:args.limit] if args.limit else items
    carried = []
    if args.recheck:
        blocked = {row["url"]: row for row in json.loads(args.recheck.read_text(encoding="utf-8"))
                   if row["status"] in ("account", "captcha", "closed") + (READY if args.keep_ready else ())}
        carried = [{**blocked[item["url"]], "weight": item["weight"]} for item in items if item["url"] in blocked]
        items = [item for item in items if item["url"] not in blocked]
    profile = answers.load_profile(args.applicant)
    base_cv = ROOT / "cv_storage" / "base_cv.json"
    cv_text = cvlib.cv_plain_text(cvlib.load_base_cv(base_cv)) if base_cv.exists() else ""
    llm = None
    if args.ai:
        from applier.llm import LLM

        llm = LLM()
    files = {"cv": "CV.pdf", "cover_letter": "Cover_Letter.pdf",
             "cover_letter_text": "(the job's cover letter, written by the applier)"}

    def answer_fn(question, kind, options, required=True):
        return answers.resolve(question, kind, options, profile, cv_text, "", llm if required else None)

    OUT.mkdir(parents=True, exist_ok=True)
    driver = lab_driver(args.lang)
    results = list(carried)
    try:
        for number, item in enumerate(items, 1):
            slug = re.sub(r"[^a-z0-9]+", "-", f"{urlsplit(item['url']).netloc}-{item.get('vacancy') or number}".lower())
            for attempt in range(2):
                try:
                    result = check(driver, item, answer_fn, files, OUT / slug)
                except WebDriverException as error:
                    result = {**item, "host": urlsplit(item["url"]).netloc, "status": "error", "reason": str(error)[:160]}
                if not browser_broken(result):
                    break
                # The browser itself broke (crash, hung page): start a new one and try this form once more,
                # otherwise every later form fails with "invalid session id".
                try:
                    driver.quit()
                except WebDriverException:
                    pass
                driver = lab_driver(args.lang)
            results.append(result)
            print(f"[{number}/{len(items)}] {result['status']:<15} {result['host']}  "
                  f"{'; '.join(result.get('missing') or []) or result.get('reason') or ''}"[:200], flush=True)
    finally:
        try:
            driver.quit()
        except WebDriverException:
            pass
        if llm is not None:
            llm.close()
        (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        write_report(results, OUT / "report.html")
    good = sum(1 for r in results if r["status"] in READY)
    print(f"Ready: {good} of {len(results)} checked forms, {score(results):.0f}% of all ads. "
          f"Report: {OUT / 'report.html'}")


if __name__ == "__main__":
    main()
