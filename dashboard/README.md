# Job Radar

A job dashboard for engineers in India, built on the hosted ats-scrapers
dataset.

Twice a day the **Job Radar** workflow downloads the slices of the dataset
listed under `sources` (about 37 ATS platforms, big-tech career APIs and
startup boards). It keeps engineering roles in India, remote roles you can do
from India, and jobs abroad that offer visa sponsorship or relocation. Then it
publishes a static site to GitHub Pages. There is no server, database or API
key.

Each visitor picks their own **roles, city, default locations, experience
levels and target companies** in *Edit my setup*. That's stored only in their
browser, and the page downloads only the role files they picked.

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
| `roles` | Role name → whole-word title keywords. A job can be in several roles. `fallback: true` ("Software engineer (general)") only applies when no specific role matched. |
| `title_exclude` | Title phrases that are never engineering roles (sales engineer, recruiter …). |
| `cities`, `india_terms` | City name → spellings; these become the city picker. Any of them, or `india_terms`, makes a job count as India. |
| `include_remote` | Keep remote roles you can do from India (see below). |
| `include_abroad_with_support` | Keep jobs outside India when they offer visa sponsorship or relocation. They show under **Where → Abroad**. |
| `stack_tags` | Tags detected from the title and description. These become the Stack filter. |
| `size_buckets` | Thresholds for the Company size filter (see below). |
| `max_age_days`, `max_jobs` | Drops stale postings and caps the feed size. |
| `remember_missing_days` | How long a job that vanished is remembered, so it keeps its Added time if it comes back. |
| `default_profile` | The setup a first-time visitor starts from. |

## Fresh jobs (posted in the last hours)

The daily feed can be up to a day behind. For jobs posted minutes ago, open
**⚡ Fresh jobs** (`/fresh.html`) and press **Check again on GitHub**, which
opens **Actions → Job Radar – Fresh → Run workflow**. It also works from the
GitHub mobile app.

That workflow asks the job boards directly (their public posting APIs, via
this repo's own scrapers): **Greenhouse, Lever, Ashby, SmartRecruiters,
Recruitee, Workable, Keka and Darwinbox**. It keeps jobs posted in the last 24 h
that pass the same role / location / visa rules. Which companies it checks is
set in the `fresh` section of `config.json`:

| Key | Companies |
|---|---|
| `feed_sources` | every company on these platforms that has a job in the Job Radar feed (~1,200) |
| `all_companies_sources` | every company in `ats-companies/<platform>.csv` (Keka, Darwinbox: small, India-heavy lists) |
| `presence_sources` | every company with *any* India/remote posting in the dataset. **Off by default:** a measured run checked 1,860 extra companies for just 3 extra jobs a day |
| `extra_companies` | your own additions, e.g. `"greenhouse:razorpay"`, `"smartrecruiters:Visa"` |

A measured run found about **11 matching jobs in the last hour and 150 in
24 h** (SmartRecruiters alone ~40/day). Expect hour-to-hour swings: many
companies post during US working hours. Workable and Darwinbox sometimes only
give a posting *date*; those jobs show as "Today / Yesterday" and only in the
24 h view. A run takes a few minutes, plus GitHub's queue. The fresh page
refreshes itself while open, shows how many minutes ago each job was posted,
and filters to 1 / 3 / 6 / 24 h using your setup and statuses from the main page.

Workday and the other sources still update only through the daily feed. The
two workflows share one queue, so they never publish over each other, and the
daily build carries the latest fresh list forward. Please run it on demand
rather than on a tight schedule: each run makes a few thousand requests to
those job boards.

## Dates: Posted vs Added

- **Posted** is the employer's own publish date from the ATS: Greenhouse
  `first_published`, Lever `createdAt`, Ashby `publishedAt`. Reposted jobs
  get a fresh date, so they can look new.
- **Added** is when the job first showed up in the feed. Each run reads the
  previously published `data/seen.json` and carries those times forward. Jobs
  that were already open on the first run show **≤ <start date>**, because
  their real arrival date is unknown.
- **NEW** means new *since your last visit* on that browser (on a first visit:
  added in the last day). Reloading the page doesn't clear the badges.
- If the previous feed can't be read (anything other than "not published
  yet"), the build fails rather than resetting every Added time.

The hosted dataset is rebuilt about once a day (around 20:00 IST), so the
workflow is scheduled for 21:37 IST, plus a 06:47 IST catch-up. GitHub often
starts scheduled runs 1–4 hours late; the page header shows when it last
updated.

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

## Your setup and statuses

Your setup and your Saved / Applied / Hidden / muted companies are stored in
your browser (`localStorage`), so each person, and each device, has their own.
To move them to another device, use **Copy to another device** at the bottom
of the page: it makes a link that copies everything when opened there. (Real
sync across devices needs accounts and a backend; that's a later step.)
**Export / Import backup** does the same with a file.

When a new version of the page is deployed, a phone that cached the old one
reloads itself once.

If you use the [Apply Helper](../apply-helper/README.md) browser extension,
jobs you submit through it are marked **Applied** here the next time you open
the page (it posts them to the page; the page only accepts messages from
itself).

## Check without publishing

Actions → Job Radar → Run workflow → tick **dry_run**. It builds the feed and
posts a summary on the run page without deploying: totals per role, place
and city, plus a per-source funnel (rows → title hint → role match → in scope
→ kept after the age limit). A big source that yields nothing is flagged as a warning.

## Run locally

```bash
pip install httpx pyarrow
python dashboard/build_jobs.py
python -m http.server -d dashboard/site 8000   # then open http://localhost:8000
```
