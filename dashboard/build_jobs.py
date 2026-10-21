"""Build the personal job dashboard feed.

Downloads the hosted ats-scrapers dataset slices (Greenhouse, Lever, Ashby by
default), keeps only roles that match ``config.json`` (title keywords,
India / remote locations, recency), and writes a compact ``jobs.json`` that the
static dashboard in ``dashboard/site`` reads.

Run locally:

    pip install httpx pyarrow
    python dashboard/build_jobs.py

The previous feed (``--previous``, a path or URL) is used to remember when each
posting was first seen, so the dashboard can show "new since yesterday". Jobs
already present on the very first run are marked as part of the baseline
(``tracking_since``) rather than pretending they were all added that day.

Company size is not in the dataset, so it is approximated by hiring volume:
the number of open roles the company has across the downloaded sources
(all roles, worldwide, not only the matching ones). Thresholds live in
``size_buckets`` in the config.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq

MANIFEST_URL = "https://storage.stapply.ai/jobhive/v1/manifest.json"
HERE = Path(__file__).resolve().parent

WANTED_COLUMNS = [
    "url",
    "apply_url",
    "title",
    "company",
    "ats_type",
    "ats_id",
    "location",
    "country_iso",
    "is_remote",
    "salary_summary",
    "department",
    "team",
    "employment_type",
    "posted_at",
    "description",
]

LEVEL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Staff+", re.compile(r"\b(staff|lead|principal)\b")),
    (
        "Senior",
        re.compile(r"\b(senior|sr\.?|sde[\s-]*(iii|3)|swe[\s-]*(iii|3)|engineer[\s-]*(iii|3))\b"),
    ),
    ("Mid", re.compile(r"\b(sde[\s-]*(ii|2)|swe[\s-]*(ii|2)|engineer[\s-]*(ii|2)|mid)\b")),
    (
        "Junior",
        re.compile(r"\b(junior|jr\.?|associate|entry|sde[\s-]*(i|1)|engineer[\s-]*(i|1))\b"),
    ),
]

TAG_RE = re.compile(r"<[^>]+>")


def term_regex(terms: list[str]) -> re.Pattern[str] | None:
    """One case-insensitive regex matching any term as a whole word (plural ok)."""
    cleaned = sorted({t.strip().lower() for t in terms if t.strip()}, key=len, reverse=True)
    if not cleaned:
        return None
    alternation = "|".join(re.escape(t) for t in cleaned)
    return re.compile(rf"(?<![a-z0-9])(?:{alternation})(?:s|es)?(?![a-z0-9])")


class Matcher:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.include = term_regex(cfg["title_include"])
        self.exclude = term_regex(cfg["title_exclude"])
        self.india = term_regex(cfg["india_terms"])
        self.home = term_regex(cfg.get("home_city_terms", []))
        self.remote_ok = term_regex(cfg.get("remote_ok_terms", []))
        self.remote_block = term_regex(cfg.get("remote_block_terms", []))
        self.include_remote = bool(cfg.get("include_remote", True))
        self.tags = {
            name: rx
            for name, terms in cfg.get("stack_tags", {}).items()
            if (rx := term_regex(terms)) is not None
        }

    def title_ok(self, title: str) -> bool:
        t = title.lower()
        if self.include is None or not self.include.search(t):
            return False
        return not (self.exclude and self.exclude.search(t))

    def place(self, location: str, country: str | None, remote_flag: Any, title: str) -> str | None:
        """Return 'home', 'india', 'remote', or None when the job is out of scope."""
        loc = location.lower()
        is_india = country == "IN" or bool(self.india and self.india.search(loc))
        if is_india:
            return "home" if self.home and self.home.search(loc) else "india"
        if not self.include_remote:
            return None
        is_remote = remote_flag is True or "remote" in loc or "remote" in title.lower()
        if not is_remote:
            return None
        if self.remote_ok and self.remote_ok.search(loc):
            return "remote"
        if country and country != "IN":
            return None
        if self.remote_block and self.remote_block.search(loc):
            return None
        return "remote"

    def stack(self, text: str) -> list[str]:
        return [name for name, rx in self.tags.items() if rx.search(text)]


def level_for(title: str) -> str:
    t = title.lower().replace("member of technical staff", "mts")
    for name, rx in LEVEL_PATTERNS:
        if rx.search(t):
            return name
    return "Unspecified"


def iso_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).date().isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10] if re.match(r"\d{4}-\d{2}-\d{2}", text) else None
    return iso_date(dt)


def load_previous(source: str | None) -> dict[str, Any] | None:
    """Return the previous feed, or None when there is genuinely none yet.

    A missing feed (404 / no file) means this is the first run. Any other
    failure aborts the build: silently starting fresh would reset every
    job's "added" date and flood the dashboard with false NEW badges.
    """
    if not source:
        return None
    if source.startswith(("http://", "https://")):
        resp = httpx.get(source, timeout=60, follow_redirects=True)
        if resp.status_code == 404:
            print("previous feed: not published yet, starting baseline", file=sys.stderr)
            return None
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data
    path = Path(source)
    if not path.exists():
        return None
    loaded: dict[str, Any] = json.loads(path.read_text())
    return loaded


def size_label(open_roles: int, buckets: dict[str, int]) -> str:
    for label, upper in sorted(buckets.items(), key=lambda kv: kv[1]):
        if open_roles < upper:
            return label
    return "Large"


def company_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def download(url: str, dest: Path) -> None:
    with httpx.stream("GET", url, timeout=600, follow_redirects=True) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in resp.iter_bytes(1 << 20):
                fh.write(chunk)


def scan(
    path: Path, matcher: Matcher, cutoff: str, company_roles: dict[str, int]
) -> list[dict[str, Any]]:
    pf = pq.ParquetFile(path)
    available = set(pf.schema_arrow.names)
    columns = [c for c in WANTED_COLUMNS if c in available]
    out: list[dict[str, Any]] = []
    for batch in pf.iter_batches(batch_size=20_000, columns=columns):
        for row in batch.to_pylist():
            key = company_key(row.get("company") or "")
            if key:
                company_roles[key] = company_roles.get(key, 0) + 1
            title = (row.get("title") or "").strip()
            if not title or not matcher.title_ok(title):
                continue
            location = (row.get("location") or "").strip()
            place = matcher.place(location, row.get("country_iso"), row.get("is_remote"), title)
            if place is None:
                continue
            posted = iso_date(row.get("posted_at"))
            if posted and posted < cutoff:
                continue
            desc = TAG_RE.sub(" ", row.get("description") or "").lower()
            ats = row.get("ats_type") or path.stem
            ats_id = row.get("ats_id")
            out.append(
                {
                    "id": f"{ats}:{ats_id}" if ats_id else row.get("url"),
                    "title": title,
                    "company": (row.get("company") or "").strip(),
                    "ats": ats,
                    "location": location or ("Remote" if place == "remote" else ""),
                    "place": place,
                    "level": level_for(title),
                    "stack": matcher.stack(f"{title.lower()} {desc}"),
                    "department": row.get("department") or row.get("team") or None,
                    "salary": row.get("salary_summary") or None,
                    "posted_at": posted,
                    "url": row.get("apply_url") or row.get("url"),
                }
            )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(HERE / "config.json"))
    parser.add_argument("--out", default=str(HERE / "site" / "data" / "jobs.json"))
    parser.add_argument("--previous", help="Path or URL of the last published jobs.json")
    parser.add_argument(
        "--local-dir",
        help="Read <source>.parquet files from this folder instead of downloading (for testing)",
    )
    args = parser.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    matcher = Matcher(cfg)
    today = datetime.now(UTC).date()
    cutoff = (today - timedelta(days=int(cfg.get("max_age_days", 60)))).isoformat()
    prev_feed = load_previous(args.previous)
    previous: dict[str, str] = {
        j["id"]: j["first_seen"] for j in (prev_feed or {}).get("jobs", []) if j.get("first_seen")
    }
    if prev_feed is None:
        tracking_since = today.isoformat()
    else:
        tracking_since = prev_feed.get("tracking_since") or min(
            previous.values(), default=today.isoformat()
        )
    buckets = {
        k: int(v) for k, v in cfg.get("size_buckets", {"Startup": 25, "Mid-size": 250}).items()
    }
    targets = [company_key(t) for t in cfg.get("target_companies", []) if company_key(t)]
    company_roles: dict[str, int] = {}

    snapshot_at: str | None = None
    manifest: dict[str, Any] = {}
    if not args.local_dir:
        manifest = httpx.get(MANIFEST_URL, timeout=60, follow_redirects=True).json()
        snapshot_at = manifest.get("generated_at")

    jobs: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for source in cfg["sources"]:
            if args.local_dir:
                path = Path(args.local_dir) / f"{source}.parquet"
                if not path.exists():
                    print(f"{source}: no local file, skipping", file=sys.stderr)
                    continue
            else:
                entry = manifest.get("by_ats", {}).get(source)
                if not entry or not entry.get("parquet"):
                    print(f"{source}: not in manifest, skipping", file=sys.stderr)
                    continue
                path = Path(tmp) / f"{source}.parquet"
                print(f"{source}: downloading {entry.get('parquet_size_bytes', 0) / 1e6:.0f} MB")
                download(entry["parquet"], path)
            found = scan(path, matcher, cutoff, company_roles)
            counts[source] = len(found)
            print(f"{source}: {len(found)} matching jobs")
            for job in found:
                jobs.setdefault(job["id"], job)
            if not args.local_dir:
                path.unlink(missing_ok=True)

    for job in jobs.values():
        job["first_seen"] = previous.get(job["id"], today.isoformat())
        key = company_key(job["company"])
        roles = company_roles.get(key, 0)
        job["open_roles"] = roles
        job["size"] = size_label(roles, buckets)
        padded = f" {key} "
        job["target"] = any(f" {t} " in padded for t in targets)

    ordered = sorted(
        jobs.values(),
        key=lambda j: (j["first_seen"], j["posted_at"] or ""),
        reverse=True,
    )[: int(cfg.get("max_jobs", 6000))]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "snapshot_at": snapshot_at,
        "tracking_since": tracking_since,
        "size_buckets": buckets,
        "counts": counts,
        "total": len(ordered),
        "new_today": sum(
            1 for j in ordered if j["first_seen"] == today.isoformat() > tracking_since
        ),
        "jobs": ordered,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {len(ordered)} jobs to {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
