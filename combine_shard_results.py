#!/usr/bin/env python3
"""Combine all eight scan shards into one deduplicated *today PT* output.

Outputs should never silently omit a shard. Run after the scan job matrix succeeds.
"""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from collect_remote_jobs import FIELDS, write_csv


def read_csv(path):
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(encoding='utf-8-sig', newline='') as fh:
        return list(csv.DictReader(fh))


def key(row):
    return (row.get('platform', '').casefold(), row.get('board_slug', '').casefold(),
            row.get('job_id') or row.get('job_url'))


def combine(folder='shards', expected=8):
    root = Path(folder)
    confirmed, review, candidates = {}, {}, {}
    errors = []
    summary = {}
    for shard in range(expected):
        prefix = root / f'ats-shard-{shard}' / f'jobs_shard_{shard}'
        local = {
            'confirmed': read_csv(Path(str(prefix) + '_us_uk_remote.csv')),
            'review': read_csv(Path(str(prefix) + '_review_location.csv')),
            'candidates': read_csv(Path(str(prefix) + '_remote_candidates.csv')),
        }
        summary[str(shard)] = {k:len(v) for k,v in local.items()}
        for dest, kind in ((confirmed,'confirmed'),(review,'review'),(candidates,'candidates')):
            for row in local[kind]:
                dest[key(row)] = row
        errfile = Path(str(prefix) + '_errors.txt')
        if errfile.exists():
            text = errfile.read_text(encoding='utf-8')
            if text.strip() != 'No board errors.':
                errors.append(f'=== Shard {shard} ===\n{text}')
    for name, rows in (
        ('jobs_us_uk_remote.csv', confirmed),
        ('jobs_review_location.csv', review),
        ('jobs_remote_candidates.csv', candidates),
    ):
        write_csv(name, sorted(rows.values(), key=lambda x: x.get('published_utc',''), reverse=True))
    Path('jobs_errors.txt').write_text('\n'.join(errors) if errors else 'No board errors.\n',encoding='utf-8')
    meta = {'shards_expected': expected,'shards_completed': len(summary),
            'confirmed_today':len(confirmed),'uncertain_location_today':len(review),
            'all_remote_candidates_today':len(candidates),
            'platform_counts':dict(Counter(r['platform'] for r in confirmed.values())),
            'per_shard':summary,'note':'Counts apply only to discovered and successfully fetched boards; not a census.'}
    Path('scan_summary.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in meta.items() if k != 'per_shard'},indent=2))
    return meta


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--shards',type=int,default=8)
    parser.add_argument('--directory',default='shards')
    args=parser.parse_args()
    combine(args.directory,args.shards)
