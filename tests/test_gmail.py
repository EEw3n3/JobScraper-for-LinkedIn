import base64
import email
import unittest
import unittest.mock
from unittest.mock import MagicMock

from utils import gmail


def encode(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def payload(subject, body, sender="hr@company.test", mime="text/plain"):
    return {"headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": sender}],
            "mimeType": "multipart/alternative",
            "parts": [{"mimeType": mime, "body": {"data": encode(body)}}]}


class FakeService:
    """Minimal stand-in for the Gmail API client used by sort_mail."""

    def __init__(self, messages, labels=()):
        self.messages_by_id = messages
        self.labels_list = [{"name": name, "id": f"L{index}"} for index, name in enumerate(labels)]
        self.modified = {}
        self.created = []
        self.inserted = []

    def users(self):
        return self

    def labels(self):
        api = MagicMock()
        api.list.return_value.execute.return_value = {"labels": self.labels_list}

        def create(userId, body):
            label = {"name": body["name"], "id": f"N{len(self.created)}"}
            self.created.append(label)
            self.labels_list.append(label)
            result = MagicMock()
            result.execute.return_value = label
            return result
        api.create.side_effect = create
        return api

    def messages(self):
        api = MagicMock()
        api.list.return_value.execute.return_value = {"messages": [{"id": key} for key in self.messages_by_id]}
        api.list_next.return_value = None
        api.get.side_effect = lambda userId, id, format: MagicMock(
            execute=MagicMock(return_value=self.messages_by_id[id]))

        def modify(userId, id, body):
            self.modified[id] = body
            return MagicMock()
        api.modify.side_effect = modify

        def insert(userId, internalDateSource, body):
            self.inserted.append(body)
            return MagicMock(execute=MagicMock(return_value={"id": "new"}))
        api.insert.side_effect = insert
        return api

    def getProfile(self, userId):
        return MagicMock(execute=MagicMock(return_value={"emailAddress": "me@example.test"}))


class ClassifyTests(unittest.TestCase):
    def test_rejection_wins_over_interview_and_thanks(self):
        body = "Thank you for applying and for the interview. Unfortunately we chose another candidate."
        self.assertEqual(gmail.classify("Your application", body), "rejection")

    def test_offer_and_interview_invites(self):
        self.assertEqual(gmail.classify("Interview invitation", "We invite you to an interview for the role."), "offer")
        self.assertEqual(gmail.classify("Ielūgums", "Uzaicinām jūs uz intervija par amatu."), "offer")
        self.assertEqual(gmail.classify("Приглашение", "Приглашаем на собеседование на должность."), "offer")

    def test_application_confirmation(self):
        self.assertEqual(gmail.classify("Anna, your application was sent to Acme", "Python Developer position"), "applied")

    def test_latvian_and_russian_rejections(self):
        self.assertEqual(gmail.classify("Pieteikums", "Diemžēl esam izvēlējušies citu kandidātu."), "rejection")
        self.assertEqual(gmail.classify("Ваш отклик", "К сожалению, мы выбрали другого кандидата."), "rejection")

    def test_non_hiring_mail_is_ignored(self):
        self.assertIsNone(gmail.classify("Payment failed", "Unfortunately your card was declined."))
        self.assertIsNone(gmail.classify("Newsletter", "Нажмите здесь, чтобы отказаться от рассылки."))

    def test_html_body_is_read(self):
        subject, sender, text = gmail.message_text(payload("Hi", "<p>We&nbsp;regret to inform you</p>", mime="text/html"))
        self.assertEqual(subject, "Hi")
        self.assertIn("regret to inform you", text.replace("\xa0", " "))


