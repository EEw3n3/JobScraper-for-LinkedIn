"""Offline, deterministic exports of stored jobs."""

import csv
from html import escape
from pathlib import Path
from urllib.parse import urlsplit


def export_jobs(db, output_path, format='html', min_score=None):
    """Overwrite an export and return its resolved path.

    A score threshold includes only completed analyses. All stored columns are
    retained; unknown columns follow the standard columns in alphabetical order.
    """
    if format not in ('html', 'csv', 'markdown'):
        raise ValueError('format must be html, csv, or markdown')
    requested = Path(output_path).expanduser()
    output = requested.resolve()
    if any(path.suffix.lower() in ('.db', '.sqlite', '.sqlite3')
           for path in (requested, output)):
        raise ValueError('Cannot export to a database file')
    database_path = getattr(db, 'path', None)
    if database_path is not None:
        database_path = Path(database_path).expanduser().resolve()
        if output == database_path or (
            output.exists() and database_path.exists()
            and output.samefile(database_path)
        ):
            raise ValueError('Cannot overwrite the source database')

    rows = list(db.rows())
    if min_score is not None:
        rows = [row for row in rows
                if row.get('analysis_status') == 'analyzed'
                and row.get('match_score') is not None
                and row['match_score'] >= min_score]
    rows.sort(key=lambda row: (
        row.get('match_score') is not None,
        row.get('match_score') if row.get('match_score') is not None else 0,
        row.get('first_seen_at') or '',
    ), reverse=True)
    fields = [
        'title', 'link', 'country', 'description', 'grade', 'salary', 'posted',
        'source', 'parse_status', 'analysis_status', 'match_score',
        'analysis_report', 'recommended_cv', 'cover_letter', 'last_error',
        'first_seen_at', 'last_seen_at',
    ]
    fields += sorted({key for row in rows for key in row} - set(fields))

    def text(value):
        return '' if value is None else str(value)

    def markup(field, value):
        value = text(value)
        safe = escape(value, quote=True)
        if field in ('link', 'source', 'source_url'):
            try:
                url = urlsplit(value)
                if (url.scheme.lower() in ('http', 'https') and url.netloc
                        and not any(char.isspace() or ord(char) < 32
                                    or ord(char) == 127 for char in value)):
                    return f'<a href="{safe}" rel="noreferrer">{safe}</a>'
            except ValueError:
                pass
        return safe

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', encoding='utf-8', newline='') as stream:
        if format == 'csv':
            writer = csv.writer(stream)
            # Check beyond leading whitespace, but preserve the original value.
            def csv_value(value):
                if isinstance(value, str) and (
                    value.lstrip().startswith(('=', '+', '-', '@'))
                    or value.startswith(('\t', '\r', '\n'))
                ):
                    return "'" + value
                return text(value)

            writer.writerow([csv_value(field) for field in fields])
            writer.writerows([csv_value(row.get(field)) for field in fields]
                             for row in rows)
        else:
            if format == 'html':
                stream.write('''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jobs</title>
<style>
body{font:16px/1.6 system-ui,sans-serif;margin:0;background:#f4f5f7;color:#18202a}
main{max-width:960px;margin:auto;padding:1rem}
article{background:white;border:1px solid #ccd2da;border-radius:8px;padding:1rem;margin:1rem 0}
h1,h2{line-height:1.25}dt{font-weight:700;margin-top:.8rem}
dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}
a{color:#0755a1}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;margin:0}
</style></head><body><main><h1>Jobs</h1>
''')
            else:
                stream.write('# Jobs\n\n')
            stream.write(f'<p>{len(rows)} jobs</p>\n\n')
            for index, row in enumerate(rows, 1):
                stream.write(f'<article><h2>Job {index}</h2><dl>\n' if format == 'html'
                             else f'## Job {index}\n\n<dl>\n')
                for field in fields:
                    label = escape(field.replace('_', ' ').capitalize())
                    value = markup(field, row.get(field))
                    if format == 'markdown':
                        value = value.replace('\r', '&#13;').replace('\n', '&#10;')
                    # HTML blocks keep external Markdown syntax inert as well.
                    stream.write(f'<dt>{label}</dt><dd><pre>{value}</pre></dd>\n')
                stream.write('</dl></article>\n' if format == 'html' else '</dl>\n\n')
            if format == 'html':
                stream.write('</main></body></html>\n')
    return output
