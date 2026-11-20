#!/usr/bin/env python3
"""Find fresh US/UK remote engineering openings on public Greenhouse and Ashby boards.

Python 3.9+. Standard library only. Reads company slugs from a public CC-BY 4.0
LastRound AI directory (or from a local CSV). Writes verified-geography and
needs-review CSVs; never treats 'Remote' alone as globally eligible.
"""

import argparse
import csv
import html
import io
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

DIRECTORY_URL = (
    "https://raw.githubusercontent.com/fyrosofttech/lastroundai-hiring-data/main/"
    "ats-directory/lastroundai-ats-company-directory-2026-08.csv"
)
USER_AGENT = "US-UK-Remote-Jobs-Collector/1.0 (public job board APIs)"
FIELDS = ["platform", "company", "title", "country", "published_local",
          "published_utc", "location", "job_url", "board_slug", "job_id"]

ROLE_RE = re.compile(
    r"\b(software engineer|software developer|backend|back.end|front.end|full.stack|"
    r"platform engineer|devops|site reliability|\bsre\b|cloud engineer|"
    r"infrastructure engineer|security engineer|machine learning engineer|"
    r"\bml engineer\b|\bai engineer\b|data engineer|staff engineer|"
    r"principal engineer|engineering lead|technical lead|solutions engineer|"
    r"python developer|react developer|node\.?js developer)\b", re.I,
)
REMOTE_RE = re.compile(r"\b(remote|work from home|fully distributed|work anywhere)\b", re.I)
NON_REMOTE_RE = re.compile(r"\b(hybrid|on[- ]?site|in[- ]?office)\b", re.I)
US_RE = re.compile(
    r"\b(united states(?: of america)?|usa|u\.?s\.?a?\.?|"
    r"remote\s*[-(,/ ]\s*us\b|us\s*[-(,/ ]\s*remote\b|"
    r"new york|california|washington state|texas|florida|massachusetts|"
    r"seattle|san francisco|los angeles|chicago|boston|austin|denver|atlanta|"
    r"pittsburgh|portland,?\s*or\b)\b", re.I
)
UK_RE = re.compile(
    r"\b(united kingdom|u\.?k\.?\b|great britain|england|scotland|"
    r"wales|northern ireland|london|manchester|edinburgh|glasgow|"
    r"birmingham|bristol|leeds|cambridge|oxford|cardiff|belfast)\b", re.I
)
GLOBAL_RE = re.compile(r"\b(worldwide|anywhere in the world|globally remote|remote worldwide)\b", re.I)


def fetch(url, retries=2):
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/csv"})
            with urlopen(req, timeout=25) as response:
                return response.read().decode("utf-8-sig")
        except HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(min(2 ** attempt + 1, 5))
                continue
            raise
        except (URLError, TimeoutError):
            if attempt < retries:
                time.sleep(min(2 ** attempt + 1, 5))
                continue
            raise


def plain(raw):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw or ""))).strip()


def date_recent(timestamp, tz, days):
    if not timestamp:
        return None
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if not dt.tzinfo:
            return None
        local = dt.astimezone(ZoneInfo(tz))
        today = datetime.now(ZoneInfo(tz)).date()
        if today - timedelta(days=days - 1) <= local.date() <= today:
            return local.isoformat(timespec="seconds")
    except (ValueError, OverflowError):
        return None
    return None


def geography(job_locations, short_description):
    """Return eligibility hints, not a legal determination of work eligibility.

    Strong location clues take precedence. Descriptions are read only to detect
    explicit country-wide remote phrases; broad mentions of countries in job
    descriptions can refer to offices, so don't use those as confirmation.
    """
    locations = " | ".join(x for x in job_locations if x)
    desc = short_description[:2000]
    us = bool(US_RE.search(locations))
    uk = bool(UK_RE.search(locations))

    # Allow strong remote-location phrases at the beginning of a description.
    if re.search(r"\b(remote\s+(?:within|from|in|across|based in)\s+(?:the\s+)?(?:United States|USA|US)\b|US[- ]based remote)\b", desc, re.I):
        us = True
    if re.search(r"\b(remote\s+(?:within|from|in|across|based in)\s+(?:the\s+)?(?:United Kingdom|UK)\b|UK[- ]based remote)\b", desc, re.I):
        uk = True

    if us and uk:
        return "US & UK"
    if us:
        return "US"
    if uk:
        return "UK"
    if GLOBAL_RE.search(locations):
        return "Review: worldwide"
    return "Review: location unspecified"


def normalize_greenhouse(company, slug, job, days):
    title = job.get("title", "")
    if not ROLE_RE.search(title):
        return []
    location = (job.get("location") or {}).get("name", "")
    description = plain(job.get("content", ""))
    if NON_REMOTE_RE.search(f"{title} {location}"):
        return []
    # No structured workplaceType for Greenhouse; require a positive signal
    # from its location/title, or at the start of its description.
    if not REMOTE_RE.search(f"{title} {location}") and not REMOTE_RE.search(description[:350]):
        return []
    country = geography([location], description)
    out = []
    for target, tz in (("US", "America/Los_Angeles"), ("UK", "Europe/London")):
        if target not in country and not country.startswith("Review"):
            continue
        local = date_recent(job.get("first_published"), tz, days)
        if local:
            out.append(dict(platform="Greenhouse", company=company, title=title.strip(),
                            country=country, published_local=local, published_utc=job["first_published"],
                            location=location, job_url=job.get("absolute_url", ""),
                            board_slug=slug, job_id=str(job.get("id", ""))))
            break
    return out


