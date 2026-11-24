#!/usr/bin/env python3
"""Collect US/UK fully remote software engineering and AI jobs published today PT.

Company boards are discovered worldwide, but results require a verified publication
date in America/Los_Angeles and US/UK location indications. Source coverage is not exhaustive.
"""

import argparse
import csv
import html
import http.client
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
from datetime import timezone
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET
from contextlib import closing

DIRECTORY_URL = (
    "https://raw.githubusercontent.com/fyrosofttech/lastroundai-hiring-data/main/"
    "ats-directory/lastroundai-ats-company-directory-2026-08.csv"
)
USER_AGENT = "USUK-Remote-Software-AI-Today/5.0 (public career feeds)"
PACIFIC = ZoneInfo("America/Los_Angeles")
SETTINGS = {"scope": "usuk", "roles": "software-ai", "workplace": "remote", "allow_undated": False, "all_dates": False, "reference_date": None}
FIELDS = ["platform", "company", "title", "country", "published_local",
          "published_utc", "published_date_pt", "location", "job_url", "board_slug", "job_id", "date_source"]
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
# Titles directly associated with building software or AI/ML systems, including AI training roles.
# Avoid matching generic engineering, marketing and sales titles without relevant qualifiers.
SOFTWARE_AI_RE = re.compile(
    r"\b(?:software\s+(?:engineer|developer|architect|engineering|development|" 
    r"engineering\s+manager)|" 
    r"back[ -]?end|front[ -]?end|full[ -]?stack|fullstack|" 
    r"(?:python|java|javascript|typescript|react|node\.?js|go|golang|ruby|rails|django|"
    r"spring boot|\.net|c#|c\+\+|rust|kotlin|swift|api|application|web|"
    r"mobile|ios|android)\s+(?:developer|engineer|architect)|"
    r"(?:platform|devops|site reliability|infrastructure|cloud|security|data|"
    r"systems|distributed systems|reliability)\s+(?:engineer|developer|architect)|"
    r"\bsre\b|devops|\bmlops\b|"
    r"(?:machine learning|deep learning|artificial intelligence|generative ai|genai|"
    r"\bai\b|\bml\b|\bllm\b|\bnlp\b|computer vision|"
    r"language model|foundation model|model training|reinforcement learning)"
    r"(?:[ /-]*(?:engineer|developer|scientist|researcher|research|architect|"
    r"trainer|evaluator|annotator|specialist|lead|manager|intern))?|"
    r"(?:research|applied)\s+(?:scientist|engineer)|"
    r"(?:prompt|ai product|ai solutions)\s+(?:engineer|developer|manager)|"
    r"(?:data|machine learning)\s+scientist|"
    r"(?:engineering|technical)\s+lead)\b", re.I,
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
GLOBAL_RE = re.compile(r"\b(worldwide|anywhere in the world|globally remote|global remote|remote worldwide|work from anywhere)\b", re.I)


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
        except (http.client.HTTPException, OSError) as exc:
            # Truncated chunked bodies (IncompleteRead), dropped connections
            # (RemoteDisconnected, ConnectionResetError) and similar socket errors.
            if attempt < retries:
                time.sleep(min(2 ** attempt + 1, 5))
                continue
            # Re-raise as URLError so every caller's existing handlers catch it.
            raise URLError(f"{type(exc).__name__}: {exc}") from exc


def get_json(url, payload=None):
    return json.loads(fetch(url, payload=payload))


def plain(raw):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw or ""))).strip()


def today_pacific():
    """Local publication day is Pacific for US and UK jobs alike."""
    return SETTINGS.get("reference_date") or datetime.now(PACIFIC).date()


