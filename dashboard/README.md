# Job Radar

A personal job dashboard built on the hosted ats-scrapers dataset.

Every day the **Job Radar** workflow does three things. It downloads the
slices of the dataset listed under `sources`: about 37 ATS platforms,
big-tech career APIs and startup boards. It keeps the roles that
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
| `sources` | Which dataset slices to pull (see the [live manifest](https://storage.stapply.ai/jobhive/v1/manifest.json) for all of them). A source that fails to download is skipped with a warning. |
| `title_include` / `title_exclude` | Whole-word title keywords. A role must hit one include term and no exclude term. |
| `india_terms`, `home_city_terms` | Locations kept, and which ones count as "Bengaluru". |
| `include_remote` | Keep remote roles you can do from India (see below). |
| `include_abroad_with_support` | Keep jobs outside India when they offer visa sponsorship or relocation. They show under **Where → Abroad**. |
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

## Where a job counts as

- **Bengaluru / Other India**: the location names an Indian city or India
  (multi-location posts show the Indian ones first).
- **Remote**: the location is only "Remote" (or names APAC, Asia or worldwide)
  and the description doesn't tie it to another region, e.g. "must be based
  in the US" or "authorized to work in the UK". The dataset's own remote flag
  isn't trusted on its own; it often marks office jobs as remote.
- **Abroad**: any other location, kept only when the description offers visa
  sponsorship or relocation. It's hidden unless you pick **Abroad**.

Visa and relocation are detected from the description text, with negations
handled ("visa sponsorship is not available", "unable to sponsor"). It's
keyword-based, so always confirm on the posting. The cases are covered by
`test_build_jobs.py`, which runs before every build.

## Your statuses

Saved, Applied, Hide and Mute company are stored in your browser
(`localStorage`). They are per device. Use **Export / Import** at the bottom
of the page to move them between your laptop and phone.

## Check without publishing

Actions → Job Radar → Run workflow → tick **dry_run**. It builds the feed and
posts a summary (counts per source and place, top remote/abroad locations) on
the run page, without deploying.

## Run locally

```bash
pip install httpx pyarrow
python dashboard/build_jobs.py
python -m http.server -d dashboard/site 8000   # then open http://localhost:8000
```
