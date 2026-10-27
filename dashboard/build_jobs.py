"""Build the Job Radar feed.

Downloads slices of the hosted ats-scrapers dataset (the sources listed in
``config.json``), keeps engineering roles in India, remote-from-India, or abroad
with visa/relocation support, and writes a static feed for ``dashboard/site``:

* ``data/index.json``        metadata: counts, roles, cities, per-source funnel
* ``data/roles/<role>.json`` the jobs for one role (a job can be in several)
* ``data/seen.json``         first/last-seen times per job, read by the next run

Each visitor picks their own roles, city and levels on the page, which then
loads only the role files it needs. Nothing about a visitor leaves their
browser.

Run locally:

    pip install httpx pyarrow
    python dashboard/build_jobs.py

Where a job can be done from ("place"):

* ``india``: the location names an Indian city or India (``cities`` lists the
  cities found).
* ``remote``: the location is just "Remote" (or names APAC / Asia /
  worldwide) and the description does not restrict it to another region
  (for example "must be based in the US").
* ``abroad``: anywhere else, kept only when the description offers visa
  sponsorship, or relocation outside the US.

Added times: ``seen.json`` from the previous run carries each job's first-seen
time forward. Jobs present on the very first run form the baseline
(``tracking_since``). A job that vanishes for a few days (say a source failed
to download) keeps its original first-seen time when it comes back.

Company size is approximated by hiring volume: how many roles the company has
open across all downloaded sources. Thresholds are in ``size_buckets``.
"""

from __future__ import annotations

import argparse
import hashlib
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
    (
        "Intern/Fresher",
        re.compile(
            r"\b(intern|internship|apprentice|trainee|graduate|new grad|fresher|freshers)\b"
        ),
    ),
    ("Staff+", re.compile(r"\b(staff|lead|principal|distinguished|architect)\b")),
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


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


class Matcher:
    def __init__(self, cfg: dict[str, Any]) -> None:
        roles: dict[str, dict[str, Any]] = cfg["roles"]
        self.roles = {
            name: rx for name, spec in roles.items() if (rx := term_regex(spec["include"]))
        }
        # "General" (plain "Software Engineer") only applies when no specific role matched.
        self.fallback_roles = [n for n, spec in roles.items() if spec.get("fallback")]
        all_terms = [t for spec in roles.values() for t in spec["include"]]
        self.include_coarse = coarse_regex(all_terms)
        self.exclude = term_regex(cfg.get("title_exclude", []))
        self.cities = {
            name: rx for name, terms in cfg["cities"].items() if (rx := term_regex(terms))
        }
        india_terms = list(cfg.get("india_terms", [])) + [
            t for terms in cfg["cities"].values() for t in terms
        ]
        self.india = term_regex(india_terms)
        self.include_remote = bool(cfg.get("include_remote", True))
        self.include_abroad = bool(cfg.get("include_abroad_with_support", True))
        self.tags = {
            name: rx
            for name, terms in cfg.get("stack_tags", {}).items()
            if (rx := term_regex(terms)) is not None
        }

    def roles_for(self, title: str) -> list[str]:
        t = title.lower()
        if self.exclude and self.exclude.search(t):
            return []
        found = [n for n, rx in self.roles.items() if rx.search(t)]
        specific = [n for n in found if n not in self.fallback_roles]
        return specific or found

    def title_ok(self, title: str) -> bool:
        return bool(self.roles_for(title))

    def is_india(self, location: str, country: str | None) -> bool:
        return country == "IN" or bool(self.india and self.india.search(location.lower()))

    def cities_in(self, text: str) -> list[str]:
        t = text.lower()
        return [name for name, rx in self.cities.items() if rx.search(t)]

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
    ) -> tuple[str, list[str]] | None:
        """Return (place, cities) or None when the job is out of scope."""
        if self.is_india(location, country):
            return "india", self.cities_in(location)
        if GENERIC_LOCATION.match(location) and self.india and self.india.search(desc):
            return "india", self.cities_in(desc)
        if self.include_remote and self.remote_ok(location, country, remote_flag, desc):
            return "remote", []
        if self.include_abroad and self.abroad_ok(location, country, support):
            return "abroad", []
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
        if place != "india" or self.india is None:
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


# ---- history --------------------------------------------------------------


