"use strict";
// QRESPONDER review UI — vanilla JS, no framework, no external calls.

let RUN_ID = null;

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) e.setAttribute(k, v);
  }
  for (const kid of kids) {
    if (kid == null) continue;
    e.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return e;
}
const $ = (id) => document.getElementById(id);
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
  return r.json();
}

// --- status ---------------------------------------------------------------
async function loadStatus() {
  try {
    const s = await api("/api/status");
    $("provider").textContent = `${s.provider} · ${s.model}`;
  } catch (_) { $("provider").textContent = "provider unavailable"; }
}

// --- new run --------------------------------------------------------------
$("run-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const file = $("f-file").files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("questionnaire", file);
  fd.append("kb", $("f-kb").value);
  fd.append("evidence", $("f-evidence").value);
  fd.append("qa", $("f-qa").value);
  fd.append("tags", $("f-tags").value);
  fd.append("mode", $("f-mode").value);
  $("run-error").classList.add("hidden");
  $("run-progress").classList.remove("hidden");
  try {
    const { run_id } = await api("/api/runs", { method: "POST", body: fd });
    RUN_ID = run_id;
    pollRun();
  } catch (e) {
    $("run-progress").classList.add("hidden");
    $("run-error").textContent = "Failed to start: " + e.message;
    $("run-error").classList.remove("hidden");
  }
});

async function pollRun() {
  const data = await api(`/api/runs/${RUN_ID}`);
  $("run-progress-text").textContent = `Status: ${data.status}…`;
  if (data.status === "done") {
    $("run-progress").classList.add("hidden");
    $("new-run").classList.add("hidden");
    $("review").classList.remove("hidden");
    renderReview(data);
  } else if (data.status === "error") {
    $("run-progress").classList.add("hidden");
    $("run-error").textContent = "Run failed: " + (data.error || "unknown");
    $("run-error").classList.remove("hidden");
  } else {
    setTimeout(pollRun, 500);
  }
}

// --- review ---------------------------------------------------------------
function renderReview(data) {
  const s = data.summary;
  const reasons = Object.entries(s.flagged_by_reason || {})
    .map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ");
  $("summary").replaceChildren(
    el("div", { class: "stat" }, el("b", {}, String(s.total)), " questions"),
    el("span", { class: "sep" }, "·"),
    el("div", { class: "stat" }, el("b", {}, String(s.auto_answered_high)), " auto-answered (high)"),
    el("span", { class: "sep" }, "·"),
    el("div", { class: "stat" }, el("b", {}, String(s.flagged)), " need review" + (reasons ? ` (${reasons})` : "")),
  );
  const list = $("items");
  list.replaceChildren();
  for (const item of data.results) list.append(renderItem(item));
}

const CONF = { high: "high", medium: "medium", low: "low" };

function citationsBlock(cites) {
  if (!cites || !cites.length) return null;
  const d = el("details", {}, el("summary", {}, `${cites.length} citation(s)`));
  for (const c of cites) {
    d.append(el("div", { class: "cite" },
      el("span", { class: "src" }, c.source), " ",
      (c.faithful === true ? "✓ " : c.faithful === false ? "✗ " : ""),
      c.snippet));
  }
  return d;
}

