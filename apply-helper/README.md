# Job Radar Apply Helper

A Chrome / Edge extension that fills job application forms on **Greenhouse,
Lever and Ashby** from your profile, so you only review and press Submit.

- Fills name, email, phone, LinkedIn/GitHub, current company, notice period,
  CTC, your resume, and common questions (work authorization, sponsorship,
  relocation, "how did you hear", equal-opportunity questions).
- Outlines what it filled in **green** and what needs you in **yellow**, with a
  panel listing the questions left.
- **Learns:** answer a yellow question yourself, press **Remember my answers**,
  and it's filled automatically on the next application.
- **Never** submits for you, never ticks consent boxes, and never overwrites
  anything you've already typed.
- When you submit, it notes the job; next time you open your
  [Job Radar](../dashboard/README.md) page, that job is marked **Applied**.

Everything (profile, resume, answers) is stored in the extension's local
storage on your computer. Nothing is sent anywhere.

## Install (2 minutes)

1. Download this folder (or the `apply-helper.zip` release) and unzip it somewhere permanent.
2. Open `chrome://extensions` (Edge: `edge://extensions`) and turn on **Developer mode**.
3. Click **Load unpacked** and pick the `apply-helper` folder.
4. The settings page opens: fill in **Your details**, upload your **Resume**,
   and check the starter **Answer bank** (edit the answers to match you).
5. Pin the extension; its toolbar button opens the settings.

## Using it

1. Open a job application (from Job Radar or anywhere) on Greenhouse, Lever or Ashby.
2. Press **Fill this application** in the panel (bottom-right), or turn on
   *Fill automatically* in settings.
3. Answer the yellow questions. Press **Remember my answers** to save them for next time.
4. Review everything, then submit yourself.

Company career pages that embed a Greenhouse form (`boards.greenhouse.io/embed/…`) work too.

## Answer bank format

| Question contains | Answer |
|---|---|
| `notice period\|how soon can you join` | `60 days` |
| `gender` | `Decline to self-identify\|Prefer not to say` |

`|` in the question means "any of these phrases"; `|` in the answer lists
fallbacks for dropdowns and radio buttons, and the first one the form offers is
used. Wrap a question in `/…/` for a regular expression. Longer (more specific)
phrases win over shorter ones, and answer-bank entries win over your profile.

## Limits

- Free-text questions ("Why do you want to join us?") are left to you.
- Workday, SuccessFactors, iCIMS etc. aren't supported yet (multi-page forms
  with accounts per company).
- If a site redesigns its form, some fields may stop being recognised; they'll
  just show as yellow. Please report what broke.
- Dropdowns built from custom components are handled for the common
  React-select style; unusual ones stay yellow.

## Tests

`tests/test_extension.py` loads the extension into Chromium and runs it
against mock Greenhouse, Lever and Ashby forms (served at the real hostnames),
checking filling, learning, consent handling, submit tracking and the Job Radar
sync.

```bash
pip install pytest playwright && playwright install chromium
python -m pytest apply-helper/tests/test_extension.py -q
```
