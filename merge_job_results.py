#!/usr/bin/env python3
"""Cumulative history of confirmed US/UK remote software + AI jobs captured on publication day.

Migration: older broad/undated records are NOT carried over. Only previous records
marked by this version of the collector are retained to avoid contaminating the new list.
"""
import csv
from datetime import datetime, timezone
from pathlib import Path

from collect_remote_jobs import FIELDS as BASE_FIELDS, SOFTWARE_AI_RE

EXTRA_FIELDS = ['location_check','first_seen_utc','last_seen_utc','collection_version']
FIELDS = BASE_FIELDS + EXTRA_FIELDS
COUNTRIES = {'US','UK','US & UK'}
VERSION = 'remote-software-ai-pt-published-v1'
OUT = Path('jobs_all.csv')


def read_rows(path):
    if not path.exists():
        return []
    with path.open(encoding='utf-8-sig',newline='') as fh:
        return list(csv.DictReader(fh))


def row_key(row):
    jid = str(row.get('job_id') or row.get('job_url') or '').strip()
    if not jid:
        return None
    return (row.get('platform','').strip().lower(),
            (row.get('board_slug') or row.get('company') or '').strip().lower(),jid)


def valid(row):
    return (row_key(row) and row.get('country') in COUNTRIES
            and bool(SOFTWARE_AI_RE.search(row.get('title') or ''))
            and bool(row.get('published_date_pt'))
            and bool(row.get('published_utc'))
            and (row.get('date_source') or '') not in ('unavailable','unknown'))


def main():
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    historical = {row_key(row):row for row in read_rows(OUT)
                  if row.get('collection_version') == VERSION and valid(row)}
    newest = {row_key(row): row for row in read_rows(Path('jobs_us_uk_remote.csv')) if valid(row)}
    for k, row in newest.items():
        previous = historical.get(k,{})
        historical[k] = {
            **{col:row.get(col,'') for col in BASE_FIELDS},
            'location_check':'US/UK remote — location indicated',
            'first_seen_utc': previous.get('first_seen_utc') or now,
            'last_seen_utc': now,
            'collection_version':VERSION,
        }
    values = sorted(historical.values(),key=lambda r:(r.get('published_date_pt',''),r.get('published_utc','')),reverse=True)
    with OUT.open('w',newline='',encoding='utf-8-sig') as fh:
        writer = csv.DictWriter(fh,fieldnames=FIELDS,extrasaction='ignore')
        writer.writeheader()
        writer.writerows(values)
    print(f'jobs_all.csv: {len(values)} historical confirmed software/AI jobs; {len(newest)} currently collected today')


if __name__=='__main__':
    main()
