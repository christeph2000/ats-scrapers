"use strict";
(async function (JR) {
  const $ = (id) => document.getElementById(id);
  let state = await JR.load();
  let savedTimer = null;
  const flash = () => { $("saved").hidden = false; clearTimeout(savedTimer); savedTimer = setTimeout(() => ($("saved").hidden = true), 1200); };
  const save = async (patch) => { await JR.save(patch); flash(); };

  // ---- profile ----
  const types = { email: "email", phone: "tel", linkedin: "url", github: "url", portfolio: "url" };
  $("profile").innerHTML = JR.PROFILE_FIELDS.map(([k, label]) =>
    `<label>${label}<input type="${types[k] || "text"}" data-k="${k}" autocomplete="off"></label>`).join("");
  $("profile").querySelectorAll("input").forEach((inp) => {
    inp.value = state.profile[inp.dataset.k] || "";
    inp.addEventListener("input", () => { state.profile[inp.dataset.k] = inp.value.trim(); save({ profile: state.profile }); });
  });

  // ---- resume ----
  function showResume() {
    $("resume-name").textContent = state.resume ? `${state.resume.name} (${Math.round(state.resume.size / 1024)} KB)` : "No resume uploaded";
    $("resume-clear").hidden = !state.resume;
  }
  $("resume").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    if (f.size > 5 * 1024 * 1024) { $("resume-name").textContent = "That file is over 5 MB."; return; }
    const buf = new Uint8Array(await f.arrayBuffer());
    let bin = "";
    for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    state.resume = { name: f.name, type: f.type || "application/pdf", size: f.size, data: btoa(bin) };
    await save({ resume: state.resume });
    showResume();
  });
  $("resume-clear").onclick = async () => { state.resume = null; await chrome.storage.local.remove("resume"); flash(); showResume(); };
  showResume();

  // ---- answer bank ----
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function drawBank() {
    $("bank").innerHTML = state.answers.map((a, i) => `<tr>
      <td><input type="text" data-i="${i}" data-f="pattern" value="${esc(a.pattern)}" placeholder="e.g. notice period"></td>
      <td><input type="text" data-i="${i}" data-f="answer" value="${esc(a.answer)}" placeholder="your answer">${a.learned ? ' <span class="tag">learned</span>' : ""}</td>
      <td><button class="del" data-del="${i}" title="Remove">✕</button></td></tr>`).join("");
  }
  $("bank").addEventListener("input", (e) => {
    const t = e.target;
    if (!t.dataset.f) return;
    state.answers[+t.dataset.i][t.dataset.f] = t.value;
    save({ answers: state.answers });
  });
  $("bank").addEventListener("click", (e) => {
    const i = e.target.dataset.del;
    if (i === undefined) return;
    state.answers.splice(+i, 1);
    save({ answers: state.answers });
    drawBank();
  });
  $("add").onclick = () => {
    state.answers.push({ pattern: "", answer: "" });
    drawBank();
    $("bank").querySelector("tr:last-child input").focus();
  };
  $("reset-bank").onclick = () => {
    const have = new Set(state.answers.map((a) => a.pattern));
    JR.DEFAULT_ANSWERS.forEach((d) => { if (!have.has(d.pattern)) state.answers.push({ ...d }); });
    save({ answers: state.answers });
    drawBank();
  };
  drawBank();

  // ---- settings ----
  ["showPanel", "autoFill"].forEach((k) => {
    $("s-" + k).checked = !!state.settings[k];
    $("s-" + k).onchange = (e) => { state.settings[k] = e.target.checked; save({ settings: state.settings }); };
  });

  // ---- applied log ----
  function drawApplied() {
    $("applied-count").textContent = state.applied.length ? `(${state.applied.length})` : "";
    $("applied").innerHTML = state.applied.length ? state.applied.map((a) => `<li>
      <span><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title || a.url)}</a> <span class="muted">${esc(a.company || "")}</span></span>
      <span class="muted">${new Date(a.at).toLocaleDateString()}${a.synced ? " · in Job Radar" : ""}</span></li>`).join("")
      : `<li class="muted">Nothing yet.</li>`;
  }
  $("applied-clear").onclick = async () => { state.applied = []; await save({ applied: [] }); drawApplied(); };
  drawApplied();
  chrome.storage.onChanged.addListener((ch) => { if (ch.applied) { state.applied = ch.applied.newValue || []; drawApplied(); } });

  // ---- backup ----
  $("export").onclick = async () => {
    const data = await chrome.storage.local.get(null);
    const blob = new Blob([JSON.stringify({ v: 1, ...data }, null, 2)], { type: "application/json" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "apply-helper-backup.json" });
    a.click();
    URL.revokeObjectURL(a.href);
  };
  $("import").onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const data = JSON.parse(await f.text());
      const patch = {};
      for (const k of ["profile", "answers", "resume", "settings", "applied"]) if (k in data) patch[k] = data[k];
      await chrome.storage.local.set(patch);
      location.reload();
    } catch { alert("That file isn't an Apply Helper backup."); }
  };
})(globalThis.JR);
