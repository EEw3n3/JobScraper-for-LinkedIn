import os
import unittest
from unittest.mock import patch

from scraper import driver


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in ("Service", "ChromeDriverManager"):
            patcher = patch.object(driver, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        patcher = patch.object(driver.webdriver, "Chrome")
        self.chrome = patcher.start()
        self.addCleanup(patcher.stop)
        self.ChromeDriverManager.return_value.install.return_value = "desktop-driver"

    def options(self):
        return self.chrome.call_args.kwargs["options"]

    def test_desktop_fallback_and_timeouts(self):
        browser = driver.setup_driver()
        self.assertIs(browser, self.chrome.return_value)
        self.ChromeDriverManager.return_value.install.assert_called_once_with()
        self.Service.assert_called_once_with("desktop-driver")
        self.assertIs(self.chrome.call_args.kwargs["service"], self.Service.return_value)
        self.assertEqual(self.options().arguments, ["--window-size=1440,1000"])
        browser.set_page_load_timeout.assert_called_once_with(60)
        browser.set_script_timeout.assert_called_once_with(30)
        browser.quit.assert_not_called()

    def test_container_environment(self):
        with patch.dict(os.environ, {
            "JOBPARSER_HEADLESS": "1",
            "CHROME_BINARY": "/usr/bin/chromium",
            "CHROMEDRIVER_PATH": "/usr/bin/chromedriver",
            "JOBPARSER_CHROME_NO_SANDBOX": "1",
        }):
            driver.setup_driver(headless=False)
        self.Service.assert_called_once_with("/usr/bin/chromedriver")
        self.ChromeDriverManager.assert_not_called()
        self.assertEqual(self.options().binary_location, "/usr/bin/chromium")
        self.assertEqual(self.options().arguments, [
            "--window-size=1440,1000", "--headless=new", "--no-sandbox",
        ])

    def test_explicit_headless_cannot_be_disabled_by_environment(self):
        with patch.dict(os.environ, {"JOBPARSER_HEADLESS": "0"}):
            driver.setup_driver(headless=True)
        self.assertIn("--headless=new", self.options().arguments)

    def test_persistent_profile_from_environment(self):
        with patch.dict(os.environ, {"CHROME_USER_DATA_DIR": "/private/profile"}):
            driver.setup_driver()
        self.assertIn("--user-data-dir=/private/profile", self.options().arguments)

    def test_boolean_environment_flags(self):
        for value in ("1", "true", " YES ", "on", "0", "false", "no", "off", "", "anything"):
            with self.subTest(value=value), patch.dict(os.environ, {
                "JOBPARSER_HEADLESS": value, "JOBPARSER_CHROME_NO_SANDBOX": value,
            }):
                driver.setup_driver()
                enabled = value.strip().lower() in ("1", "true", "yes", "on")
                self.assertEqual("--headless=new" in self.options().arguments, enabled)
                self.assertEqual("--no-sandbox" in self.options().arguments, enabled)

    def test_timeout_failure_closes_browser_and_preserves_error(self):
        browser = self.chrome.return_value
        for method in ("set_page_load_timeout", "set_script_timeout"):
            for quit_error in (None, RuntimeError("quit failed")):
                with self.subTest(method=method, quit_error=quit_error):
                    browser.reset_mock(side_effect=True)
                    error = RuntimeError("timeout configuration failed")
                    getattr(browser, method).side_effect = error
                    browser.quit.side_effect = quit_error
                    with self.assertRaises(RuntimeError) as caught:
                        driver.setup_driver()
                    self.assertIs(caught.exception, error)
                    browser.quit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
