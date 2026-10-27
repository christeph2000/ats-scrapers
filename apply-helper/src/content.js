// Runs on Greenhouse / Lever / Ashby pages: shows the panel when an application
// form is on screen, fills it on request, and notes the job when you submit.
"use strict";

(function (JR) {
  let panel = null;
  let lastFields = [];
  let autoFilledFor = null;
  const helperFilled = new WeakSet();

  const formOnPage = () => {
    const inputs = [...document.querySelectorAll("input, textarea, select")]
      .filter((el) => !["hidden", "submit", "button", "search"].includes(el.type));
    const hasContact = inputs.some((el) => el.type === "email" || /e-?mail/i.test(el.name + el.id)) ||
      inputs.some((el) => el.type === "file");
    return hasContact && inputs.length >= 3;
  };

  const needsYou = (fields) => fields.filter((f) => !JR.isFilled(f) && f.kind !== "file" || (f.kind === "file" && !JR.isFilled(f) && f.required));

  function describe(results, fields) {
    const filled = results.filter((r) => r === "filled").length;
    const missing = needsYou(fields);
    const req = missing.filter((f) => f.required).length;
    return `<span class="ok">Filled ${filled}</span> · ` +
      (missing.length ? `<span class="warn">${missing.length} need you${req ? ` (${req} required)` : ""}</span>` : "nothing left") +
      `<br>Green = filled by the helper, yellow = yours to answer.`;
  }

  async function fillAll() {
    const data = await JR.load();
    panel.busy(true);
    panel.status("Filling…");
    const fields = JR.collectFields(document);
    const results = [];
    for (const f of fields) {
      let r;
      try { r = await JR.fillField(f, data); } catch (e) { console.warn("[Apply Helper]", f.question, e); r = "missing"; }
      results.push(r);
      if (r === "filled") helperFilled.add(f.els[0]);
      JR.mark(f, r === "filled" ? "filled" : JR.isFilled(f) ? "" : "missing");
    }
    // Answers can reveal follow-up questions; pick those up once.
    await new Promise((r) => setTimeout(r, 250));
    const more = JR.collectFields(document).filter((f) => !fields.some((o) => o.els[0] === f.els[0]));
    for (const f of more) {
      const r = await JR.fillField(f, data).catch(() => "missing");
      results.push(r);
      if (r === "filled") helperFilled.add(f.els[0]);
      JR.mark(f, r === "filled" ? "filled" : JR.isFilled(f) ? "" : "missing");
    }
    lastFields = fields.concat(more);
    const empty = !data.profile.email && !data.profile.firstName;
    panel.status(empty
      ? `<span class="warn">Your profile is empty.</span> Add your details in <b>Settings</b> first.`
      : describe(results, lastFields));
    panel.needs(needsYou(lastFields));
    panel.busy(false);
  }

  async function remember() {
    const fields = lastFields.length ? lastFields : JR.collectFields(document);
    const data = await JR.load();
    let saved = 0;
    for (const f of fields) {
      if (["file", "consent"].includes(f.kind) || !JR.isFilled(f)) continue;
      if (helperFilled.has(f.els[0])) continue;              // the helper's own answer
      if (JR.profileKeyFor(f.question, f.els[0])) continue;  // name, email, CTC… live in the profile
      const ans = JR.currentAnswer(f);
      const pattern = JR.patternFromQuestion(f.question);
      if (!ans || !pattern || pattern.length < 4) continue;
      // Personal free-text (cover letters etc.) doesn't belong in the bank.
      if (ans.length > 200) continue;
      const existing = data.answers.find((a) => JR.norm(a.pattern) === pattern);
      if (existing) existing.answer = ans; else data.answers.push({ pattern, answer: ans, learned: true });
      JR.mark(f, "filled");
      saved++;
    }
    await JR.save({ answers: data.answers });
    panel.status(saved ? `<span class="ok">Saved ${saved} answer${saved === 1 ? "" : "s"}</span> to your answer bank.`
      : "Nothing new to remember. Answer the yellow questions first.");
    panel.needs(needsYou(fields));
  }

  // ---- applied tracking ----------------------------------------------------

  function jobMeta() {
    const href = location.href;
    const ref = document.referrer || "";
    const key = JR.jobKey(href) || JR.jobKey(ref);
    const slug = (location.pathname.split("/")[1] || "").replace(/[-_]+/g, " ");
    const company = new URLSearchParams(location.search).get("for") || slug;
    const h1 = document.querySelector("h1, [class*='posting-headline'] h2, [class*='title']");
    const title = (h1 && h1.textContent.trim()) || document.title;
    return { key, url: href.split("#")[0], title: title.slice(0, 140), company: company.replace(/\b\w/g, (c) => c.toUpperCase()) };
  }

  async function recordApplied() {
    const meta = jobMeta();
    const data = await JR.load();
    const exists = data.applied.find((a) => (meta.key && a.key === meta.key) || a.url === meta.url);
    if (exists) return;
    data.applied.unshift({ ...meta, at: new Date().toISOString(), synced: false });
    await JR.save({ applied: data.applied.slice(0, 2000) });
  }

  const looksLikeSubmit = (el) => {
    const b = el.closest("button, input[type=submit]");
    if (!b || b.closest("[data-jr-panel]")) return false;
    const t = (b.textContent || b.value || "").trim().toLowerCase();
    return b.type === "submit" || /^(submit|submit application|apply|send application)\b/.test(t);
  };
  document.addEventListener("submit", () => { if (formOnPage()) recordApplied(); }, true);
  document.addEventListener("click", (e) => { if (looksLikeSubmit(e.target) && formOnPage()) recordApplied(); }, true);

  // ---- lifecycle -----------------------------------------------------------

  // Don't touch the page until it has loaded and the site's framework has
  // finished its first render; adding nodes earlier can make React throw them away.
  let ready = false;
  const whenLoaded = document.readyState === "complete"
    ? Promise.resolve() : new Promise((r) => window.addEventListener("load", r, { once: true }));
  whenLoaded.then(() => setTimeout(() => { ready = true; check(); }, 1200));

  async function check() {
    if (!ready || (panel && panel.closedByUser)) return;
    if (!formOnPage()) return;
    const data = await JR.load();
    if (!data.settings.showPanel) return;
    if (!panel) panel = new JR.Panel({ fill: fillAll, remember });
    else panel.attach();
    if (data.settings.autoFill && autoFilledFor !== location.href) {
      autoFilledFor = location.href;
      fillAll();
    }
  }
  let timer = null;
  new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(check, 600); })
    .observe(document.documentElement, { childList: true, subtree: true });
})(globalThis.JR);
