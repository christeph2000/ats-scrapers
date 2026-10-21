"""Tests for the Job Radar filters, using real strings seen in the dataset.

Run: python -m pytest dashboard/test_build_jobs.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_jobs import Matcher, clean_text, support_tags

CFG = json.loads((Path(__file__).resolve().parent / "config.json").read_text())
M = Matcher(CFG)


def place(location: str, desc: str = "", country: str | None = None, remote=None) -> str | None:
    text = clean_text(desc)
    return M.place(location, country, remote, text, support_tags(text))


@pytest.mark.parametrize(
    "loc",
    [
        "Bangalore, India",
        "Bengaluru, Karnataka, India, APAC",
        "Bangalore, IND; Hyderabad, IND",
        "Hybrid - Bangalore, India",
        "IN-Karnataka-Bangalore",
        "India - Bengaluru; India - Chennai ; India - Gurugram",
    ],
)
def test_bengaluru(loc: str) -> None:
    assert place(loc) == "home"


@pytest.mark.parametrize(
    "loc",
    ["India", "Pune, Maharashtra, India", "Remote - India", "India (Remote-First)", "Gurugram"],
)
def test_rest_of_india(loc: str) -> None:
    assert place(loc) == "india"


@pytest.mark.parametrize(
    "loc", ["Remote", "Remote, Global", "Remote (APAC)", "Anywhere", "Fully Remote"]
)
def test_open_remote(loc: str) -> None:
    assert place(loc, "We are a distributed team.", remote=True) == "remote"


@pytest.mark.parametrize(
    "loc",
    [
        "San Francisco",
        "London",
        "Sydney",
        "Chile",
        "Chile, Remote",
        "Peru - Fully Remote",
        "South Africa - Gauteng (ZipRecruiter)",
        "Remote - Costa Rica",
        "Pittsburgh, PA or Remote",
        "Remote/Hybrid if local to Maryland",
        "Remote - US",
        "Remote - EMEA",
        "AU: Sydney (45 Clarence St)",
    ],
)
def test_other_places_dropped(loc: str) -> None:
    # The dataset often flags these as remote; without visa/relocation they must go.
    assert place(loc, "Great team.", remote=True) is None


@pytest.mark.parametrize(
    "desc",
    [
        "Candidates must be based in the United States.",
        "You must be authorized to work in the US.",
        "This is a US-based role.",
        "Open to candidates residing in Europe.",
        "Must overlap with PST hours.",
    ],
)
def test_remote_restricted_by_description(desc: str) -> None:
    assert place("Remote", desc) is None


def test_remote_mentioning_india_is_kept() -> None:
    assert place("Remote", "We hire in Europe and from India.") == "remote"


def test_remote_with_other_country_code_dropped() -> None:
    assert place("Remote", "", country="US") is None


@pytest.mark.parametrize(
    "desc,expected",
    [
        (
            "Visa sponsorship: we do sponsor visas, however we aren't able to sponsor every role.",
            ["Visa sponsorship"],
        ),
        (
            "We do sponsor and take over sponsorship of employment visas for this role.",
            ["Visa sponsorship"],
        ),
        (
            "We can help you relocate to the UK. We can sponsor visas.",
            ["Visa sponsorship", "Relocation"],
        ),
        ("Certain positions may be eligible for visa sponsorship.", ["Visa sponsorship"]),
        ("Relocation assistance is available to meet this expectation.", ["Relocation"]),
        (
            "Generous pre-IPO stock options, relocation assistance and annual bonuses.",
            ["Relocation"],
        ),
        ("We offer relocation assistance and an allowance to eligible candidates.", ["Relocation"]),
        # negatives
        ("US persons only. Visa sponsorship is not available.", []),
        ("This role does not provide visa sponsorship.", []),
        ("Position is not eligible for visa sponsorship.", []),
        (
            "Unfortunately we are unable to provide visa sponsorship or support visa applications.",
            [],
        ),
        (
            "Must have work authorization that does not now or in the future require sponsorship of a visa.",
            [],
        ),
        ("Lincoln Property Company does not sponsor employment visas, including H-1B.", []),
        ("Relocation benefits are not available for this position.", []),
        ("This is not a remote position and will require relocation if not already local.", []),
        ("Experience with Visa, Mastercard and Amex payment rails.", []),
        ("Company-sponsored events and an annual stipend.", []),
    ],
)
def test_support_tags(desc: str, expected: list[str]) -> None:
    assert support_tags(clean_text(desc)) == expected


def test_abroad_kept_only_with_support() -> None:
    assert (
        place("Berlin, Germany", "We offer full visa sponsorship and relocation support.")
        == "abroad"
    )
    assert place("Berlin, Germany", "Visa sponsorship is not available.") is None


def test_multi_location_display() -> None:
    loc = "Bengaluru, India; Sydney, Australia; Santiago, Chile"
    assert M.display_location(loc, "home") == "Bengaluru, India (+2 outside India)"


@pytest.mark.parametrize(
    "title,ok",
    [
        ("Senior Backend Engineer", True),
        ("Software Engineer II, Platform", True),
        ("Engineering Manager", False),
        ("Frontend Engineer", False),
        ("Software Engineer Intern", False),
    ],
)
def test_titles(title: str, ok: bool) -> None:
    assert M.title_ok(title) is ok
