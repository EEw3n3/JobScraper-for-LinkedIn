import threading
import os
import unittest
from unittest.mock import Mock, patch

from selenium.common.exceptions import InvalidSessionIdException, NoSuchElementException

from scraper import collector, parser
from utils.validation import description_status


class ScraperTests(unittest.TestCase):
    def setUp(self):
        parser.reset_navigation_state()
        self.addCleanup(parser.reset_navigation_state)

    def test_description_validation(self):
        for text in (None, "", "Could not extract", "Too short"):
            with self.subTest(text=text):
                self.assertEqual(description_status(text), "missing_description")
        self.assertEqual(description_status(
            "LinkedIn requires authorization to view (Auth wall hard block)"
        ), "auth_required")
        self.assertEqual(description_status("Develop and maintain Python services with our engineering team. " * 10), "parsed")

    def test_extract_rejects_invalid_description(self):
        driver = Mock(current_url="https://example.test/jobs/view/1", title="Job")
        driver.find_elements.return_value = []
        for text in ("Could not extract", "Short", "Sign in to view this job"):
            with self.subTest(text=text), patch.object(parser, "load_job_page"):
                driver.find_element.return_value.text = text
                job = {"Link": driver.current_url, "Source": "CV", "Title": "Engineer"}
                with self.assertRaises(parser.DescriptionError) as caught:
                    parser.extract_job_details(driver, job)
                self.assertEqual(caught.exception.status, description_status(text))
                self.assertNotIn("Description", job)

    def test_auth_redirect_rejected(self):
        driver = Mock(current_url="https://example.test/authwall", title="Job")
        with patch.object(parser, "load_job_page"), self.assertRaises(parser.DescriptionError) as caught:
            parser.extract_job_details(driver, {"Link": driver.current_url})
        self.assertEqual(caught.exception.status, "auth_required")

    def test_interactive_auth_wait_can_continue(self):
        driver = Mock()
        with patch.dict(os.environ, {"JOBPARSER_INTERACTIVE": "1", "JOBPARSER_INTERACTIVE_WAIT": "30"}), patch.object(parser, "is_access_blocked", side_effect=[True, False]), patch.object(parser, "interruptible_wait") as wait, patch.object(parser.time, "monotonic", side_effect=[0, 0, 0]):
            self.assertTrue(parser.wait_for_manual_access(driver))
        wait.assert_called_once_with(2)

    def test_noninteractive_auth_does_not_wait(self):
        driver = Mock()
        with patch.dict(os.environ, {"JOBPARSER_INTERACTIVE": "0"}), patch.object(parser, "is_access_blocked", return_value=True), patch.object(parser, "interruptible_wait") as wait:
            self.assertFalse(parser.wait_for_manual_access(driver))
        wait.assert_not_called()

    def test_valid_description_returns_job(self):
        driver = Mock(current_url="https://example.test/jobs/view/1", title="Job")
        driver.find_elements.return_value = []
        text = "Develop and maintain Python services with our engineering team. " * 10
        driver.find_element.return_value.text = text
        job = {"Link": driver.current_url, "Source": "CV", "Title": "Engineer"}
        with patch.object(parser, "load_job_page"):
            self.assertIs(parser.extract_job_details(driver, job), job)
        self.assertEqual(job["Description"], text.strip())

    def test_missing_description_raises(self):
        driver = Mock(current_url="https://example.test/jobs/view/1", title="Job")
        driver.find_elements.return_value = []
        driver.find_element.side_effect = NoSuchElementException("missing")
        job = {"Link": driver.current_url, "Source": "CV", "Title": "Engineer"}
        with patch.object(parser, "load_job_page"), self.assertRaises(parser.DescriptionError) as caught:
            parser.extract_job_details(driver, job)
        self.assertEqual(caught.exception.status, "missing_description")

    def test_stop_prevents_navigation(self):
        parser.stop_event.set()
        driver = Mock()
        with self.assertRaises(parser.RateLimitError):
            parser.load_job_page(driver, "https://example.test")
        driver.get.assert_not_called()

    def test_exhausted_429_stops_other_workers(self):
        driver = Mock()
        with patch.object(parser, "RATE_LIMIT_RETRIES", 0), patch.object(parser, "interruptible_wait"), patch.object(parser, "is_rate_limited", return_value=True):
            with self.assertRaises(parser.RateLimitError):
                parser.load_job_page(driver, "https://example.test")
        self.assertTrue(parser.stop_event.is_set())
        other = Mock()
        with self.assertRaises(parser.RateLimitError):
            parser.load_job_page(other, "https://example.test")
        other.get.assert_not_called()

    def test_wait_is_interruptible(self):
        started = threading.Event()
        stopped = threading.Event()

        def wait():
            started.set()
            try:
                parser.interruptible_wait(60)
            except parser.RateLimitError:
                stopped.set()

        worker = threading.Thread(target=wait, daemon=True)
        worker.start()
        self.assertTrue(started.wait(1))
        parser.stop_event.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertTrue(stopped.is_set())

    def test_reset_clears_shared_state(self):
        parser.stop_event.set()
        parser.set_rate_limit_cooldown(0)
        parser.reset_navigation_state()
        self.assertFalse(parser.stop_event.is_set())
        self.assertEqual(parser._rate_limit_until, 0)
        self.assertEqual(parser._next_navigation_at, 0)

    def test_fatal_navigation_propagates(self):
        driver = Mock()
        driver.get.side_effect = InvalidSessionIdException("invalid session id")
        with self.assertRaises(parser.BrowserSessionError):
            parser.load_job_page(driver, "https://example.test")

    def collect(self, callback, max_pages=0, limit=None, scroll_error=None, exclude_links=None):
        driver = Mock(current_url="https://example.test/jobs", title="Jobs")
        link = Mock(text="Engineer")
        link.get_attribute.return_value = "https://example.test/jobs/view/1?tracking=test"
        card = Mock()
        card.find_element.side_effect = lambda by, value: link if value == "a" else Mock(text="2 hours ago")
        driver.find_elements.return_value = [card, card]
        driver.execute_script.side_effect = scroll_error
        site = {"url": driver.current_url, "source": "LinkedIn", "country": "Test"}
        with patch.object(collector, "load_job_page"), patch.object(collector, "is_rate_limited", return_value=False), patch.object(collector, "wait_for_navigation_slot"), patch.object(collector, "interruptible_wait"):
            return collector.collect_links(driver, site, callback, max_pages, limit, exclude_links)

    def test_callback_dedup_and_return(self):
        saved = []
        jobs = self.collect(saved.append)
        self.assertEqual(jobs, saved)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["Link"], "https://example.test/jobs/view/1")

    def test_linkedin_menu_links_are_not_jobs(self):
        driver = Mock(current_url="https://example.test/jobs", title="Jobs")
        menu = Mock(text="Finance")
        menu.get_attribute.return_value = "https://example.test/jobs/finance-jobs-riga"
        card = Mock()
        card.find_element.return_value = menu
        driver.find_elements.return_value = [card]
        site = {"url": driver.current_url, "source": "LinkedIn", "country": "Test"}
        with patch.object(collector, "load_job_page"), patch.object(collector, "is_rate_limited", return_value=False):
            self.assertEqual(collector.collect_links(driver, site, Mock(), 0), [])

    def test_resume_excludes_saved_links_from_limit(self):
        callback = Mock()
        self.assertEqual(self.collect(callback, limit=1, exclude_links={"https://example.test/jobs/view/1"}), [])
        callback.assert_not_called()

    def test_callback_failure_propagates_unchanged(self):
        for error in (RuntimeError("database failed"), NoSuchElementException("callback failed"), InvalidSessionIdException("callback failed")):
            with self.subTest(error=error):
                with self.assertRaises(type(error)) as caught:
                    self.collect(Mock(side_effect=error))
                self.assertIs(caught.exception, error)

    def test_persists_before_scroll_failure(self):
        saved = []
        with self.assertRaises(RuntimeError):
            self.collect(saved.append, max_pages=1, scroll_error=RuntimeError("scroll failed"))
        self.assertEqual(len(saved), 1)

    def test_limit_prevents_scroll(self):
        jobs = self.collect(Mock(), max_pages=10, limit=1, scroll_error=AssertionError("must not scroll"))
        self.assertEqual(len(jobs), 1)

    def test_collector_respects_stop(self):
        parser.stop_event.set()
        with self.assertRaises(parser.RateLimitError):
            self.collect(Mock())


if __name__ == "__main__":
    unittest.main()
