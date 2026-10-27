// Shared storage helpers and defaults. Loaded first in every content script and
// in the settings page. Everything lives in chrome.storage.local on this machine.
"use strict";

var JR = globalThis.JR || (globalThis.JR = {});

JR.PROFILE_FIELDS = [
  ["firstName", "First name"],
  ["lastName", "Last name"],
  ["email", "Email"],
  ["phone", "Phone (with country code, e.g. +91 98xxxxxxx)"],
  ["city", "Current city"],
  ["country", "Country"],
  ["currentCompany", "Current company"],
  ["currentTitle", "Current job title"],
  ["yearsExperience", "Total years of experience"],
  ["noticePeriod", "Notice period (e.g. 60 days)"],
  ["currentCtc", "Current CTC (e.g. 25 LPA)"],
  ["expectedCtc", "Expected CTC (e.g. 35 LPA)"],
  ["linkedin", "LinkedIn URL"],
  ["github", "GitHub URL"],
  ["portfolio", "Portfolio / website URL"],
];

// Starting answer bank. "pattern" is matched against the question text
// (lowercased): "a|b" means either phrase; /regex/ is a regular expression.
// "answer" may list fallbacks with "|": the first one that matches an option wins.
JR.DEFAULT_ANSWERS = [
  { pattern: "legally authorized to work in india|authorised to work in india|eligible to work in india|right to work in india", answer: "Yes" },
  { pattern: "require sponsorship|require visa sponsorship|need sponsorship|need visa sponsorship|require immigration sponsorship", answer: "No" },
  { pattern: "willing to relocate|open to relocation|open to relocating", answer: "Yes" },
  { pattern: "hybrid|work from the office|work from office|in-office|in office|on-site|onsite", answer: "Yes" },
  { pattern: "how did you hear|where did you hear|how did you find|how did you learn about", answer: "Company website|Careers page|Website|Other" },
  { pattern: "previously worked|previously been employed|worked for us before|former employee|ever worked at", answer: "No" },
  { pattern: "gender", answer: "Decline to self-identify|Prefer not to say|I don't wish to answer|Decline" },
  { pattern: "race|ethnicity", answer: "Decline to self-identify|Prefer not to say|I don't wish to answer|Decline" },
  { pattern: "veteran", answer: "I don't wish to answer|Decline to self-identify|Prefer not to say|I am not a protected veteran" },
  { pattern: "disability", answer: "I don't wish to answer|Decline to self-identify|Prefer not to say|No, I don't have a disability" },
];

JR.DEFAULT_SETTINGS = { autoFill: false, showPanel: true };

JR.load = async function () {
  const got = await chrome.storage.local.get(["profile", "answers", "resume", "settings", "applied"]);
  return {
    profile: got.profile || {},
    answers: Array.isArray(got.answers) ? got.answers : JR.DEFAULT_ANSWERS.slice(),
    resume: got.resume || null,
    settings: { ...JR.DEFAULT_SETTINGS, ...(got.settings || {}) },
    applied: Array.isArray(got.applied) ? got.applied : [],
  };
};

JR.save = (patch) => chrome.storage.local.set(patch);

// Identify a job from an ATS URL in the same "ats:id" form Job Radar uses.
JR.jobKey = function (href) {
  let u;
  try { u = new URL(href); } catch { return null; }
  const host = u.hostname, path = u.pathname;
  const ghParam = u.searchParams.get("gh_jid") || u.searchParams.get("token");
  if (host.includes("greenhouse.io")) {
    const m = path.match(/\/jobs\/(\d+)/) || (ghParam && [0, ghParam]);
    return m ? "greenhouse:" + m[1] : null;
  }
  if (host.includes("lever.co")) {
    const m = path.match(/^\/[^/]+\/([0-9a-f-]{36})/i);
    return m ? "lever:" + m[1] : null;
  }
  if (host.includes("ashbyhq.com")) {
    const m = path.match(/^\/[^/]+\/([0-9a-f-]{36})/i);
    return m ? "ashby:" + m[1] : null;
  }
  return null;
};