class SortMailTests(unittest.TestCase):
    def setUp(self):
        patcher = unittest.mock.patch.object(gmail, "READ_INTERVAL", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_service(self, labels=()):
        return FakeService({
            "a": {"labelIds": ["INBOX"], "payload": payload("Application update", "Unfortunately the position has been filled.")},
            "b": {"labelIds": ["INBOX"], "payload": payload("Next steps", "We would like to schedule an interview.")},
            "c": {"labelIds": ["INBOX"], "payload": payload("Sale", "Unfortunately the sale ends today.")},
            "d": {"labelIds": ["INBOX"], "payload": payload("New jobs", "Interview-ready Python roles",
                                                           sender="LinkedIn <jobalerts-noreply@linkedin.com>")},
            "e": {"labelIds": ["INBOX"], "payload": {**payload("Kino jaunumi", "Intervija ar aktieri un īpašs piedāvājums"),
                                                     "headers": [{"name": "Subject", "value": "Kino jaunumi"},
                                                                 {"name": "List-Unsubscribe", "value": "<mailto:x>"}]}},
            "f": {"labelIds": ["INBOX"], "payload": {**payload("Your application to Acme", "Unfortunately we chose another candidate."),
                                                     "headers": [{"name": "Subject", "value": "Your application to Acme"},
                                                                 {"name": "From", "value": "jobs-noreply@linkedin.com"},
                                                                 {"name": "List-Unsubscribe", "value": "<mailto:x>"}]}},
        }, labels)

    def test_dry_run_changes_nothing(self):
        service = self.make_service()
        counts = gmail.sort_mail(service, dry_run=True, log=lambda line: None)
        self.assertEqual(counts, {"offer": 1, "rejection": 2, "applied": 0, "skipped": 3})
        self.assertEqual(service.modified, {})
        self.assertEqual(service.created, [])

    def test_labels_are_created_and_offers_stay_in_inbox(self):
        service = self.make_service()
        gmail.sort_mail(service, move=True, log=lambda line: None)
        self.assertEqual(len(service.created), len(gmail.LABELS))
        self.assertEqual(service.modified["a"]["removeLabelIds"], ["INBOX"])
        self.assertNotIn("removeLabelIds", service.modified["b"])
        self.assertEqual(set(service.modified), {"a", "b", "f"})

    def test_already_labelled_messages_are_skipped(self):
        service = self.make_service(labels=gmail.LABELS.values())
        service.messages_by_id["a"]["labelIds"].append("L1")
        gmail.sort_mail(service, log=lambda line: None)
        self.assertEqual(service.created, [])
        self.assertEqual(set(service.modified), {"b", "f"})


class DigestTests(unittest.TestCase):
    english = "You will maintain our servers and work with the team on the cloud."
    rows = [
        {"title": "Python <Dev>", "link": "https://example.test/1", "country": "Latvia",
         "parse_status": "parsed", "match_score": 40, "first_seen_at": "1", "description": english},
        {"title": "Admin", "link": "https://example.test/2", "country": "Latvia",
         "parse_status": "parsed", "match_score": 80, "first_seen_at": "2", "description": english},
        {"title": "Broken", "link": "https://example.test/3", "parse_status": "auth_required"},
        {"title": "Senior Admin", "link": "https://example.test/4", "parse_status": "parsed", "match_score": 99,
         "description": english},
        {"title": "IT-Administrator (m/w/d)", "link": "https://example.test/5", "parse_status": "parsed",
         "match_score": 95, "description": "Wir suchen für die IT einen Administrator und Sie arbeiten mit der Technik."},
    ]

    def test_rows_are_filtered_and_sorted(self):
        self.assertEqual([row["title"] for row in gmail.digest_rows(self.rows)], ["Admin", "Python <Dev>"])
        self.assertEqual([row["title"] for row in gmail.digest_rows(self.rows, min_score=50)], ["Admin"])

    def test_digest_is_inserted_not_sent(self):
        service = FakeService({})
        gmail.insert_digest(service, gmail.digest_rows(self.rows), title="Run")
        body = service.inserted[0]
        self.assertIn("UNREAD", body["labelIds"])
        message = email.message_from_bytes(base64.urlsafe_b64decode(body["raw"]))
        self.assertEqual(message["To"], "me@example.test")
        html = message.get_payload()[1].get_payload(decode=True).decode("utf-8")
        self.assertIn("Python &lt;Dev&gt;", html)


if __name__ == "__main__":
    unittest.main()
