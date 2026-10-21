"""Sort replies to job applications into Gmail labels and add vacancy digests.

Only the gmail.modify scope is used: messages are labelled (and optionally
moved out of the inbox) or inserted into the owner's own mailbox. Nothing is
ever sent, and nothing is deleted.
"""

import base64
import re
import time
from email.message import EmailMessage
from email.utils import formatdate
from html import escape, unescape
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
LABELS = {
    "offer": "JobScraper/Offers and interviews",
    "rejection": "JobScraper/Rejections",
    "applied": "JobScraper/Applications",
    "digest": "JobScraper/Jobs",
}
# The client library retries 429 and rate-limit 403 responses with backoff.
RETRIES = 8
# New projects can have a low per-minute quota; a message read costs 5 units.
READ_INTERVAL = 0.25
# Job alert newsletters mention "interview" and "apply" without being replies.
IGNORED_SENDERS = ("jobalerts-noreply@linkedin.com", "jobs-listings@linkedin.com")

# Checked in this order: a rejection often also thanks for the application or
# mentions a past interview, so it must win over the other categories.
KEYWORDS = {
    "rejection": (
        "unfortunately", "regret to inform", "not to move forward", "not be moving forward",
        "not moving forward", "decided not to proceed", "decided to proceed with other",
        "other candidates", "another candidate", "not been selected", "not selected",
        "position has been filled", "no longer considering", "unable to offer you",
        "diemžēl", "neesat izvēlēts", "citu kandidātu", "citam kandidātam",
        "к сожалению", "вынуждены отказать", "решили отказать", "выбрали другого кандидата",
    ),
    "offer": (
        "job offer", "offer letter", "pleased to offer", "happy to offer", "we would like to offer",
        "invite you to an interview", "invite you for an interview", "invitation to interview",
        "interview invitation", "schedule an interview", "schedule a call", "book a time",
        "technical interview", "home assignment", "test assignment", "next stage",
        "uzaicinām", "uzaicinājums", "intervij", "darba piedāvājum",
        "приглашаем", "приглашение на", "собеседовани", "оффер", "тестовое задание",
    ),
    "applied": (
        "your application was sent", "application received", "received your application",
        "thank you for applying", "thanks for applying", "thank you for your application",
        "application has been submitted", "pieteikums saņemts", "paldies par pieteikumu",
        "спасибо за отклик", "ваш отклик", "ваша заявка получена",
    ),
}

# Keywords like "unfortunately" are only meaningful in a hiring context.
JOB_CONTEXT = (
    "application", "applied", "applying", "position", "role", "vacancy", "candidate",
    "recruit", "hiring", "interview", "offer",
    "pieteikum", "vakanc", "amat", "kandidāt", "intervij",
    "ваканс", "резюме", "кандидат", "отклик", "должност", "собеседован",
)


def classify(subject, body):
    text = " ".join(f"{subject or ''} {body or ''}".lower().split())
    if not any(word in text for word in JOB_CONTEXT):
        return None
    for category in ("rejection", "offer", "applied"):
        if any(keyword in text for keyword in KEYWORDS[category]):
            return category
    return None


def _decode(data):
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def message_text(payload):
    """Return (subject, sender, plain text) of a Gmail API 'full' payload."""
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    plain, html = [], []

    def walk(part):
        data = part.get("body", {}).get("data")
        mime = part.get("mimeType", "")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            html.append(unescape(re.sub(r"<[^>]+>", " ", _decode(data))))
        for child in part.get("parts", []):
            walk(child)

    walk(payload)
    return headers.get("subject", ""), headers.get("from", ""), "\n".join(plain or html)


