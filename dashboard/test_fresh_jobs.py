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
from fresh_jobs import companies_from_feed, select_fresh, slug_from_url

CFG = json.loads((Path(__file__).resolve().parent / "config.json").read_text())
M = Matcher(CFG)
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


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
        posted_at=NOW - timedelta(minutes=minutes_ago),
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
        ("https://careers.acme.com/jobs/123?gh_jid=123", None),
    ],
)
def test_slug_from_url(url, expected):
    assert slug_from_url(url) == expected


def test_companies_from_feed_uses_urls_directory_and_extras(tmp_path):
    (tmp_path / "roles").mkdir()
    (tmp_path / "index.json").write_text(
        json.dumps({"roles": {"Backend": {"file": "roles/backend.json"}}})
    )
    jobs = [
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
    ]
    (tmp_path / "roles" / "backend.json").write_text(json.dumps({"jobs": jobs}))
    found = companies_from_feed(tmp_path, ["lever:extra-co", "bogus:x"])
    assert found == {
        ("greenhouse", "acme"): "Acme",
        ("greenhouse", "10beauty"): "10Beauty",
        ("lever", "extra-co"): "extra-co",
    }


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
            job(ats_id="6", location="Remote", description="Must be based in the United States."),
        ),  # US-only remote
    ]
    out = select_fresh(scraped, {("greenhouse", "acme"): "Acme Corp"}, M, NOW - timedelta(hours=24))
    assert [j["id"] for j in out] == ["greenhouse:2", "greenhouse:1"]
    assert out[0]["company"] == "Acme Corp" and out[0]["posted_ts"] == "2026-10-29T11:55Z"
    assert out[0]["cities"] == ["Bengaluru"] and out[0]["roles"] == ["Backend"]
