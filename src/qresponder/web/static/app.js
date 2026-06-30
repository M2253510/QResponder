"use strict";
// QRESPONDER UI — vanilla JS, no framework, no external calls.

// ---- tiny DOM + fetch helpers ----
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const kid of kids) { if (kid == null) continue; e.append(kid.nodeType ? kid : document.createTextNode(kid)); }
  return e;
}
const $ = (id) => document.getElementById(id);
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `${r.status}`);
  return data;
}
const jpost = (p, body) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const jpatch = (p, body) => api(p, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const jput = (p, body) => api(p, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const S = { workspaces: [], current: null, status: {}, doctor: null };
const root = () => $("app");

// ---- bootstrap ----
async function boot() {
  try { S.status = await api("/api/status"); $("provider").textContent = `${S.status.provider} · ${S.status.model}`; } catch (_) {}
  refreshDoctor();
  await loadWorkspaces();
  if (!S.workspaces.length) return showWizard();
  S.current = S.current || S.workspaces[0].id;
  renderSwitcher();
  showHome();
}
async function refreshDoctor() {
  try { S.doctor = await api("/api/doctor"); $("doctor-dot").className = "doctor-dot " + (S.doctor.ok ? "ok" : "bad"); }
  catch (_) { $("doctor-dot").className = "doctor-dot bad"; }
}
async function loadWorkspaces() { S.workspaces = await api("/api/workspaces"); }

function renderSwitcher() {
  const sw = $("ws-switcher");
  sw.classList.remove("hidden");
  const sel = el("select", { onchange: (e) => { if (e.target.value === "__new") return showWizard(); S.current = e.target.value; showHome(); } });
  for (const w of S.workspaces) sel.append(el("option", { value: w.id, ...(w.id === S.current ? { selected: "selected" } : {}) }, w.name));
  sel.append(el("option", { value: "__new" }, "+ New workspace"));
  sw.replaceChildren(sel);
}

// ---- setup wizard ----
const WIZ_STEPS = ["Name", "Model", "Knowledge base", "Approved answers", "Evidence", "Ready"];
function showWizard() {
  const st = { i: 0, wid: null };
  $("ws-switcher").classList.add("hidden");
  const render = () => {
    const pills = el("div", { class: "steps" }, ...WIZ_STEPS.map((s, i) =>
      el("span", { class: "step-pill " + (i === st.i ? "active" : i < st.i ? "done" : "") }, `${i + 1}. ${s}`)));
    const body = el("div", {});
    root().replaceChildren(el("div", { class: "card" }, el("h1", {}, "Set up a workspace"), pills, body));
    STEP[st.i](body, st, render);
  };
  render();
}

const STEP = [
  // 0 — Name
  (body, st, next) => {
    body.append(el("p", { class: "why" }, "Name this workspace — usually a client or framework. ",
      el("span", { class: "eg" }, 'e.g. "Acme — SOC 2".')));
    const input = el("input", { placeholder: "Acme — SOC 2", value: st.name || "" });
    const err = el("div", {});
    const create = el("button", { class: "btn primary", onclick: async () => {
      try { const ws = await jpost("/api/workspaces", { name: input.value }); st.wid = ws.id; st.name = ws.name; st.i = 1; await loadWorkspaces(); next(); }
      catch (e) { err.replaceChildren(el("div", { class: "error" }, e.message)); }
    } }, "Create & continue");
    body.append(el("label", { class: "field" }, "Workspace name", input), el("div", { class: "btn-row" }, create), err);
  },
  // 1 — Model
  (body, st, next) => {
    body.append(el("p", { class: "why" }, "Where does the model run? Local is private and needs no key."));
    const cards = el("div", { class: "choice-cards" },
      el("div", { class: "choice selected" }, el("div", { class: "tag" }, "Recommended"),
        el("h3", {}, "Run locally (private, no key)"),
        el("div", { class: "muted" }, "Point QRESPONDER at Ollama/vLLM in .env. Nothing leaves this host.")),
      el("div", { class: "choice" }, el("div", { class: "tag" }, "Cloud"),
        el("h3", {}, "Use an API"),
        el("div", { class: "muted" }, "Set the provider + key in .env on the server. The key never enters this page.")));
    cards.children[0].addEventListener("click", () => { cards.children[0].classList.add("selected"); cards.children[1].classList.remove("selected"); });
    cards.children[1].addEventListener("click", () => { cards.children[1].classList.add("selected"); cards.children[0].classList.remove("selected"); });
    const active = el("div", { class: "provider" }, `Active: ${S.status.provider} · ${S.status.model}`);
    const result = el("div", {});
    const test = el("button", { class: "btn", onclick: async () => {
      result.replaceChildren(el("span", { class: "muted" }, "Testing…"));
      await refreshDoctor();
      if (S.doctor && S.doctor.ok) result.replaceChildren(el("div", { class: "ok-msg" }, "✓ Connection OK — model reachable."));
      else { const bad = (S.doctor?.checks || []).find((c) => !c.ok); result.replaceChildren(el("div", { class: "error" }, "✗ " + (bad ? bad.detail : "Not reachable. Check .env / that your local model is running."))); }
    } }, "Test connection");
    const cont = el("button", { class: "btn primary", onclick: () => { if (!S.doctor || !S.doctor.ok) return; st.i = 2; next(); } });
    cont.textContent = "Continue";
    const note = el("div", { class: "muted" }, "A green connection check is required to continue.");
    body.append(cards, el("div", { class: "card" }, active, el("div", { class: "btn-row" }, test), result), note, el("div", { class: "btn-row" }, cont));
    if (S.doctor && S.doctor.ok) result.replaceChildren(el("div", { class: "ok-msg" }, "✓ Connection OK."));
  },
  // 2 — KB
  (body, st, next) => {
    body.append(el("p", { class: "why" }, "Add your security policies, SOC 2 summary, architecture docs — anything you'd cite when answering ",
      el("span", { class: "eg" }, "(PDF/DOCX/MD/TXT).")));
    body.append(assetManager(st.wid, "kb"));
    body.append(el("p", { class: "muted" }, "Tags scope which docs answer which questionnaire (e.g. tag SOC 2 docs ‘soc2’)."));
    body.append(el("div", { class: "btn-row" }, el("button", { class: "btn primary", onclick: () => { st.i = 3; next(); } }, "Continue")));
  },
  // 3 — Approved answers (optional)
  (body, st, next) => {
    body.append(el("p", { class: "why" }, "Answers you've already written and trust — used first and verbatim. Optional: the flywheel builds this as you review."));
    body.append(qaManager(st.wid));
    body.append(el("div", { class: "btn-row" }, el("button", { class: "btn primary", onclick: () => { st.i = 4; next(); } }, "Continue"),
      el("button", { class: "btn ghost", onclick: () => { st.i = 4; next(); } }, "Skip")));
  },
  // 4 — Evidence (optional)
  (body, st, next) => {
    body.append(el("p", { class: "why" }, "Files that get attached to “please attach…” questions — the real SOC 2 PDF, IR plan. Not used as answer text."));
    body.append(assetManager(st.wid, "evidence"));
    body.append(el("div", { class: "btn-row" }, el("button", { class: "btn primary", onclick: () => { st.i = 5; next(); } }, "Continue"),
      el("button", { class: "btn ghost", onclick: () => { st.i = 5; next(); } }, "Skip")));
  },
  // 5 — Ready
  (body, st) => {
    body.append(el("p", { class: "why" }, "You're set. Upload a questionnaire and let QRESPONDER draft grounded, cited answers — you review every one."));
    body.append(el("div", { class: "btn-row" }, el("button", { class: "btn primary", onclick: async () => {
      S.current = st.wid; await loadWorkspaces(); renderSwitcher(); showHome("answer");
    } }, "Answer a questionnaire →")));
  },
];

// ---- asset manager (KB / evidence) with drag-drop + tag editor ----
function assetManager(wid, kind) {
  const wrap = el("div", {});
  const dz = el("div", { class: "dropzone" }, el("div", {}, el("strong", {}, "Drop files here"), " or click to choose"));
  const fileInput = el("input", { type: "file", multiple: "multiple", class: "hidden" });
  dz.addEventListener("click", () => fileInput.click());
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
  dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("drag"); upload(e.dataTransfer.files); });
  fileInput.addEventListener("change", () => upload(fileInput.files));
  const list = el("ul", { class: "assets" });
  const err = el("div", {});

  async function upload(files) {
    if (!files || !files.length) return;
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    err.replaceChildren();
    try { const res = await api(`/api/workspaces/${wid}/${kind}`, { method: "POST", body: fd }); render(res.files); }
    catch (e) { err.replaceChildren(el("div", { class: "error" }, e.message)); }
  }
  function render(files) {
    if (!files.length) {
      list.replaceChildren(el("li", {}, el("div", { class: "empty-teach" },
        kind === "kb" ? el("span", {}, el("strong", {}, "Start by adding your security policies"), " — the documents you'd cite when answering.")
                      : el("span", {}, el("strong", {}, "Add evidence files"), " — the docs attached to “please attach…” questions."))));
      return;
    }
    list.replaceChildren(...files.map((f) => {
      const tagInput = el("input", { class: "tagedit", value: (f.tags || []).join(", "), placeholder: "tags (comma-sep)" });
      const save = el("button", { class: "btn ghost", onclick: async () => { const r = await api(`/api/workspaces/${wid}/${kind}/${encodeURIComponent(f.name)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tags: tagInput.value.split(",").map(s => s.trim()).filter(Boolean) }) }); render(r.files); } }, "Save tags");
      const del = el("button", { class: "btn danger ghost", onclick: async () => { const r = await api(`/api/workspaces/${wid}/${kind}/${encodeURIComponent(f.name)}`, { method: "DELETE" }); render(r.files); } }, "Remove");
      return el("li", {}, el("span", { class: "fname" }, f.name), tagInput, save, del);
    }));
  }
  api(`/api/workspaces/${wid}/${kind}`).then((r) => render(r.files)).catch(() => render([]));
  wrap.append(dz, fileInput, err, list);
  return wrap;
}

// ---- approved-answers manager ----
function qaManager(wid) {
  const wrap = el("div", {});
  const q = el("input", { placeholder: "Question" });
  const a = el("textarea", { placeholder: "Approved answer" });
  const t = el("input", { placeholder: "tags (comma-sep)" });
  const list = el("ul", { class: "assets" });
  const add = el("button", { class: "btn", onclick: async () => {
    if (!q.value.trim() || !a.value.trim()) return;
    await jpost(`/api/workspaces/${wid}/qa`, { question: q.value, answer: a.value, tags: t.value.split(",").map(s => s.trim()).filter(Boolean) });
    q.value = a.value = t.value = ""; load();
  } }, "Add approved answer");
  async function load() {
    const { entries } = await api(`/api/workspaces/${wid}/qa`);
    if (!entries.length) { list.replaceChildren(el("li", {}, el("div", { class: "empty-teach" }, el("strong", {}, "No approved answers yet"), " — add any you already trust, or let the flywheel build them as you review."))); return; }
    list.replaceChildren(...entries.map((e) => el("li", {},
      el("span", { class: "fname" }, `${e.question} → ${e.answer.slice(0, 60)}${e.answer.length > 60 ? "…" : ""}  (v${e.version})`),
      el("button", { class: "btn danger ghost", onclick: async () => { await api(`/api/workspaces/${wid}/qa/${e.index}`, { method: "DELETE" }); load(); } }, "Delete"))));
  }
  load();
  wrap.append(el("div", { class: "form" }, el("label", { class: "field" }, "Question", q), el("label", { class: "field" }, "Answer", a), el("label", { class: "field" }, "Tags", t), el("div", { class: "btn-row" }, add)), list);
  return wrap;
}

// ---- workspace home (tabs: Answer | Settings) ----
function showHome(tab = "answer") {
  renderSwitcher();
  const wid = S.current;
  const tabs = el("div", { class: "tabs" },
    el("div", { class: "tab " + (tab === "answer" ? "active" : ""), onclick: () => showHome("answer") }, "Answer a questionnaire"),
    el("div", { class: "tab " + (tab === "flagged" ? "active" : ""), onclick: () => showHome("flagged") }, "Flagged"),
    el("div", { class: "tab " + (tab === "settings" ? "active" : ""), onclick: () => showHome("settings") }, "Settings"));
  const view = el("div", {});
  root().replaceChildren(tabs, view);
  if (tab === "answer") answerView(view, wid);
  else if (tab === "flagged") flaggedView(view, wid);
  else settingsView(view, wid);
}

// ---- cross-file flagged tab (Phase 8 E) ----
async function flaggedView(view, wid) {
  view.append(el("p", { class: "muted" }, "Unresolved questions grouped across all this workspace's runs. Answer once → inserted everywhere and saved to the library."));
  const host = el("div", {});
  view.append(host);
  async function load() {
    const { groups } = await api(`/api/workspaces/${wid}/flagged`);
    if (!groups.length) { host.replaceChildren(el("div", { class: "empty-teach" }, el("strong", {}, "Nothing flagged"), " — run some questionnaires; unresolved items show up here.")); return; }
    host.replaceChildren(...groups.map((g) => {
      const ta = el("textarea", {}, g.draft || "");
      const tags = el("input", { class: "tagedit", placeholder: "tags (optional)" });
      const status = el("span", { class: "muted" });
      const btn = el("button", { class: "btn primary", onclick: async () => {
        if (!ta.value.trim()) return;
        btn.disabled = true;
        try {
          const r = await api(`/api/workspaces/${wid}/flagged/resolve`, { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question: g.question, answer: ta.value, tags: tags.value.split(",").map(s => s.trim()).filter(Boolean) }) });
          status.textContent = `✓ resolved in ${r.updated} file(s)` + (r.trained ? " · added to library" : "");
          setTimeout(load, 600);
        } catch (e) { status.textContent = "Error: " + e.message; btn.disabled = false; }
      } }, "Resolve everywhere");
      return el("div", { class: "item" },
        el("div", { class: "item-head" }, el("div", { class: "q-text" }, g.question),
          el("span", { class: "chip reason" }, `${g.count} file(s)`)),
        el("div", { class: "muted" }, "Appears in: " + g.files.join(", ")),
        el("div", { class: "answer-box" }, ta),
        el("div", { class: "actions" }, tags, btn, status));
    }));
  }
  load();
}

// ---- answer + review ----
function answerView(view, wid) {
  const card = el("div", { class: "card" }, el("h1", {}, "Answer a questionnaire"),
    el("p", { class: "muted" }, "Upload an Excel/Word/PDF questionnaire. Every answer is grounded, cited, and yours to review — and each accept trains this workspace."));
  const file = el("input", { type: "file", accept: ".xlsx,.xlsm,.docx,.pdf" });
  const tags = el("input", { placeholder: "tag scope (optional, defaults to workspace)" });
  const mode = el("select", {}, el("option", { value: "" }, "default mode"), el("option", { value: "in_context" }, "in-context"), el("option", { value: "retrieval" }, "retrieval"));
  const progress = el("div", { class: "muted hidden" }, el("span", { class: "spinner" }), " Running…");
  const err = el("div", {});
  const reviewHost = el("div", {});
  const run = el("button", { class: "btn primary", onclick: async () => {
    if (!file.files[0]) return;
    err.replaceChildren(); progress.classList.remove("hidden");
    const fd = new FormData(); fd.append("questionnaire", file.files[0]);
    if (tags.value.trim()) fd.append("tags", tags.value.trim());
    if (mode.value) fd.append("mode", mode.value);
    try {
      const { run_id } = await api(`/api/workspaces/${wid}/runs`, { method: "POST", body: fd });
      poll(run_id, reviewHost, progress, err);
    } catch (e) { progress.classList.add("hidden"); err.replaceChildren(el("div", { class: "error" }, e.message)); }
  } }, "Run");
  card.append(el("div", { class: "form" }, el("label", { class: "field" }, "Questionnaire file", file),
    el("div", { class: "row" }, el("label", { class: "field" }, "Tags", tags), el("label", { class: "field" }, "Mode", mode)),
    el("div", { class: "btn-row" }, run), progress, err));

  // Live batch dashboard launcher.
  const batchFiles = el("input", { type: "file", multiple: "multiple", accept: ".xlsx,.xlsm,.docx,.pdf" });
  const dashHost = el("div", {});
  const batchBtn = el("button", { class: "btn", onclick: async () => {
    if (!batchFiles.files.length) return;
    const fd = new FormData();
    for (const f of batchFiles.files) fd.append("files", f);
    try {
      const r = await api(`/api/workspaces/${wid}/batch-stream`, { method: "POST", body: fd });
      renderDashboard(dashHost, r.batch_id, r.n_files);
    } catch (e) { dashHost.replaceChildren(el("div", { class: "error" }, e.message)); }
  } }, "Run batch (live dashboard)");
  const batchCard = el("div", { class: "card" }, el("h2", {}, "Batch — live command center"),
    el("p", { class: "muted" }, "Process many files at once and watch the grounded pipeline in real time."),
    el("label", { class: "field" }, "Questionnaire files", batchFiles),
    el("div", { class: "btn-row" }, batchBtn));

  view.append(card, reviewHost, batchCard, dashHost);
}

// --- live processing dashboard (Phase 8 D) ---
function renderDashboard(host, batchId, nFiles) {
  const counts = { files_done: 0, tier1: 0, generated: 0, flagged: 0, errors: 0 };
  const stat = (label, id) => el("div", { class: "dash-stat" }, el("b", { id: "ds-" + id }, "0"),
    el("span", { class: "dash-label" }, label));
  const tracker = el("div", { class: "dash-tracker" },
    el("div", { class: "dash-stat" }, el("b", { id: "ds-files" }, "0/" + nFiles), el("span", { class: "dash-label" }, "files")),
    stat("matched (Tier-1)", "tier1"), stat("generated", "generated"),
    stat("flagged", "flagged"), stat("errors", "errors"));
  const consoleBox = el("div", { class: "dash-console" });
  const bar = el("div", { class: "dash-bar" }, el("div", { class: "dash-bar-fill", id: "dash-fill" }));
  const done = el("div", { class: "dash-done" });
  host.replaceChildren(el("div", { class: "dash card" },
    el("h2", {}, "Processing"), tracker, bar, el("h4", { class: "dash-h" }, "AI thinking"), consoleBox, done));

  const set = (id, v) => { const e = $("ds-" + id); if (e) e.textContent = v; };
  const line = (cls, text) => {
    const t = new Date().toLocaleTimeString();
    consoleBox.append(el("div", { class: "cline " + (cls || "") }, el("span", { class: "cts" }, t + " "), text));
    consoleBox.scrollTop = consoleBox.scrollHeight;
  };

  const es = new EventSource(`/api/runs/${batchId}/stream`);
  es.onmessage = (ev) => {
    const e = JSON.parse(ev.data);
    switch (e.type) {
      case "file_started": line("", `▶ ${e.file} — started`); break;
      case "parsed": line("", `parsed ${e.questions} question(s)`); break;
      case "retrieved": line("dim", `  retrieved k=${e.k} top=${e.top_score ?? "—"}`); break;
      case "tier1_reuse": counts.tier1++; set("tier1", counts.tier1); line("ok", `  ✓ reused approved answer (Tier-1)`); break;
      case "generated": counts.generated++; set("generated", counts.generated); break;
      case "faithfulness": line(e.passed ? "ok" : "bad", `  faithfulness ${e.passed ? "PASS" : "FAIL"}`); break;
      case "flagged": counts.flagged++; set("flagged", counts.flagged); line("warn", `  ⚠ flagged: ${(e.reason||"").replace(/_/g," ")}`); break;
      case "question_done": line(e.confidence === "high" ? "ok" : e.confidence === "low" ? "bad" : "warn", `  · ${e.status} (${e.confidence})`); break;
      case "file_done":
        counts.files_done++; set("files", counts.files_done + "/" + nFiles);
        const fill = $("dash-fill"); if (fill) fill.style.width = Math.round(100 * counts.files_done / nFiles) + "%";
        line("ok", `■ ${e.file} done — ${e.answered} answered, ${e.flagged} flagged`); break;
      case "error": counts.errors++; set("errors", counts.errors); line("bad", `✗ error: ${e.error}`); break;
      case "_end":
        es.close();
        api(`/api/runs/${batchId}/events`).then((snap) => {
          done.replaceChildren(el("div", { class: "ok-msg" }, "Batch complete."),
            snap.zip ? el("a", { class: "btn primary", href: `/api/runs/${batchId}/download/${snap.zip}`, download: snap.zip }, "Download filled originals (ZIP)") : el("span"));
        });
        break;
    }
  };
  es.onerror = () => { line("bad", "stream closed"); es.close(); };
}

async function poll(runId, host, progress, err) {
  const d = await api(`/api/runs/${runId}`);
  if (d.status === "done") { progress.classList.add("hidden"); renderReview(host, runId, d); }
  else if (d.status === "error") { progress.classList.add("hidden"); err.replaceChildren(el("div", { class: "error" }, "Run failed: " + (d.error || "unknown"))); }
  else setTimeout(() => poll(runId, host, progress, err), 500);
}

const CONF = { high: "high", medium: "medium", low: "low" };
function citations(cites) {
  if (!cites || !cites.length) return null;
  const d = el("details", {}, el("summary", {}, `${cites.length} citation(s)`));
  for (const c of cites) d.append(el("div", { class: "cite" }, el("span", { class: "src" }, c.source), " ",
    (c.faithful === true ? "✓ " : c.faithful === false ? "✗ " : ""), c.snippet));
  return d;
}

function renderReview(host, runId, d) {
  const s = d.summary;
  const reasons = Object.entries(s.flagged_by_reason || {}).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ");
  const summary = el("div", { class: "summary" },
    el("div", { class: "stat" }, el("b", {}, String(s.total)), " questions"), el("span", { class: "sep" }, "·"),
    el("div", { class: "stat" }, el("b", {}, String(s.auto_answered_high)), " auto (high)"), el("span", { class: "sep" }, "·"),
    el("div", { class: "stat" }, el("b", {}, String(s.flagged)), " need review" + (reasons ? ` (${reasons})` : "")));
  const items = el("div", {});
  for (const it of d.results) items.append(reviewItem(runId, it));
  const exportRes = el("div", { class: "export-result" });
  const exportBtn = el("button", { class: "btn primary", onclick: async () => {
    exportRes.textContent = "Exporting…";
    try { const res = await api(`/api/runs/${runId}/export`, { method: "POST" });
      exportRes.replaceChildren(...Object.values(res.artifacts).map((n) => el("a", { href: `/api/runs/${runId}/download/${n}`, download: n }, n)));
      if (res.writeback?.fallback) exportRes.append(el("div", { class: "fallback" }, "Write-back fell back to the separate file (workbook had images/charts)."));
    } catch (e) { exportRes.textContent = "Export failed: " + e.message; }
  } }, "Export draft");
  host.replaceChildren(summary, items, el("div", { class: "card" }, exportBtn, exportRes),
    el("p", { class: "muted" }, "Output is a draft. Nothing is submitted."));
}

function reviewItem(runId, item) {
  const card = el("div", { class: "item" });
  const badges = el("div", { class: "badges" }, el("span", { class: "chip " + (CONF[item.confidence] || "low") }, item.confidence));
  if (item.review_reason && item.review_reason !== "none") badges.append(el("span", { class: "chip reason" }, item.review_reason.replace(/_/g, " ")));
  card.append(el("div", { class: "item-head" }, el("div", { class: "q-text" }, item.question_text), badges));
  const isAttachment = item.answer_type === "attachment";
  let textarea = null, chosenInterp = null, chosenAtt = null;
  if (!isAttachment) { textarea = el("textarea", {}, item.answer || ""); card.append(el("div", { class: "answer-box" }, textarea)); }

  if (item.review_reason === "ambiguous" && (item.candidates || []).length) {
    const p = el("div", { class: "panel" }, el("h4", {}, "Interpretations — pick one"));
    item.candidates.forEach((c, i) => {
      const radio = el("input", { type: "radio", name: "i-" + item.question_id, id: `i-${item.question_id}-${i}` });
      radio.addEventListener("change", () => { chosenInterp = c.interpretation; if (textarea) textarea.value = c.answer || ""; });
      p.append(el("div", { class: "option" }, radio, el("label", { for: `i-${item.question_id}-${i}` },
        el("div", {}, el("b", {}, c.interpretation)), el("div", { class: "muted" }, c.answer || "(no supported answer)"), citations(c.citations))));
    });
    card.append(p);
  }
  if (isAttachment || item.review_reason === "attachment_unresolved") {
    const cands = (item.attachment_candidates || []).length ? item.attachment_candidates : (item.attachment_path ? [item.attachment_path] : []);
    if (cands.length) {
      const p = el("div", { class: "panel" }, el("h4", {}, "Attachment — confirm a file"));
      cands.forEach((fn, i) => { const radio = el("input", { type: "radio", name: "a-" + item.question_id, id: `a-${item.question_id}-${i}` });
        if (i === 0 && item.attachment_path) { radio.checked = true; chosenAtt = fn; }
        radio.addEventListener("change", () => { chosenAtt = fn; });
        p.append(el("div", { class: "option" }, radio, el("label", { for: `a-${item.question_id}-${i}` }, fn))); });
      card.append(p);
    }
  }
  if (item.review_reason === "conflict" && item.conflict_with)
    card.append(el("div", { class: "panel warn" }, el("h4", {}, "Conflict — reconcile"),
      el("div", { class: "conflict-grid" }, el("div", { class: "col" }, el("h4", {}, "This answer"), item.answer || "—"),
        el("div", { class: "col" }, el("h4", {}, "Conflicts with"), item.conflict_with))));
  if ((item.review_reason === "unsupported" || item.review_reason === "faithfulness_fail" || item.review_reason === "library_candidate") && item.missing_info)
    card.append(el("div", { class: "panel warn" }, el("h4", {}, "Why flagged"), item.missing_info));

  card.append(citations(item.citations) || el("span"));
  const status = el("span", { class: "muted" });
  const acceptBtn = el("button", { class: "btn primary" }, item.review_reason === "ambiguous" ? "Accept selected" : "Accept");
  acceptBtn.addEventListener("click", async () => {
    const body = { approved_by: "web" };
    if (chosenAtt) body.attachment = chosenAtt;
    else if (chosenInterp) { body.interpretation = chosenInterp; body.answer = textarea ? textarea.value : null; }
    else if (textarea) body.answer = textarea.value;
    acceptBtn.disabled = true;
    try { const res = await api(`/api/runs/${runId}/items/${item.question_id}/accept`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      card.classList.add("accepted");
      badges.replaceChildren(el("span", { class: "chip trained" }, res.trained ? "✓ added to library" : "✓ answered"));
    } catch (e) { status.textContent = "Error: " + e.message; acceptBtn.disabled = false; }
  });
  card.append(el("div", { class: "actions" }, acceptBtn,
    el("button", { class: "btn ghost", onclick: () => { card.style.opacity = .55; } }, "Leave for review"), status));
  return card;
}

// ---- settings view ----
async function settingsView(view, wid) {
  view.append(el("div", { class: "warn-banner" }, "This UI has no authentication and holds your full security posture. Keep it on 127.0.0.1 — don't expose it to a network without putting auth in front."));

  // Completion / analytics (Phase 10 D) — local read only.
  try {
    const s = await api(`/api/workspaces/${wid}/stats`);
    const stat = (label, val) => el("div", { class: "dash-stat" }, el("b", {}, String(val)), el("span", { class: "dash-label" }, label));
    const reasons = Object.entries(s.flagged_by_reason || {}).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ");
    view.append(el("div", { class: "card" }, el("h2", {}, "Analytics"),
      el("div", { class: "dash-tracker" },
        stat("runs", s.n_runs), stat("questions", s.total_questions),
        stat("completion", Math.round(s.completion_rate * 100) + "%"),
        stat("auto (high+med)", Math.round(s.auto_answer_rate_high_med * 100) + "%"),
        stat("flagged", s.flagged)),
      reasons ? el("p", { class: "muted" }, "Flagged: " + reasons) : el("span"),
      el("p", { class: "muted" }, `~${s.time_saved_minutes} min saved — ${s.time_saved_note}`)));
  } catch (_) {}

  // Model status
  const modelCard = el("div", { class: "card" }, el("h2", {}, "Model"),
    el("div", { class: "provider" }, `${S.status.provider} · ${S.status.model}`),
    el("p", { class: "muted" }, "Provider and key live in .env on the server — never in this page or per-workspace. Local models need no key."));
  const dres = el("div", {});
  modelCard.append(el("div", { class: "btn-row" }, el("button", { class: "btn", onclick: async () => { dres.textContent = "Testing…"; await refreshDoctor(); dres.replaceChildren(S.doctor?.ok ? el("span", { class: "ok-msg" }, "✓ reachable") : el("div", { class: "error" }, "✗ not reachable — check .env")); } }, "Test connection")), dres);
  view.append(modelCard);

  // KB
  view.append(el("div", { class: "card" }, el("h2", {}, "Knowledge base documents"),
    el("p", { class: "muted" }, "Cited when answering. Tag docs to scope which answer which questionnaire."), assetManager(wid, "kb")));
  // Approved answers
  view.append(el("div", { class: "card" }, el("h2", {}, "Approved answers"),
    el("p", { class: "muted" }, "Used first and verbatim. Grown automatically each time you accept a reviewed answer."), qaManager(wid)));
  // Evidence
  view.append(el("div", { class: "card" }, el("h2", {}, "Evidence vault"),
    el("p", { class: "muted" }, "Attached to “please attach…” questions; not used as answer text."), assetManager(wid, "evidence")));
  // Engine settings
  const ws = await api(`/api/workspaces/${wid}`);
  const set = ws.settings || {};
  const modeSel = el("select", {}, ...["in_context", "retrieval"].map((m) => el("option", { value: m, ...(set.kb_mode === m ? { selected: "selected" } : {}) }, m)));
  const faith = el("input", { type: "checkbox", ...(set.verify_faithfulness !== false ? { checked: "checked" } : {}) });
  const conflict = el("input", { type: "checkbox", ...(set.detect_conflicts !== false ? { checked: "checked" } : {}) });
  const tagsDefault = el("input", { value: (set.tags || []).join(", "), placeholder: "default tag scope" });
  const saveRes = el("div", {});
  const save = el("button", { class: "btn primary", onclick: async () => {
    try { await jpatch(`/api/workspaces/${wid}/settings`, { kb_mode: modeSel.value, verify_faithfulness: faith.checked, detect_conflicts: conflict.checked, tags: tagsDefault.value.split(",").map(s => s.trim()).filter(Boolean) });
      saveRes.replaceChildren(el("span", { class: "ok-msg" }, "✓ saved")); }
    catch (e) { saveRes.replaceChildren(el("div", { class: "error" }, e.message)); }
  } }, "Save settings");
  view.append(el("div", { class: "card" }, el("h2", {}, "Engine settings"),
    el("label", { class: "field" }, "Retrieval mode (in-context dumps the KB; retrieval ranks it — better for large KBs)", modeSel),
    el("label", { class: "field" }, el("span", {}, faith, " Verify faithfulness (check each claim is entailed by its citation)")),
    el("label", { class: "field" }, el("span", {}, conflict, " Detect cross-source conflicts (flag contradictory answers)")),
    el("label", { class: "field" }, "Default tag scope", tagsDefault),
    el("div", { class: "btn-row" }, save), saveRes));

  // Danger zone
  view.append(el("div", { class: "card" }, el("h2", {}, "Danger zone"),
    el("div", { class: "btn-row" }, el("button", { class: "btn danger", onclick: async () => {
      if (!confirm(`Delete workspace "${S.current}" and all its files? This cannot be undone.`)) return;
      await api(`/api/workspaces/${wid}`, { method: "DELETE" }); S.current = null; await loadWorkspaces();
      if (S.workspaces.length) { S.current = S.workspaces[0].id; showHome(); } else showWizard();
    } }, "Delete this workspace"))));
}

boot();