def fetch_json(source: str) -> dict[str, Any] | None:
    """Read JSON from a URL or path. None means "doesn't exist (yet)".

    Any other failure raises: silently starting fresh would reset every job's
    added time and flood the page with false NEW badges.
    """
    if source.startswith(("http://", "https://")):
        resp = httpx.get(source, timeout=120, follow_redirects=True)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data
    path = Path(source)
    if not path.exists():
        return None
    loaded: dict[str, Any] = json.loads(path.read_text())
    return loaded


def load_history(base: str | None) -> tuple[dict[str, list[str]], str | None, str | None]:
    """Return ({job id: [first_seen, last_seen, ("b")]}, tracking_since, scope).

    Reads ``seen.json`` from the last published site, falling back once to the
    older single-file ``jobs.json`` layout. ``scope`` is the fingerprint of the
    config that produced it (None for the old layout).
    """
    if not base:
        return {}, None, None
    base = base.rstrip("/")
    seen = fetch_json(f"{base}/seen.json")
    if seen is not None:
        return dict(seen.get("jobs", {})), seen.get("tracking_since"), seen.get("scope")
    legacy = fetch_json(f"{base}/jobs.json")
    if legacy is None:
        print("no previous feed: starting a new baseline", file=sys.stderr)
        return {}, None, None
    jobs = {j["id"]: [j["first_seen"], j["first_seen"]] for j in legacy.get("jobs", [])}
    since = legacy.get("tracking_since") or min((v[0] for v in jobs.values()), default=None)
    return jobs, since, None


SCOPE_KEYS = [
    "sources",
    "roles",
    "title_exclude",
    "india_terms",
    "cities",
    "include_remote",
    "include_abroad_with_support",
    "max_age_days",
]


