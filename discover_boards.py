#!/usr/bin/env python3
"""Auto-discover known ATS boards from independently maintained worldwide directories.

This is NOT a list of all ATS customers. It downloads worldwide public-source
snapshots, merges them with an optional personal override, and records counts
and failures so missing coverage is visible, not silently reported as zero jobs.
"""
import argparse
from collections import Counter
import csv
import io
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# Public datasets: StoreSignal CC BY 4.0; veresk06 MIT repository; Riley-Dorrington
# curated datasets CC BY-NC 4.0 (personal, noncommercial use only).
SOURCES = {
    "storesignal": "https://raw.githubusercontent.com/storesignalhq/ats-job-boards/main/boards.csv",
    "openats": "https://raw.githubusercontent.com/veresk06/open-ats-feed/main/actor/data/companies.json",
    "workday": "https://raw.githubusercontent.com/Riley-Dorrington/job-board-aggregator/main/data/workday_companies.json",
    "bamboohr": "https://raw.githubusercontent.com/Riley-Dorrington/job-board-aggregator/main/data/bamboohr_companies.json",
}
SUPPORTED = {"greenhouse", "ashby", "workable", "smartrecruiters", "recruitee", "breezy", "workday", "bamboohr", "rippling"}
# Rippling has no public customer directory: boards come from a committed seed list
# (harvested from the Common Crawl URL index for ats.rippling.com) plus extra_boards.csv.
SEED_FILES = ("rippling_boards.csv",)
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}$")
WORKDAY_TENANT = re.compile(r"^wd\d+$", re.I)


def valid(platform, slug):
    if platform == "workday":
        p = urlparse(slug)
        return p.scheme == "https" and bool(re.fullmatch(r"[a-z0-9-]+\.wd\d+(?:\.myworkdayjobs\.com|\.myworkdaysite\.com)", p.hostname or "")) and bool(p.path.strip("/"))
    return bool(SLUG.fullmatch(slug))


def add(out, platform, company, slug):
    platform = str(platform or "").strip().lower().replace(" ", "").replace("_", "")
    if platform in ("breezyhr", "breezy"):
        platform = "breezy"
    if platform not in SUPPORTED or not slug:
        return
    slug = str(slug).strip().rstrip("/")
    if platform == "workday" and "|" in slug:
        parts = slug.split("|")
        if len(parts) != 3 or not all(SLUG.fullmatch(x) for x in parts):
            return
        tenant, instance, site = parts
        if WORKDAY_TENANT.match(tenant) or not instance.startswith("wd"):
            return
        slug = f"https://{tenant}.{instance}.myworkdayjobs.com/{site}"
    if not valid(platform, slug):
        return
    out[(platform, slug.lower())] = (platform, str(company or slug).strip(), slug)


def parse_storesignal(text):
    out = {}
    for item in csv.DictReader(io.StringIO(text)):
        add(out, item.get("ats"), item.get("company_name"), item.get("slug"))
    return out


def parse_riley(text, platform):
    out = {}
    data = json.loads(text)
    if isinstance(data, dict):
        # Tolerate either a flat list or a mapping, without guessing tokens.
        data = data.get("companies") or data.get("boards") or data.get("data") or []
    for item in data:
        if isinstance(item, str):
            slug = item
            company = item.split("|")[0]
        elif isinstance(item, dict):
            slug = item.get("slug") or item.get("board_slug") or item.get("url") or item.get("id")
            company = item.get("company") or item.get("name") or slug
        else:
            continue
        add(out, platform, company, slug)
    return out


