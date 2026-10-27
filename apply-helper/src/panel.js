// The small floating panel on application pages. Lives in a shadow root so the
// site's CSS can't break it (and ours can't break the site).
"use strict";

(function (JR) {
  const CSS = `
  :host { all: initial; }
  .card { position: fixed; right: 16px; bottom: 16px; z-index: 2147483647; width: 300px; max-height: 70vh;
    display: flex; flex-direction: column; background: #fff; color: #1d1f1c; border: 1px solid #e3e1da;
    border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,.18); font: 13px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  .card.min .body { display: none; }
  .head { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid #eee; cursor: default; }
  .head b { flex: 1; font-size: 13px; }
  .icon { background: none; border: 0; cursor: pointer; color: #6b6f68; font-size: 14px; padding: 2px 4px; }
  .body { padding: 10px 12px 12px; overflow: auto; display: flex; flex-direction: column; gap: 8px; }
  .btn { font: inherit; padding: 8px 10px; border-radius: 8px; border: 1px solid #d6d4cc; background: #fff; cursor: pointer; color: inherit; }
  .btn.primary { background: #2f6f5e; border-color: #2f6f5e; color: #fff; font-weight: 600; }
  .btn:disabled { opacity: .6; cursor: default; }
  .row { display: flex; gap: 6px; }
  .row .btn { flex: 1; }
  .status { color: #444; }
  .ok { color: #2f9e6e; font-weight: 600; } .warn { color: #b07800; font-weight: 600; }
  ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  li button { all: unset; display: block; width: 100%; box-sizing: border-box; padding: 6px 8px; border-radius: 6px;
    background: #fdf6e3; cursor: pointer; font-size: 12.5px; }
  li button:hover { background: #f9ecc8; }
  li .req { color: #b4521d; font-weight: 700; margin-left: 4px; }
  .note { color: #6b6f68; font-size: 12px; }
  a { color: #2f6f5e; cursor: pointer; text-decoration: underline; }
  @media (prefers-color-scheme: dark) {
    .card { background: #1c1f1c; color: #e8e8e3; border-color: #2c302c; }
    .head { border-color: #2c302c; } .status { color: #cfcfca; }
    .btn { background: #232723; border-color: #3a3f3a; } li button { background: #3a3220; color: #f3e6c4; }
    li button:hover { background: #4a3f27; } .note { color: #9a9e96; } a { color: #7cc4ad; }
  }`;

  JR.Panel = class {
    constructor(handlers) {
      this.h = handlers;
      this.host = document.createElement("div");
      this.host.setAttribute("data-jr-panel", "1");
      const root = this.host.attachShadow({ mode: "open" });
      root.innerHTML = `<style>${CSS}</style>
        <div class="card" part="card">
          <div class="head"><b>Job Radar Apply Helper</b>
            <button class="icon" data-a="min" title="Minimise">–</button>
            <button class="icon" data-a="close" title="Hide on this page">✕</button></div>
          <div class="body">
            <div class="status" data-s="status">Ready to fill this application.</div>
            <button class="btn primary" data-a="fill">Fill this application</button>
            <div data-s="needs"></div>
            <div class="row"><button class="btn" data-a="remember" title="Save the answers you typed into the highlighted questions, for next time">Remember my answers</button></div>
            <div class="note">Fills from your profile and answer bank. Review everything, then submit yourself.
              <a data-a="settings">Settings</a></div>
          </div>
        </div>`;
      this.root = root;
      root.addEventListener("click", (e) => {
        const a = e.target.closest("[data-a]")?.dataset.a;
        if (!a) return;
        if (a === "min") root.querySelector(".card").classList.toggle("min");
        else if (a === "close") this.host.remove();
        else if (a === "settings") chrome.runtime.sendMessage({ type: "openOptions" });
        else this.h[a] && this.h[a]();
      });
      document.documentElement.appendChild(this.host);
    }
    $(s) { return this.root.querySelector(`[data-s="${s}"]`); }
    busy(on) { this.root.querySelector('[data-a="fill"]').disabled = on; }
    status(html) { this.$("status").innerHTML = html; }
    needs(list) {
      const box = this.$("needs");
      box.innerHTML = "";
      if (!list.length) return;
      const title = document.createElement("div");
      title.className = "warn";
      title.textContent = `Needs you (${list.length})`;
      const ul = document.createElement("ul");
      list.slice(0, 25).forEach((f) => {
        const li = document.createElement("li");
        const b = document.createElement("button");
        b.textContent = (f.question || "(unlabelled field)").slice(0, 90);
        if (f.required) { const r = document.createElement("span"); r.className = "req"; r.textContent = "required"; b.appendChild(r); }
        b.onclick = () => { f.els[0].scrollIntoView({ block: "center", behavior: "smooth" }); setTimeout(() => f.els[0].focus(), 300); };
        li.appendChild(b);
        ul.appendChild(li);
      });
      box.append(title, ul);
    }
  };
})(globalThis.JR);