def date_recent(timestamp, tz, days):
    """Accept absolute timestamps only when their Pacific local date is in range."""
    if not timestamp:
        return None
    try:
        dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if not dt.tzinfo:
            return None
        local = dt.astimezone(PACIFIC)
        today = today_pacific()
        if SETTINGS.get("all_dates") or today - timedelta(days=days - 1) <= local.date() <= today:
            return local.isoformat(timespec="seconds")
    except (ValueError, OverflowError, TypeError):
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
    # Explicit worldwide eligibility includes applicants in both target countries.
    # Do not infer this from a generic company-description mention of global offices.
    if GLOBAL_RE.search(locations) or re.search(
        r"\b(?:this (?:role|position) is (?:fully )?remote worldwide|"
        r"work (?:remotely )?from anywhere in the world|"
        r"we hire (?:remotely )?worldwide)\b", desc, re.I
    ):
        return "US & UK"
    return "Review: location unspecified"


def role_ok(title):
    role = SETTINGS["roles"]
    return role == "all" or bool((SOFTWARE_AI_RE if role == "software-ai" else ROLE_RE).search(title or ""))


def remote_ok(title, location, description="", structured=None):
    if SETTINGS["workplace"] == "any":
        return True
    if str(structured or "").lower() in ("hybrid", "onsite", "on-site", "on_site"):
        return False
    if NON_REMOTE_RE.search(f"{title} {location}"):
        return False
    if structured is True or str(structured or "").strip().lower() in ("remote", "fully remote", "100% remote"):
        return True
    if REMOTE_RE.search(f"{title} {location}"):
        return True
    # A random "remote" mention in the benefits section is insufficient.
    intro = description[:1600]
    return bool(re.search(
        r"\b(?:fully remote|100% remote|entirely remote|remote-only|"
        r"remote position|remote role|work from home|work from anywhere|"
        r"this position is remote|this role is remote)\b", intro, re.I
    ))


def target_zones(country):
    # A single time zone for today's publication window, regardless of job country.
    return (("PT", "America/Los_Angeles"),)


def make_undated(platform, company, title, country, location, url, slug, jid):
    if not SETTINGS["allow_undated"]:
        return []
    return [dict(platform=platform, company=company, title=title, country=country,
                 published_local="", published_utc="", published_date_pt="", location=location,
                 job_url=url, board_slug=slug, job_id=str(jid), date_source="unavailable")]


def normalize_greenhouse(company, slug, job, days):
    title = job.get("title", "")
    if not role_ok(title):
        return []
    location = (job.get("location") or {}).get("name", "")
    description = plain(job.get("content", ""))
    if not remote_ok(title, location, description):
        return []
    country = geography([location], description)
    out = []
    if not job.get("first_published"):
        return make_undated("Greenhouse", company, title.strip(), country, location,
                            job.get("absolute_url", ""), slug, job.get("id", ""))
    for target, tz in target_zones(country):
        local = date_recent(job.get("first_published"), tz, days)
        if local:
            out.append(dict(platform="Greenhouse", company=company, title=title.strip(),
                            country=country, published_local=local, published_utc=job["first_published"],
                            published_date_pt=local[:10], location=location, job_url=job.get("absolute_url", ""),
                            board_slug=slug, job_id=str(job.get("id", "")), date_source="first_published"))
            break
    return out