function renderItem(item) {
  const card = el("div", { class: "item", id: "item-" + item.question_id });
  const badges = el("div", { class: "badges" },
    el("span", { class: "chip " + (CONF[item.confidence] || "low") }, item.confidence),
  );
  if (item.review_reason && item.review_reason !== "none")
    badges.append(el("span", { class: "chip reason" }, item.review_reason.replace(/_/g, " ")));
  card.append(el("div", { class: "item-head" },
    el("div", { class: "q-text" }, item.question_text), badges));

  // Editable answer (skipped for pure attachment items).
  const isAttachment = item.answer_type === "attachment";
  let textarea = null;
  if (!isAttachment) {
    textarea = el("textarea", {}, item.answer || "");
    card.append(el("div", { class: "answer-box" }, textarea));
  }

  // Special-case panels.
  let chosenInterpretation = null;
  let chosenAttachment = null;

  if (item.review_reason === "ambiguous" && item.candidates && item.candidates.length) {
    const panel = el("div", { class: "panel" }, el("h4", {}, "Interpretations — pick one"));
    item.candidates.forEach((c, i) => {
      const radio = el("input", { type: "radio", name: "interp-" + item.question_id, id: `i-${item.question_id}-${i}` });
      radio.addEventListener("change", () => { chosenInterpretation = c.interpretation; if (textarea) textarea.value = c.answer || ""; });
      const opt = el("div", { class: "option" }, radio,
        el("label", { for: `i-${item.question_id}-${i}` },
          el("div", {}, el("b", {}, c.interpretation)),
          el("div", { class: "muted" }, c.answer || "(no supported answer)"),
          citationsBlock(c.citations)));
      panel.append(opt);
    });
    card.append(panel);
  }

  if (item.review_reason === "attachment_unresolved" || isAttachment) {
    const cands = item.attachment_candidates && item.attachment_candidates.length
      ? item.attachment_candidates : (item.attachment_path ? [item.attachment_path] : []);
    if (cands.length) {
      const panel = el("div", { class: "panel" }, el("h4", {}, "Attachment — confirm a file"));
      cands.forEach((fn, i) => {
        const radio = el("input", { type: "radio", name: "att-" + item.question_id, id: `a-${item.question_id}-${i}` });
        if (i === 0 && item.attachment_path) { radio.checked = true; chosenAttachment = fn; }
        radio.addEventListener("change", () => { chosenAttachment = fn; });
        panel.append(el("div", { class: "option" }, radio,
          el("label", { for: `a-${item.question_id}-${i}` }, fn)));
      });
      card.append(panel);
    }
  }

  if (item.review_reason === "conflict" && item.conflict_with) {
    card.append(el("div", { class: "panel warn" }, el("h4", {}, "Conflict — reconcile"),
      el("div", { class: "conflict-grid" },
        el("div", { class: "col" }, el("h4", {}, "This answer"), item.answer || "—"),
        el("div", { class: "col" }, el("h4", {}, "Conflicts with"), item.conflict_with))));
  }

  if (item.review_reason === "library_candidate") {
    card.append(el("div", { class: "panel" }, el("h4", {}, "Suggested approved answer"),
      el("div", { class: "muted" }, item.missing_info || "Confirm this approved answer fits.")));
  }

  if ((item.review_reason === "unsupported" || item.review_reason === "faithfulness_fail") && item.missing_info) {
    card.append(el("div", { class: "panel warn" }, el("h4", {}, "Why flagged"), item.missing_info));
  }

  card.append(citationsBlock(item.citations) || el("span"));

  // Actions.
  const status = el("span", { class: "muted" });
  const acceptBtn = el("button", { class: "btn primary" },
    item.review_reason === "ambiguous" ? "Accept selected" : "Accept");
  acceptBtn.addEventListener("click", async () => {
    const body = { approved_by: "web" };
    if (chosenAttachment) body.attachment = chosenAttachment;
    else if (chosenInterpretation) { body.interpretation = chosenInterpretation; body.answer = textarea ? textarea.value : null; }
    else if (textarea) body.answer = textarea.value;
    acceptBtn.disabled = true;
    try {
      const res = await api(`/api/runs/${RUN_ID}/items/${item.question_id}/accept`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      card.classList.add("accepted");
      badges.replaceChildren(el("span", { class: "chip trained" },
        res.trained ? "✓ added to library" : "✓ answered"));
    } catch (e) { status.textContent = "Error: " + e.message; acceptBtn.disabled = false; }
  });
  const leaveBtn = el("button", { class: "btn ghost" }, "Leave for review");
  leaveBtn.addEventListener("click", () => { card.style.opacity = .55; });
  card.append(el("div", { class: "actions" }, acceptBtn, leaveBtn, status));
  return card;
}

// --- export ---------------------------------------------------------------
$("export-btn").addEventListener("click", async () => {
  const out = $("export-result");
  out.textContent = "Exporting…";
  try {
    const res = await api(`/api/runs/${RUN_ID}/export`, { method: "POST" });
    out.replaceChildren();
    for (const name of Object.values(res.artifacts)) {
      out.append(el("a", { href: `/api/runs/${RUN_ID}/download/${name}`, download: name }, name));
    }
    if (res.writeback && res.writeback.fallback) {
      out.append(el("div", { class: "fallback" },
        "Note: write-back fell back to the separate file (workbook had images/charts)."));
    }
  } catch (e) { out.textContent = "Export failed: " + e.message; }
});

loadStatus();
