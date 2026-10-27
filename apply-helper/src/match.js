// Working out what a form field is asking, and what to answer.
"use strict";

(function (JR) {
  const norm = (s) => String(s || "")
    .toLowerCase()
    .replace(/\*/g, " ")
    .replace(/[’']/g, "'")
    .replace(/[^a-z0-9'+#./ ]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  JR.norm = norm;

  const textOf = (el) => (el ? (el.innerText || el.textContent || "") : "");

  // The visible question for a field: <label for>, aria-labelledby, a wrapping
  // <label>, the fieldset legend, or the question block the ATS wraps it in.
  JR.questionFor = function (el) {
    const doc = el.ownerDocument;
    const parts = [];
    const lb = el.getAttribute("aria-labelledby");
    if (lb) lb.split(/\s+/).forEach((id) => parts.push(textOf(doc.getElementById(id))));
    if (el.id && !parts.join("").trim()) {
      const lab = doc.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab) parts.push(textOf(lab));
    }
    if (!parts.join("").trim()) {
      const wrap = el.closest("label");
      if (wrap && !["radio", "checkbox"].includes(el.type)) parts.push(textOf(wrap));
    }
    if (!parts.join("").trim()) {
      const box = el.closest(
        "fieldset, .application-question, .application-field, .field, [class*='question'], " +
        "[class*='Question'], [class*='fieldEntry'], [class*='field-entry'], .form-group, li"
      );
      if (box) {
        const head = box.querySelector("legend, label, .application-label, [class*='label'], [class*='Label'], h3, h4, p");
        parts.push(textOf(head || box).split("\n")[0]);
      }
    }
    if (!parts.join("").trim()) parts.push(el.getAttribute("aria-label") || el.placeholder || el.name || "");
    return parts.join(" ").replace(/\s+/g, " ").trim();
  };

  // For radio/checkbox options: the text of that one option.
  JR.optionText = function (el) {
    if (el.tagName === "BUTTON") return textOf(el).trim();
    const doc = el.ownerDocument;
    if (el.id) {
      const lab = doc.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab) return textOf(lab).trim();
    }
    const wrap = el.closest("label");
    if (wrap) return textOf(wrap).trim();
    return el.getAttribute("aria-label") || el.value || "";
  };

  // Question for a group of radios/checkboxes sharing a name (or container).
  JR.groupQuestion = function (inputs) {
    const first = inputs[0];
    const fs = first.closest("fieldset");
    if (fs) {
      const lg = fs.querySelector("legend");
      if (lg) return textOf(lg).trim();
    }
    const lb = first.closest("[role=radiogroup], [role=group]");
    if (lb && lb.getAttribute("aria-labelledby")) {
      const t = textOf(first.ownerDocument.getElementById(lb.getAttribute("aria-labelledby")));
      if (t.trim()) return t.trim();
    }
    // Walk up until the container holds more text than just the options.
    const options = inputs.map((i) => JR.optionText(i)).filter(Boolean);
    const group = new Set(inputs);
    let node = first.parentElement;
    for (let depth = 0; node && depth < 6; depth++, node = node.parentElement) {
      // Don't climb into a container that holds other questions' fields.
      const others = [...node.querySelectorAll("input, select, textarea, button")].filter((x) => !group.has(x) && x.type !== "hidden");
      if (others.length) break;
      // The container's text with the option labels cut out; what's left is the question.
      let t = textOf(node);
      for (const o of options) t = t.split(o).join("\n");
      const q = t.split("\n").map((s) => s.trim()).find((l) => norm(l).length > 1);
      if (q && node.contains(inputs[inputs.length - 1])) return q;
    }
    return first.name || "";
  };

  // ---- profile fields ----------------------------------------------------

  // [profile key, question regex, name/id/autocomplete hints]
  const PROFILE_RULES = [
    ["firstName", /\b(first|given|preferred first) name\b/, /first_?name|given|fname/],
    ["lastName", /\b(last|family) name\b|\bsurname\b/, /last_?name|family|surname|lname/],
    ["fullName", /^(full |legal |your )?name$|\bfull name\b|\blegal name\b/, /^name$|full_?name|_systemfield_name/],
    ["email", /\be-?mail\b/, /e-?mail/],
    ["phone", /\b(phone|mobile|contact number|telephone)\b/, /phone|mobile|tel/],
    ["linkedin", /linkedin/, /linkedin/],
    ["github", /github/, /github/],
    ["portfolio", /portfolio|personal (web)?site|^website\b|other website/, /portfolio|website/],
    ["currentCompany", /current (company|employer|organi[sz]ation)|^company$|^org(ani[sz]ation)?$/, /^org$|current_?company/],
    ["currentTitle", /current (job )?(title|role|designation|position)/, /current_?title/],
    // Only general experience; "years of experience with Java" belongs in the answer bank.
    ["yearsExperience", /^(?!.*\b(with|in|using|on|of working with)\s+\S).*((total |overall |professional )?years of (total |overall |professional |relevant |work )?experience|total experience)/, /^experience$|total_?experience/],
    ["noticePeriod", /notice period|how soon can you (join|start)|earliest (joining|start)/, /notice/],
    ["currentCtc", /current (ctc|salary|compensation|fixed|annual)|present (ctc|salary)/, /current_?(ctc|salary)/],
    ["expectedCtc", /expected (ctc|salary|compensation)|salary expectation|desired (salary|compensation)|compensation expectation/, /expected_?(ctc|salary)/],
    ["city", /current (city|location)|where are you (currently )?(based|located)|^location$|^city$|location \(city\)/, /^location$|city/],
    ["country", /^country$|country of residence/, /country/],
  ];

  function profileValue(profile, key) {
    if (key === "fullName") return [profile.firstName, profile.lastName].filter(Boolean).join(" ");
    return profile[key] || "";
  }

  // ---- answer bank -------------------------------------------------------

  function patternMatches(pattern, q) {
    const p = String(pattern || "").trim();
    if (!p) return false;
    if (p.length > 2 && p.startsWith("/") && p.endsWith("/")) {
      try { return new RegExp(p.slice(1, -1), "i").test(q); } catch { return false; }
    }
    return p.split("|").map(norm).filter(Boolean).some((alt) => q.includes(alt));
  }

  // Return {value, source} for a question, or null. Explicit answers win over
  // the profile, longer (more specific) patterns win over shorter ones.
  JR.answerFor = function (question, el, data) {
    const q = norm(question);
    if (!q) return null;
    const hits = data.answers
      .filter((a) => a.answer && patternMatches(a.pattern, q))
      .sort((a, b) => b.pattern.length - a.pattern.length);
    if (hits.length) return { value: hits[0].answer, source: "answer bank" };
    const hint = norm([el && el.name, el && el.id, el && el.getAttribute("autocomplete")].join(" "));
    for (const [key, rx, hintRx] of PROFILE_RULES) {
      if (rx.test(q) || (hint && hintRx.test(hint) && q.length < 40)) {
        const v = profileValue(data.profile, key);
        if (v) return { value: v, source: "profile" };
        return null;
      }
    }
    return null;
  };

  // Which profile field (if any) a question is about.
  JR.profileKeyFor = function (question, el) {
    const q = norm(question);
    const hint = norm([el && el.name, el && el.id].join(" "));
    for (const [key, rx, hintRx] of PROFILE_RULES) if (rx.test(q) || (hint && hintRx.test(hint) && q.length < 40)) return key;
    return null;
  };

  // Pick the option that best fits an answer like "Decline to self-identify|Prefer not to say".
  JR.bestOption = function (answer, options) {
    const opts = options.map((o) => ({ o, t: norm(o.text) })).filter((x) => x.t && !/^(select|choose|please select|--)/.test(x.t));
    for (const alt of String(answer).split("|").map(norm).filter(Boolean)) {
      const exact = opts.find((x) => x.t === alt);
      if (exact) return exact.o;
      const yesNo = /^(yes|no)$/.test(alt) && opts.find((x) => x.t.startsWith(alt + " ") || x.t.startsWith(alt + ",") || x.t === alt);
      if (yesNo) return yesNo.o;
      const starts = opts.find((x) => x.t.startsWith(alt));
      if (starts) return starts.o;
      if (alt.length >= 4) {
        const inside = opts.find((x) => x.t.includes(alt) || alt.includes(x.t));
        if (inside) return inside.o;
      }
    }
    return null;
  };

  // Patterns the user creates from their own answers: the question, trimmed of
  // filler, so it matches the same question at other companies.
  JR.patternFromQuestion = function (question) {
    return norm(question)
      .replace(/^(please )?(tell us|let us know|share|describe|provide|enter|indicate)( your| us)? /, "")
      .replace(/\b(required|optional)\b/g, "")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 80);
  };
})(globalThis.JR);
