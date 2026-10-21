"""Build the personal job dashboard feed.

Downloads slices of the hosted ats-scrapers dataset (the sources listed in
``config.json``), keeps the roles that match the config, and writes a compact
``jobs.json`` for the static dashboard in ``dashboard/site``.

Run locally:

    pip install httpx pyarrow
    python dashboard/build_jobs.py

Where a job can be done from ("place"):

* ``home`` / ``india``: the location names an Indian city (or India itself).
* ``remote``: the location is just "Remote" (or names APAC / Asia /
  worldwide) and the description does not restrict it to another region
  (for example "must be based in the US").
* ``abroad``: anywhere else, kept only when the description offers visa
  sponsorship or relocation support.

Everything else is dropped.

Added dates: the previous feed (``--previous``) carries each job's first-seen
date forward. Jobs present on the very first run form the baseline
(``tracking_since``).

Company size is approximated by hiring volume: how many roles the company has
open across all downloaded sources. Thresholds are in ``size_buckets``.
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
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

MANIFEST_URL = "https://storage.stapply.ai/jobhive/v1/manifest.json"
HERE = Path(__file__).resolve().parent

LIGHT_COLUMNS = ["title", "company"]
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
ENTITY_RE = re.compile(r"&(nbsp|amp|#\d+|[a-z]+);")
SPACE_RE = re.compile(r"\s+")

# ---- remote ---------------------------------------------------------------

# Words that only say *how* the job is worked, not *where*. A remote location
# string that still has something left after removing these names a specific
# place (e.g. "Remote - Costa Rica", "San Francisco") and is not "remote from
# India".
REMOTE_WORDS = re.compile(
    r"\b(remote|remotely|fully|full|100|first|friendly|only|work from home|wfh|"
    r"distributed|anywhere|worldwide|global|globally|international|location|"
    r"open|any|home|based|position|role|team|hybrid)\b",
    re.I,
)
# Connector words are only ignored in lowercase: "Remote, OR" is Oregon and
# "Remote, IN" is ambiguous (Indiana), so those must not be stripped.
REMOTE_CONNECTORS = re.compile(r"\b(of|in|the|or|and|with)\b")
REMOTE_REGION_OK = re.compile(r"\b(apac|asia|asia pacific|asia-pacific|south asia)\b", re.I)
# Workday hides multi-location postings behind "2 Locations" etc.
GENERIC_LOCATION = re.compile(r"^\s*\d+\s+locations?\s*$", re.I)

_US_STATES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|"
    "NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY"
)
_US_STATE_NAMES = (
    "alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|"
    "hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|"
    "michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|"
    "new mexico|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|"
    "south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|"
    "west virginia|wisconsin|wyoming"
)
US_LOCATION_NAME = re.compile(rf"\b(united states|usa|u\.s\.a?\.?|{_US_STATE_NAMES})\b", re.I)
US_LOCATION_CODE = re.compile(rf"(,|-|\s)\s*({_US_STATES})\b(?![a-z])")
REMOTE_SIGNAL = re.compile(r"\b(remote|anywhere|worldwide|work from home|wfh)\b")

_REGIONS = (
    r"(the )?(us|u\.s\.?|usa|united states|america|canada|uk|u\.k\.|united kingdom|britain|"
    r"england|ireland|europe|european union|eu|emea|latam|latin america|north america|"
    r"the americas|americas|australia|new zealand|germany|france|spain|portugal|poland|"
    r"netherlands|brazil|mexico|argentina|colombia|chile|japan|singapore|philippines|israel)"
)
REMOTE_RESTRICTED = re.compile(
    rf"\b(based|located|reside|residing|live|living|resident|residents|domiciled) "
    rf"(in|within|inside) {_REGIONS}\b"
    rf"|\b(authori[sz]ed|eligible|authori[sz]ation|right) to work in {_REGIONS}\b"
    r"|\b(us|u\.s\.|usa|uk|eu|canada|europe|emea|latam)[- ](based|only|residents?|citizens?)\b"
    r"|\b(within|across) the (us|u\.s\.|united states|uk|eu|continental us)\b"
    r"|\b(est|edt|pst|pdt|cst|cdt|mst|cet|cest|gmt|bst)\b.{0,25}\b(hours|time ?zone|overlap)"
)
INDIA_IN_TEXT = re.compile(r"\b(in|from|across|within|including|throughout) india\b")

# ---- visa / relocation ----------------------------------------------------

VISA_POSITIVE = re.compile(
    r"\bwe (do |can |will |are able to |happily |gladly )?sponsor (work |employment |your )?visas?\b"
    r"|\bvisa sponsorship (is |are |may be |can be )?(available|provided|offered|possible)\b"
    r"|\b(offer|offers|offering|provide|provides|providing|include|includes|including|with|"
    r"plus|support|supports) (full |complete )?(visa sponsorship|visa support|sponsorship of "
    r"(your |employment |work )?visas?)\b"
    r"|\b(eligible|available) for (visa|immigration) sponsorship\b"
    r"|\bsponsorship (is |will be )?(available|provided|offered)\b"
    r"|\btake over (your )?(visa )?sponsorship\b"
    r"|\b(help|assist|support) (you )?(with |in )?(obtaining |getting |securing )?"
    r"(a |your |the )?(work )?(visa|work permit|blue card)\b"
    r"|\b(visa|immigration|work permit) (support|assistance|help)\b"
)
RELOCATION_POSITIVE = re.compile(
    r"\brelocation (assistance|support|package|packages|allowance|bonus|benefits?|"
    r"stipend|budget|help|reimbursement)\b"
    r"|\b(offer|offers|offering|provide|provides|providing|include|includes|including|"
    r"with|plus|cover|covers) (full |generous |a )?relocation\b"
    r"|\b(help|assist|support) (you )?(to )?relocate\b"
    r"|\brelocation (is |will be |can be )?(available|provided|offered|covered|supported)\b"
)
NEG_BEFORE = re.compile(
    r"\b(no|not|unable|cannot|can't|cant|don't|dont|doesn't|doesnt|won't|wont|without|never|"
    r"nor|neither|aren't|arent|isn't|isnt|unfortunately)\b[^.;!?]{0,40}$"
)
NEG_AFTER = re.compile(
    r"^[^.;!?]{0,12}\b((is|are|will be) (not|un)|not (available|provided|offered|possible|"
    r"eligible|supported)|unavailable)"
)


def clean_text(html: str) -> str:
    text = TAG_RE.sub(" ", html)
    text = ENTITY_RE.sub(" ", text)
    return SPACE_RE.sub(" ", text).lower()


def positive_mention(rx: re.Pattern[str], text: str) -> bool:
    """True when ``rx`` matches somewhere that is not negated nearby."""
    for m in rx.finditer(text):
        before = text[max(0, m.start() - 60) : m.start()]
        after = text[m.end() : m.end() + 40]
        if NEG_BEFORE.search(before) or NEG_AFTER.search(after):
            continue
        return True
    return False


def support_tags(text: str) -> list[str]:
    tags = []
    if positive_mention(VISA_POSITIVE, text):
        tags.append("Visa sponsorship")
    if positive_mention(RELOCATION_POSITIVE, text):
        tags.append("Relocation")
    return tags


# ---- matching -------------------------------------------------------------


def term_regex(terms: list[str]) -> re.Pattern[str] | None:
    """One case-insensitive regex matching any term as a whole word (plural ok)."""
    cleaned = sorted({t.strip().lower() for t in terms if t.strip()}, key=len, reverse=True)
    if not cleaned:
        return None
    alternation = "|".join(re.escape(t) for t in cleaned)
    return re.compile(rf"(?<![a-z0-9])(?:{alternation})(?:s|es)?(?![a-z0-9])")


def coarse_regex(terms: list[str]) -> str:
    """RE2-compatible prefilter (no lookarounds) for pyarrow.compute."""
    cleaned = sorted({t.strip().lower() for t in terms if t.strip()}, key=len, reverse=True)
    return "(?i)(" + "|".join(re.escape(t) for t in cleaned) + ")"


class Matcher:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.include = term_regex(cfg["title_include"])
        self.exclude = term_regex(cfg["title_exclude"])
        self.include_coarse = coarse_regex(cfg["title_include"])
        self.india = term_regex(cfg["india_terms"])
        self.home = term_regex(cfg.get("home_city_terms", []))
        self.include_remote = bool(cfg.get("include_remote", True))
        self.include_abroad = bool(cfg.get("include_abroad_with_support", True))
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

    def is_india(self, location: str, country: str | None) -> bool:
        return country == "IN" or bool(self.india and self.india.search(location.lower()))

    def remote_ok(self, location: str, country: str | None, remote_flag: Any, desc: str) -> bool:
        """Remote from India: no other place named, no other region required."""
        loc = location.lower()
        if not (remote_flag is True or REMOTE_SIGNAL.search(loc)):
            return False
        if country and country != "IN":
            return False
        residue = REMOTE_CONNECTORS.sub(
            " ", REMOTE_REGION_OK.sub(" ", REMOTE_WORDS.sub(" ", location))
        )
        if re.sub(r"[\W_]+", "", residue):
            return False  # names a specific non-India place (or isn't a place at all)
        if INDIA_IN_TEXT.search(desc):
            return True
        return not REMOTE_RESTRICTED.search(desc)

    def place(
        self,
        location: str,
        country: str | None,
        remote_flag: Any,
        desc: str,
        support: list[str],
    ) -> str | None:
        if self.is_india(location, country):
            return "home" if self.home and self.home.search(location.lower()) else "india"
        if GENERIC_LOCATION.match(location) and self.india and self.india.search(desc):
            return "home" if self.home and self.home.search(desc) else "india"
        if self.include_remote and self.remote_ok(location, country, remote_flag, desc):
            return "remote"
        if self.include_abroad and self.abroad_ok(location, country, support):
            return "abroad"
        return None

    @staticmethod
    def abroad_ok(location: str, country: str | None, support: list[str]) -> bool:
        """Visa sponsorship anywhere; relocation only outside the US.

        US "relocation assistance" is almost always domestic and comes without
        a visa, so on its own it doesn't help someone moving from India.
        """
        if "Visa sponsorship" in support:
            return True
        if "Relocation" not in support:
            return False
        is_us = (
            country == "US"
            or bool(US_LOCATION_NAME.search(location))
            or bool(US_LOCATION_CODE.search(location))
        )
        return not is_us

    def display_location(self, location: str, place: str) -> str:
        """For multi-location postings, show the Indian locations first."""
        if place not in ("home", "india") or self.india is None:
            return location
        parts = [p.strip() for p in re.split(r"\s*[;|]\s*|\s+/\s+", location) if p.strip()]
        if len(parts) <= 1:
            return location
        local = [p for p in parts if self.india.search(p.lower())] or parts[:1]
        others = len(parts) - len(local)
        shown = "; ".join(local[:3]) + (f" +{len(local) - 3}" if len(local) > 3 else "")
        return shown + (f" (+{others} outside India)" if others else "")

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
    with httpx.stream("GET", url, timeout=900, follow_redirects=True) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in resp.iter_bytes(1 << 20):
                fh.write(chunk)


def scan(
    path: Path, matcher: Matcher, cutoff: str, company_roles: dict[str, int]
) -> list[dict[str, Any]]:
    """Scan one parquet slice row group by row group.

    Cheap columns (title, company) are read for every row to count company
    hiring volume and prefilter titles with pyarrow; the heavy columns
    (description etc.) are only read for row groups that have candidates.
    """
    pf = pq.ParquetFile(path)
    available = set(pf.schema_arrow.names)
    light = [c for c in LIGHT_COLUMNS if c in available]
    heavy = [c for c in WANTED_COLUMNS if c in available]
    out: list[dict[str, Any]] = []
    for rg in range(pf.num_row_groups):
        small = pf.read_row_group(rg, columns=light)
        if "company" in small.column_names:
            for item in pc.value_counts(small["company"]).to_pylist():
                key = company_key(item["values"] or "")
                if key:
                    company_roles[key] = company_roles.get(key, 0) + int(item["counts"])
        titles = pc.fill_null(small["title"], "")
        mask = pc.match_substring_regex(titles, matcher.include_coarse)
        if not pc.any(mask).as_py():
            continue
        rows = pf.read_row_group(rg, columns=heavy).filter(mask).to_pylist()
        for row in rows:
            job = build_job(row, matcher, cutoff, path.stem)
            if job:
                out.append(job)
    return out


def build_job(
    row: dict[str, Any], matcher: Matcher, cutoff: str, source: str
) -> dict[str, Any] | None:
    title = SPACE_RE.sub(" ", row.get("title") or "").strip()
    if not title or not matcher.title_ok(title):
        return None
    posted = iso_date(row.get("posted_at"))
    if posted and posted < cutoff:
        return None
    location = SPACE_RE.sub(" ", row.get("location") or "").strip()
    desc = clean_text(row.get("description") or "")
    support = support_tags(desc)
    place = matcher.place(location, row.get("country_iso"), row.get("is_remote"), desc, support)
    if place is None:
        return None
    ats = row.get("ats_type") or source
    ats_id = row.get("ats_id")
    return {
        "id": f"{ats}:{ats_id}" if ats_id else row.get("url"),
        "title": title,
        "company": (row.get("company") or "").strip(),
        "ats": ats,
        "location": matcher.display_location(location, place)
        or ("Remote" if place == "remote" else ""),
        "location_full": location,
        "place": place,
        "level": level_for(title),
        "stack": matcher.stack(f"{title.lower()} {desc}"),
        "support": support,
        "department": row.get("department") or row.get("team") or None,
        "salary": row.get("salary_summary") or None,
        "posted_at": posted,
        "url": row.get("apply_url") or row.get("url"),
    }


def dedupe_key(job: dict[str, Any]) -> tuple[str, str, str]:
    return (company_key(job["company"]), job["title"].lower(), job["location_full"].lower())


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
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

    jobs: dict[tuple[str, str, str], dict[str, Any]] = {}
    counts: dict[str, int] = {}
    failed: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for source in cfg["sources"]:
            if args.local_dir:
                path = Path(args.local_dir) / f"{source}.parquet"
                if not path.exists():
                    print(f"{source}: no local file, skipping", file=sys.stderr)
                    continue
            else:
                entry = manifest.get("by_ats", {}).get(source)
                if not entry or not entry.get("parquet") or not entry.get("rows"):
                    print(f"{source}: not in manifest or empty, skipping", file=sys.stderr)
                    continue
                path = Path(tmp) / f"{source}.parquet"
                size_mb = entry.get("parquet_size_bytes", 0) / 1e6
                print(f"{source}: downloading {size_mb:.0f} MB", flush=True)
                try:
                    download(entry["parquet"], path)
                except httpx.HTTPError as exc:
                    print(f"::warning::{source}: download failed ({exc}), skipped")
                    failed.append(source)
                    continue
            try:
                found = scan(path, matcher, cutoff, company_roles)
            except (OSError, pa.ArrowException) as exc:
                print(f"::warning::{source}: could not read slice ({exc}), skipped")
                failed.append(source)
                continue
            finally:
                if not args.local_dir:
                    path.unlink(missing_ok=True)
            counts[source] = len(found)
            print(f"{source}: {len(found)} matching jobs", flush=True)
            for job in found:
                jobs.setdefault(dedupe_key(job), job)

    for job in jobs.values():
        job["first_seen"] = previous.get(job["id"], today.isoformat())
        key = company_key(job["company"])
        roles = company_roles.get(key, 0)
        job["open_roles"] = roles
        job["size"] = size_label(roles, buckets)
        padded = f" {key} "
        job["target"] = any(f" {t} " in padded for t in targets)
        if job["location_full"] == job["location"]:
            del job["location_full"]

    ordered = sorted(
        jobs.values(),
        key=lambda j: (j["first_seen"], j["posted_at"] or ""),
        reverse=True,
    )[: int(cfg.get("max_jobs", 15000))]

    places: dict[str, int] = {}
    for j in ordered:
        places[j["place"]] = places.get(j["place"], 0) + 1
    print("by place:", places)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "snapshot_at": snapshot_at,
        "tracking_since": tracking_since,
        "size_buckets": buckets,
        "counts": counts,
        "failed_sources": failed,
        "places": places,
        "total": len(ordered),
        "new_today": sum(
            1 for j in ordered if j["first_seen"] == today.isoformat() > tracking_since
        ),
        "jobs": ordered,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {len(ordered)} jobs to {out} ({out.stat().st_size / 1e6:.1f} MB)")
    if failed and len(failed) == len(cfg["sources"]):
        print("every source failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
