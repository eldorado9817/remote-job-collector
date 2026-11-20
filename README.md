# US + UK remote engineering jobs — 8 ATS platforms

This package upgrades the existing Greenhouse + Ashby collector with **Workable, SmartRecruiters, Recruitee, Breezy HR, Workday, and BambooHR**.

## Schedule and result locations

- Runs **9:00 AM and 5:00 PM America/Los_Angeles** every day (PST/PDT automatically).
- GitHub repository **Code → `jobs_all.csv`** is the cumulative history. `first_seen_utc` is when the collector first saw the role, not the employer's posting date.
- Each run also uploads **Actions → selected workflow run → Artifacts** containing `jobs_all.csv`, `jobs_us_uk_remote.csv`, `jobs_review_location.csv`, and `jobs_errors.txt` (14-day retention).
- Manual run: **Actions → US and UK Remote Jobs → Run workflow**.

## Upgrade steps

1. Extract ZIP. Copy these files into your GitHub repository, preserving paths: `collect_remote_jobs.py`, `merge_job_results.py`, `extra_boards.csv`, `.github/workflows/daily-remote-jobs.yml`.
2. Merge `.gitignore` if your repository already has one.
3. Commit to the repository's default branch. Ensure repository GitHub Actions workflow has **Read and write permissions** so it can commit `jobs_all.csv` (`contents: write` is already requested in YAML; your organization may impose restrictions).
4. Run the workflow manually once and view artifacts and `jobs_all.csv`.

## How to add companies for the six new ATS platforms

Edit `extra_boards.csv` with columns `platform,company,board_slug`. Each row is a *known* company careers board, not a keyword search. Examples in the file include some public sample boards and a full Workday URL.

```
platform,company,board_slug
workable,Hugging Face,huggingface
smartrecruiters,Bosch,BoschGroup
recruitee,bunq,bunq
breezy,Breezy HR,breezy
workday,NVIDIA,https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite
bamboohr,Your BambooHR Company,company-subdomain
```

The **BambooHR line is illustrative**, so replace `company-subdomain` with the actual prefix from `https://company-subdomain.bamboohr.com/careers`. For Workday you **must provide the real full careers URL** (`https://tenant.wd5.myworkdayjobs.com/SITE`), rather than just a company name. Some Workday sites use slightly different configurations and may need a tailored adapter. Use a unique company row per board. Do not guess board identifiers.

The bundled original public directory currently provides mostly Greenhouse and Ashby boards. **Only new-platform boards that are actually present in `extra_boards.csv` (or a supplied `--directory` CSV) get scanned**. Adding platform integrations by itself does not create a worldwide company directory. Add additional verified boards to expand coverage. You may supply your own eight-platform directory with `platform,company,board_slug` fields using `--directory path.csv`.

## Local usage

Uses **Python 3.11+** and standard library only; no API keys or `pip install` needed.

```
python collect_remote_jobs.py --days 1 --max-boards 0 --workers 4
python merge_job_results.py
```

Arguments: `--days 1` today only; `--days 3` three calendar days. `--max-boards 0` all configured boards; `--workers 4` concurrency. Use a small number of workers to respect public boards.

## Posting dates and caveats

| ATS | Public source | Date field |
| --- | --- | --- |
| Greenhouse | public board jobs API | `first_published` |
| Ashby | posting API | `publishedAt` (may change after republishing) |
| Workable | widget accounts API | `published_on` |
| SmartRecruiters | postings API (offset pagination) | `releasedDate` |
| Recruitee | public offers feed | `published_at` |
| Breezy HR | public `/json` feed | `published_date` |
| Workday | CXS job details | `jobPostingInfo.startDate` when supplied |
| BambooHR | public careers job detail | `datePosted` when supplied |

**Date-only values are compared as calendar dates**, with `date_source` indicating date precision. Missing or relative-only Workday dates are skipped instead of silently treating an updated/created date as a published date. A site may require auth, use another schema, remove fields, or rate-limit; see `jobs_errors.txt`.

**Remote-country matching is deliberately conservative but imperfect.** A US/UK location marker is not proof an employer accepts applicants living there. Unspecified remote geography goes into `jobs_review_location.csv`. Workday and BambooHR may provide weak remote flags; some real remote jobs will be missed. Always verify geography and work eligibility on the application page.

The collector stores roles passing the **publish-date window** rather than every active role. The cumulative `jobs_all.csv` retains historically found jobs, including positions that may subsequently close. New platform-specific date sources are recorded in the `date_source` column.

## Source references

- Greenhouse and Ashby sample board directory: https://github.com/fyrosofttech/lastroundai-hiring-data/tree/main/ats-directory
- Workable public API: https://workable.readme.io/reference/jobs-1
- SmartRecruiters postings: https://developers.smartrecruiters.com/reference/v1listpostings
- Recruitee offers: https://docs.recruitee.com/reference/offers
- Other public endpoints and caveats: https://github.com/Mersetti/ats-job-board-apis
- GitHub workflows: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule

## Validation

Python syntax and locally mocked examples across all eight ATS adapters were tested. The GitHub run and each company's current live responses **have not** been end-to-end verified. Some sample boards might have zero recent jobs or may change their APIs.
