#!/usr/bin/env python3
"""Find fresh US/UK remote engineering openings across eight public ATS platforms.

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
import os
from urllib.parse import urlparse, quote
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
USER_AGENT = "US-UK-Remote-Jobs-Collector/2.0 (public career feeds)"
FIELDS = ["platform", "company", "title", "country", "published_local",
          "published_utc", "location", "job_url", "board_slug", "job_id", "date_source"]
SUPPORTED = {"greenhouse", "ashby", "workable", "smartrecruiters", "recruitee",
             "breezy", "breezyhr", "workday", "bamboohr"}

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
    r"wales|northern ireland|\bGB\b|london|manchester|edinburgh|glasgow|"
    r"birmingham|bristol|leeds|cambridge|oxford|cardiff|belfast)\b", re.I
)
GLOBAL_RE = re.compile(r"\b(worldwide|anywhere in the world|globally remote|remote worldwide)\b", re.I)


def fetch(url, retries=2, payload=None):
    """Fetch a public ATS JSON/text feed. Retry only transient errors."""
    for attempt in range(retries + 1):
        try:
            headers = {"User-Agent": USER_AGENT, "Accept": "application/json,text/csv",
                       "Content-Type": "application/json"}
            body = json.dumps(payload).encode() if payload is not None else None
            req = Request(url, headers=headers, data=body, method="POST" if body else "GET")
            with urlopen(req, timeout=18) as response:
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


def get_json(url, payload=None):
    return json.loads(fetch(url, payload=payload))


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
                            board_slug=slug, job_id=str(job.get("id", "")), date_source="first_published"))
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
                         board_slug=slug, job_id=job.get("jobUrl", "").split("?")[0].rstrip("/").split("/")[-1], date_source="publishedAt")]
    return []


def parse_posting_date(value):
    """Return UTC-aware date/time and source precision; never guess missing dates."""
    if not value:
        return None
    txt = str(value).strip()
    if txt.endswith(" UTC"):
        txt = txt[:-4].replace(" ", "T") + "+00:00"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", txt):
        return datetime.fromisoformat(txt + "T00:00:00+00:00"), "date"
    try:
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # The provider sometimes sends a naive datetime; leave it unknown.
            return None
        return dt, "timestamp"
    except ValueError:
        return None


def normalize_generic(platform, company, slug, job, days):
    title = str(job.get("title") or "")
    if not ROLE_RE.search(title):
        return []
    location = str(job.get("location") or "")
    description = plain(str(job.get("description") or ""))
    # Structured remote = true, but hybrid does not count as remote-only.
    workplace = str(job.get("workplace") or "").lower()
    if workplace in ("hybrid", "onsite", "on-site", "on_site"):
        return []
    if not (job.get("remote") is True or workplace in ("remote", "fully remote") or
            REMOTE_RE.search(f"{title} {location} {description[:350]}")):
        return []
    if NON_REMOTE_RE.search(f"{title} {location}"):
        return []
    country = geography([location, str(job.get("other_locations") or "")], description)
    parsed = parse_posting_date(job.get("date"))
    if not parsed:
        return []  # no usable publication date
    dt, precision = parsed
    for target, tz in (("US", "America/Los_Angeles"), ("UK", "Europe/London")):
        if target not in country and not country.startswith("Review"):
            continue
        local = dt.astimezone(ZoneInfo(tz))
        today = datetime.now(ZoneInfo(tz)).date()
        # Date-only values express a calendar date with no timezone: compare directly.
        pub_date = dt.date() if precision == "date" else local.date()
        if today - timedelta(days=days - 1) <= pub_date <= today:
            return [dict(platform=platform, company=company, title=title.strip(),
                         country=country, published_local=pub_date.isoformat() if precision == "date" else local.isoformat(timespec="seconds"),
                         published_utc=dt.date().isoformat() if precision == "date" else dt.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds"),
                         location=location, job_url=str(job.get("url") or ""),
                         board_slug=slug, job_id=str(job.get("id") or job.get("url") or ""),
                         date_source=str(job.get("date_source") or "unknown") + (" (date only)" if precision == "date" else ""))]
    return []


def text_location(location):
    if isinstance(location, str):
        return location
    if isinstance(location, list):
        return " | ".join(text_location(x) for x in location)
    if isinstance(location, dict):
        parts = [location.get(k) for k in ("name", "city", "region", "state", "country", "countryName")]
        country = location.get("country")
        if isinstance(country, dict):
            parts[-1] = country.get("name") or country.get("code")
        return ", ".join(str(x) for x in parts if isinstance(x, (str, int)) and x)
    return ""


def check_candidate(p, company, slug, data, days):
    return normalize_generic(p, company, slug, data, days)


def jobs_workable(company, slug, days):
    data = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{quote(slug)}?details=true")
    rows = []
    for j in data.get("jobs", []):
        location = text_location(j.get("locations") or []) or text_location({k: j.get(k) for k in ("city", "state", "country")})
        rows += check_candidate("Workable", company, slug, dict(
            title=j.get("title"), location=location, description=j.get("description"),
            workplace=j.get("workplace_type"), remote=j.get("telecommuting") is True,
            date=j.get("published_on"), date_source="published_on",
            url=j.get("url") or j.get("application_url"),
            id=j.get("shortcode") or j.get("code")), days)
    return rows


def jobs_smartrecruiters(company, slug, days):
    rows, offset = [], 0
    for _ in range(100):  # 10,000 postings cap per board
        data = get_json(f"https://api.smartrecruiters.com/v1/companies/{quote(slug)}/postings?limit=100&offset={offset}")
        jobs = data.get("content") or []
        for j in jobs:
            loc = j.get("location") or {}
            label = text_location(loc)
            remote = loc.get("remote") is True or str(loc.get("locationType") or "").upper() == "REMOTE"
            rows += check_candidate("SmartRecruiters", company, slug, dict(
                title=j.get("name"), location=label, remote=remote,
                workplace="hybrid" if loc.get("hybrid") else "remote" if remote else "",
                description="", date=j.get("releasedDate"), date_source="releasedDate",
                url=f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}", id=j.get("id")), days)
        offset += len(jobs)
        if not jobs or offset >= data.get("totalFound", offset):
            break
    return rows


def jobs_recruitee(company, slug, days):
    data = get_json(f"https://{slug}.recruitee.com/api/offers/")
    rows = []
    for j in data.get("offers", []):
        loc = text_location(j.get("locations") or []) or text_location({k:j.get(k) for k in ("city", "country")})
        remote = j.get("remote") is True
        rows += check_candidate("Recruitee", company, slug, dict(
            title=j.get("title"), location=loc, remote=remote,
            workplace="hybrid" if j.get("hybrid") is True else "remote" if remote else "",
            description=(j.get("description") or "") + " " + (j.get("requirements") or ""),
            date=j.get("published_at"), date_source="published_at",
            url=j.get("careers_url") or j.get("careers_apply_url"),
            id=j.get("id") or j.get("guid")), days)
    return rows


def jobs_breezy(company, slug, days):
    data = get_json(f"https://{slug}.breezy.hr/json")
    jobs = data if isinstance(data, list) else data.get("jobs", [])
    rows = []
    for j in jobs:
        loc = j.get("location") or {}
        remote = loc.get("is_remote") is True
        rows += check_candidate("Breezy HR", company, slug, dict(
            title=j.get("name") or j.get("title"), location=text_location(loc),
            other_locations=text_location(j.get("locations") or []), remote=remote,
            date=j.get("published_date"), date_source="published_date",
            url=j.get("url"), id=j.get("id") or j.get("friendly_id")), days)
    return rows


def jobs_bamboohr(company, slug, days):
    data = get_json(f"https://{slug}.bamboohr.com/careers/list")
    jobs = data.get("result", [])
    if isinstance(jobs, dict):
        jobs = jobs.get("jobs", [])
    rows = []
    for j in jobs:
        title = j.get("jobOpeningName") or j.get("title")
        if not title or not ROLE_RE.search(title):
            continue
        jid = j.get("id")
        if jid is None:
            continue
        location = text_location(j.get("location") or j.get("atsLocation"))
        if not (j.get("isRemote") is True or REMOTE_RE.search(f"{title} {location}")):
            # Bamboo's isRemote is often missing; check detail for remote language.
            pass
        try:
            detail = get_json(f"https://{slug}.bamboohr.com/careers/{quote(str(jid))}/detail")
        except (ValueError, HTTPError, URLError, TimeoutError):
            continue
        d = detail.get("result") or {}
        if "jobOpening" in d:
            d = d["jobOpening"]
        desc = d.get("description") or ""
        rows += check_candidate("BambooHR", company, slug, dict(
            title=title, location=location or text_location(d.get("location")),
            description=desc, remote=j.get("isRemote") is True or d.get("isRemote") is True,
            date=d.get("datePosted"), date_source="datePosted",
            url=f"https://{slug}.bamboohr.com/careers/{jid}", id=jid), days)
    return rows


def workday_endpoint(slug):
    """Full board URL is required; extracting tenant from hostname avoids guesses."""
    u = urlparse(slug)
    if u.scheme != "https" or not (u.hostname or "").endswith(".myworkdayjobs.com"):
        raise ValueError("Workday board_slug must be full https://tenant.wdN.myworkdayjobs.com/SITE URL")
    parts = [p for p in u.path.split("/") if p]
    if not parts:
        raise ValueError("Workday URL missing site")
    tenant = u.hostname.split(".")[0]
    site = parts[0]
    return f"https://{u.hostname}/wday/cxs/{quote(tenant)}/{quote(site)}", site


def workday_date(j, detail):
    info = detail.get("jobPostingInfo") or {}
    raw = info.get("startDate") or info.get("postedDate")
    if raw:
        return raw, "jobPostingInfo.startDate"
    # Workday postedOn is often relative to the employer's local time.
    # Do not turn relative ages into invented absolute publication dates.
    return None, None


def jobs_workday(company, slug, days):
    base, site = workday_endpoint(slug)
    rows, offset = [], 0
    for _ in range(200):  # max 4000 postings per board
        data = get_json(base + "/jobs", payload={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""})
        jobs = data.get("jobPostings") or []
        for j in jobs:
            title = j.get("title") or ""
            if not ROLE_RE.search(title):
                continue
            path = j.get("externalPath") or ""
            if not path.startswith("/"):
                continue
            try:
                detail = get_json(base + path)
            except (ValueError, HTTPError, URLError, TimeoutError):
                continue
            info = detail.get("jobPostingInfo") or {}
            posted, date_source = workday_date(j, detail)
            if not posted:
                continue
            location = " | ".join(filter(None, [text_location(j.get("locationsText")),
                                   text_location(info.get("location")), text_location(info.get("additionalLocations") or [])]))
            workplace = str(info.get("workplaceType") or "")
            desc = info.get("jobDescription") or ""
            rows += check_candidate("Workday", company, slug, dict(
                title=title, location=location, description=desc, workplace=workplace,
                date=posted, date_source=date_source, url=f"{base.split('/wday/cxs/')[0]}/{site}{path}",
                id=info.get("jobReqId") or path), days)
        offset += len(jobs)
        if not jobs or offset >= data.get("total", offset):
            break
    return rows


ADAPTERS = {
    "workable": jobs_workable,
    "smartrecruiters": jobs_smartrecruiters,
    "recruitee": jobs_recruitee,
    "breezy": jobs_breezy,
    "breezyhr": jobs_breezy,
    "workday": jobs_workday,
    "bamboohr": jobs_bamboohr,
}


def process_board(board, days):
    platform, company, slug = board
    try:
        if platform == "greenhouse":
            jobs = get_json(f"https://boards-api.greenhouse.io/v1/boards/{quote(slug)}/jobs?content=true").get("jobs", [])
            rows = [row for j in jobs for row in normalize_greenhouse(company, slug, j, days)]
        elif platform == "ashby":
            jobs = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{quote(slug)}").get("jobs", [])
            rows = [row for j in jobs for row in normalize_ashby(company, slug, j, days)]
        else:
            rows = ADAPTERS[platform](company, slug, days)
        return rows, None
    except (ValueError, KeyError, TypeError, HTTPError, URLError, TimeoutError) as exc:
        return [], f"{platform}/{slug}: {str(exc)[:180]}"


def load_boards(data):
    """Accept ats_vendor/company_name/board_slug or platform/company/board_slug."""
    boards = []
    for r in csv.DictReader(io.StringIO(data)):
        platform = (r.get("ats_vendor") or r.get("platform") or "").strip().lower().replace(" ", "")
        if platform not in SUPPORTED:
            continue
        slug = (r.get("board_slug") or r.get("slug") or r.get("url") or "").strip()
        if not slug:
            continue
        company = (r.get("company_name") or r.get("company") or slug).strip()
        boards.append((platform, company, slug))
    return boards


def interleave(boards):
    buckets = {key: [] for key in ("greenhouse", "ashby", "workable", "smartrecruiters", "recruitee", "breezy", "workday", "bamboohr")}
    for p, name, slug in boards:
        canonical = "breezy" if p == "breezyhr" else p
        buckets[canonical].append((canonical, name, slug))
    output = []
    size = max((len(v) for v in buckets.values()), default=0)
    for i in range(size):
        for bucket in buckets.values():
            if i < len(bucket):
                output.append(bucket[i])
    return output


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
    parser.add_argument("--extra-boards", default="extra_boards.csv", help="CSV with additional platform/company/board_slug rows")
    args = parser.parse_args()
    if args.days < 1 or args.max_boards < 0 or args.workers < 1 or args.workers > 12:
        parser.error("days >= 1, max-boards >= 0, and 1 <= workers <= 12 are required")
    try:
        data = Path(args.directory).read_text(encoding="utf-8-sig") if args.directory else fetch(DIRECTORY_URL)
        boards = load_boards(data)
        # Additional boards are configured explicitly because the default directory
        # has mostly Greenhouse/Ashby. This avoids inventing company identifiers.
        extra_path = Path(args.extra_boards)
        if extra_path.exists():
            boards.extend(load_boards(extra_path.read_text(encoding="utf-8-sig")))
        boards = list(dict.fromkeys(boards))
    except (ValueError, KeyError, OSError, HTTPError, URLError) as exc:
        sys.exit(f"Unable to load board directory: {exc}. Pass --directory path/to/file.csv")
    boards = interleave(boards)
    boards = boards[:args.max_boards] if args.max_boards else boards
    from collections import Counter
    print(f"Checking {len(boards)} company boards in last {args.days} calendar day(s); platforms: {dict(Counter(b[0] for b in boards))}", flush=True)
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
    Path(f"{args.output}_errors.txt").write_text("\n".join(errors) if errors else "No board errors.\n", encoding="utf-8")
    print("NOTE: Country hints are not a guarantee of hiring eligibility. Read each listing before applying.")

if __name__ == "__main__":
    main()