def scope_fingerprint(cfg: dict[str, Any]) -> str:
    """Fingerprint of the config keys that decide which jobs are in the feed."""
    blob = json.dumps({k: cfg.get(k) for k in SCOPE_KEYS}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def apply_age_limit(found: list[dict[str, Any]], cutoff: str, source: str) -> list[dict[str, Any]]:
    """Drop postings older than ``cutoff``, unless the source's dates are broken.

    The dataset only holds jobs that are still open, so this only trims
    long-running "evergreen" posts. When *every* dated job from a source is
    older than the cutoff (seen with Keka and JOIN), the dates are wrong rather
    than the jobs being old: keep them all and hide the dates instead.
    """
    dated = [j for j in found if j["posted_at"]]
    if len(found) >= 20 and dated and all(j["posted_at"] < cutoff for j in dated):
        print(f"::warning::{source}: every posted date is older than {cutoff}; ignoring its dates")
        for j in found:
            j["posted_at"] = None
        return found
    return [j for j in found if not j["posted_at"] or j["posted_at"] >= cutoff]


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


# ---- scanning -------------------------------------------------------------

FUNNEL_STEPS = ["rows", "title_hint", "role_match", "in_scope", "kept"]


def scan(
    path: Path,
    matcher: Matcher,
    company_roles: dict[str, int],
    funnel: dict[str, int],
) -> list[dict[str, Any]]:
    """Scan one parquet slice row group by row group.

    Cheap columns (title, company) are read for every row to count company
    hiring volume and prefilter titles with pyarrow; the heavy columns
    (description etc.) are only read for rows whose title might match.
    ``funnel`` counts how many rows survive each step, so a source that
    suddenly yields nothing can be told apart from one that failed.
    """
    pf = pq.ParquetFile(path)
    available = set(pf.schema_arrow.names)
    light = [c for c in LIGHT_COLUMNS if c in available]
    heavy = [c for c in WANTED_COLUMNS if c in available]
    out: list[dict[str, Any]] = []
    for rg in range(pf.num_row_groups):
        small = pf.read_row_group(rg, columns=light)
        funnel["rows"] += small.num_rows
        if "company" in small.column_names:
            for item in pc.value_counts(small["company"]).to_pylist():
                key = company_key(item["values"] or "")
                if key:
                    company_roles[key] = company_roles.get(key, 0) + int(item["counts"])
        titles = pc.fill_null(small["title"], "")
        mask = pc.match_substring_regex(titles, matcher.include_coarse)
        hits = pc.sum(mask).as_py() or 0
        if not hits:
            continue
        funnel["title_hint"] += hits
        rows = pf.read_row_group(rg, columns=heavy).filter(mask).to_pylist()
        for row in rows:
            job = build_job(row, matcher, path.stem, funnel)
            if job:
                out.append(job)
    return out


def build_job(
    row: dict[str, Any],
    matcher: Matcher,
    source: str,
    funnel: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    funnel = funnel if funnel is not None else dict.fromkeys(FUNNEL_STEPS, 0)
    title = SPACE_RE.sub(" ", row.get("title") or "").strip()
    roles = matcher.roles_for(title) if title else []
    if not roles:
        return None
    funnel["role_match"] += 1
    posted = iso_date(row.get("posted_at"))
    location = SPACE_RE.sub(" ", row.get("location") or "").strip()
    desc = clean_text(row.get("description") or "")
    support = support_tags(desc)
    where = matcher.place(location, row.get("country_iso"), row.get("is_remote"), desc, support)
    if where is None:
        return None
    funnel["in_scope"] += 1
    place, cities = where
    ats = row.get("ats_type") or source
    ats_id = row.get("ats_id")
    job: dict[str, Any] = {
        "id": f"{ats}:{ats_id}" if ats_id else row.get("url"),
        "title": title,
        "company": (row.get("company") or "").strip(),
        "ats": ats,
        "roles": roles,
        "location": matcher.display_location(location, place)
        or ("Remote" if place == "remote" else "India" if place == "india" else ""),
        "place": place,
        "cities": cities,
        "level": level_for(title),
        "stack": matcher.stack(f"{title.lower()} {desc}"),
        "support": support,
        "salary": row.get("salary_summary") or None,
        "posted_at": posted,
        "url": row.get("apply_url") or row.get("url"),
    }
    if location and location != job["location"]:
        job["location_full"] = location
    return job


def dedupe_key(job: dict[str, Any]) -> tuple[str, str, str]:
    loc = job.get("location_full") or job["location"]
    return (company_key(job["company"]), job["title"].lower(), loc.lower())


# ---- main -----------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--config", default=str(HERE / "config.json"))
    parser.add_argument("--out-dir", default=str(HERE / "site" / "data"))
    parser.add_argument(
        "--previous",
        help="Base URL or folder of the last published data/ (for seen.json)",
    )
    parser.add_argument(
        "--local-dir",
        help="Read <source>.parquet files from this folder instead of downloading (for testing)",
    )
    parser.add_argument("--app-version", default="dev", help="Page version (e.g. the git sha)")
    args = parser.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    matcher = Matcher(cfg)
    run_at = datetime.now(UTC).replace(second=0, microsecond=0)
    run_stamp = run_at.isoformat().replace("+00:00", "Z")
    cutoff = (run_at.date() - timedelta(days=int(cfg.get("max_age_days", 180)))).isoformat()
    keep_seen_days = int(cfg.get("remember_missing_days", 30))

    history, tracking_since, prev_scope = load_history(args.previous)
    is_baseline = tracking_since is None
    tracking_since = tracking_since or run_stamp
    scope = scope_fingerprint(cfg)
    # When the config widens (new roles, cities, sources...), jobs that only now
    # come into scope were usually open already: mark them as baseline, not NEW.
    rescoped = not is_baseline and prev_scope != scope
    if rescoped:
        print("::notice::Feed scope changed: newly included jobs are marked as already open")
    buckets = {
        k: int(v) for k, v in cfg.get("size_buckets", {"Startup": 25, "Mid-size": 250}).items()
    }
    company_roles: dict[str, int] = {}

    snapshot_at: str | None = None
    manifest: dict[str, Any] = {}
    if not args.local_dir:
        manifest = httpx.get(MANIFEST_URL, timeout=60, follow_redirects=True).json()
        snapshot_at = manifest.get("generated_at")

    jobs: dict[str, dict[str, Any]] = {}
    key_source: dict[tuple[str, str, str], str] = {}
    counts: dict[str, int] = {}
    funnels: dict[str, dict[str, int]] = {}
    failed: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for source in cfg["sources"]:
            entry: dict[str, Any] = {}
            if args.local_dir:
                path = Path(args.local_dir) / f"{source}.parquet"
                if not path.exists():
                    print(f"{source}: no local file, skipping", file=sys.stderr)
                    continue
            else:
                entry = manifest.get("by_ats", {}).get(source) or {}
                if not entry.get("parquet") or not entry.get("rows"):
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
            funnel = dict.fromkeys(FUNNEL_STEPS, 0)
            try:
                found = scan(path, matcher, company_roles, funnel)
            except (OSError, pa.ArrowException) as exc:
                print(f"::warning::{source}: could not read slice ({exc}), skipped")
                failed.append(source)
                continue
            finally:
                if not args.local_dir:
                    path.unlink(missing_ok=True)
            if entry.get("rows") and funnel["rows"] < 0.9 * int(entry["rows"]):
                print(
                    f"::warning::{source}: read {funnel['rows']} rows, manifest says {entry['rows']}"
                )
            found = apply_age_limit(found, cutoff, source)
            funnel["kept"] = len(found)
            funnels[source] = funnel
            added = 0
            for job in found:
                key = dedupe_key(job)
                # Same company + title + location from a *different* source is the
                # same posting seen twice (e.g. on a job board and the ATS).
                # Within one source they're separate openings, so keep both.
                if key_source.get(key, job["ats"]) != job["ats"]:
                    continue
                key_source[key] = job["ats"]
                if job["id"] not in jobs:
                    jobs[job["id"]] = job
                    added += 1
            counts[source] = added
            print(f"{source}: {added} jobs  funnel {funnel}", flush=True)

    # Added times, company size.
    seen_out: dict[str, list[str]] = {}
    for jid, job in jobs.items():
        prev = history.get(jid)
        if prev:
            first, flagged = prev[0], len(prev) > 2
        else:
            first, flagged = run_stamp, rescoped
        job["first_seen"] = first
        seen_out[jid] = [first, run_stamp, "b"] if flagged else [first, run_stamp]
        if flagged:
            job["baseline"] = True
        roles_open = company_roles.get(company_key(job["company"]), 0)
        job["open_roles"] = roles_open
        job["size"] = size_label(roles_open, buckets)
    # Remember jobs that disappeared recently, so a source that fails for a day
    # doesn't make all of its jobs look new when it comes back.
    forget_before = (run_at - timedelta(days=keep_seen_days)).isoformat().replace("+00:00", "Z")
    for jid, entry in history.items():
        if jid not in seen_out and entry[1] >= forget_before:
            seen_out[jid] = entry

    ordered = sorted(
        jobs.values(), key=lambda j: (j["first_seen"], j["posted_at"] or ""), reverse=True
    )[: int(cfg.get("max_jobs", 60000))]

    out = Path(args.out_dir)
    (out / "roles").mkdir(parents=True, exist_ok=True)
    for old in (out / "roles").glob("*.json"):
        old.unlink()

    def tally(key: str) -> dict[str, int]:
        c: dict[str, int] = {}
        for j in ordered:
            vals = j[key] if isinstance(j[key], list) else [j[key]]
            for v in vals:
                c[v] = c.get(v, 0) + 1
        return dict(sorted(c.items(), key=lambda kv: -kv[1]))

    role_files: dict[str, dict[str, Any]] = {}
    for role in cfg["roles"]:
        subset = [j for j in ordered if role in j["roles"]]
        fname = f"roles/{slug(role)}.json"
        (out / fname).write_text(
            json.dumps({"role": role, "jobs": subset}, ensure_ascii=False, separators=(",", ":"))
        )
        role_files[role] = {"file": fname, "count": len(subset)}

    new_in_run = (
        0
        if is_baseline
        else sum(1 for j in ordered if j["first_seen"] == run_stamp and not j.get("baseline"))
    )
    index = {
        "generated_at": run_stamp,
        "snapshot_at": snapshot_at,
        "tracking_since": tracking_since,
        "app_version": args.app_version,
        "size_buckets": buckets,
        "roles": role_files,
        "cities": tally("cities"),
        "places": tally("place"),
        "total": len(ordered),
        "new_in_run": new_in_run,
        "scope_changed": rescoped,
        "counts": counts,
        "funnel": funnels,
        "failed_sources": failed,
        "default_profile": cfg.get("default_profile", {}),
    }
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
    (out / "seen.json").write_text(
        json.dumps(
            {"tracking_since": tracking_since, "scope": scope, "jobs": seen_out},
            separators=(",", ":"),
        )
    )
    legacy = out / "jobs.json"
    legacy.unlink(missing_ok=True)

    sizes = {r: f"{(out / f['file']).stat().st_size / 1e6:.1f}MB" for r, f in role_files.items()}
    print(f"wrote {len(ordered)} jobs, {new_in_run} new; role files {sizes}")
    if failed and len(failed) == len(cfg["sources"]):
        print("every source failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
