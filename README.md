# US + UK **Remote Software & AI — Published Today** (9 ATS providers)

GitHub Actions collector for **software engineering and AI-related openings**, **fully remote**, with a public US or UK hiring-location indication, **published on the current Pacific calendar day**. Source company boards are discovered worldwide. Jobs with unverified dates or outside-US/UK location indications are **not** included in the confirmed-today CSV.

## Platforms and publication fields

| ATS | Checked publication date | Notes |
|---|---|---|
| Greenhouse | `first_published` | Requires the individual-job detail endpoint. `updated_at` is never substituted. |
| Ashby | `publishedAt` | Means **last published**, so a republished job can count as today. |
| Workable | `published_on`, or date in public worldwide XML | XML is a subset of Workable listings, not a global census. |
| SmartRecruiters | `releasedDate` | Uses paginated public company feeds. |
| Recruitee | `published_at` | When exposed on the company's public offer feed. |
| Breezy HR | `published_date` | When present in the public feed. |
| Workday | `jobPostingInfo.startDate` | Sometimes only a date (no time zone); do not invent a time. |
| BambooHR | `datePosted` | When available on job detail. |
| Rippling | `createdOn` | From the job detail endpoint (the only timestamp exposed). Creation time can precede publication, so a job published today but created earlier is missed; an old job is never reported as today. |

**Dates:** A timestamp must have a timezone to count. Its **Pacific local date** is compared to the date of the scan. A date-only field is compared as written, marked `(date only)` in `date_source`, because the ATS gives no timezone. Undated, unknown and older jobs do not appear in the confirmed-today file. Full publication-field availability and semantics vary between sites.

## Default filters

- `--scope usuk`: explicit US, UK, or worldwide remote hiring indication.
- `--workplace remote`: reject identified hybrid and on-site positions.
- `--roles software-ai`: relevant titles such as Software Engineer, Python Developer, React Developer, Backend/Full-Stack, DevOps, SRE, Platform, Cloud/Data Engineer, AI Engineer, ML Engineer, MLOps, AI Researcher, Data Scientist, Prompt Engineer, AI Trainer and AI Evaluation.
- `--days 1`: **today only**, using `America/Los_Angeles` for US **and** UK jobs.
- No `--all-dates`, `--allow-undated`, or `--roles all` switches in the workflow.

The classifier uses public job-title and ATS location clues and is a **best-effort** filter; read each job posting for hiring eligibility and mandatory time-zone/state constraints.

## Run schedule and coverage

Runs at **4:00 AM, 10:00 AM and 4:00 PM America/Los_Angeles** every day with GitHub Actions' IANA time-zone-aware cron, including PDT/PST transitions. Each run **attempts all discovered company boards** by launching **eight shards in parallel**. It does **not** scan one-eighth of boards every four days anymore. The large Workable worldwide XML is read once on shard 0. **Every shard** uses `--skip-workable-boards` to maintain identical directory indexing, preventing gaps in partitioning.

Worldwide company discovery uses public directories; `extra_boards.csv` is optional. Rippling publishes no customer directory, so its boards come from the committed `rippling_boards.csv` seed list (slugs harvested from the Common Crawl URL index for `ats.rippling.com` and checked against the public API). Add more Rippling boards there or in `extra_boards.csv` as `rippling,Company,slug`, where the slug is the path segment in `https://ats.rippling.com/<slug>/jobs`. No public source indexes all employers, and sites can rate-limit, block, remove feeds or omit publication fields. Coverage and successful scan counts are NOT guaranteed. Eight parallel runners can consume **substantially more free GitHub Actions minutes**, especially on a private repository. The job matrix has a five-hour per-shard safety timeout; some runs may fail or exceed a plan's included allowance. Please monitor **Actions → Usage**.

At 4 PM only jobs published up to that run are discoverable. Jobs published later in the Pacific day may be **missed** by the strict today-only filter at the next day's 4 AM run; consider an optional separate backfill scan if needed. Scheduled workflows may also start late.

## Deploy to your repository

1. Download the ZIP, extract it and copy its files into the repository. Preserve `.github/workflows/daily-remote-jobs.yml`.
2. Keep the repository's default branch with workflow permissions **Settings → Actions → General → Workflow permissions → Read and write permissions** if needed (the workflow declares `contents: write`).
3. Commit updated Python scripts, `extra_boards.csv` (optional), `tests/`, and the workflow file.
4. In GitHub click **Actions → US + UK Remote Software and AI Jobs — Posted Today → Run workflow**.
5. Wait for `discover`, all eight `scan` shards, and `aggregate` to complete. If a shard fails, combined results intentionally **do not** silently omit that shard; its logs/artifacts remain for diagnostics.

## Result files

| File | Meaning |
|---|---|
| **`jobs_us_uk_remote.csv`** | **Confirmed location-matched, remotely classified software/AI jobs published today Pacific** from all completed shards. This is your primary daily file. |
| `jobs_review_location.csv` | Posted today and remote technical roles, but the employer's US/UK location eligibility could not be confidently inferred. Not confirmed US/UK. |
| `jobs_remote_candidates.csv` | Both confirmed and location-review records from today's scan. |
| **`jobs_all.csv`** | Cumulative history of matching results from successful scans, committed into your repo. **Not** a today-only list; sort/filter `published_date_pt` for any specific day. |
| `jobs_errors.txt` | Public API errors from scan shards. |
| `discovered_boards.csv` | Discovered company boards, not job results. |
| `discovery_summary.json` | Board directory counts and source failures. |
| `scan_summary.json` | Shard completion, counts and platform breakdown. |

Download combined daily CSVs through **Actions → successful run → Artifacts → `today-us-uk-remote-software-ai-...`**, retention 14 days. Cumulative `jobs_all.csv` remains under the repo **Code** tab. Every shard also has a short-lived diagnostic artifact.

**Migration:** Earlier versions collected all occupations and/or older and undated jobs. To avoid mixing them into the new history, only confirmed US/UK software/AI rows with a publication date are kept. `jobs_all.csv` has six columns: `platform, company, title, country, published_date_pt, job_url` (deduplicated by `job_url`).

## Local run (sample: first shard)

```bash
python discover_boards.py --minimum 500
python collect_remote_jobs.py --directory discovered_boards.csv \
  --scope usuk --roles software-ai --workplace remote \
  --days 1 --max-boards 0 --workers 4 \
  --shard-count 8 --shard-index 0 --skip-workable-boards --workable-global
```

For a repeatable test on a particular date (still using Pacific timezone), append `--reference-date 2026-11-20`. If you run only one shard you do **not** have full discovered-board coverage. To scan the entire directory locally in a single process use `--shard-count 1 --shard-index 0`, but this can take hours.

Run local tests:

```bash
python -m unittest discover -s tests -v
```

Python 3.11+; no external Python libraries required. This is a tested *code package*; it has **not** been run end-to-end against every live provider from GitHub Actions. Respect public API terms and rate limits.
