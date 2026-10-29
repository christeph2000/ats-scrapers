"""Find jobs posted in the last day, straight from Greenhouse, Lever and Ashby.

The main feed is built from a dataset that is refreshed once a day. This
script instead asks the job boards directly (their public posting APIs, via
this repo's own scrapers) so it can show jobs within minutes of being posted.

To stay light on those APIs it only checks companies that matter here: every
Greenhouse / Lever / Ashby company that appears in the current Job Radar feed,
plus ``fresh_extra_companies`` from the config. Each job goes through the same
role / location / visa rules as the main feed (``build_jobs.Matcher``).

    pip install -e . beautifulsoup4 pyarrow
    python dashboard/fresh_jobs.py --feed dashboard/site/data --out dashboard/site/data/fresh.json
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_jobs import Matcher, build_job, company_key

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ATS = ("greenhouse", "lever", "ashby")


# ---- which companies to check -------------------------------------------


def slug_from_url(url: str) -> tuple[str, str] | None:
    """(ats, board slug) from a job's URL, when it's on the ATS's own domain."""
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
    return None


def load_directory() -> dict[str, dict[str, str]]:
    """{ats: {company_key(name or slug): slug}} from the repo's company lists."""
    out: dict[str, dict[str, str]] = {}
    for ats in ATS:
        path = REPO / "ats-companies" / f"{ats}.csv"
        table: dict[str, str] = {}
        if path.exists():
            with path.open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    slug = (row.get("slug") or "").strip()
                    if slug:
                        table.setdefault(company_key(row.get("name") or ""), slug)
                        table.setdefault(company_key(slug), slug)
        out[ats] = table
    return out


def companies_from_feed(feed_dir: Path, extra: list[str]) -> dict[tuple[str, str], str]:
    """{(ats, slug): display name} for the companies worth checking."""
    directory = load_directory()
    found: dict[tuple[str, str], str] = {}
    index = json.loads((feed_dir / "index.json").read_text())
    for info in index["roles"].values():
        path = feed_dir / info["file"]
        if not path.exists():
            continue
        for job in json.loads(path.read_text())["jobs"]:
            ats = job.get("ats")
            if ats not in ATS:
                continue
            hit = slug_from_url(job.get("url") or "")
            slug = (
                hit[1] if hit and hit[0] == ats else directory[ats].get(company_key(job["company"]))
            )
            if slug:
                found.setdefault((ats, slug), job["company"])
    for item in extra:
        ats, _, slug = item.partition(":")
        if ats in ATS and slug:
            found.setdefault((ats, slug), slug)
    return found


# ---- fetching -------------------------------------------------------------


def scraper_for(ats: str, slug: str) -> Any:
    from ats_scrapers.scrapers.ashby import AshbyScraper
    from ats_scrapers.scrapers.greenhouse import GreenhouseScraper
    from ats_scrapers.scrapers.lever import LeverScraper

    cls = {"greenhouse": GreenhouseScraper, "lever": LeverScraper, "ashby": AshbyScraper}[ats]
    return cls(slug, timeout=25.0)


async def fetch_all(
    companies: dict[tuple[str, str], str], concurrency: int
) -> tuple[list[tuple[str, str, Any]], list[str]]:
    sem = asyncio.Semaphore(concurrency)
    results: list[tuple[str, str, Any]] = []
    failed: list[str] = []

    async def one(ats: str, slug: str) -> None:
        async with sem:
            try:
                jobs = await scraper_for(ats, slug).afetch()
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


def select_fresh(
    scraped: list[tuple[str, str, Any]],
    names: dict[tuple[str, str], str],
    matcher: Matcher,
    since: datetime,
) -> list[dict[str, Any]]:
    """Jobs posted after ``since`` that pass the feed's rules, newest first."""
    since_s = stamp(since) or ""
    out: dict[str, dict[str, Any]] = {}
    for ats, slug, job in scraped:
        posted = stamp(getattr(job, "posted_at", None))
        if not posted or posted < since_s:
            continue
        row = to_row(job)
        row["company"] = names.get((ats, slug)) or row.get("company") or slug
        built = build_job(row, matcher, ats)
        if not built:
            continue
        built["posted_ts"] = posted
        out.setdefault(built["id"], built)
    return sorted(out.values(), key=lambda j: j["posted_ts"], reverse=True)


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
    fresh_cfg = cfg.get("fresh", {})
    window = int(fresh_cfg.get("window_hours", 24))
    matcher = Matcher(cfg)
    companies = companies_from_feed(Path(args.feed), fresh_cfg.get("extra_companies", []))
    per_ats = {a: sum(1 for k in companies if k[0] == a) for a in ATS}
    print(f"checking {len(companies)} companies {per_ats}", flush=True)

    started = time.monotonic()
    now = datetime.now(UTC)
    scraped, failed = asyncio.run(fetch_all(companies, int(fresh_cfg.get("concurrency", 24))))
    jobs = select_fresh(scraped, companies, matcher, now - timedelta(hours=window))
    took = round(time.monotonic() - started)

    payload = {
        "generated_at": stamp(now),
        "window_hours": window,
        "companies_checked": per_ats,
        "companies_failed": len(failed),
        "postings_seen": len(scraped),
        "seconds": took,
        "workflow_url": args.workflow_url,
        "jobs": jobs,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    hours = {
        h: sum(1 for j in jobs if j["posted_ts"] >= stamp(now - timedelta(hours=h)))
        for h in (1, 3, 6, 24)
    }
    print(f"{len(scraped)} postings seen in {took}s; fresh {hours}; {len(failed)} boards failed")
    if failed:
        print("failed (first 20): " + ", ".join(failed[:20]))
    if companies and len(failed) > 0.5 * len(companies):
        print("::error::More than half the boards failed; not publishing")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
