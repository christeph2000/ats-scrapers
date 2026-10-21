# Job Radar

A personal job dashboard built on the hosted ats-scrapers dataset.

Every morning the **Job Radar** workflow does three things. It downloads the
Greenhouse, Lever and Ashby slices of the dataset. It keeps the roles that
match `config.json`. Then it publishes a static page to GitHub Pages. There is
no server, database or API key.

## One-time setup

1. **Settings → Pages → Build and deployment → Source:** set it to *GitHub Actions*.
2. **Actions tab:** if GitHub says workflows are disabled on this fork, enable them.
3. **Actions → Job Radar → Run workflow:** this does the first build. It takes
   about 5 minutes.
4. Open `https://<your-username>.github.io/ats-scrapers/`.

After that it rebuilds daily at 06:47 IST, and again whenever you push a
change under `dashboard/`.

## Tuning what shows up

Edit `config.json` and push. The keys:

| Key | What it does |
|---|---|
| `sources` | Which dataset slices to pull. Others include `workday` (~480 MB) and `smartrecruiters`. |
| `title_include` / `title_exclude` | Whole-word title keywords. A role must hit one include term and no exclude term. |
| `india_terms`, `home_city_terms` | Locations kept, and which ones count as "Bengaluru". |
| `include_remote`, `remote_ok_terms`, `remote_block_terms` | Keeps remote roles unless they are tied to another region, e.g. "Remote - US". |
| `stack_tags` | Tags detected from the title and description. These become the Stack filter. |
| `target_companies` | Companies marked ★ on the page, with a ★ Targets filter. Matches whole words, so "UiPath" also matches "UiPath India". |
| `size_buckets` | Thresholds for the Company size filter (see below). |
| `max_age_days`, `max_jobs` | Drops stale postings and caps the feed size. |

## Dates: Posted vs Added

- **Posted** is the employer's own publish date from the ATS: Greenhouse
  `first_published`, Lever `createdAt`, Ashby `publishedAt`. Reposted jobs
  get a fresh date, so they can look new.
- **Added** is the run in which the job first showed up in *your* feed. Each
  run reads the previously published `jobs.json` and carries those dates
  forward. Jobs that were already open on the first run show **≤ <start date>**,
  because their real arrival date is unknown. **NEW** marks jobs added in
  the latest run.
- If the previous feed can't be read (anything other than "not published
  yet"), the build fails rather than resetting every Added date.

The hosted dataset is rebuilt about once a day (around 20:00 IST), so the
workflow runs at 21:37 IST to pick it up, plus a 06:47 IST catch-up run.

## Company size

The dataset has no headcount, so size is approximated by **hiring volume**:
how many roles the company has open across the downloaded sources, worldwide
and in all functions. The default buckets are Startup (<25), Mid-size (<250)
and Large (250+). It's a rough signal: a big company that only lists a few
roles on Greenhouse will look small.

## Your statuses

Saved, Applied, Hide and Mute company are stored in your browser
(`localStorage`). They are per device. Use **Export / Import** at the bottom
of the page to move them between your laptop and phone.

## Run locally

```bash
pip install httpx pyarrow
python dashboard/build_jobs.py
python -m http.server -d dashboard/site 8000   # then open http://localhost:8000
```
