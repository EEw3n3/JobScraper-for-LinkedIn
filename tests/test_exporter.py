import csv
import os
from pathlib import Path
import tempfile
import unittest
from html.parser import HTMLParser

from utils.exporter import export_jobs


class FakeDatabase:
    def __init__(self, rows):
        self.data = rows

    def rows(self):
        return self.data


class Links(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.hrefs = []
        self.tags = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == 'a':
            self.hrefs.append(dict(attrs)['href'])


class ExporterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = [
            dict(title='pending-job', parse_status='pending', analysis_status='pending'),
            dict(title='older-job', match_score=80, first_seen_at='2026-01-01',
                 analysis_status='analyzed'),
            dict(title='failed-job', first_seen_at='2026-02-01', parse_status='error',
                 analysis_status='error', last_error='Connection failed'),
            dict(title='newer-job', match_score=80, first_seen_at='2026-02-01',
                 analysis_status='analyzed', description='Full description\nSecond line',
                 analysis_report='Full analysis', recommended_cv='example-cv',
                 cover_letter='Full letter', source='https://example.org/source',
                 link='https://example.org/job?a=1&b=2', country='Suomi: \u00e4',
                 custom_field='Extra detail'),
            dict(title='highest-job', match_score=99, first_seen_at='2025-01-01',
                 analysis_status='analyzed'),
            dict(title='zero-job', match_score=0, analysis_status='analyzed'),
        ]
        self.db = FakeDatabase(self.rows)

    def test_formats_sort_complete_utf8_and_reproducible_overwrite(self):
        for format in ('html', 'csv', 'markdown'):
            with self.subTest(format=format):
                path = self.root / 'nested' / f'jobs.{format}'
                result = export_jobs(self.db, path, format)
                self.assertEqual(result, path.resolve())
                content = result.read_text(encoding='utf-8')
                titles = ['highest-job', 'newer-job', 'older-job', 'zero-job',
                          'failed-job', 'pending-job']
                positions = [content.index(title) for title in titles]
                self.assertEqual(positions, sorted(positions))
                for value in ('Full description', 'Second line', 'Full analysis',
                              'example-cv', 'Full letter', 'Connection failed',
                              'https://example.org/source', 'Extra detail', 'Suomi: \u00e4'):
                    self.assertIn(value, content)
                original = result.read_bytes()
                result.write_text('obsolete content' * 10000, encoding='utf-8')
                export_jobs(self.db, path, format)
                self.assertEqual(original, result.read_bytes())
                self.assertEqual(self.rows[0]['title'], 'pending-job')

    def test_threshold_excludes_unanalyzed_and_includes_boundary(self):
        self.rows.append(dict(title='stale-job', match_score=100, analysis_status='pending'))
        for format in ('html', 'csv', 'markdown'):
            for threshold in (0, 80, 100):
                with self.subTest(format=format, threshold=threshold):
                    content = export_jobs(self.db, self.root / 'out', format, threshold).read_text()
                    for title in ('pending-job', 'failed-job', 'stale-job'):
                        self.assertNotIn(title, content)
                    self.assertEqual('older-job' in content, threshold <= 80)
                    self.assertEqual('zero-job' in content, threshold == 0)

    def test_markup_escapes_external_content_and_allows_only_web_links(self):
        payload = '<script>alert("x")</script> &\n\n[click](javascript:alert(1))'
        urls = ['javascript:alert(1)', 'data:text/html,bad', '//example.org',
                'https://example.org/"onmouseover="bad', 'https://[invalid',
                'https://example.org/\nattack', 'https://example.org/?a=1&b=2',
                'http://example.org/job']
        db = FakeDatabase([dict(title=payload, description=payload, analysis_report=payload,
                                cover_letter=payload, recommended_cv=payload, link=url)
                           for url in urls])
        for format in ('html', 'markdown'):
            content = export_jobs(db, self.root / 'out', format).read_text()
            parsed = Links(content)
            self.assertNotIn('script', parsed.tags)
            self.assertIn('&lt;script&gt;', content)
            self.assertIn('&amp;', content)
            self.assertEqual(parsed.hrefs, [urls[3], urls[6], urls[7]])
            self.assertIn('<pre>', content)
            if format == 'markdown':
                self.assertIn('&#10;&#10;[click]', content)

    def test_csv_formula_protection_and_multiline_roundtrip(self):
        dangerous = ['=1+1', '+cmd', '-cmd', '@SUM(A1)', '  =1', '\t+1',
                     '\r-1', '\n@x', ' \t =1', '\u00a0=1', '\tordinary']
        safe = ['ordinary, "quoted"\nnext line', '  ordinary', '', None, -5]
        db = FakeDatabase([dict(title=value) for value in dangerous + safe])
        export_jobs(db, self.root / 'out.csv', 'csv')
        with (self.root / 'out.csv').open(encoding='utf-8', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([row['title'] for row in rows],
                         ["'" + value for value in dangerous]
                         + ['' if value is None else str(value) for value in safe])

    def test_empty_exports_and_default_html(self):
        for format in ('html', 'csv', 'markdown'):
            path = export_jobs(FakeDatabase([]), self.root / 'out', format)
            self.assertIn('title' if format == 'csv' else '0 jobs', path.read_text())
        self.assertTrue(export_jobs(self.db, self.root / 'default').read_text().startswith('<!doctype html>'))

    def test_rejects_database_paths_and_invalid_format_without_overwrite(self):
        for name in ('jobs.db', 'jobs.SQLITE', 'jobs.sqlite3', 'source.backup'):
            path = self.root / name
            path.write_bytes(b'database sentinel')
            self.db.path = path
            with self.assertRaises(ValueError):
                export_jobs(self.db, path)
            self.assertEqual(path.read_bytes(), b'database sentinel')
        for suffix in ('.db', '.SQLITE', '.sqlite3'):
            path = self.root / ('other' + suffix)
            with self.assertRaises(ValueError):
                export_jobs(self.db, path)
            self.assertFalse(path.exists())
        with self.assertRaises(ValueError):
            export_jobs(self.db, self.root / 'out', 'pdf')
        self.assertFalse((self.root / 'out').exists())

    def test_rejects_hardlink_to_database(self):
        self.db.path = self.root / 'source.data'
        self.db.path.write_bytes(b'database sentinel')
        alias = self.root / 'alias.html'
        try:
            os.link(self.db.path, alias)
        except OSError as error:
            self.skipTest(f'Hard links unavailable: {error}')
        with self.assertRaises(ValueError):
            export_jobs(self.db, alias)
        self.assertEqual(self.db.path.read_bytes(), b'database sentinel')


if __name__ == '__main__':
    unittest.main()
