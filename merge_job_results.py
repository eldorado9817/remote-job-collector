#!/usr/bin/env python3
"""Merge each new scan into a single cumulative GitHub-tracked jobs_all.csv.

Deduplicates on (platform, job_id), preserves first_seen_utc, and refreshes
last_seen_utc and location status for every job found in the latest scan.
Uses only Python's standard library.
"""

import csv
from datetime import datetime, timezone
from pathlib import Path

BASE_FIELDS = [
    "platform", "company", "title", "country", "published_local",
    "published_utc", "location", "job_url", "board_slug", "job_id", "date_source"
]
EXTRA_FIELDS = ["location_check", "first_seen_utc", "last_seen_utc"]
FIELDS = BASE_FIELDS + EXTRA_FIELDS
OUTPUT = Path("jobs_all.csv")


def read_rows(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def key(row):
    identifier = (row.get("job_id") or row.get("job_url") or "").strip()
    if not identifier:
        return None
    return ((row.get("platform") or "").strip().lower(), identifier)


def main():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    previous = {key(row): row for row in read_rows(OUTPUT) if key(row)}
    current = {}
    for filename, label in (
        ("jobs_us_uk_remote.csv", "Location indicated: US/UK"),
        ("jobs_review_location.csv", "Location needs review"),
    ):
        for row in read_rows(Path(filename)):
            row_key = key(row)
            if row_key:
                current[row_key] = (row, label)

    for row_key, (row, label) in current.items():
        old = previous.get(row_key, {})
        previous[row_key] = {
            **{col: row.get(col, "") for col in BASE_FIELDS},
            "location_check": label,
            "first_seen_utc": old.get("first_seen_utc") or now,
            "last_seen_utc": now,
        }

    all_rows = sorted(
        previous.values(),
        key=lambda row: (row.get("published_utc") or "", row.get("first_seen_utc") or ""),
        reverse=True,
    )
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Updated {OUTPUT}: {len(all_rows)} total distinct jobs; "
          f"{len(current)} found in this scan")


if __name__ == "__main__":
    main()
