# Free GitHub Actions job collector — US and UK remote engineering

Runs `collect_remote_jobs.py` once a day at **7:17 PM America/Los_Angeles**, including automatic daylight-saving adjustments. You can also trigger it manually.

## Setup (GitHub.com)

1. Make a new repository. Public repositories use standard GitHub-hosted runners for free. Private repositories have a monthly compute allowance; check your plan.
2. Upload `collect_remote_jobs.py` into the repository root.
3. Create `.github/workflows/daily-remote-jobs.yml` using the corresponding file provided here. If GitHub's browser uploader doesn't preserve `.github/workflows/`, create the file via **Add file → Create new file**, entering the full path as the filename.
4. In **Actions**, select **US and UK Remote Jobs**, choose **Run workflow**, and then view the run.
5. In the completed run, scroll to **Artifacts** and download the `us-uk-remote-jobs-*` ZIP. It contains CSV files with results.

## Details

- The script uses Python's standard library, so no `pip install` is necessary.
- Default mode scans all company boards from the linked public ATS directory (approximately several thousand), using 4 workers. If too slow or rate-limited, change `--max-boards 0` to `--max-boards 500` to try a smaller sample.
- `--days 1` means postings dated *today* in US Pacific or London time as appropriate.
- Output CSVs: `jobs_us_uk_remote.csv` and `jobs_review_location.csv`. Errors, when present, go to `jobs_errors.txt`.
- The scheduled job can start late during periods of high GitHub Actions load. In public repositories, scheduled workflows can be automatically disabled after 60 days of repository inactivity.
- Results are uploaded as GitHub Actions run artifacts for 14 days. They're **not automatically added to your repository**, and later jobs don't retain the previous run's state. For historical first-seen timestamps, add persistent storage separately.
- Public repos expose workflow code and potentially artifacts. If you want to keep job targeting private, use a private repository and stay within your free usage allowance.
- Respect Greenhouse/Ashby rate limits and terms; the directory may contain inactive company boards. No guarantee that all current jobs are captured.
- Date fields have platform-specific semantics. Ashby `publishedAt` may reflect republication, and work-eligibility hints need verification against the job description.

Original board directory source (CC BY 4.0): https://github.com/fyrosofttech/lastroundai-hiring-data/tree/main/ats-directory

Greenhouse API: https://docs.greenhouse.io/job-board

Ashby public API: https://developers.ashbyhq.com/docs/public-job-posting-api
