// Finding the fields on an application form and filling them.
"use strict";

(function (JR) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const isVisible = (el) => {
    if (el.type === "file") return true; // ATS file inputs are often visually hidden
    const r = el.getBoundingClientRect();
    const st = el.ownerDocument.defaultView.getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
  };

  const SKIP_TYPES = new Set(["hidden", "submit", "button", "reset", "image", "password", "search"]);
  const skip = (el) =>
    el.disabled || el.readOnly || SKIP_TYPES.has(el.type) ||
    /captcha|recaptcha|g-recaptcha|h-captcha|honeypot/i.test(el.name + " " + el.id + " " + el.className) ||
    el.closest("[data-jr-panel], [aria-hidden='true'], .g-recaptcha, .h-captcha");

  JR.isRequired = (el, question) =>
    el.required || el.getAttribute("aria-required") === "true" || /\*\s*$|\*/.test(question || "");

  // React and similar frameworks ignore plain `el.value = x`; use the native
  // setter and fire the events a real keystroke would.
  function setNativeValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    el.focus();
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }
  JR.setNativeValue = setNativeValue;

  function clickLike(el) {
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      const Ev = type.startsWith("pointer") && window.PointerEvent ? PointerEvent : MouseEvent;
      el.dispatchEvent(new Ev(type, { bubbles: true, cancelable: true, view: el.ownerDocument.defaultView }));
    }
  }

  // The element a react-select style dropdown uses to show the chosen value.
  function comboValue(el) {
    let node = el.parentElement;
    for (let i = 0; node && i < 4; i++, node = node.parentElement) {
      const v = node.querySelector("[class*='single-value'], [class*='singleValue'], [class*='multi-value'], [class*='multiValue']");
      if (v) return v;
    }
    return null;
  }

  // ---- discover ----------------------------------------------------------

  // Returns a list of "fields": {kind, els, question, required}
  JR.collectFields = function (root) {
    const doc = root.ownerDocument || root;
    const fields = [];
    const groups = new Map();
    let groupSeq = 0;
    root.querySelectorAll("input, textarea, select").forEach((el) => {
      if (skip(el)) return;
      if (el.type === "radio" || el.type === "checkbox") {
        if (!isVisible(el) && !isVisible(el.closest("label") || el)) return;
        let key = el.type + ":" + el.name;
        if (!el.name) {
          const box = el.closest("fieldset, [role=radiogroup], [role=group]") || el.parentElement?.parentElement || el.parentElement;
          if (!box.dataset.jrGroup) box.dataset.jrGroup = String(++groupSeq);
          key = el.type + ":#" + box.dataset.jrGroup;
        }
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(el);
        return;
      }
      if (!isVisible(el)) return;
      const combobox = el.getAttribute("role") === "combobox" || /react-select|select__input/.test(el.id + " " + el.className) ||
        el.getAttribute("aria-autocomplete") === "list";
      const kind = el.type === "file" ? "file" : el.tagName === "SELECT" ? "select" : combobox ? "combobox" : "text";
      const question = JR.questionFor(el);
      fields.push({ kind, els: [el], question, required: JR.isRequired(el, question) });
    });
    // Yes/No asked with two buttons (Ashby does this) instead of radios.
    const seen = new Set();
    root.querySelectorAll("button").forEach((b) => {
      const box = b.parentElement;
      if (!box || seen.has(box) || b.type === "submit" || b.closest("[data-jr-panel], form[role=search]")) return;
      const btns = [...box.children].filter((c) => c.tagName === "BUTTON");
      const labels = btns.map((x) => x.textContent.trim().toLowerCase()).sort().join("/");
      if (btns.length === 2 && labels === "no/yes" && isVisible(btns[0])) {
        seen.add(box);
        const question = JR.groupQuestion(btns);
        fields.push({ kind: "buttons", els: btns, question, required: JR.isRequired(btns[0], question) });
      }
    });
    for (const els of groups.values()) {
      // A lone checkbox ("I agree to the privacy policy") is a consent box: never tick it for you.
      const kind = els[0].type === "radio" ? "radio" : els.length === 1 ? "consent" : "checkbox";
      const question = kind === "consent" ? JR.optionText(els[0]) : JR.groupQuestion(els);
      fields.push({ kind, els, question, required: els.some((e) => JR.isRequired(e, question)) });
    }
    // Keep document order so the panel lists questions top to bottom.
    fields.sort((a, b) => (a.els[0].compareDocumentPosition(b.els[0]) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
    return fields;
  };

  JR.isFilled = function (field) {
    const el = field.els[0];
    if (field.kind === "radio" || field.kind === "checkbox" || field.kind === "consent") return field.els.some((e) => e.checked);
    if (field.kind === "buttons") {
      return field.els.some((b) => b.getAttribute("aria-pressed") === "true" || b.getAttribute("aria-checked") === "true" ||
        b.getAttribute("data-jr-picked") === "1" || /\b(active|selected|checked|isSelected)\b|_active|_selected/i.test(b.className));
    }
    if (field.kind === "file") return el.files && el.files.length > 0 || !!el.closest("form")?.querySelector("[data-jr-resume-done]");
    if (field.kind === "combobox") {
      const shown = comboValue(el);
      return !!(shown && shown.textContent.trim()) || !!el.value.trim();
    }
    if (field.kind === "select") return el.selectedIndex > 0 || (el.value && !/^(|select|choose)/i.test(el.options[el.selectedIndex]?.text || ""));
    return !!el.value.trim();
  };

  // ---- fill --------------------------------------------------------------

  async function fillCombobox(el, answer) {
    for (const alt of String(answer).split("|")) {
      setNativeValue(el, "");
      el.focus();
      clickLike(el);
      setNativeValue(el, alt.trim());
      let options = [];
      for (let i = 0; i < 15 && !options.length; i++) {
        await sleep(100);
        const doc = el.ownerDocument;
        const listId = el.getAttribute("aria-controls") || el.getAttribute("aria-owns");
        const scope = (listId && doc.getElementById(listId)) || doc;
        options = [...scope.querySelectorAll("[role=option], [id*='-option-'], [class*='option']")]
          .filter((o) => o.offsetParent !== null && o.textContent.trim());
      }
      const pick = JR.bestOption(alt, options.map((o) => ({ text: o.textContent, node: o })));
      if (pick) { clickLike(pick.node); await sleep(60); return true; }
    }
    el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    setNativeValue(el, "");
    return false;
  }

  async function fillFile(el, resume) {
    if (!resume || !resume.data) return false;
    const bytes = Uint8Array.from(atob(resume.data), (c) => c.charCodeAt(0));
    const file = new File([bytes], resume.name || "resume.pdf", { type: resume.type || "application/pdf" });
    const dt = new DataTransfer();
    dt.items.add(file);
    el.files = dt.files;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.setAttribute("data-jr-resume-done", "1");
    return true;
  }

  const isResumeQuestion = (field) => /resume|cv\b|curriculum/i.test(field.question + " " + field.els[0].name + " " + field.els[0].id) &&
    !/cover/i.test(field.question + " " + field.els[0].name + " " + field.els[0].id);

  // Fill one field. Returns "filled", "skipped" (already had a value) or "missing".
  JR.fillField = async function (field, data) {
    if (JR.isFilled(field)) return "skipped";
    if (field.kind === "consent") return "missing";
    if (field.kind === "file") {
      if (!isResumeQuestion(field)) return "missing";
      return (await fillFile(field.els[0], data.resume)) ? "filled" : "missing";
    }
    const ans = JR.answerFor(field.question, field.els[0], data);
    if (!ans) return "missing";
    const el = field.els[0];
    if (field.kind === "text") { setNativeValue(el, ans.value.split("|")[0]); return "filled"; }
    if (field.kind === "select") {
      const pick = JR.bestOption(ans.value, [...el.options].map((o) => ({ text: o.text, node: o })));
      if (!pick) return "missing";
      setNativeValue(el, pick.node.value);
      return "filled";
    }
    if (field.kind === "radio") {
      const pick = JR.bestOption(ans.value, field.els.map((e) => ({ text: JR.optionText(e), node: e })));
      if (!pick) return "missing";
      pick.node.click();
      return "filled";
    }
    if (field.kind === "checkbox") {
      const wanted = String(ans.value).split(/[,;]/).map((s) => s.trim()).filter(Boolean);
      let n = 0;
      for (const w of wanted) {
        const pick = JR.bestOption(w, field.els.map((e) => ({ text: JR.optionText(e), node: e })));
        if (pick && !pick.node.checked) { pick.node.click(); n++; }
      }
      return n ? "filled" : "missing";
    }
    if (field.kind === "combobox") return (await fillCombobox(el, ans.value)) ? "filled" : "missing";
    if (field.kind === "buttons") {
      const pick = JR.bestOption(ans.value, field.els.map((b) => ({ text: b.textContent, node: b })));
      if (!pick) return "missing";
      pick.node.click();
      pick.node.setAttribute("data-jr-picked", "1");
      return "filled";
    }
    return "missing";
  };

  // The current answer a user gave, to remember it in the answer bank.
  JR.currentAnswer = function (field) {
    const el = field.els[0];
    if (field.kind === "radio" || field.kind === "checkbox") {
      return field.els.filter((e) => e.checked).map((e) => JR.optionText(e)).join(", ");
    }
    if (field.kind === "buttons") {
      const on = field.els.find((b) => b.getAttribute("aria-pressed") === "true" || b.getAttribute("data-jr-picked") === "1" ||
        /\b(active|selected|checked)\b|_active|_selected/i.test(b.className));
      return on ? on.textContent.trim() : "";
    }
    if (field.kind === "select") return el.selectedIndex > 0 ? el.options[el.selectedIndex].text.trim() : "";
    if (field.kind === "combobox") {
      const shown = comboValue(el);
      return (shown ? shown.textContent : el.value).trim();
    }
    if (field.kind === "text") return el.value.trim();
    return "";
  };

  JR.mark = function (field, state) {
    for (const el of field.els) {
      const target = field.kind === "buttons" ? el.parentElement
        : field.kind === "radio" || field.kind === "checkbox" || field.kind === "consent"
        ? (el.closest("fieldset, [role=radiogroup], [role=group]") || el.parentElement)
        : field.kind === "combobox" ? (el.closest("[class*='control'], [class*='container']") || el) : el;
      target.style.outline = state === "filled" ? "2px solid #2f9e6e" : state === "missing" ? "2px solid #e0a100" : "";
      target.style.outlineOffset = "2px";
      target.setAttribute("data-jr-state", state || "");
    }
  };
})(globalThis.JR);
