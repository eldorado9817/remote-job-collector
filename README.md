# US + UK remote jobs: persistent GitHub CSV

This is an update to your existing Greenhouse + Ashby collector. It runs automatically **every day at 9:00 AM and 5:00 PM America/Los_Angeles** (PST/PDT adjusted automatically). You can also run it manually from GitHub's **Actions** tab.

## What's new

- After every successful scan, GitHub Actions merges matches into **`jobs_all.csv`** at the repository root and commits it to your repository. Open this file on the **Code** tab to view your results without downloading artifacts.
- The CSV includes job title, company, posting date, remote geography hint, original application URL, and when the collector first/last saw that job.
- Jobs are deduplicated by platform and job ID across runs; old jobs remain in the CSV even after they fall outside the 1-day search window. The `last_seen_utc` field means last time found in a scan, **not proof that the job remains open**.
- Location-uncertain remote jobs are included in the **same** CSV, labeled `Location needs review`.
- The scan-specific CSVs are still available as 14-day Actions artifacts for troubleshooting.

## Install or upgrade

1. Open your GitHub repository's **Code** tab.
2. Upload or update **`collect_remote_jobs.py`**, **`merge_job_results.py`**, and **`.gitignore`** in the root of the repository. If `.gitignore` already exists, merge its contents rather than replacing your other ignore rules.
3. Replace **`.github/workflows/daily-remote-jobs.yml`** with the version included in this ZIP.
4. Commit the changes on the repository's **default branch**. The workflow requires the repository's GitHub Actions token to be able to write repository contents. Its YAML explicitly requests `contents: write`; if an organization restricts the token from writing, an administrator must permit it.
5. Run **Actions → US and UK Remote Jobs → Run workflow** once to test the setup.
6. After it finishes, go to the **Code** tab and open **`jobs_all.csv`**. Subsequent scheduled scans automatically update the same file.

## Output columns

- `platform`, `company`, `title`, `country`, `location_check`
- `published_local`, `published_utc`, `location`
- `job_url`, `board_slug`, `job_id`
- `first_seen_utc`, `last_seen_utc`

## Caveats

- These are location hints, not verification of eligibility to work from a specific country. Verify a job's requirements before applying.
- `first_seen_utc` means the first time **your collector observed** the job, not when the employer first posted it.
- The source script uses Greenhouse `first_published` and Ashby `publishedAt`, which have different semantics. Republishing can change Ashby dates.
- Scheduled runs may be delayed. If a scan fails, it will not overwrite the persistent CSV; see the failed run's logs and backup artifacts.
- Jobs remain in `jobs_all.csv` after later scans; it is a cumulative history, not a list of only currently open jobs.
- A large historical CSV can increase repository size over time. For large-scale scanning, a database is more scalable.
- Public GitHub repositories make `jobs_all.csv` publicly visible. Use a private repository if you need privacy (subject to free action-minute allowances).

Directory credit: https://github.com/fyrosofttech/lastroundai-hiring-data/tree/main/ats-directory

GitHub scheduling documentation: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule
