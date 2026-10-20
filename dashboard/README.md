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
| `max_age_days`, `max_jobs` | Drops stale postings and caps the feed size. |

## How "new" works

Each run reads yesterday's published `jobs.json` and carries over the date
each job was first seen. So **NEW** and *Seen in last 24h* mean new to you,
not just recently posted.

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
