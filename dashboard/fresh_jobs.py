"""Find jobs posted in the last day, straight from the job boards.

The main feed is built from a dataset that is refreshed once a day. This
script instead asks the job boards directly (their public posting APIs, via
this repo's own scrapers) so it can show jobs within minutes of being posted.

Which companies get checked (each tagged with where it came from, so a run can
report what every option adds):

* ``feed``     companies with a job in the current Job Radar feed
* ``presence`` companies with *any* India or remote posting in the daily
               dataset, for the platforms in ``fresh.presence_sources``
* ``list``     every company in ``ats-companies/<ats>.csv`` for small,
               India-heavy platforms (``fresh.all_companies_sources``)
* ``extra``    ``fresh.extra_companies`` from the config

Each job goes through the same role / location / visa rules as the main feed
(``build_jobs.Matcher``). Some platforms only give a posting *date*; those jobs
are marked ``approx`` and the page only shows them in the 24 h view.

    pip install -e . beautifulsoup4 pyarrow
    python dashboard/fresh_jobs.py --feed dashboard/site/data --out dashboard/site/data/fresh.json
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_jobs import MANIFEST_URL, Matcher, build_job, company_key, download

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ATS = (
    "greenhouse",
    "lever",
    "ashby",
    "smartrecruiters",
    "recruitee",
    "workable",
    "keka",
    "darwinbox",
)
# Platforms whose listing has no description (we skip the per-job detail
# calls): remote jobs can't be checked for "US only" wording, so they're dropped.
NO_DESCRIPTION = {"smartrecruiters", "workable"}
CONCURRENCY = {"workable": 3, "darwinbox": 4, "smartrecruiters": 8, "keka": 8}
PER_BOARD_TIMEOUT = 150


# ---- which companies to check -------------------------------------------


def slug_from_url(url: str) -> tuple[str, str] | None:
    """(ats, board identifier) from a job's URL, when it's on the ATS's domain."""
    try:
        u = urlparse(url)
    except ValueError:
        return None
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
    if "greenhouse.io" in host:
        q = parse_qs(u.query)
        if "for" in q:
            return "greenhouse", q["for"][0]
        if parts and parts[0] not in ("embed", "v1"):
            return "greenhouse", parts[0]
    if host.endswith("lever.co") and parts:
        return "lever", parts[0]
    if host.endswith("ashbyhq.com") and parts:
        return "ashby", parts[0]
    if host.endswith("smartrecruiters.com") and parts:
        return "smartrecruiters", parts[0]
    if host.endswith(".recruitee.com"):
        return "recruitee", host.split(".")[0]
    if host == "apply.workable.com" and parts and parts[0] not in ("api", "j"):
        return "workable", parts[0]
    if host.endswith(".keka.com"):
        return "keka", f"https://{host}/careers"
    if ".darwinbox." in host:
        return "darwinbox", host.split(".")[0]
    return None


DISPLAY_NAMES: dict[tuple[str, str], str] = {}


def load_directory() -> dict[str, dict[str, str]]:
    """{ats: {company_key(name or slug): slug}} from the repo's company lists.

    Also fills ``DISPLAY_NAMES`` ({(ats, slug): "Proper Name"}) so the page can
    show "GoDaddy" rather than the board slug "godaddy".
    """
    out: dict[str, dict[str, str]] = {}
    for ats in ATS:
        path = REPO / "ats-companies" / f"{ats}.csv"
        table: dict[str, str] = {}
        if path.exists():
            with path.open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    slug = (row.get("slug") or "").strip()
                    if slug:
                        if (row.get("name") or "").strip():
                            DISPLAY_NAMES.setdefault((ats, slug), row["name"].strip())
                        table.setdefault(company_key(row.get("name") or ""), slug)
                        table.setdefault(company_key(slug), slug)
        out[ats] = table
    return out


def resolve(ats: str, company: str, url: str, directory: dict[str, dict[str, str]]) -> str | None:
    hit = slug_from_url(url or "")
    if hit and hit[0] == ats:
        return hit[1]
    return directory.get(ats, {}).get(company_key(company or ""))


Companies = dict[tuple[str, str], dict[str, str]]


def add(found: Companies, ats: str, slug: str, name: str, origin: str) -> None:
    if (ats, slug) not in found:
        found[(ats, slug)] = {
            "name": DISPLAY_NAMES.get((ats, slug)) or name or slug,
            "origin": origin,
        }


def companies_from_feed(
    feed_dir: Path, extra: list[str], sources: tuple[str, ...] = ATS
) -> Companies:
    """Companies with a job in the published feed, plus config extras."""
    directory = load_directory()
    found: Companies = {}
    index = json.loads((feed_dir / "index.json").read_text())
    for info in index["roles"].values():
        path = feed_dir / info["file"]
        if not path.exists():
            continue
        for job in json.loads(path.read_text())["jobs"]:
            ats = job.get("ats")
            if ats in sources:
                slug = resolve(ats, job["company"], job.get("url") or "", directory)
                if slug:
                    add(found, ats, slug, job["company"], "feed")
    for item in extra:
        ats, _, slug = item.partition(":")
        if ats in ATS and slug:
            add(found, ats, slug, slug, "extra")
    return found