def get_service(secrets_dir):
    """Authorize once in the browser; the refresh token is kept in secrets_dir."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError("Install requirements-gmail.txt to use Gmail") from None
    secrets_dir = Path(secrets_dir)
    client_file = secrets_dir / "gmail_credentials.json"
    token_file = secrets_dir / "gmail_token.json"
    credentials = None
    if token_file.exists():
        credentials = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except Exception:
            # Apps in Google's "Testing" mode lose refresh tokens after 7 days.
            credentials = None
    if not credentials or not credentials.valid:
        if not client_file.exists():
            raise RuntimeError(f"OAuth client file not found: {client_file}")
        credentials = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES).run_local_server(port=0)
    token_file.write_text(credentials.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def ensure_labels(service):
    existing = {label["name"]: label["id"]
                for label in service.users().labels().list(userId="me").execute().get("labels", [])}
    ids = {}
    for category, name in LABELS.items():
        if name not in existing:
            existing[name] = service.users().labels().create(
                userId="me", body={"name": name, "labelListVisibility": "labelShow",
                                   "messageListVisibility": "show"}).execute()["id"]
        ids[category] = existing[name]
    return ids


def sort_mail(service, days=30, limit=500, dry_run=False, move=False, log=print):
    """Label recent inbox replies; return a count per category."""
    label_ids = {} if dry_run else ensure_labels(service)
    known = set(label_ids.values())
    query = f"in:inbox newer_than:{days}d -category:promotions -category:social"
    counts = {"offer": 0, "rejection": 0, "applied": 0, "skipped": 0}
    request = service.users().messages().list(userId="me", q=query, maxResults=min(limit, 500))
    seen = 0
    while request is not None and seen < limit:
        response = request.execute(num_retries=RETRIES)
        for item in response.get("messages", [])[:limit - seen]:
            seen += 1
            time.sleep(READ_INTERVAL)
            message = service.users().messages().get(userId="me", id=item["id"], format="full").execute(num_retries=RETRIES)
            if known & set(message.get("labelIds", [])):
                counts["skipped"] += 1
                continue
            payload = message.get("payload", {})
            subject, sender, body = message_text(payload)
            bulk = any(h["name"].lower() == "list-unsubscribe" for h in payload.get("headers", []))
            if any(s in sender.lower() for s in IGNORED_SENDERS):
                category = None
            elif bulk and not any(word in subject.lower() for word in JOB_CONTEXT):
                # Newsletters only count when the subject is about an application.
                category = None
            else:
                category = classify(subject, body)
            if category is None:
                counts["skipped"] += 1
                continue
            counts[category] += 1
            log(f"[{category}] {sender} | {subject}")
            if not dry_run:
                change = {"addLabelIds": [label_ids[category]]}
                # Offers stay in the inbox so they are never missed.
                if move and category != "offer":
                    change["removeLabelIds"] = ["INBOX"]
                service.users().messages().modify(userId="me", id=item["id"], body=change).execute(num_retries=RETRIES)
        request = service.users().messages().list_next(request, response)
    return counts


def application_texts(service, days=180, limit=300):
    """Subject and snippet of each email under the "Applications" label, to recognise jobs already applied to."""
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    label_id = next((label["id"] for label in labels if label["name"] == LABELS["applied"]), None)
    if label_id is None:
        return []
    texts = []
    request = service.users().messages().list(userId="me", labelIds=[label_id], q=f"newer_than:{days}d",
                                              maxResults=min(limit, 500))
    while request is not None and len(texts) < limit:
        response = request.execute(num_retries=RETRIES)
        for item in response.get("messages", [])[:limit - len(texts)]:
            time.sleep(READ_INTERVAL)
            message = service.users().messages().get(userId="me", id=item["id"], format="metadata",
                                                     metadataHeaders=["Subject"]).execute(num_retries=RETRIES)
            subject = next((h["value"] for h in message.get("payload", {}).get("headers", [])
                            if h["name"].lower() == "subject"), "")
            texts.append(f"{subject} {unescape(message.get('snippet', ''))}")
        request = service.users().messages().list_next(request, response)
    return texts


def digest_rows(rows, min_score=None):
    from utils.filters import is_unsuitable_job, is_wanted_language

    rows = [row for row in rows if row.get("parse_status") == "parsed" and not is_unsuitable_job(row.get("title"))
            and is_wanted_language(row.get("description"))]
    if min_score is not None:
        rows = [row for row in rows if (row.get("match_score") or 0) >= min_score]
    return sorted(rows, key=lambda row: (row.get("match_score") or -1, row.get("first_seen_at") or ""), reverse=True)


def build_digest(rows, address, title):
    message = EmailMessage()
    message["From"] = message["To"] = address
    message["Subject"] = f"{title}: {len(rows)}"
    message["Date"] = formatdate(localtime=True)
    lines = [f"{row.get('title')} | {row.get('country')} | {row.get('link')}" for row in rows]
    message.set_content("\n".join(lines) or "No new jobs.")
    items = "".join(
        "<tr><td>{score}</td><td><a href=\"{link}\">{title}</a></td><td>{country}</td><td>{grade}</td><td>{posted}</td></tr>".format(
            score="" if row.get("match_score") is None else row["match_score"],
            **{key: escape(str(row.get(key) or "")) for key in ("link", "title", "country", "grade", "posted")})
        for row in rows)
    message.add_alternative(
        f"<h3>{escape(title)}</h3><table border=\"1\" cellpadding=\"4\" cellspacing=\"0\">"
        f"<tr><th>Score</th><th>Job</th><th>Location</th><th>Grade</th><th>Posted</th></tr>{items}</table>",
        subtype="html")
    return message


def insert_digest(service, rows, title="JobScraper"):
    """Put a vacancy list into the owner's own inbox; nothing is sent."""
    label_id = ensure_labels(service)["digest"]
    address = service.users().getProfile(userId="me").execute()["emailAddress"]
    raw = base64.urlsafe_b64encode(build_digest(rows, address, title).as_bytes()).decode("ascii")
    return service.users().messages().insert(
        userId="me", internalDateSource="dateHeader",
        body={"raw": raw, "labelIds": ["INBOX", "UNREAD", label_id]}).execute()["id"]