def parse_openats(text):
    """Read veresk06's actual {providers: {ats: {live: [[slug,count], ...]}}}.

    Also accepts simple flat lists and legacy maps for forward compatibility.
    Counts are historical and are never treated as today's job count.
    """
    out = {}
    data = json.loads(text)
    if isinstance(data, dict) and isinstance(data.get("providers"), dict):
        for platform, provider in data["providers"].items():
            if not isinstance(provider, dict):
                continue
            for row in provider.get("live", []):
                if isinstance(row, (tuple, list)) and row:
                    slug = row[0]
                    add(out, platform, slug, slug)
                elif isinstance(row, str):
                    add(out, platform, row, row)
        return out

    if isinstance(data, list):
        pairs = [(None, x) for x in data]
    elif isinstance(data, dict):
        pairs = []
        for k, v in data.items():
            if isinstance(v, list):
                pairs.extend((k, item) for item in v)
            elif isinstance(v, dict):
                pairs.extend((k, {"slug": sk, **(val if isinstance(val, dict) else {})}) for sk, val in v.items())
    else:
        pairs = []
    for default_platform, item in pairs:
        if isinstance(item, str):
            add(out, default_platform, item, item)
        elif isinstance(item, dict):
            p = item.get("ats") or item.get("provider") or item.get("platform") or item.get("type") or default_platform
            slug = item.get("slug") or item.get("token") or item.get("board_slug") or item.get("company_slug")
            if not slug and item.get("url"):
                parsed = urlparse(item["url"])
                slug = parsed.hostname.split(".")[0] if p in ("breezy", "recruitee") and parsed.hostname else parsed.path.strip("/").split("/")[0]
            add(out, p, item.get("company_name") or item.get("name"), slug)
    return out


def fetch(url):
    req = Request(url, headers={"User-Agent": "WorldwideATSCollector/3.0 (public source attribution)", "Accept": "text/plain,application/json,text/csv"})
    with urlopen(req, timeout=90) as response:
        return response.read().decode("utf-8-sig")


def read_local(out, path):
    for row in csv.DictReader(Path(path).open(encoding="utf-8-sig", newline="")):
        add(out, row.get("platform") or row.get("ats"), row.get("company") or row.get("company_name"), row.get("board_slug") or row.get("slug"))


def build(fetcher=fetch, extra_path="extra_boards.csv", failures=None, seed_paths=SEED_FILES):
    failures = failures if failures is not None else []
    out = {}
    for source, url in SOURCES.items():
        try:
            contents = fetcher(url)
            parsed = (parse_storesignal(contents) if source == "storesignal" else
                      parse_openats(contents) if source == "openats" else
                      parse_riley(contents, source))
            if not parsed:
                raise ValueError("source returned no recognizable boards")
            out.update(parsed)
            print(f"Discovered {len(parsed):,} entries from {source}", flush=True)
        except Exception as error:
            failures.append(f"{source}: {type(error).__name__}: {str(error)[:160]}")
            print(f"WARNING: {failures[-1]}", flush=True)
    for seed in seed_paths:
        if Path(seed).is_file():
            before = len(out)
            read_local(out, seed)
            print(f"Loaded {len(out) - before:,} new entries from {seed}", flush=True)
        else:
            failures.append(f"{seed}: seed file missing")
    if extra_path and Path(extra_path).is_file():
        read_local(out, extra_path)
    return sorted(out.values(), key=lambda x: (x[0], x[2].lower()))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default="discovered_boards.csv")
    ap.add_argument("--extra-boards", default="extra_boards.csv", help="Optional extra boards to supplement global discovery")
    ap.add_argument("--minimum", type=int, default=500, help="Fail if fewer than N boards discovered (default 500)")
    args = ap.parse_args()
    failures = []
    boards = build(extra_path=args.extra_boards, failures=failures)
    with open(args.output, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(("platform", "company", "board_slug"))
        writer.writerows(boards)
    counts = dict(Counter(row[0] for row in boards))
    Path("discovery_summary.json").write_text(json.dumps({"boards":len(boards), "platform_counts":counts, "source_failures":failures, "directory_is_exhaustive":False}, indent=2), encoding="utf-8")
    print(f"Wrote {len(boards):,} discovered boards to {args.output}: {counts}", flush=True)
    if len(boards) < args.minimum:
        raise SystemExit(f"Only {len(boards)} boards discovered; sources may have failed. See discovery_summary.json; required >= {args.minimum}")


if __name__ == "__main__":
    main()