def companies_from_lists(sources: list[str]) -> Companies:
    load_directory()
    found: Companies = {}
    for ats in sources:
        path = REPO / "ats-companies" / f"{ats}.csv"
        if path.exists():
            with path.open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    if (row.get("slug") or "").strip():
                        add(found, ats, row["slug"].strip(), row.get("name") or "", "list")
    return found


def discover_presence(matcher: Matcher, sources: list[str]) -> dict[str, list[list[str]]]:
    """{ats: [[slug, name], ...]} for companies with any India / remote posting
    in today's dataset (any job title), read from the hosted parquet slices."""
    import pyarrow.parquet as pq

    directory = load_directory()
    manifest = httpx.get(MANIFEST_URL, timeout=60, follow_redirects=True).json()
    out: dict[str, list[list[str]]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for ats in sources:
            entry = manifest.get("by_ats", {}).get(ats) or {}
            if not entry.get("parquet"):
                continue
            path = Path(tmp) / f"{ats}.parquet"
            download(entry["parquet"], path)
            pf = pq.ParquetFile(path)
            cols = [
                c
                for c in ("company", "location", "country_iso", "is_remote", "url")
                if c in pf.schema_arrow.names
            ]
            seen: dict[str, str] = {}
            for batch in pf.iter_batches(batch_size=50_000, columns=cols):
                for row in batch.to_pylist():
                    loc = row.get("location") or ""
                    country = row.get("country_iso")
                    if not (
                        matcher.is_india(loc, country)
                        or matcher.remote_ok(loc, country, row.get("is_remote"), "")
                    ):
                        continue
                    slug = resolve(ats, row.get("company") or "", row.get("url") or "", directory)
                    if slug:
                        seen.setdefault(slug, row.get("company") or slug)
            path.unlink(missing_ok=True)
            out[ats] = [[s, n] for s, n in sorted(seen.items())]
            print(f"presence: {ats} {len(seen)} companies with India/remote postings", flush=True)
    return out


def load_presence(
    feed_dir: Path, matcher: Matcher, sources: list[str], max_age_h: int
) -> dict[str, Any]:
    """Cached presence list (data/fresh_companies.json) if recent, else rebuild."""
    path = feed_dir / "fresh_companies.json"
    if path.exists():
        cached = json.loads(path.read_text())
        age = datetime.now(UTC) - datetime.fromisoformat(
            cached["generated_at"].replace("Z", "+00:00")
        )
        if age < timedelta(hours=max_age_h) and sorted(cached.get("sources", [])) == sorted(
            sources
        ):
            print(f"presence: using cached list from {cached['generated_at']}")
            return cached
    fresh = {
        "generated_at": stamp(datetime.now(UTC)),
        "sources": sources,
        "companies": discover_presence(matcher, sources),
    }
    path.write_text(json.dumps(fresh, ensure_ascii=False, separators=(",", ":")))
    return fresh


def merge(*groups: Companies) -> Companies:
    out: Companies = {}
    for g in groups:
        for k, v in g.items():
            out.setdefault(k, v)
    return out


# ---- fetching -------------------------------------------------------------


def scraper_for(ats: str, slug: str) -> Any:
    from ats_scrapers.scrapers import get_scraper

    kwargs: dict[str, Any] = {"timeout": 25.0}
    if ats in NO_DESCRIPTION:
        kwargs["include_descriptions"] = False
    return get_scraper(ats, slug, **kwargs)


async def fetch_all(
    companies: Companies, default_concurrency: int
) -> tuple[list[tuple[str, str, Any]], list[str]]:
    sems = {a: asyncio.Semaphore(CONCURRENCY.get(a, default_concurrency)) for a in ATS}
    results: list[tuple[str, str, Any]] = []
    failed: list[str] = []

    async def one(ats: str, slug: str) -> None:
        async with sems[ats]:
            try:
                jobs = await asyncio.wait_for(scraper_for(ats, slug).afetch(), PER_BOARD_TIMEOUT)
            except Exception as exc:  # one bad board must not stop the run
                failed.append(f"{ats}:{slug} ({type(exc).__name__})")
                return
            results.extend((ats, slug, j) for j in jobs)

    await asyncio.gather(*(one(a, s) for a, s in companies))
    return results, failed


def to_row(job: Any) -> dict[str, Any]:
    """A scraper ``Job`` as a dataset-style row for ``build_job``."""
    data = job.model_dump(mode="python")
    ats = data.get("ats_type")
    data["ats_type"] = getattr(ats, "value", ats)
    data["url"] = str(data.get("url") or "")
    if data.get("apply_url"):
        data["apply_url"] = str(data["apply_url"])
    return data


def stamp(value: Any) -> str | None:
    if not isinstance(value, datetime):
        return None
    dt = value if value.tzinfo else value.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="minutes").replace("+00:00", "Z")


def date_only(value: datetime) -> bool:
    """True when the platform gave a date but no time of day."""
    return (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0)


