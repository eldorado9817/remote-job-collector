#!/usr/bin/env python3
"""Cumulative history of confirmed US/UK remote software + AI jobs captured on publication day.

jobs_all.csv keeps only the columns people read: platform, company, title, country,
published_date_pt and job_url. Rows are deduplicated by job_url.
"""
import csv
from pathlib import Path

from collect_remote_jobs import SOFTWARE_AI_RE

FIELDS = ['platform', 'company', 'title', 'country', 'published_date_pt', 'job_url']
COUNTRIES = {'US', 'UK', 'US & UK'}
OUT = Path('jobs_all.csv')


def read_rows(path):
    if not path.exists():
        return []
    with path.open(encoding='utf-8-sig', newline='') as fh:
        return list(csv.DictReader(fh))


def row_key(row):
    url = (row.get('job_url') or '').strip().rstrip('/').lower()
    return url or None


def project(row):
    out = {col: (row.get(col) or '').strip() for col in FIELDS}
    # Older files had only published_local (Pacific ISO timestamp); derive the date.
    if not out['published_date_pt']:
        out['published_date_pt'] = (row.get('published_local') or '').strip()[:10]
    return out


def valid_history(row):
    return (row_key(row) and row.get('country') in COUNTRIES
            and bool(SOFTWARE_AI_RE.search(row.get('title') or ''))
            and len(row.get('published_date_pt') or '') == 10)


def valid_new(row):
    """Today's scan rows still carry full metadata; require a real publication date."""
    return (valid_history(project(row))
            and bool(row.get('published_utc'))
            and (row.get('date_source') or '') not in ('unavailable', 'unknown'))


def main():
    newest_rows = sorted((r for r in read_rows(Path('jobs_us_uk_remote.csv')) if valid_new(r)),
                         key=lambda r: r.get('published_utc', ''), reverse=True)
    merged = {}
    # Newest first so today's data wins and keeps its time order within a day.
    for row in newest_rows:
        merged.setdefault(row_key(row), project(row))
    for row in map(project, read_rows(OUT)):
        if valid_history(row):
            merged.setdefault(row_key(row), row)
    values = sorted(merged.values(), key=lambda r: r['published_date_pt'], reverse=True)
    with OUT.open('w', newline='', encoding='utf-8-sig') as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(values)
    print(f'jobs_all.csv: {len(values)} historical confirmed software/AI jobs; {len(newest_rows)} currently collected today')


if __name__ == '__main__':
    main()