def normalize_ashby(company, slug, job, days):
    title = job.get("title", "")
    if not role_ok(title) or job.get("isListed") is False:
        return []
    # Ashby's isRemote=true can include HYBRID. workplaceType must be Remote.
    if not remote_ok(title, job.get("location", ""), structured=job.get("workplaceType")):
        return []
    locations = [job.get("location", "")]
    locations += [x.get("location", "") for x in job.get("secondaryLocations", [])]
    primary_country = ((job.get("address") or {}).get("postalAddress") or {}).get("addressCountry", "")
    locations.append(primary_country)
    for secondary in job.get("secondaryLocations", []):
        locations.append((secondary.get("address") or {}).get("addressCountry", ""))
    description = plain(job.get("descriptionPlain") or job.get("descriptionHtml", ""))
    country = geography(locations, description)
    if not job.get("publishedAt"):
        return make_undated("Ashby", company, title.strip(), country, job.get("location", ""),
                            job.get("jobUrl", ""), slug, job.get("jobUrl", ""))
    for target, tz in target_zones(country):
        local = date_recent(job.get("publishedAt"), tz, days)
        if local:
            return [dict(platform="Ashby", company=company, title=title.strip(),
                         country=country, published_local=local, published_utc=job["publishedAt"],
                         published_date_pt=local[:10], location=job.get("location", ""), job_url=job.get("jobUrl", ""),
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
        try:
            dt = parsedate_to_datetime(txt)
            return (dt, "timestamp") if dt.tzinfo else None
        except (TypeError, ValueError, IndexError):
            return None


def normalize_generic(platform, company, slug, job, days):
    title = str(job.get("title") or "")
    if not role_ok(title):
        return []
    location = str(job.get("location") or "")
    description = plain(str(job.get("description") or ""))
    workplace = job.get("workplace")
    # Explicit hybrid/on-site metadata overrides a separate is_remote flag.
    arrangement = workplace if str(workplace or "").strip() else (True if job.get("remote") is True else None)
    if not remote_ok(title, location, description, structured=arrangement):
        return []
    country = geography([location, str(job.get("other_locations") or "")], description)
    parsed = parse_posting_date(job.get("date"))
    if not parsed:
        return make_undated(platform, company, title.strip(), country, location,
                            str(job.get("url") or ""), slug, job.get("id") or job.get("url") or "")
    dt, precision = parsed
    for target, tz in target_zones(country):
        local = dt.astimezone(PACIFIC)
        today = today_pacific()
        # Date-only values express a calendar date with no timezone: compare directly.
        pub_date = dt.date() if precision == "date" else local.date()
        if SETTINGS.get("all_dates") or today - timedelta(days=days - 1) <= pub_date <= today:
            return [dict(platform=platform, company=company, title=title.strip(),
                         country=country, published_local=pub_date.isoformat() if precision == "date" else local.isoformat(timespec="seconds"),
                         published_utc=dt.date().isoformat() if precision == "date" else dt.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds"),
                         published_date_pt=pub_date.isoformat(), location=location, job_url=str(job.get("url") or ""),
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
        remote = isinstance(loc, dict) and loc.get("is_remote") is True
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
        if not title or not role_ok(title):
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
    if u.scheme != "https" or not (u.hostname or "").endswith((".myworkdayjobs.com", ".myworkdaysite.com")):
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
            if not role_ok(title):
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


def jobs_workable_global(days):
    """Official Workable hourly global XML feed; stream, never load 140+MB into RAM.

    It includes jobs published to Jobs by Workable, not jobs opted out of that board.
    """
    feed_url = "https://www.workable.com/boards/workable.xml"
    rows, visited = [], 0
    req = Request(feed_url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml"})
    with closing(urlopen(req, timeout=240)) as stream:
        context = ET.iterparse(stream, events=("start", "end"))
        _, root = next(context)
        for event, elem in context:
            if event != "end" or elem.tag.rsplit("}", 1)[-1].lower() != "job":
                continue
            visited += 1
            fields = {x.tag.rsplit("}", 1)[-1].lower(): (x.text or "").strip() for x in elem}
            title = fields.get("title", "")
            if role_ok(title):
                company = fields.get("company", "Unknown Workable company")
                location = ", ".join(x for x in (fields.get("city"), fields.get("state"), fields.get("country")) if x)
                date_raw = fields.get("date") or ""
                try:
                    dt = parsedate_to_datetime(date_raw)
                    date_raw = dt.isoformat() if dt.tzinfo else ""
                except (ValueError, TypeError, IndexError):
                    date_raw = ""
                rows += normalize_generic("Workable", company, "workable_global_feed", dict(
                    title=title, location=location, description=fields.get("description", ""),
                    remote=fields.get("remote", "").lower() == "true",
                    date=date_raw, date_source="workable.xml date",
                    url=fields.get("url", ""), id=fields.get("referencenumber") or fields.get("url", "")), days)
            root.clear()
    print(f"Workable worldwide feed: parsed {visited:,} listings; {len(rows):,} matches", flush=True)
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


def jobs_greenhouse(company, slug, days):
    """Greenhouse's list endpoint does NOT reliably contain first_published.

    Official API documents the timestamp in the per-job detail endpoint.
    Fetch detail only for candidate remote software/AI jobs, avoiding date
    estimates based on updated_at (which is not a publication timestamp).
    """
    base = f"https://boards-api.greenhouse.io/v1/boards/{quote(slug)}/jobs"
    listings = get_json(base + "?content=true").get("jobs", [])
    rows = []
    for listing in listings:
        title = listing.get("title") or ""
        location = (listing.get("location") or {}).get("name", "")
        if not role_ok(title) or not remote_ok(title, location, plain(listing.get("content", ""))):
            continue
        jid = listing.get("id")
        if not jid:
            continue
        try:
            detail = get_json(base + f"/{quote(str(jid))}?content=true")
        except (ValueError, HTTPError, URLError, TimeoutError) as exc:
            print(f"WARNING: Greenhouse detail {slug}/{jid}: {str(exc)[:120]}", flush=True)
            continue
        rows.extend(normalize_greenhouse(company, slug, detail, days))
    return rows


def process_board(board, days):
    platform, company, slug = board
    try:
        if platform == "greenhouse":
            rows = jobs_greenhouse(company, slug, days)
        elif platform == "ashby":
            jobs = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{quote(slug)}").get("jobs", [])
            rows = [row for j in jobs for row in normalize_ashby(company, slug, j, days)]
        else:
            rows = ADAPTERS[platform](company, slug, days)
        return rows, None
    except (ValueError, KeyError, TypeError, HTTPError, URLError, TimeoutError) as exc:
        return [], f"{platform}/{slug}: {str(exc)[:180]}"
    except Exception as exc:  # never let one misbehaving board abort the whole shard
        return [], f"{platform}/{slug}: unexpected {type(exc).__name__}: {str(exc)[:160]}"


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
    parser.add_argument("--max-boards", type=int, default=0,
                        help="Maximum boards per shard to check, 0 = all (default 0)")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent requests; keep low (default 4)")
    parser.add_argument("--directory", help="Optional path to the ATS directory CSV, instead of downloading it")
    parser.add_argument("--output", default="jobs", help="CSV filename prefix (default jobs)")
    parser.add_argument("--extra-boards", default="extra_boards.csv", help="Optional extra board CSV")
    parser.add_argument("--scope", choices=("usuk", "worldwide"), default="usuk", help="Only US/UK eligible locations (default usuk)")
    parser.add_argument("--roles", choices=("software-ai", "engineering", "all"), default="software-ai", help="Software engineering and AI titles by default")
    parser.add_argument("--workplace", choices=("remote", "any"), default="remote", help="Fully remote only by default")
    parser.add_argument("--allow-undated", action="store_true", help="Keep jobs with no public date; label undated")
    parser.add_argument("--all-dates", action="store_true", help="Include older jobs; do not use for published-today collection")
    parser.add_argument("--reference-date", help="Optional YYYY-MM-DD Pacific date to scan for (reproducible backfills)")
    parser.add_argument("--workable-global", action="store_true", help="Read official worldwide Workable XML feed (only one shard)")
    parser.add_argument("--skip-workable-boards", action="store_true", help="Exclude individual Workable company boards from sharding; pass to every shard when using global feed")
    parser.add_argument("--shard-count", type=int, default=1, help="Rotate large board lists over N scans")
    parser.add_argument("--shard-index", type=int, default=0, help="Zero-based index among shards")
    args = parser.parse_args()
    if args.days < 1 or args.max_boards < 0 or args.workers < 1 or args.workers > 12 or args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("days >= 1, max-boards >= 0, 1 <= workers <= 12, and 0 <= shard-index < shard-count are required")
    if args.reference_date:
        try:
            ref = datetime.strptime(args.reference_date, "%Y-%m-%d").date()
        except ValueError:
            parser.error("--reference-date must be YYYY-MM-DD")
    else:
        ref = None
    SETTINGS.update(scope=args.scope, roles=args.roles, workplace=args.workplace,
                    allow_undated=args.allow_undated, all_dates=args.all_dates, reference_date=ref)
    print(f"Pacific date window: {today_pacific()} (America/Los_Angeles)", flush=True)
    try:
        data = Path(args.directory).read_text(encoding="utf-8-sig") if args.directory else fetch(DIRECTORY_URL)
        boards = load_boards(data)
        # Optional manual additions supplement, but do not replace, the
        # automatically discovered worldwide directory of public boards.
        extra_path = Path(args.extra_boards)
        if extra_path.exists():
            boards.extend(load_boards(extra_path.read_text(encoding="utf-8-sig")))
        boards = list(dict.fromkeys(boards))
    except (ValueError, KeyError, OSError, HTTPError, URLError) as exc:
        sys.exit(f"Unable to load board directory: {exc}. Pass --directory path/to/file.csv")
    boards = interleave(boards)
    if args.workable_global or args.skip_workable_boards:
        boards = [b for b in boards if b[0] != "workable"]
    total = len(boards)
    boards = [b for i, b in enumerate(boards) if i % args.shard_count == args.shard_index]
    boards = boards[:args.max_boards] if args.max_boards else boards
    print(f"World-wide discovered company boards: {total:,}; scan shard {args.shard_index + 1}/{args.shard_count}; current batch {len(boards):,}", flush=True)
    from collections import Counter
    window = "all publication dates" if args.all_dates else f"last {args.days} calendar day(s)"
    print(f"Checking {len(boards)} company boards ({window}); platforms: {dict(Counter(b[0] for b in boards))}", flush=True)
    found, errors = [], []
    if args.workable_global:
        try:
            found.extend(jobs_workable_global(args.days))
        except (HTTPError, URLError, TimeoutError, ET.ParseError, ValueError) as exc:
            errors.append(f"Workable global XML: {str(exc)[:200]}")
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(process_board, b, args.days) for b in boards]
        for n, fut in enumerate(as_completed(futures), 1):
            rows, error = fut.result()
            found.extend(rows)
            if error:
                errors.append(error)
            if n % 100 == 0:
                print(f"Checked {n}/{len(boards)} boards; candidate jobs: {len(found)}", flush=True)
    found = sorted({(r["platform"], r["board_slug"], r["job_id"]): r for r in found}.values(),
                   key=lambda r: r["published_utc"], reverse=True)
    if args.scope == "usuk" and args.workplace == "remote":
        verified = [r for r in found if r["country"] in ("US", "UK", "US & UK")]
        review = [r for r in found if r["country"].startswith("Review")]
    else:
        verified, review = [], found
    write_csv(f"{args.output}_us_uk_remote.csv", verified)
    write_csv(f"{args.output}_review_location.csv", review)
    write_csv(f"{args.output}_remote_candidates.csv", found)
    print(f"\nSaved {len(verified)} location-matched remote jobs to {args.output}_us_uk_remote.csv")
    print(f"Saved {len(review)} remote jobs requiring eligibility review to {args.output}_review_location.csv")
    print(f"Saved {len(found)} remote candidates to {args.output}_remote_candidates.csv")
    print(f"Errors: {len(errors)} boards. Public API may rate-limit or remove boards.")
    Path(f"{args.output}_errors.txt").write_text("\n".join(errors) if errors else "No board errors.\n", encoding="utf-8")
    print("NOTE: Country hints are not a guarantee of hiring eligibility. Read each listing before applying.")

if __name__ == "__main__":
    main()