def select_fresh(
    scraped: list[tuple[str, str, Any]],
    companies: Companies,
    matcher: Matcher,
    since: datetime,
) -> list[dict[str, Any]]:
    """Jobs posted after ``since`` that pass the feed's rules, newest first."""
    since_s = stamp(since) or ""
    since_day = since.date().isoformat()
    out: dict[str, dict[str, Any]] = {}
    for ats, slug, job in scraped:
        posted_dt = getattr(job, "posted_at", None)
        posted = stamp(posted_dt)
        if not posted:
            continue
        approx = date_only(posted_dt)
        if (posted[:10] < since_day) if approx else (posted < since_s):
            continue
        row = to_row(job)
        info = companies.get((ats, slug), {})
        row["company"] = info.get("name") or row.get("company") or slug
        built = build_job(row, matcher, ats)
        if not built:
            continue
        if built["place"] == "remote" and not (row.get("description") or "").strip():
            continue  # can't check a remote job for "US only" wording without its text
        built["posted_ts"] = posted
        if approx:
            built["approx"] = True
        built["origin"] = info.get("origin", "feed")
        out.setdefault(built["id"], built)
    return sorted(out.values(), key=lambda j: j["posted_ts"], reverse=True)


def window_counts(
    jobs: list[dict[str, Any]], now: datetime, key: str | None = None
) -> dict[str, Any]:
    """Jobs per 1/3/6/24 h window (date-only jobs count in 24 h only), optionally per ``key``."""

    def count(sel: list[dict[str, Any]]) -> dict[str, int]:
        res = {}
        for h in (1, 3, 6, 24):
            cut = stamp(now - timedelta(hours=h)) or ""
            res[f"{h}h"] = sum(
                1
                for j in sel
                if (h == 24 or not j.get("approx"))
                and j["posted_ts"] >= cut[: 10 if j.get("approx") else None]
            )
        return res

    if key is None:
        return count(jobs)
    groups = sorted({j[key] for j in jobs})
    return {g: count([j for j in jobs if j[key] == g]) for g in groups}


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--config", default=str(HERE / "config.json"))
    parser.add_argument(
        "--feed", default=str(HERE / "site" / "data"), help="Folder with the current feed"
    )
    parser.add_argument("--out", default=str(HERE / "site" / "data" / "fresh.json"))
    parser.add_argument("--workflow-url", default="", help="Link shown on the page for refreshing")
    args = parser.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    fc = cfg.get("fresh", {})
    window = int(fc.get("window_hours", 24))
    matcher = Matcher(cfg)
    feed_dir = Path(args.feed)

    presence_sources = list(fc.get("presence_sources", []))
    all_sources = list(fc.get("all_companies_sources", []))
    feed_sources = tuple(dict.fromkeys(fc.get("feed_sources", ["greenhouse", "lever", "ashby"])))
    groups = [companies_from_feed(feed_dir, fc.get("extra_companies", []), feed_sources)]
    if presence_sources:
        presence = load_presence(
            feed_dir, matcher, presence_sources, int(fc.get("presence_cache_hours", 20))
        )
        pres: Companies = {}
        load_directory()
        for ats, items in presence["companies"].items():
            for slug, name in items:
                add(pres, ats, slug, name, "presence")
        groups.append(pres)
    if all_sources:
        groups.append(companies_from_lists(all_sources))
    companies = merge(*groups)

    per_ats = {
        a: sum(1 for k in companies if k[0] == a) for a in ATS if any(k[0] == a for k in companies)
    }
    per_origin: dict[str, int] = {}
    for v in companies.values():
        per_origin[v["origin"]] = per_origin.get(v["origin"], 0) + 1
    print(f"checking {len(companies)} companies {per_ats} by origin {per_origin}", flush=True)

    started = time.monotonic()
    now = datetime.now(UTC)
    scraped, failed = asyncio.run(fetch_all(companies, int(fc.get("concurrency", 24))))
    jobs = select_fresh(scraped, companies, matcher, now - timedelta(hours=window))
    took = round(time.monotonic() - started)
    failed_by_ats: dict[str, int] = {}
    for f in failed:
        a = f.split(":", 1)[0]
        failed_by_ats[a] = failed_by_ats.get(a, 0) + 1

    payload = {
        "generated_at": stamp(now),
        "window_hours": window,
        "companies_checked": per_ats,
        "companies_by_origin": per_origin,
        "companies_failed": len(failed),
        "failed_by_ats": failed_by_ats,
        "postings_seen": len(scraped),
        "seconds": took,
        "counts": window_counts(jobs, now),
        "counts_by_origin": window_counts(jobs, now, "origin"),
        "counts_by_ats": window_counts(jobs, now, "ats"),
        "workflow_url": args.workflow_url,
        "jobs": jobs,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"{len(scraped)} postings in {took}s; fresh {payload['counts']}; failed {failed_by_ats}")
    if failed:
        print("failed (first 20): " + ", ".join(failed[:20]))
    if companies and len(failed) > 0.5 * len(companies):
        print("::error::More than half the boards failed; not publishing")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
