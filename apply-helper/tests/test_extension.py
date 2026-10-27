"""End-to-end check of the extension in Chromium against mock ATS pages.

The fixtures mimic the structure of real Greenhouse (job-boards), Lever and
Ashby application forms. Playwright serves them at the real hostnames, so the
extension's content scripts load exactly as they would on the live sites.

    pip install playwright pytest
    python -m pytest apply-helper/tests/test_extension.py -q
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

EXT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixtures"
SITE = EXT.parent / "dashboard" / "site"

GH_URL = "https://job-boards.greenhouse.io/acme/jobs/1234567"
LEVER_URL = "https://jobs.lever.co/acme/0f6b1c2e-1111-4222-8333-444455556666/apply"
ASHBY_URL = "https://jobs.ashbyhq.com/acme/9a8b7c6d-1111-4222-8333-444455556666/application"
RADAR_URL = "https://tester.github.io/ats-scrapers/"

PROFILE = {
    "firstName": "Asha",
    "lastName": "Menon",
    "email": "asha@example.com",
    "phone": "+91 98765 43210",
    "city": "Bengaluru",
    "currentCompany": "Example Retail",
    "linkedin": "https://linkedin.com/in/asha",
    "github": "https://github.com/asha",
    "noticePeriod": "60 days",
    "currentCtc": "25 LPA",
    "expectedCtc": "35 LPA",
}


@pytest.fixture(scope="module")
def ctx():
    with playwright.sync_playwright() as p, tempfile.TemporaryDirectory() as profile_dir:
        context = p.chromium.launch_persistent_context(
            profile_dir,
            headless=True,
            channel="chromium",  # the new headless mode, which supports extensions
            args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"],
            executable_path=os.environ.get("CHROMIUM_PATH") or None,
        )
        for url, name in [(GH_URL, "greenhouse.html"), (LEVER_URL, "lever.html"), (ASHBY_URL, "ashby.html")]:
            body = (FIX / name).read_text()
            context.route(url + "*", page_server(body))
        context.route(RADAR_URL + "**", serve_radar)
        sw = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
        resume = base64.b64encode(b"%PDF-1.4 fake resume").decode()
        sw.evaluate(
            """([profile, resume]) => chrome.storage.local.set({
                 profile, resume: {name: 'Asha_Menon.pdf', type: 'application/pdf', size: 20, data: resume},
                 settings: {showPanel: true, autoFill: false}})""",
            [PROFILE, resume],
        )
        # Close the settings tab the extension opens on install.
        for pg in context.pages:
            if pg.url.startswith("chrome-extension://"):
                pg.close()
        yield context, sw
        context.close()


def page_server(body):
    def handler(route, _request=None):
        route.fulfill(body=body, content_type="text/html")

    return handler


def serve_radar(route, _request=None):
    path = route.request.url.split("/ats-scrapers/", 1)[1].split("?")[0] or "index.html"
    if path == "data/index.json":
        idx = {
            "generated_at": "2026-10-28T10:00Z", "tracking_since": "2026-10-21", "app_version": "dev",
            "size_buckets": {"Startup": 25, "Mid-size": 250}, "total": 1,
            "roles": {"Backend": {"file": "roles/backend.json", "count": 1}},
            "cities": {"Bengaluru": 1}, "places": {"india": 1}, "counts": {"greenhouse": 1},
            "default_profile": {"roles": ["Backend"], "city": "Bengaluru", "places": ["city", "india"]},
        }
        return route.fulfill(body=json.dumps(idx), content_type="application/json")
    if path == "data/roles/backend.json":
        job = {"id": "greenhouse:1234567", "title": "Senior Backend Engineer", "company": "Acme", "ats": "greenhouse",
               "roles": ["Backend"], "location": "Bengaluru", "place": "india", "cities": ["Bengaluru"],
               "level": "Senior", "stack": [], "support": [], "posted_at": "2026-10-25",
               "first_seen": "2026-10-27T20:00Z", "open_roles": 30, "size": "Mid-size", "url": GH_URL}
        return route.fulfill(body=json.dumps({"jobs": [job]}), content_type="application/json")
    return route.fulfill(body=(SITE / "index.html").read_text(), content_type="text/html")


def fill(page):
    page.wait_for_selector("[data-jr-panel]", state="attached", timeout=10000)
    page.locator("[data-jr-panel]").locator('button[data-a="fill"]').click()
    # The panel may be briefly detached while the site re-renders; wait until it's back and idle.
    page.wait_for_function(
        """() => { const p = document.querySelector('[data-jr-panel]');
                   return p && !p.shadowRoot.querySelector('[data-a=fill]').disabled &&
                          !/Filling/.test(p.shadowRoot.querySelector('[data-s=status]').textContent); }"""
    )
    return page.locator("[data-jr-panel]").locator('[data-s="status"]').inner_text()


def test_greenhouse(ctx):
    context, _ = ctx
    page = context.new_page()
    page.goto(GH_URL)
    status = fill(page)
    v = page.input_value
    assert v("#first_name") == "Asha" and v("#last_name") == "Menon" and v("#email") == "asha@example.com"
    assert v("#phone") == "+91 98765 43210" and v("#question_1") == "https://linkedin.com/in/asha"
    assert page.eval_on_selector("#question_2", "e => e.options[e.selectedIndex].text") == "Yes"
    assert page.text_content("#q3-value") == "No"                       # react-select style combobox
    assert v("#question_4") == "60 days"
    assert page.eval_on_selector("#question_5", "e => e.options[e.selectedIndex].text") == "Decline to self-identify"
    assert page.eval_on_selector("#resume", "e => e.files[0] && e.files[0].name") == "Asha_Menon.pdf"
    assert v("#question_6") == ""                                        # free text: left to you
    assert not page.is_checked("#consent")                               # consent: never ticked
    assert "Filled 10" in status and "2 need you" in status
    needs = page.locator("[data-jr-panel]").locator('[data-s="needs"] li').all_inner_texts()
    assert [n.split("?")[0] for n in needs] == ["Why do you want to work at Acme", "I agree to the processing of my data per the privacy policyrequired"]
    page.close()


def test_panel_survives_react_rerender_but_stays_closed_when_closed(ctx):
    context, _ = ctx
    page = context.new_page()
    page.goto(GH_URL)
    page.wait_for_timeout(2600)                       # past both of the fixture's re-renders
    assert page.locator("[data-jr-panel]").count() == 1
    page.locator("[data-jr-panel]").locator('button[data-a="close"]').click()
    page.evaluate("() => document.body.appendChild(document.createElement('div'))")  # page activity
    page.wait_for_timeout(1000)
    assert page.locator("[data-jr-panel]").count() == 0
    page.close()


def test_lever(ctx):
    context, _ = ctx
    page = context.new_page()
    page.goto(LEVER_URL)
    fill(page)
    v = lambda n: page.input_value(f'[name="{n}"]')
    assert v("name") == "Asha Menon" and v("email") == "asha@example.com" and v("org") == "Example Retail"
    assert v("urls[LinkedIn]") == "https://linkedin.com/in/asha" and v("urls[GitHub]") == "https://github.com/asha"
    assert page.is_checked('[name="cards[abc][field0]"][value="Yes"]')  # willing to relocate
    assert v("cards[abc][field2]") == "25 LPA"
    assert v("cards[abc][field3]") == ""                                 # not in the bank yet
    assert page.eval_on_selector('[name="resume"]', "e => e.files.length") == 1
    page.close()


def test_learning_then_reuse(ctx):
    context, sw = ctx
    page = context.new_page()
    page.goto(LEVER_URL)
    fill(page)
    page.fill('[name="cards[abc][field3]"]', "6")
    page.check('[name="cards[abc][field1]"][value="Kafka"]')
    page.locator("[data-jr-panel]").locator('button[data-a="remember"]').click()
    page.wait_for_timeout(300)
    answers = sw.evaluate("async () => (await chrome.storage.local.get('answers')).answers")
    learned = {a["pattern"]: a["answer"] for a in answers if a.get("learned")}
    assert learned.get("years of experience with java") == "6"
    assert learned.get("which of these have you used in production") == "Kafka"
    assert not any("email" in p or "name" == p for p in learned)       # profile fields aren't copied
    page.close()
    again = context.new_page()
    again.goto(LEVER_URL)
    fill(again)
    assert again.input_value('[name="cards[abc][field3]"]') == "6"
    assert again.is_checked('[name="cards[abc][field1]"][value="Kafka"]')
    again.close()


def test_ashby(ctx):
    context, _ = ctx
    page = context.new_page()
    page.goto(ASHBY_URL)
    fill(page)
    assert page.input_value("#_systemfield_name") == "Asha Menon"
    assert page.input_value("#_systemfield_email") == "asha@example.com"
    assert page.input_value("#phone-q") == "+91 98765 43210"
    assert page.input_value("#ctc-q") == "35 LPA"
    assert page.eval_on_selector("._yesno button._active", "e => e.textContent") == "No"
    assert page.eval_on_selector("#hear-q", "e => e.options[e.selectedIndex].text") == "Company website"
    assert page.eval_on_selector("#_systemfield_resume", "e => e.files.length") == 1
    page.close()


def test_submit_is_recorded_and_synced_to_job_radar(ctx):
    context, sw = ctx
    page = context.new_page()
    page.goto(GH_URL)
    fill(page)
    page.fill("#question_6", "Because.")
    page.check("#consent")
    page.click("button[type=submit]")
    page.wait_for_selector("body[data-submitted='1']")
    page.wait_for_timeout(300)
    applied = sw.evaluate("async () => (await chrome.storage.local.get('applied')).applied")
    assert applied[0]["key"] == "greenhouse:1234567" and applied[0]["synced"] is False
    page.close()

    radar = context.new_page()
    radar.goto(RADAR_URL)
    radar.wait_for_selector("dialog#setup[open]")
    radar.click("#f-save")
    # The job moves straight from Inbox to the Applied tab.
    radar.wait_for_function("() => /Applied\\s*1/.test(document.getElementById('tabs').textContent)", timeout=5000)
    status = radar.evaluate("() => JSON.parse(localStorage.getItem('jobradar.v1')).status")
    assert status == {"greenhouse:1234567": "applied"}
    radar.wait_for_timeout(300)
    applied = sw.evaluate("async () => (await chrome.storage.local.get('applied')).applied")
    assert applied[0]["synced"] is True
    radar.close()
