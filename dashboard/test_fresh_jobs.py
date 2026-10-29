"""Tests for the fresh-jobs script (no network: scraper results are faked).

Run: python -m pytest dashboard/test_fresh_jobs.py -q
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_jobs import Matcher
from fresh_jobs import (
    companies_from_feed,
    companies_from_lists,
    merge,
    select_fresh,
    slug_from_url,
    window_counts,
)

CFG = json.loads((Path(__file__).resolve().parent / "config.json").read_text())
M = Matcher(CFG)
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
ACME = {("greenhouse", "acme"): {"name": "Acme Corp", "origin": "feed"}}


class FakeJob:
    def __init__(self, **kw):
        self.kw = kw
        self.posted_at = kw.get("posted_at")

    def model_dump(self, mode="python"):
        return dict(self.kw)


def job(
    ats="greenhouse",
    ats_id="1",
    title="Senior Backend Engineer",
    location="Bengaluru, India",
    minutes_ago=30,
    description="We build Java services.",
    posted_at=None,
):
    return FakeJob(
        url=f"https://job-boards.greenhouse.io/acme/jobs/{ats_id}",
        title=title,
        company="acme",
        ats_type=ats,
        ats_id=ats_id,
        location=location,
        country_iso=None,
        is_remote=None,
        description=description,
        posted_at=posted_at or NOW - timedelta(minutes=minutes_ago),
    )


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://job-boards.greenhouse.io/acme/jobs/123", ("greenhouse", "acme")),
        (
            "https://boards.greenhouse.io/embed/job_app?for=crossriverbank&token=1",
            ("greenhouse", "crossriverbank"),
        ),
        ("https://jobs.lever.co/acme/0f6b1c2e-1111-4222-8333-444455556666", ("lever", "acme")),
        ("https://jobs.eu.lever.co/acme/x", ("lever", "acme")),
        ("https://jobs.ashbyhq.com/acme/9a8b/application", ("ashby", "acme")),
        ("https://jobs.smartrecruiters.com/Bosch/744000012345678", ("smartrecruiters", "Bosch")),
        ("https://acme.recruitee.com/o/backend-engineer", ("recruitee", "acme")),
        ("https://apply.workable.com/acme/j/ABC123/", ("workable", "acme")),
        ("https://acme.keka.com/careers/jobdetails/42", ("keka", "https://acme.keka.com/careers")),
        ("https://airtel.darwinbox.in/ms/candidate/careers/a1b2", ("darwinbox", "airtel")),
        ("https://careers.acme.com/jobs/123?gh_jid=123", None),
    ],
)
def test_slug_from_url(url, expected):
    assert slug_from_url(url) == expected


def _feed(tmp_path, jobs):
    (tmp_path / "roles").mkdir()
    (tmp_path / "index.json").write_text(json.dumps({"roles": {"B": {"file": "roles/b.json"}}}))
    (tmp_path / "roles" / "b.json").write_text(json.dumps({"jobs": jobs}))


def test_companies_from_feed_uses_urls_directory_and_extras(tmp_path):
    _feed(
        tmp_path,
        [
            {
                "ats": "greenhouse",
                "company": "Acme",
                "url": "https://job-boards.greenhouse.io/acme/jobs/1",
            },
            # Custom careers domain: slug comes from the repo's company list.
            {
                "ats": "greenhouse",
                "company": "10Beauty",
                "url": "https://10beauty.com/careers?gh_jid=5",
            },
            {"ats": "workday", "company": "BigCo", "url": "https://bigco.wd1.myworkdayjobs.com/x"},
        ],
    )
    found = companies_from_feed(tmp_path, ["lever:extra-co", "bogus:x"])
    assert found == {
        ("greenhouse", "acme"): {"name": "Acme", "origin": "feed"},
        ("greenhouse", "10beauty"): {"name": "10Beauty", "origin": "feed"},
        ("lever", "extra-co"): {"name": "extra-co", "origin": "extra"},
    }


def test_board_slugs_get_proper_names_from_the_company_lists(tmp_path):
    _feed(
        tmp_path,
        [
            {
                "ats": "greenhouse",
                "company": "1910genetics",
                "url": "https://job-boards.greenhouse.io/1910genetics/jobs/1",
            }
        ],
    )
    assert companies_from_feed(tmp_path, [])[("greenhouse", "1910genetics")]["name"] == "1910"


def test_whole_lists_for_small_platforms_and_merge_keeps_first_origin():
    lists = companies_from_lists(["darwinbox"])
    assert ("darwinbox", "airtel") in lists and lists[("darwinbox", "airtel")] == {
        "name": "Airtel",
        "origin": "list",
    }
    merged = merge({("darwinbox", "airtel"): {"name": "Airtel", "origin": "feed"}}, lists)
    assert merged[("darwinbox", "airtel")]["origin"] == "feed"


def test_select_fresh_keeps_recent_in_scope_jobs_newest_first():
    scraped = [
        ("greenhouse", "acme", job(ats_id="1", minutes_ago=30)),
        ("greenhouse", "acme", job(ats_id="2", minutes_ago=5)),
        ("greenhouse", "acme", job(ats_id="3", minutes_ago=60 * 30)),  # too old
        ("greenhouse", "acme", job(ats_id="4", location="San Francisco, CA")),  # out of scope
        ("greenhouse", "acme", job(ats_id="5", title="Account Executive")),  # not engineering
        (
            "greenhouse",
            "acme",
            job(ats_id="7", title="Clinical (SAS) Programmer- Lead"),
        ),  # not software
        (
            "greenhouse",
            "acme",
            job(ats_id="6", location="Remote", description="Must be based in the United States."),
        ),
    ]
    out = select_fresh(scraped, ACME, M, NOW - timedelta(hours=24))
    assert [j["id"] for j in out] == ["greenhouse:2", "greenhouse:1"]
    assert out[0]["company"] == "Acme Corp" and out[0]["posted_ts"] == "2026-10-29T11:55Z"
    assert (
        out[0]["cities"] == ["Bengaluru"]
        and out[0]["roles"] == ["Backend"]
        and out[0]["origin"] == "feed"
    )


def test_remote_job_without_description_is_dropped():
    out = select_fresh(
        [("greenhouse", "acme", job(location="Remote", description=""))],
        ACME,
        M,
        NOW - timedelta(hours=24),
    )
    assert out == []


def test_date_only_postings_are_marked_approx_and_only_count_in_24h():
    today = datetime(2026, 10, 29, tzinfo=UTC)  # platform gave a date, no time
    scraped = [
        ("greenhouse", "acme", job(ats_id="8", posted_at=today)),
        ("greenhouse", "acme", job(ats_id="9", minutes_ago=20)),
    ]
    out = select_fresh(scraped, ACME, M, NOW - timedelta(hours=24))
    approx = {j["id"]: j.get("approx", False) for j in out}
    assert approx == {"greenhouse:8": True, "greenhouse:9": False}
    assert window_counts(out, NOW) == {"1h": 1, "3h": 1, "6h": 1, "24h": 2}
