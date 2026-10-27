// Runs on Job Radar pages: hands over the jobs you submitted through the
// helper so Job Radar can mark them Applied. Only talks to pages that
// identify as Job Radar (<meta name="job-radar">).
"use strict";

(async function (JR) {
  if (!document.querySelector('meta[name="job-radar"]')) return;
  const { applied } = await JR.load();
  const pending = applied.filter((a) => !a.synced);
  if (!pending.length) return;

  window.addEventListener("message", async (e) => {
    if (e.source !== window || !e.data || e.data.source !== "jobradar-page" || e.data.type !== "ack") return;
    const done = new Set(e.data.keys || []);
    const data = await JR.load();
    data.applied.forEach((a) => { if (done.has(a.key || a.url)) a.synced = true; });
    await JR.save({ applied: data.applied });
  });
  window.postMessage({
    source: "jobradar-ext",
    type: "applied",
    items: pending.map(({ key, url, title, company, at }) => ({ key, url, title, company, at })),
  }, location.origin);
})(globalThis.JR);