def normalize_ashby(company, slug, job, days):
    title = job.get("title", "")
    if not ROLE_RE.search(title) or job.get("isListed") is False:
        return []
    # Ashby's isRemote=true can include HYBRID. workplaceType must be Remote.
    if job.get("workplaceType") != "Remote":
        return []
    locations = [job.get("location", "")]
    locations += [x.get("location", "") for x in job.get("secondaryLocations", [])]
    primary_country = ((job.get("address") or {}).get("postalAddress") or {}).get("addressCountry", "")
    locations.append(primary_country)
    for secondary in job.get("secondaryLocations", []):
        locations.append((secondary.get("address") or {}).get("addressCountry", ""))
    description = plain(job.get("descriptionPlain") or job.get("descriptionHtml", ""))
    country = geography(locations, description)
    for target, tz in (("US", "America/Los_Angeles"), ("UK", "Europe/London")):
        if target not in country and not country.startswith("Review"):
            continue
        local = date_recent(job.get("publishedAt"), tz, days)
        if local:
            return [dict(platform="Ashby", company=company, title=title.strip(),
                         country=country, published_local=local, published_utc=job["publishedAt"],
                         location=job.get("location", ""), job_url=job.get("jobUrl", ""),
                         board_slug=slug, job_id=job.get("jobUrl", "").split("?")[0].rstrip("/").split("/")[-1])]
    return []


def process_board(board, days):
    platform, company, slug = board
    if platform == "greenhouse":
        endpoint = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    else:
        endpoint = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        jobs = json.loads(fetch(endpoint)).get("jobs", [])
        normalize = normalize_greenhouse if platform == "greenhouse" else normalize_ashby
        return [row for job in jobs for row in normalize(company, slug, job, days)], None
    except (ValueError, HTTPError, URLError, TimeoutError) as exc:
        return [], f"{platform}/{slug}: {str(exc)[:120]}"


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=1, help="Calendar days including today (default 1)")
    parser.add_argument("--max-boards", type=int, default=250,
                        help="Maximum boards to check, 0 = all; default 250 for a faster first run")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent requests; keep low (default 4)")
    parser.add_argument("--directory", help="Optional path to the ATS directory CSV, instead of downloading it")
    parser.add_argument("--output", default="jobs", help="CSV filename prefix (default jobs)")
    args = parser.parse_args()
    if args.days < 1 or args.max_boards < 0 or args.workers < 1 or args.workers > 12:
        parser.error("days >= 1, max-boards >= 0, and 1 <= workers <= 12 are required")
    try:
        data = Path(args.directory).read_text(encoding="utf-8-sig") if args.directory else fetch(DIRECTORY_URL)
        boards = [(r["ats_vendor"].strip().lower(), r["company_name"].strip(), r["board_slug"].strip())
                  for r in csv.DictReader(io.StringIO(data))
                  if r.get("ats_vendor", "").strip().lower() in ("greenhouse", "ashby") and r.get("board_slug")]
    except (ValueError, KeyError, OSError, HTTPError, URLError) as exc:
        sys.exit(f"Unable to load board directory: {exc}. Pass --directory path/to/file.csv")
    # Interleave platforms so even a short test covers both.
    gh = [b for b in boards if b[0] == "greenhouse"]
    ash = [b for b in boards if b[0] == "ashby"]
    mixed = [v for pair in zip(gh, ash) for v in pair]
    mixed += gh[len(ash):] if len(gh) > len(ash) else ash[len(gh):]
    boards = mixed[:args.max_boards] if args.max_boards else mixed
    print(f"Checking {len(boards)} company boards, posted in last {args.days} calendar day(s)...", flush=True)
    found, errors = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(process_board, b, args.days) for b in boards]
        for n, fut in enumerate(as_completed(futures), 1):
            rows, error = fut.result()
            found.extend(rows)
            if error:
                errors.append(error)
            if n % 100 == 0:
                print(f"Checked {n}/{len(boards)} boards; candidate jobs: {len(found)}", flush=True)
    found = sorted({(r["platform"], r["job_id"]): r for r in found}.values(),
                   key=lambda r: r["published_utc"], reverse=True)
    verified = [r for r in found if not r["country"].startswith("Review")]
    review = [r for r in found if r["country"].startswith("Review")]
    write_csv(f"{args.output}_us_uk_remote.csv", verified)
    write_csv(f"{args.output}_review_location.csv", review)
    print(f"\nSaved {len(verified)} location-matched jobs to {args.output}_us_uk_remote.csv")
    print(f"Saved {len(review)} remote jobs requiring eligibility review to {args.output}_review_location.csv")
    print(f"Errors: {len(errors)} boards. Public API may rate-limit or remove boards.")
    if errors:
        Path(f"{args.output}_errors.txt").write_text("\n".join(errors), encoding="utf-8")
    print("NOTE: Country hints are not a guarantee of hiring eligibility. Read each listing before applying.")

if __name__ == "__main__":
    main()
