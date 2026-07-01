"use strict";
// QRESPONDER dashboard — vanilla JS, no framework, no external calls.
// Thin presentation over the existing engine endpoints. The single grounded path
// is the only path; this file moves no answering logic.

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
const csv = (s) => (s || "").split(",").map((x) => x.trim()).filter(Boolean);
function toast(msg, kind = "") {
  const t = el("div", { class: "toast " + kind, role: "status", "aria-live": "polite" }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), 4200);
}

// --- inline icon set (local SVG, no external assets) ---
const ICONS = {
  upload: '<path d="M12 16V4M6 10l6-6 6 6M4 20h16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
  book: '<path d="M4 5a2 2 0 012-2h13v16H6a2 2 0 00-2 2V5z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M9 3v14" fill="none" stroke="currentColor" stroke-width="2"/>',
  flag: '<path d="M5 21V4m0 0h11l-2 4 2 4H5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
  copy: '<path d="M8 8h11v11H8zM5 16V5h11" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>',
  check: '<path d="M4 12l5 5L20 6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
  plug: '<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 01-12 0V8zM12 17v4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
};
function icon(name) {
  const wrap = document.createElement("span");
  wrap.innerHTML = `<svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">${ICONS[name] || ""}</svg>`;
  return wrap.firstChild;
}
function emptyState(glyph, title, text, action) {
  return el("div", { class: "empty" }, el("div", { class: "glyph" }, icon(glyph)),
    el("h3", {}, title), el("p", {}, text), action || null);
}
function skeleton(kind, n = 3) {
  const wrap = el("div", {});
  for (let i = 0; i < n; i++) wrap.append(el("div", { class: "skeleton " + (kind || "sk-row"), "aria-hidden": "true" }));
  return wrap;
}
// Accessible modal shell: backdrop-click + Escape close, initial focus.
function openModal(titleText, bodyNodes, footNodes) {
  const bg = el("div", { class: "modal-bg", role: "dialog", "aria-modal": "true", "aria-label": titleText });
  const close = () => { bg.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  bg.addEventListener("click", (e) => { if (e.target === bg) close(); });
  const xBtn = el("button", { class: "x", "aria-label": "Close dialog", onclick: close }, "×");
  bg.append(el("div", { class: "modal" },
    el("div", { class: "modal-head" }, el("h2", {}, titleText), xBtn),
    el("div", { class: "modal-body" }, ...(bodyNodes || [])),
    footNodes ? el("div", { class: "modal-foot" }, ...footNodes) : null));
  document.body.append(bg);
  const focusable = bg.querySelector("input, textarea, select, button.primary");
  if (focusable) setTimeout(() => focusable.focus(), 30);
  return { bg, close };
}

const S = { workspaces: [], current: null, status: {}, doctor: null, page: "upload", recent: {} };
const root = () => $("app");

// ---- bootstrap ----
async function boot() {
  await refreshStatus();
  refreshDoctor();
  await loadWorkspaces();
  if (!S.workspaces.length) { hideChrome(); return showWizard(); }
  S.current = S.current || S.workspaces[0].id;
  renderSwitcher();
  renderNav();
  go(S.page);
}
function hideChrome() { $("nav").classList.add("hidden"); $("ws-switcher").classList.add("hidden"); }
async function refreshStatus() {
  try {
    S.status = await api("/api/status");
    const ms = $("model-status");
    ms.className = "model-status " + (S.status.active ? "ok" : "bad");
    ms.querySelector(".ms-text").textContent = `${S.status.provider}/${S.status.model}`;
    ms.title = (S.status.active ? "Reachable" : "Unreachable: " + (S.status.reason || "")) +
      " — server-side; your API key never reaches this page";
  } catch (_) {}
}
async function refreshDoctor() {
  try { S.doctor = await api("/api/doctor"); } catch (_) { S.doctor = { ok: false }; }
}
async function loadWorkspaces() { S.workspaces = await api("/api/workspaces"); }

function renderNav() {
  const nav = $("nav");
  nav.classList.remove("hidden");
  for (const a of nav.querySelectorAll(".nav-item")) {
    a.classList.toggle("active", a.dataset.page === S.page);
    a.onclick = () => go(a.dataset.page);
  }
}
function renderSwitcher() {
  const sw = $("ws-switcher");
  sw.classList.remove("hidden");
  const sel = el("select", { onchange: (e) => { if (e.target.value === "__new") return showWizard(); S.current = e.target.value; go(S.page); } });
  for (const w of S.workspaces) sel.append(el("option", { value: w.id, ...(w.id === S.current ? { selected: "selected" } : {}) }, w.name));
  sel.append(el("option", { value: "__new" }, "+ New workspace"));
  sw.replaceChildren(sel);
}

function go(page) {
  S.page = page;
  renderNav();
  const view = el("div", {});
  root().replaceChildren(view);
  const wid = S.current;
  if (page === "kb") kbPage(view, wid);
  else if (page === "ask") askPage(view, wid);
  else if (page === "settings") settingsPage(view, wid);
  else uploadPage(view, wid);
}

// ============================================================================
// PART C/D/E — Upload screen + live processing + per-file results
// ============================================================================
const UPLOAD_EXTS = [".docx", ".pdf", ".xlsx", ".xlsm", ".csv"];
function uploadPage(view, wid) {
  view.append(el("div", { class: "page-head" }, el("h1", {}, "Upload questionnaires"),
    el("div", { class: "sub" }, "Drop your files and let QRESPONDER draft grounded, cited answers — every answer reviewable, nothing submitted.")));

  // --- model + preset controls ---
  const provSel = el("select", {}, el("option", { value: "" }, `default (${S.status.provider})`));
  const modelSel = el("select", {}, el("option", { value: "" }, "default model"));
  const presetSel = el("select", {}, el("option", { value: "" }, "no preset (default style)"));
  const instr = el("textarea", { placeholder: "Optional style guidance (tone, length). Style only — never changes what's grounded." });
  loadProviders(provSel, modelSel);
  loadPresets(presetSel, wid);

  const controls = el("div", { class: "card" }, el("h2", {}, "Model for this upload"),
    el("p", { class: "muted" }, "Pick the provider/model for this batch. Keys stay server-side — only names are shown. An unreachable provider blocks the run (no mock fallback)."),
    el("div", { class: "row" },
      el("label", { class: "field" }, "Provider", provSel),
      el("label", { class: "field" }, "Model", modelSel),
      el("label", { class: "field" }, "Preset", presetSel)),
    el("label", { class: "field" }, el("span", {}, "Agent instructions (optional)"), instr));

  // --- dropzone + file list ---
  const picked = [];
  const dz = el("div", { class: "dropzone", role: "button", tabindex: "0", "aria-label": "Add questionnaire files" },
    el("div", { class: "dz-glyph" }, icon("upload")),
    el("div", {}, el("strong", {}, "Drop your questionnaire files here"), " or click to choose"),
    el("div", { class: "dz-hint" }, "supports .docx, .pdf, .xlsx, .csv — up to 50 per batch"));
  const fileInput = el("input", { type: "file", multiple: "multiple", class: "hidden", accept: UPLOAD_EXTS.join(",") });
  dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
  const list = el("ul", { class: "filelist" });
  const err = el("div", {});
  const runHost = el("div", {});

  function fmtSize(n) { return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(0) + " KB" : (n / 1048576).toFixed(1) + " MB"; }
  function add(files) {
    for (const f of files) {
      const ext = "." + (f.name.split(".").pop() || "").toLowerCase();
      const ok = UPLOAD_EXTS.includes(ext);
      if (picked.length < 50) picked.push({ f, ok });
    }
    renderPicked();
  }
  function renderPicked() {
    if (!picked.length) { list.replaceChildren(); return; }
    list.replaceChildren(...picked.map((p, i) => el("li", {},
      el("span", { class: "fname" }, p.f.name),
      el("span", { class: "fsize" }, fmtSize(p.f.size)),
      p.ok ? el("span", { class: "ok", title: "supported" }, "✓") : el("span", { class: "bad", title: "unsupported type — will be skipped" }, "✗ unsupported"),
      el("span", { class: "x", title: "remove", onclick: () => { picked.splice(i, 1); renderPicked(); } }, "×"))));
  }
  dz.addEventListener("click", () => fileInput.click());
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
  dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("drag"); add(e.dataTransfer.files); });
  fileInput.addEventListener("change", () => add(fileInput.files));

  const runBtn = el("button", { class: "btn primary", onclick: async () => {
    const good = picked.filter((p) => p.ok);
    if (!good.length) { err.replaceChildren(el("div", { class: "error" }, "Add at least one supported file (.docx/.pdf/.xlsx/.csv).")); return; }
    err.replaceChildren();
    const fd = new FormData();
    for (const p of good) fd.append("files", p.f);
    if (provSel.value) fd.append("provider", provSel.value);
    if (modelSel.value) fd.append("model", modelSel.value);
    runBtn.disabled = true;
    try {
      const r = await api(`/api/workspaces/${wid}/batch-stream`, { method: "POST", body: fd });
      (S.recent[wid] = S.recent[wid] || []).unshift({ id: r.batch_id, n: r.n_files, files: good.map((p) => p.f.name) });
      renderDashboard(runHost, wid, r.batch_id, r.n_files);
      picked.length = 0; renderPicked();
    } catch (e) { err.replaceChildren(el("div", { class: "error" }, e.message)); }
    finally { runBtn.disabled = false; }
  } }, "Process batch");

  const uploadCard = el("div", { class: "card" }, dz, fileInput, list, err,
    el("div", { class: "btn-row" }, runBtn));

  view.append(controls, uploadCard, runHost, recentUploads(wid));
}

async function loadProviders(provSel, modelSel) {
  let provs = [];
  try { provs = await api("/api/providers"); } catch (_) { return; }
  for (const p of provs) {
    const opt = el("option", { value: p.name, ...(p.configured ? {} : { disabled: "disabled" }) },
      `${p.label}${p.configured ? "" : " (set key in .env)"}`);
    provSel.append(opt);
  }
  const fill = (name) => {
    // Empty value = "default provider" → resolve to the active one so its models show.
    const resolved = name || (S.status && S.status.provider);
    const p = provs.find((x) => x.name === resolved);
    modelSel.replaceChildren(el("option", { value: "" }, "default model"));
    for (const m of (p?.models || [])) modelSel.append(el("option", { value: m.id }, m.name || m.id));
    if (p && !p.reachable && p.configured) modelSel.append(el("option", { value: "", disabled: "disabled" }, `(${p.reason || "unreachable"})`));
    if (p && p.configured && !(p.models || []).length) modelSel.append(el("option", { value: "", disabled: "disabled" }, "(no models listed)"));
  };
  provSel.addEventListener("change", () => fill(provSel.value));
  // Populate the model list on load for the active/default provider — don't wait
  // for the user to re-pick the provider.
  fill(provSel.value);
}
async function loadPresets(sel, wid) {
  try {
    const { builtin, custom } = await api(`/api/workspaces/${wid}/presets`);
    for (const name of Object.keys(builtin || {})) sel.append(el("option", { value: name }, name));
    for (const name of Object.keys(custom || {})) sel.append(el("option", { value: name }, name + " (custom)"));
  } catch (_) {}
}

function recentUploads(wid) {
  const recent = S.recent[wid] || [];
  const card = el("div", { class: "card" }, el("h2", {}, "Recent uploads"));
  if (!recent.length) { card.append(el("p", { class: "muted" }, "Batches you process this session show up here.")); return card; }
  for (const r of recent.slice(0, 8)) card.append(el("div", { class: "bresult" },
    el("span", { class: "bfn" }, `Batch ${r.id}`),
    el("span", { class: "bstats" }, el("span", {}, `${r.n} file(s)`), el("span", { class: "faint" }, r.files.slice(0, 3).join(", ") + (r.files.length > 3 ? "…" : ""))),
    el("button", { class: "btn ghost sm", onclick: (e) => { renderDashboard(e.target.closest(".card").parentElement, wid, r.id, r.n); } }, "View")));
  return card;
}

// --- live processing dashboard (real pipeline events; no faked log lines) ---
function renderDashboard(host, wid, batchId, nFiles) {
  const counts = { files: 0, completed: 0, inprog: 0, errors: 0, tier1: 0, flagged: 0, total: 0 };
  const tile = (id, label, cls) => el("div", { class: "dash-tile " + (cls || "") }, el("b", { id: "dt-" + id }, "0"), el("span", {}, label));
  const tiles = el("div", { class: "dash-tiles" },
    el("div", { class: "dash-tile" }, el("b", { id: "dt-files" }, "0/" + nFiles), el("span", {}, "files")),
    tile("completed", "completed"), tile("inprog", "in progress"),
    tile("errors", "errors", "err"), tile("tier1", "matched (Tier-1)", "tier1"), tile("flagged", "flagged", "flag"));
  const consoleBox = el("div", { class: "dash-console" });
  const fill = el("div", { class: "dash-bar-fill", id: "dash-fill" });
  const statusBadge = el("span", { class: "chip pending" }, "PROCESSING");
  const summaryLine = el("div", { class: "dash-summary-line" });
  const done = el("div", { class: "dash-done" });
  const resultsHost = el("div", {});
  const card = el("div", { class: "card" },
    el("div", { class: "dash-head" }, el("div", {}, el("h2", {}, "Processing batch"), el("div", { class: "b-id" }, `${batchId} · ${nFiles} document(s)`)), statusBadge),
    el("div", { class: "dash-bar" }, fill), tiles,
    el("h4", { class: "dash-h" }, "AI thinking"), consoleBox, summaryLine, done);
  host.replaceChildren(card, resultsHost);

  const set = (id, v) => { const e = $("dt-" + id); if (e) e.textContent = v; };
  const line = (cls, text) => {
    const t = new Date().toLocaleTimeString();
    consoleBox.append(el("div", { class: "cline " + (cls || "") }, el("span", { class: "cts" }, t + " "), text));
    consoleBox.scrollTop = consoleBox.scrollHeight;
  };
  counts.inprog = nFiles; set("inprog", nFiles);

  const es = new EventSource(`/api/runs/${batchId}/stream`);
  es.onmessage = (ev) => {
    const e = JSON.parse(ev.data);
    switch (e.type) {
      case "file_started": line("", `parser: started ${e.file}`); break;
      case "parsed": counts.total += e.questions || 0; line("ok", `parser:completed extracted ${e.questions} question(s)`); break;
      case "question_started": line("dim", `  question ${(e.id ?? "")}: ${(e.text || "").slice(0, 70)}`); break;
      case "retrieved": line("dim", `  retrieval: k=${e.k} top_score=${e.top_score ?? "—"}`); break;
      case "tier1_reuse": counts.tier1++; set("tier1", counts.tier1); line("ok", `  tier1:reuse approved answer (score ${e.score})`); break;
      case "library_candidate": line("warn", `  tier1:candidate close match (score ${e.score}) — needs review`); break;
      case "generated": line("", `  generate: drafted from grounded context`); break;
      case "attachment": line("", `  attachment: resolving evidence file`); break;
      case "faithfulness": line(e.passed ? "ok" : "bad", `  faithfulness: ${e.passed ? "passed" : "failed"}`); break;
      case "flagged": counts.flagged++; set("flagged", counts.flagged); line("warn", `  flagged: ${(e.reason || "").replace(/_/g, " ")}`); break;
      case "question_done": line(e.confidence === "high" ? "ok" : e.confidence === "low" ? "bad" : "warn", `  done: ${e.status} (${e.confidence})`); break;
      case "file_done":
        counts.files++; counts.completed++; counts.inprog = Math.max(0, counts.inprog - 1);
        set("files", counts.files + "/" + nFiles); set("completed", counts.completed); set("inprog", counts.inprog);
        $("dash-fill").style.width = Math.round(100 * counts.files / nFiles) + "%";
        line("ok", `file:done ${e.file} — ${e.answered} answered, ${e.flagged} flagged`); break;
      case "error": counts.errors++; counts.inprog = Math.max(0, counts.inprog - 1);
        set("errors", counts.errors); set("inprog", counts.inprog); line("bad", `error: ${e.error}`); break;
      case "_end":
        es.close();
        statusBadge.className = "chip " + (counts.flagged ? "review" : "done");
        statusBadge.textContent = counts.flagged ? "NEEDS REVIEW" : "DONE";
        summaryLine.textContent = `Found ${counts.total} total question(s) across the batch. ${counts.flagged} flagged and marked for review in the output files.`;
        api(`/api/runs/${batchId}/events`).then((snap) => {
          done.replaceChildren(
            snap.zip ? el("a", { class: "btn primary", href: `/api/runs/${batchId}/download/${snap.zip}`, download: snap.zip }, "Download all results (.zip)") : el("span"),
            el("button", { class: "btn ghost", onclick: async () => { await api(`/api/runs/${batchId}/audit`, { method: "POST" }).catch(() => {}); toast("Audit pack written to the run folder.", "good"); } }, "Build audit pack"));
        });
        renderBatchResults(resultsHost, batchId);
        break;
    }
  };
  es.onerror = () => { line("bad", "stream closed"); es.close(); };
}

async function renderBatchResults(host, batchId) {
  let files = [];
  try { files = (await api(`/api/runs/${batchId}/files`)).files; } catch (_) { return; }
  if (!files.length) return;
  const rows = files.map((f) => {
    const s = f.summary || {};
    const ok = f.ok !== false;
    const badge = !ok ? el("span", { class: "chip low" }, "ERROR")
      : (s.flagged ? el("span", { class: "chip review" }, "NEEDS REVIEW") : el("span", { class: "chip done" }, "DONE"));
    const stats = ok ? el("span", { class: "bstats" },
      el("span", {}, el("b", {}, String(s.answered ?? 0)), " / ", String(s.total ?? 0), " answered"),
      el("span", {}, el("b", {}, String(s.flagged ?? 0)), " flagged"),
      el("span", {}, el("b", {}, String(s.matched_tier1 ?? 0)), " KB-direct"),
      el("span", {}, el("b", {}, String(s.model_calls ?? 0)), " model calls"),
      el("span", {}, "~", el("b", {}, String(s.tokens_est ?? 0)), " tokens (est)"))
      : el("span", { class: "bstats" }, el("span", { class: "bad" }, f.error || "failed"));
    return el("div", { class: "bresult" }, el("span", { class: "bfn" }, f.file), badge, stats,
      f.artifact ? el("a", { class: "btn ghost sm", href: `/api/runs/${batchId}/files/${encodeURIComponent(f.stem)}/download`, download: "" }, "Download") : el("span"));
  });
  host.replaceChildren(el("div", { class: "card" }, el("h2", {}, "Batch results"), ...rows));
}

// ============================================================================
// Ask screen — one question through the SAME grounded path (run_ask/orchestrate).
// Thin: POST /api/workspaces/{id}/ask; renders answer + confidence + citations +
// audit, or the honest abstention. No answering logic here.
// ============================================================================
const CONF_CLASS = { high: "high", medium: "medium", low: "low" };

function askPage(view, wid) {
  view.append(el("div", { class: "page-head" }, el("h1", {}, "Ask"),
    el("div", { class: "sub" }, "Ask one question and get a grounded, cited answer — the exact same path as a questionnaire. If your KB doesn't support it, it says so instead of guessing.")));

  const provSel = el("select", {}, el("option", { value: "" }, `default (${S.status.provider})`));
  const modelSel = el("select", {}, el("option", { value: "" }, "default model"));
  loadProviders(provSel, modelSel);
  const tags = el("input", { placeholder: "tag scope (optional, defaults to workspace)" });
  const incl = el("input", { placeholder: "include only these sources (name/tag, comma-sep)" });
  const excl = el("input", { placeholder: "exclude these sources (name/tag, comma-sep)" });
  const question = el("textarea", { placeholder: "e.g. Do you encrypt customer data at rest?", style: "min-height:90px" });

  const controls = el("div", { class: "card" },
    el("label", { class: "field" }, el("span", {}, "Question"), question),
    el("div", { class: "row" },
      el("label", { class: "field" }, "Provider", provSel),
      el("label", { class: "field" }, "Model", modelSel),
      el("label", { class: "field" }, "Tag scope", tags)),
    el("div", { class: "row" },
      el("label", { class: "field" }, "Include sources", incl),
      el("label", { class: "field" }, "Exclude sources", excl)),
    el("p", { class: "muted" }, "Keys stay server-side — only names are shown. An unreachable provider blocks the run (no mock fallback)."));

  const progress = el("div", { class: "muted hidden" }, el("span", { class: "spinner" }), " Thinking…");
  const err = el("div", {});
  const out = el("div", {});

  const body = () => ({
    question: question.value,
    provider: provSel.value || null, model: modelSel.value || null,
    tags: csv(tags.value), include_sources: csv(incl.value), exclude_sources: csv(excl.value),
  });
  async function run(path, extra, busyBtn) {
    if (!question.value.trim()) return;
    err.replaceChildren(); progress.classList.remove("hidden"); if (busyBtn) busyBtn.disabled = true;
    try {
      const r = await jpost(`/api/workspaces/${wid}/${path}`, { ...body(), ...(extra || {}) });
      out.replaceChildren(groundedResult(r, { wid, question: question.value, rerun: run }));
    } catch (e) { err.replaceChildren(el("div", { class: "error" }, e.message)); }
    finally { progress.classList.add("hidden"); if (busyBtn) busyBtn.disabled = false; }
  }
  const askBtn = el("button", { class: "btn primary" }, "Ask");
  askBtn.addEventListener("click", () => run("ask", null, askBtn));

  controls.append(el("div", { class: "btn-row" }, askBtn, progress), err);
  view.append(controls, out);
}

// Shared grounded-result renderer — answer NEVER shown without its grounding
// (confidence + citations, or the honest abstention). opts (optional) enables the
// inline Regenerate (style-only guidance) + Save-to-library actions.
function groundedResult(r, opts) {
  const flagged = r.status === "needs_review" || (r.review_reason && r.review_reason !== "none");
  const card = el("div", { class: "item" + (flagged ? "" : " accepted") });
  const badges = el("div", { class: "badges" },
    el("span", { class: "chip " + (CONF_CLASS[r.confidence] || "low") }, r.confidence));
  if (flagged) badges.append(el("span", { class: "chip review" }, (r.review_reason || "needs review").replace(/_/g, " ")));
  else badges.append(el("span", { class: "chip done" }, r.status));
  card.append(el("div", { class: "item-head" }, el("div", { class: "q-text" }, r.question_text), badges));

  if (flagged && !r.answer) {
    card.append(el("div", { class: "panel warn" }, el("h4", {}, "No grounded answer found — flagged for review"),
      el("div", {}, r.missing_info || "The knowledge base doesn't support an answer to this question. Add a supporting document or an approved answer, then ask again.")));
  } else {
    // Signature: the answer sits on the provenance rail, tagged with its top source.
    const topSrc = (r.citations && r.citations[0] && r.citations[0].source) || "grounded context";
    card.append(el("div", { class: "answer-box" }, el("div", { class: "rail" },
      el("span", { class: "rail-src" }, "grounded in · " + topSrc), el("div", {}, r.answer || "(no answer)"))));
    if (flagged && r.missing_info) card.append(el("div", { class: "panel warn" }, el("h4", {}, "Why flagged"), r.missing_info));
  }

  const cites = citationsBlock(r.citations);
  if (cites) card.append(cites);
  else if (!flagged) card.append(el("p", { class: "muted" }, "No citations attached."));
  const audit = auditBlock(r.audit);
  if (audit) card.append(audit);

  // Inline Regenerate (style-only guidance) + Save-to-library — reuses the same
  // grounded path; regenerate can't force an answer, and Save trains the library.
  if (opts && opts.rerun) {
    const guidance = el("input", { placeholder: "Guidance for regenerate — style only (e.g. “shorter, first person”). Never changes what's grounded.", "aria-label": "Regenerate guidance (style only)" });
    const regen = el("button", { class: "btn" }, icon("copy"), "Regenerate");
    regen.addEventListener("click", () => opts.rerun("regenerate", { guidance: guidance.value }, regen));
    const actions = el("div", { class: "actions" }, regen);
    if (!flagged && r.answer) {
      const save = el("button", { class: "btn primary" }, icon("check"), "Save to library");
      save.addEventListener("click", async () => {
        save.disabled = true;
        try {
          await jpost(`/api/workspaces/${opts.wid}/qa`, { question: opts.question || r.question_text, answer: r.answer, approved_by: "ask" });
          toast("Saved to the answer library.", "good");
        } catch (e) { toast(e.message, "bad"); save.disabled = false; }
      });
      actions.append(save);
    }
    card.append(el("div", { class: "rail" }, el("span", { class: "rail-src" }, "refine"), guidance), actions);
  }
  return card;
}

function citationsBlock(cites) {
  if (!cites || !cites.length) return null;
  const d = el("details", {}, el("summary", {}, `${cites.length} citation(s)`));
  for (const c of cites) d.append(el("div", { class: "cite" },
    el("span", { class: "src" }, c.source), " ",
    (c.faithful === true ? "✓ " : c.faithful === false ? "✗ " : ""), c.snippet));
  return d;
}

function auditBlock(a) {
  if (!a) return null;
  const d = el("details", {}, el("summary", {}, "Audit — retrieved → cited → faithfulness → confidence"));
  const box = el("div", { class: "panel" });
  if ((a.retrieved || []).length) box.append(el("div", { class: "muted" }, `Retrieved ${a.retrieved.length} candidate(s) from the KB.`));
  if ((a.cited || []).length) box.append(el("div", { class: "muted" }, `Cited ${a.cited.length} source(s).`));
  if (a.faithfulness && a.faithfulness.passed != null)
    box.append(el("div", { class: a.faithfulness.passed ? "ok-msg" : "error" },
      `Faithfulness: ${a.faithfulness.passed ? "passed" : "failed"}${a.faithfulness.reason ? " — " + a.faithfulness.reason : ""}`));
  if (a.confidence_rationale) box.append(el("div", { class: "muted" }, "Confidence: " + a.confidence_rationale));
  if ((a.sources_used || []).length) box.append(el("div", { class: "faint" }, "Sources used: " + a.sources_used.join(", ")));
  if ((a.sources_excluded || []).length) box.append(el("div", { class: "faint" }, "Sources excluded: " + a.sources_excluded.join(", ")));
  d.append(box);
  return d;
}

// ============================================================================
// PART B — Knowledge Base page (Entries / Flagged / Duplicates)
// ============================================================================
function kbPage(view, wid) {
  view.append(el("div", { class: "page-head" }, el("h1", {}, "Knowledge Base"),
    el("div", { class: "sub" }, "Manage Q&A entries, connect a source, review flagged questions, and resolve duplicates.")));
  const sub = el("div", { class: "subtabs" });
  const body = el("div", {});
  const tab = (key, label) => el("div", { class: "subtab" + (kbPage._t === key ? " active" : ""), onclick: () => { kbPage._t = key; render(); } }, label);
  function render() {
    sub.replaceChildren(tab("entries", "Entries"), tab("documents", "Documents & sources"),
      tab("flagged", "Flagged"), tab("duplicates", "Duplicates"));
    body.replaceChildren();
    if (kbPage._t === "documents") documentsTab(body, wid);
    else if (kbPage._t === "flagged") flaggedTab(body, wid);
    else if (kbPage._t === "duplicates") duplicatesTab(body, wid);
    else entriesTab(body, wid);
  }
  kbPage._t = kbPage._t || "entries";
  view.append(sub, body);
  render();
}

// --- Tab: Documents & sources (upload files + connect Confluence/Notion/etc.) ---
function documentsTab(host, wid) {
  host.append(el("div", { class: "card" }, el("h2", {}, "Knowledge base documents"),
    el("p", { class: "muted" }, "Cited when answering. Drop files, or connect a source below. Tag docs to scope which answer which questionnaire."),
    assetManager(wid, "kb")));
  host.append(connectPanel(wid));
}

// --- Tab 1: Entries ---
async function entriesTab(host, wid) {
  host.append(el("div", { class: "statgrid" }, skeleton("sk-tile", 1).firstChild, skeleton("sk-tile", 1).firstChild),
    el("div", { class: "card" }, skeleton("sk-row", 5)));
  let entries = [];
  try { entries = (await api(`/api/workspaces/${wid}/qa`)).entries; } catch (e) { host.replaceChildren(el("div", { class: "error" }, e.message)); return; }
  host.replaceChildren();
  const cats = [...new Set(entries.map((e) => (e.tags && e.tags[0]) || "uncategorized"))].sort();

  const stats = el("div", { class: "statgrid" },
    el("div", { class: "statcard" }, el("div", { class: "v grad" }, String(entries.length)), el("div", { class: "l" }, "Total Q&A pairs")),
    el("div", { class: "statcard" }, el("div", { class: "v" }, String(cats.length)), el("div", { class: "l" }, "Categories")));

  const search = el("input", { class: "search", type: "search", placeholder: "Search questions & answers…" });
  const catFilter = el("select", {}, el("option", { value: "" }, "All categories"), ...cats.map((c) => el("option", { value: c }, c)));
  const tblHost = el("div", {});
  const toolbar = el("div", { class: "toolbar" },
    el("div", { class: "grow" }, search), catFilter, el("div", { class: "spacer" }),
    el("button", { class: "btn primary sm", onclick: () => openQaModal(wid, null, () => entriesTab(host.replaceChildren() || host, wid)) }, "+ Add Q&A"),
    el("button", { class: "btn ghost sm", onclick: () => openImportModal(wid, () => entriesTab(host.replaceChildren() || host, wid)) }, "Import"),
    el("button", { class: "btn ghost sm", onclick: () => exportMenu(wid) }, "Export"));

  function draw() {
    const q = search.value.trim().toLowerCase();
    const cat = catFilter.value;
    const rows = entries.filter((e) => {
      const ecat = (e.tags && e.tags[0]) || "uncategorized";
      if (cat && ecat !== cat) return false;
      if (q && !(e.question.toLowerCase().includes(q) || (e.answer || "").toLowerCase().includes(q))) return false;
      return true;
    });
    if (!rows.length) {
      tblHost.replaceChildren(entries.length
        ? el("div", { class: "empty-teach" }, el("strong", {}, "No matching entries"), " — adjust the search or filter.")
        : emptyState("book", "No approved answers yet",
            "Add a Q&A pair or import a CSV/XLSX. Answers here are used first, verbatim — and the flywheel grows this as you review.",
            el("div", { class: "btn-row", style: "justify-content:center" },
              el("button", { class: "btn primary", onclick: () => openQaModal(wid, null, () => entriesTab(host.replaceChildren() || host, wid)) }, "Add your first Q&A"),
              el("button", { class: "btn ghost", onclick: () => openImportModal(wid, () => entriesTab(host.replaceChildren() || host, wid)) }, "Import a file"))));
      return;
    }
    const table = el("table", { class: "tbl" }, el("thead", {}, el("tr", {},
      el("th", {}, "Category"), el("th", {}, "Question"), el("th", {}, "Answer"), el("th", { class: "acts" }, "Actions"))));
    const tb = el("tbody", {});
    for (const e of rows) {
      const ecat = (e.tags && e.tags[0]) || "uncategorized";
      tb.append(el("tr", {},
        el("td", {}, el("span", { class: "cat-badge" }, ecat)),
        el("td", { class: "q" }, el("div", { class: "truncate" }, e.question)),
        el("td", { class: "a" }, el("div", { class: "truncate" }, e.answer || "")),
        el("td", { class: "acts" },
          el("button", { class: "btn ghost sm", onclick: () => openQaModal(wid, e, () => entriesTab(host.replaceChildren() || host, wid)) }, "Edit"),
          el("button", { class: "btn danger sm", onclick: async () => {
            if (!confirm("Delete this Q&A entry? This cannot be undone.")) return;
            await api(`/api/workspaces/${wid}/qa/${e.index}`, { method: "DELETE" }); toast("Entry deleted."); entriesTab(host.replaceChildren() || host, wid);
          } }, "Del"))));
    }
    table.append(tb);
    tblHost.replaceChildren(el("div", { class: "card flush" }, el("div", { class: "tbl-scroll" }, table)));
  }
  search.addEventListener("input", draw);
  catFilter.addEventListener("change", draw);
  host.append(stats, toolbar, tblHost);
  draw();
}

function openQaModal(wid, entry, onSaved) {
  const isEdit = !!entry;
  const catInput = el("input", { value: entry ? ((entry.tags && entry.tags[0]) || "") : "", placeholder: "e.g. security, privacy" });
  const q = el("textarea", { placeholder: "The question as it appears in questionnaires" }, entry ? entry.question : "");
  const a = el("textarea", { placeholder: "Your approved answer (used first, verbatim)" }, entry ? entry.answer : "");
  const err = el("div", {});
  const cancel = el("button", { class: "btn ghost" }, "Cancel");
  const save = el("button", { class: "btn primary" }, "Save changes");
  const m = openModal(isEdit ? "Edit Q&A" : "Add Q&A", [
    el("label", { class: "field" }, "Category", catInput),
    el("label", { class: "field" }, "Question", q),
    el("label", { class: "field" }, "Answer", a), err,
  ], [cancel, save]);
  cancel.addEventListener("click", m.close);
  save.addEventListener("click", async () => {
    if (!q.value.trim() || !a.value.trim()) { err.replaceChildren(el("div", { class: "error" }, "Add both a question and an answer to save.")); return; }
    const tags = catInput.value.trim() ? [catInput.value.trim()] : [];
    try {
      if (isEdit) await jput(`/api/workspaces/${wid}/qa/${entry.index}`, { question: q.value, answer: a.value, tags });
      else await jpost(`/api/workspaces/${wid}/qa`, { question: q.value, answer: a.value, tags });
      m.close(); toast(isEdit ? "Entry updated." : "Entry added.", "good"); onSaved && onSaved();
    } catch (e) { err.replaceChildren(el("div", { class: "error" }, e.message)); }
  });
}

function openImportModal(wid, onDone) {
  const fileInput = el("input", { type: "file", multiple: "multiple", accept: ".csv,.json,.xlsx,.xlsm,.md,.markdown,.txt,.docx" });
  const res = el("div", {});
  const closeBtn = el("button", { class: "btn ghost" }, "Close");
  const imp = el("button", { class: "btn primary" }, "Import");
  const m = openModal("Import Q&A", [
    el("p", { class: "muted" }, "CSV / JSON / XLSX / Markdown / DOCX. Each pair routes through the library (dedup + version)."),
    fileInput, res,
  ], [closeBtn, imp]);
  closeBtn.addEventListener("click", m.close);
  imp.addEventListener("click", async () => {
    if (!fileInput.files.length) { res.replaceChildren(el("div", { class: "error" }, "Choose at least one file to import.")); return; }
    const fd = new FormData();
    for (const f of fileInput.files) fd.append("files", f);
    res.replaceChildren(el("span", { class: "muted" }, el("span", { class: "spinner" }), " Importing…"));
    try {
      const r = await api(`/api/workspaces/${wid}/qa/import`, { method: "POST", body: fd });
      const added = r.added ?? r.imported ?? 0, updated = r.updated ?? 0, skipped = (r.skipped ?? 0) + ((r.rejected || []).length);
      res.replaceChildren(el("div", { class: "ok-msg" }, `Imported ${added} · updated ${updated} · skipped ${skipped}. Library now holds ${r.total ?? "?"} entries.`));
      (r.rejected || []).forEach((x) => res.append(el("div", { class: "faint" }, `skipped ${x.name}: ${x.reason}`)));
      onDone && onDone();
    } catch (e) { res.replaceChildren(el("div", { class: "error" }, e.message)); }
  });
}

function exportMenu(wid) {
  const m = openModal("Export library", [
    el("p", { class: "muted" }, "Download the full answer library."),
    el("div", { class: "btn-row" },
      el("a", { class: "btn primary", href: `/api/workspaces/${wid}/qa/export?fmt=csv`, download: "qa_library.csv" }, "Export CSV"),
      el("a", { class: "btn ghost", href: `/api/workspaces/${wid}/qa/export?fmt=json`, download: "qa_library.json" }, "Export JSON")),
  ]);
  for (const a of m.bg.querySelectorAll("a")) a.addEventListener("click", m.close);
}

// --- Tab 2: Flagged (cross-file resolve) ---
async function flaggedTab(host, wid) {
  let groups = [];
  try { groups = (await api(`/api/workspaces/${wid}/flagged`)).groups; } catch (e) { host.append(el("div", { class: "error" }, e.message)); return; }
  const selected = new Set();

  const count = el("div", { class: "statgrid" },
    el("div", { class: "statcard" }, el("div", { class: "v grad" }, String(groups.length)), el("div", { class: "l" }, "Unresolved")));

  const chips = el("div", { class: "filter-chips" },
    el("span", { class: "fchip active" }, "Unresolved"),
    el("span", { class: "fchip", title: "Resolution history isn't persisted across restarts in this local tool." }, "Resolved"),
    el("span", { class: "fchip" }, "Dismissed"), el("span", { class: "fchip" }, "All"));

  const actions = el("div", { class: "toolbar" }, chips, el("div", { class: "spacer" }),
    el("a", { class: "btn ghost sm", href: `/api/workspaces/${wid}/flagged/export`, download: "flagged.csv" }, "Export CSV"),
    el("button", { class: "btn ghost sm", onclick: async () => {
      try { const r = await api(`/api/workspaces/${wid}/flagged/sync`, { method: "POST" }); toast(`Synced — cleared ${r.cleared} matched item(s).`, "good"); flaggedTab(host.replaceChildren() || host, wid); }
      catch (e) { toast(e.message, "bad"); }
    } }, "Sync with KB"),
    el("button", { class: "btn ghost sm", onclick: () => { S.page = "kb"; kbPage._t = "duplicates"; go("kb"); } }, "Remove Duplicates"));

  const helper = el("div", { class: "helper-line", html:
    "CSV round-trip: <strong>Export as CSV</strong>, fill the blank answer cells, <strong>Import</strong> them from the Entries tab, then <strong>Sync with KB</strong> to clear matched items." });

  const cards = el("div", {});
  if (!groups.length) {
    cards.append(emptyState("flag", "Nothing flagged",
      "When a batch produces questions your KB can't answer, they group here across every file — answer once, resolve everywhere.",
      el("button", { class: "btn primary", onclick: () => go("upload") }, "Process a questionnaire")));
  } else {
    for (const g of groups) cards.append(flaggedCard(wid, g, selected, () => flaggedTab(host.replaceChildren() || host, wid)));
  }
  host.append(count, actions, helper, cards);
}

function flaggedCard(wid, g, selected, reload) {
  const editor = el("div", { class: "hidden" });
  const card = el("div", { class: "item" });
  const cb = el("input", { type: "checkbox", onchange: (e) => { e.target.checked ? selected.add(g.question) : selected.delete(g.question); } });
  const head = el("div", { class: "item-head" },
    el("div", { style: "display:flex;gap:10px;align-items:flex-start" }, cb, el("div", { class: "q-text" }, g.question)),
    el("div", { class: "badges" }, el("span", { class: "chip pending" }, "PENDING"), el("span", { class: "chip reason" }, (g.reason || "").replace(/_/g, " "))));
  const meta = el("div", { class: "muted" }, `${g.count} occurrence(s) / ${g.files.length} file(s) · Affected files: ${g.files.join(", ")}`);

  const catInput = el("input", { class: "tagedit", placeholder: "category (optional)" });
  const ta = el("textarea", {}, g.draft || "");
  const status = el("span", { class: "muted" });
  const submit = el("button", { class: "btn primary", onclick: async () => {
    if (!ta.value.trim()) return;
    submit.disabled = true;
    try {
      const r = await api(`/api/workspaces/${wid}/flagged/resolve`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: g.question, answer: ta.value, tags: catInput.value.trim() ? [catInput.value.trim()] : [] }) });
      toast(`Resolved in ${r.updated} file(s)` + (r.trained ? " · added to library once" : ""), "good");
      setTimeout(reload, 500);
    } catch (e) { status.textContent = "Error: " + e.message; submit.disabled = false; }
  } }, "Submit answer");
  editor.append(el("div", { class: "panel" }, el("h4", {}, "Provide answer — inserted into every affected file and saved to the library once"),
    el("label", { class: "field" }, "Category", catInput),
    el("label", { class: "field" }, "Answer", ta),
    el("div", { class: "actions" }, submit, status)));

  const provide = el("button", { class: "btn primary sm", onclick: () => editor.classList.toggle("hidden") }, "Provide Answer");
  const dismiss = el("button", { class: "btn ghost sm", onclick: () => { card.style.opacity = .45; provide.disabled = true; } }, "Dismiss");
  card.append(head, meta, el("div", { class: "actions" }, provide, dismiss), editor);
  return card;
}

// --- Tab 3: Duplicates (kb-check) ---
async function duplicatesTab(host, wid) {
  host.append(el("p", { class: "muted" }, "A read-only scan of your library for near-duplicates and internal contradictions. Merging is opt-in and version-bumps the canonical (never deletes); contradictions are shown for you to resolve."));
  const body = el("div", {});
  host.append(body);
  let rep;
  try { rep = await api(`/api/workspaces/${wid}/kb-check`); } catch (e) { body.append(el("div", { class: "error" }, e.message)); return; }
  const dups = rep.duplicates || [], cons = rep.contradictions || [];

  const stats = el("div", { class: "statgrid" },
    el("div", { class: "statcard" }, el("div", { class: "v grad" }, String(dups.length)), el("div", { class: "l" }, "Near-duplicate pairs")),
    el("div", { class: "statcard" }, el("div", { class: "v" }, String(cons.length)), el("div", { class: "l" }, "Contradictions")));
  body.append(stats);

  if (dups.length) {
    const merge = el("button", { class: "btn primary sm", onclick: async () => {
      if (!confirm(`Merge ${dups.length} near-duplicate pair(s)? This version-bumps the canonical entry — nothing is deleted.`)) return;
      try { const r = await api(`/api/workspaces/${wid}/kb-check/merge`, { method: "POST" }); toast(`Merged ${r.merged} pair(s).`, "good"); duplicatesTab(host.replaceChildren() || host, wid); }
      catch (e) { toast(e.message, "bad"); }
    } }, "Remove Duplicates");
    body.append(el("div", { class: "toolbar" }, el("h2", { style: "margin:0" }, "Near-duplicates"), el("div", { class: "spacer" }), merge));
    for (const d of dups) body.append(dupPair(d, "duplicate"));
  }
  if (cons.length) {
    body.append(el("h2", { style: "margin-top:18px" }, "Contradictions — resolve manually"));
    for (const c of cons) body.append(dupPair(c, "contradiction"));
  }
  if (!dups.length && !cons.length) body.append(el("div", { class: "empty-teach" }, el("strong", {}, "Library looks clean"), " — no near-duplicates or contradictions found."));
}

function dupPair(p, kind) {
  const sim = p.similarity != null ? ` · similarity ${Math.round(p.similarity * 100)}%` : "";
  return el("div", { class: "item" },
    el("div", { class: "item-head" }, el("span", { class: "chip " + (kind === "contradiction" ? "low" : "medium") }, kind), el("span", { class: "faint" }, sim)),
    el("div", { class: "conflict-grid", style: "display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px" },
      el("div", { class: "panel" }, el("h4", {}, "Entry A"), el("div", {}, p.a_question || ""), el("div", { class: "muted" }, p.a_answer || "")),
      el("div", { class: "panel" }, el("h4", {}, "Entry B"), el("div", {}, p.b_question || ""), el("div", { class: "muted" }, p.b_answer || ""))));
}

// ============================================================================
// Settings page (model, assets, engine, analytics, danger)
// ============================================================================
async function settingsPage(view, wid) {
  view.append(el("div", { class: "page-head" }, el("h1", {}, "Settings"),
    el("div", { class: "sub" }, "Model, knowledge assets, engine behavior, and workspace analytics — all local.")));
  view.append(el("div", { class: "warn-banner" }, "This UI has no authentication and holds your full security posture. Keep it on 127.0.0.1 — don't expose it to a network without putting auth in front."));

  // Analytics (Phase 10 D) — local read only.
  try {
    const s = await api(`/api/workspaces/${wid}/stats`);
    const reasons = Object.entries(s.flagged_by_reason || {}).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ");
    view.append(el("div", { class: "card" }, el("h2", {}, "Analytics"),
      el("div", { class: "statgrid" },
        el("div", { class: "statcard" }, el("div", { class: "v" }, String(s.n_runs)), el("div", { class: "l" }, "Runs")),
        el("div", { class: "statcard" }, el("div", { class: "v" }, String(s.total_questions)), el("div", { class: "l" }, "Questions")),
        el("div", { class: "statcard" }, el("div", { class: "v grad" }, Math.round(s.completion_rate * 100) + "%"), el("div", { class: "l" }, "Completion")),
        el("div", { class: "statcard" }, el("div", { class: "v" }, Math.round(s.auto_answer_rate_high_med * 100) + "%"), el("div", { class: "l" }, "Auto (high+med)")),
        el("div", { class: "statcard" }, el("div", { class: "v" }, String(s.flagged)), el("div", { class: "l" }, "Flagged"))),
      reasons ? el("p", { class: "muted" }, "Flagged: " + reasons) : el("span"),
      el("p", { class: "muted" }, `~${s.time_saved_minutes} min saved — ${s.time_saved_note}`)));
  } catch (_) {}

  // Model status
  const dres = el("div", {});
  view.append(el("div", { class: "card" }, el("h2", {}, "Model"),
    el("div", { class: "model-status " + (S.status.active ? "ok" : "bad") }, el("span", { class: "ms-dot" }), el("span", { class: "ms-text" }, `${S.status.provider}/${S.status.model}`)),
    el("p", { class: "muted" }, "Provider and key live in .env on the server — never in this page. Local models need no key."),
    el("div", { class: "btn-row" }, el("button", { class: "btn", onclick: async () => { dres.textContent = "Testing…"; await refreshDoctor(); await refreshStatus(); dres.replaceChildren(S.doctor?.ok ? el("span", { class: "ok-msg" }, "✓ reachable") : el("div", { class: "error" }, "✗ not reachable — check .env")); } }, "Test connection")), dres));

  // Evidence lives here; KB documents + source connectors moved to Knowledge Base ▸ Documents & sources.
  view.append(el("div", { class: "card" }, el("h2", {}, "Knowledge base & sources"),
    el("p", { class: "muted" }, "Manage KB documents and connect Confluence / Notion / SharePoint / OneDrive from "),
    el("div", { class: "btn-row" }, el("button", { class: "btn", onclick: () => { kbPage._t = "documents"; go("kb"); } }, "Knowledge Base ▸ Documents & sources"))));
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
  view.append(el("div", { class: "card" }, el("h2", {}, "Engine settings"),
    el("label", { class: "field" }, "Retrieval mode (in-context dumps the KB; retrieval ranks it — better for large KBs)", modeSel),
    el("label", { class: "field" }, el("span", {}, faith, " Verify faithfulness (check each claim is entailed by its citation)")),
    el("label", { class: "field" }, el("span", {}, conflict, " Detect cross-source conflicts (flag contradictory answers)")),
    el("label", { class: "field" }, "Default tag scope", tagsDefault),
    el("div", { class: "btn-row" }, el("button", { class: "btn primary", onclick: async () => {
      try { await jpatch(`/api/workspaces/${wid}/settings`, { kb_mode: modeSel.value, verify_faithfulness: faith.checked, detect_conflicts: conflict.checked, tags: csv(tagsDefault.value) });
        saveRes.replaceChildren(el("span", { class: "ok-msg" }, "✓ saved")); }
      catch (e) { saveRes.replaceChildren(el("div", { class: "error" }, e.message)); }
    } }, "Save settings")), saveRes));

  // Danger zone
  view.append(el("div", { class: "card" }, el("h2", {}, "Danger zone"),
    el("div", { class: "btn-row" }, el("button", { class: "btn danger", onclick: async () => {
      if (!confirm(`Delete workspace "${S.current}" and all its files? This cannot be undone.`)) return;
      await api(`/api/workspaces/${wid}`, { method: "DELETE" }); S.current = null; await loadWorkspaces();
      if (S.workspaces.length) { S.current = S.workspaces[0].id; renderSwitcher(); go("upload"); } else { hideChrome(); showWizard(); }
    } }, "Delete this workspace"))));
}

// ---- Connect a source (folder/website/SaaS connectors) ----
function connectPanel(wid) {
  const card = el("div", { class: "card" }, el("div", { style: "display:flex;gap:10px;align-items:center;margin-bottom:6px" },
    el("span", { style: "color:var(--accent)" }, icon("plug")), el("h2", { style: "margin:0" }, "Connect a source")),
    el("p", { class: "muted" }, "Pull documents from where they live into this workspace's KB. Credentials stay server-side in .env; connectors run only when you click Connect — never during answering."));
  const sel = el("select", { "aria-label": "Connector type" });
  const authHost = el("div", {});
  const fieldsHost = el("div", {});
  const tags = el("input", { class: "tagedit", placeholder: "tags (optional)" });
  const status = el("div", {});
  const connectBtn = el("button", { class: "btn primary" }, "Connect");
  let conns = [];
  const spec = () => conns.find((c) => c.type === sel.value);
  const reload = () => api("/api/connectors").then((list) => { conns = list; renderFields(); });

  function renderAuth(c) {
    authHost.replaceChildren();
    if (!c.oauth) {
      if (c.needs_cred && !c.configured)
        authHost.append(el("div", { class: "warn-banner" }, `Credential not set — add ${c.cred_hint || "the token"} on the server, then Connect.`));
      return true; // no oauth gate
    }
    if (!c.oauth_configured) {
      authHost.append(el("div", { class: "warn-banner" },
        `${c.label} sign-in isn't set up yet. Register an OAuth app and set its client id/secret in .env (redirect URI: this server + /api/oauth/callback). You can also paste a personal token in .env instead.`));
      return c.configured; // may still be usable via a static .env token
    }
    if (c.oauth_connected) {
      authHost.append(el("div", { class: "rail" },
        el("span", { class: "rail-src" }, "signed in"),
        el("div", { style: "display:flex;gap:10px;align-items:center" },
          el("span", { class: "chip done" }, el("span", { class: "dot" }), `Signed in with ${c.label}`),
          el("button", { class: "btn ghost sm", onclick: async () => {
            await api(`/api/oauth/${c.oauth_provider}`, { method: "DELETE" }); toast(`Disconnected ${c.label}.`); reload();
          } }, "Disconnect"))));
      return true;
    }
    // Configured but not signed in → the Sign-in button.
    const signIn = el("button", { class: "btn primary", onclick: async () => {
      try {
        const { authorize_url } = await api(`/api/oauth/${c.oauth_provider}/start`);
        const w = window.open(authorize_url, "qr-oauth", "width=560,height=720");
        toast(`Opened ${c.label} sign-in — approve, then come back.`);
        const onMsg = (ev) => { if (ev.data === "qr-oauth-done") { window.removeEventListener("message", onMsg); clearInterval(poll); reload(); } };
        window.addEventListener("message", onMsg);
        // Fallback: poll status in case the popup can't postMessage back.
        const poll = setInterval(async () => {
          const st = await api("/api/oauth/status").catch(() => []);
          if ((st.find((x) => x.provider === c.oauth_provider) || {}).connected) { clearInterval(poll); window.removeEventListener("message", onMsg); reload(); }
          if (w && w.closed) { clearInterval(poll); }
        }, 2000);
      } catch (e) { toast(e.message, "bad"); }
    } }, icon("plug"), `Sign in with ${c.label}`);
    authHost.append(el("div", { class: "btn-row" }, signIn,
      el("span", { class: "muted" }, "You'll approve access in a new tab. Your token is stored on the server, never in this page.")));
    return false; // gate Connect until signed in
  }

  function renderFields() {
    const c = spec(); if (!c) return;
    const ready = renderAuth(c);
    // Confluence + signed in → offer a space PICKER (choose "Engineering" by name)
    // and a page limit, instead of a raw space-key text field.
    if (c.type === "confluence" && c.oauth_connected) { renderConfluencePicker(); }
    else {
      const inputs = {};
      fieldsHost.replaceChildren(...c.fields.map((f) => {
        const inp = el("input", { type: f.type === "number" ? "number" : "text", placeholder: f.label,
          value: f.name === "depth" ? "1" : f.name === "max_pages" ? "20" : "" });
        inputs[f.name] = inp;
        return el("label", { class: "field" }, f.label, inp);
      }));
      fieldsHost._inputs = inputs;
    }
    connectBtn.disabled = !ready;
    connectBtn.title = ready ? "" : "Sign in first";
    status.replaceChildren();
  }

  function renderConfluencePicker() {
    const spaceSel = el("select", { "aria-label": "Confluence space" }, el("option", { value: "" }, "Loading spaces…"));
    const spaceKey = el("input", { placeholder: "or type a space key (e.g. ENG)" });
    const maxItems = el("input", { type: "number", value: "500", min: "1", "aria-label": "Max pages" });
    // Effective space = the picked one, else the typed fallback.
    fieldsHost._inputs = { space: { get value() { return spaceSel.value || spaceKey.value; } },
                           max_items: maxItems };
    fieldsHost.replaceChildren(
      el("label", { class: "field" }, "Space (pulls the whole space)", spaceSel),
      el("div", { class: "row" },
        el("label", { class: "field" }, "Or space key", spaceKey),
        el("label", { class: "field" }, "Max pages", maxItems)));
    api("/api/connectors/confluence/spaces").then(({ spaces }) => {
      if (!spaces || !spaces.length) { spaceSel.replaceChildren(el("option", { value: "" }, "No spaces found — type a key")); return; }
      spaceSel.replaceChildren(el("option", { value: "" }, "Select a space…"),
        ...spaces.map((s) => el("option", { value: s.key }, `${s.name} (${s.key})`)));
    }).catch((e) => { spaceSel.replaceChildren(el("option", { value: "" }, "Couldn't list spaces — type a key")); toast(e.message, "bad"); });
  }
  connectBtn.addEventListener("click", async () => {
    const c = spec(); if (!c) return;
    const body = { type: c.type, tags: csv(tags.value) };
    for (const [k, inp] of Object.entries(fieldsHost._inputs || {})) if (inp.value.trim()) body[k] = inp.value.trim();
    connectBtn.disabled = true; status.replaceChildren(el("span", { class: "muted" }, el("span", { class: "spinner" }), " Connecting…"));
    try {
      const r = await jpost(`/api/workspaces/${wid}/connect`, body);
      const n = (r.accepted || []).length;
      status.replaceChildren(el("div", { class: "ok-msg" }, `Ingested ${n} document(s) into the KB.`));
      (r.rejected || []).forEach((x) => status.append(el("div", { class: "faint" }, `skipped ${x.name}: ${x.reason}`)));
    } catch (e) { status.replaceChildren(el("div", { class: "error" }, e.message)); }
    finally { connectBtn.disabled = false; }
  });
  sel.addEventListener("change", renderFields);
  api("/api/connectors").then((list) => {
    conns = list;
    sel.replaceChildren(...list.map((c) => {
      const mark = c.oauth ? (c.oauth_connected ? " ✓" : "") : (c.needs_cred ? (c.configured ? " ✓" : "") : "");
      return el("option", { value: c.type }, c.label + mark);
    }));
    renderFields();
  }).catch(() => card.append(el("div", { class: "muted" }, "Connectors unavailable.")));
  card.append(el("div", { class: "row" }, el("label", { class: "field" }, "Source", sel), el("label", { class: "field" }, "Tags", tags)),
    authHost, fieldsHost, el("div", { class: "btn-row" }, connectBtn), status);
  return card;
}

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
    if (!files.length) { list.replaceChildren(el("li", {}, el("div", { class: "empty-teach" },
      kind === "kb" ? el("span", {}, el("strong", {}, "Add your security policies"), " — the documents you'd cite when answering.")
                    : el("span", {}, el("strong", {}, "Add evidence files"), " — attached to “please attach…” questions.")))); return; }
    list.replaceChildren(...files.map((f) => {
      const tagInput = el("input", { class: "tagedit", value: (f.tags || []).join(", "), placeholder: "tags (comma-sep)" });
      const save = el("button", { class: "btn ghost sm", onclick: async () => { const r = await jpatch(`/api/workspaces/${wid}/${kind}/${encodeURIComponent(f.name)}`, { tags: csv(tagInput.value) }); render(r.files); } }, "Save tags");
      const del = el("button", { class: "btn danger sm", onclick: async () => { const r = await api(`/api/workspaces/${wid}/${kind}/${encodeURIComponent(f.name)}`, { method: "DELETE" }); render(r.files); } }, "Remove");
      return el("li", {}, el("span", { class: "fname" }, f.name), tagInput, save, del);
    }));
  }
  api(`/api/workspaces/${wid}/${kind}`).then((r) => render(r.files)).catch(() => render([]));
  wrap.append(dz, fileInput, err, list);
  return wrap;
}

// ============================================================================
// Setup wizard (first run / new workspace)
// ============================================================================
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
  (body, st, next) => {
    body.append(el("p", { class: "why" }, "Name this workspace — usually a client or framework. ", el("span", { class: "eg" }, 'e.g. "Acme — SOC 2".')));
    const input = el("input", { placeholder: "Acme — SOC 2", value: st.name || "" });
    const err = el("div", {});
    body.append(el("label", { class: "field" }, "Workspace name", input), el("div", { class: "btn-row" },
      el("button", { class: "btn primary", onclick: async () => {
        try { const ws = await jpost("/api/workspaces", { name: input.value }); st.wid = ws.id; st.name = ws.name; st.i = 1; await loadWorkspaces(); next(); }
        catch (e) { err.replaceChildren(el("div", { class: "error" }, e.message)); }
      } }, "Create & continue")), err);
  },
  (body, st, next) => {
    body.append(el("p", { class: "why" }, "Where does the model run? Local is private and needs no key."));
    const active = el("div", { class: "model-status " + (S.status.active ? "ok" : "bad") }, el("span", { class: "ms-dot" }), el("span", { class: "ms-text" }, `${S.status.provider}/${S.status.model}`));
    const result = el("div", {});
    const test = el("button", { class: "btn", onclick: async () => {
      result.replaceChildren(el("span", { class: "muted" }, "Testing…"));
      await refreshDoctor(); await refreshStatus();
      if (S.doctor && S.doctor.ok) result.replaceChildren(el("div", { class: "ok-msg" }, "✓ Connection OK — model reachable."));
      else { const bad = (S.doctor?.checks || []).find((c) => !c.ok); result.replaceChildren(el("div", { class: "error" }, "✗ " + (bad ? bad.detail : "Not reachable. Check .env / that your local model is running."))); }
    } }, "Test connection");
    const cont = el("button", { class: "btn primary", onclick: () => { if (!S.doctor || !S.doctor.ok) return; st.i = 2; next(); } }, "Continue");
    body.append(el("div", { class: "card" }, active, el("div", { class: "btn-row" }, test), result),
      el("div", { class: "muted" }, "A green connection check is required to continue."), el("div", { class: "btn-row" }, cont));
    if (S.doctor && S.doctor.ok) result.replaceChildren(el("div", { class: "ok-msg" }, "✓ Connection OK."));
  },
  (body, st, next) => {
    body.append(el("p", { class: "why" }, "Add your security policies, SOC 2 summary, architecture docs — anything you'd cite ", el("span", { class: "eg" }, "(PDF/DOCX/MD/TXT).")));
    body.append(assetManager(st.wid, "kb"));
    body.append(el("div", { class: "btn-row" }, el("button", { class: "btn primary", onclick: () => { st.i = 3; next(); } }, "Continue")));
  },
  (body, st, next) => {
    body.append(el("p", { class: "why" }, "Answers you've already written and trust — used first and verbatim. Optional: the flywheel builds this as you review."));
    body.append(qaQuickAdd(st.wid));
    body.append(el("div", { class: "btn-row" }, el("button", { class: "btn primary", onclick: () => { st.i = 4; next(); } }, "Continue"),
      el("button", { class: "btn ghost", onclick: () => { st.i = 4; next(); } }, "Skip")));
  },
  (body, st, next) => {
    body.append(el("p", { class: "why" }, "Files attached to “please attach…” questions — the real SOC 2 PDF, IR plan. Not used as answer text."));
    body.append(assetManager(st.wid, "evidence"));
    body.append(el("div", { class: "btn-row" }, el("button", { class: "btn primary", onclick: () => { st.i = 5; next(); } }, "Continue"),
      el("button", { class: "btn ghost", onclick: () => { st.i = 5; next(); } }, "Skip")));
  },
  (body, st) => {
    body.append(el("p", { class: "why" }, "You're set. Upload a questionnaire and let QRESPONDER draft grounded, cited answers — you review every one."));
    body.append(el("div", { class: "btn-row" }, el("button", { class: "btn primary", onclick: async () => {
      S.current = st.wid; await loadWorkspaces(); renderSwitcher(); renderNav(); go("upload");
    } }, "Go to upload →")));
  },
];
function qaQuickAdd(wid) {
  const wrap = el("div", {});
  const q = el("input", { placeholder: "Question" });
  const a = el("textarea", { placeholder: "Approved answer" });
  const list = el("ul", { class: "assets" });
  async function load() {
    const { entries } = await api(`/api/workspaces/${wid}/qa`);
    if (!entries.length) { list.replaceChildren(el("li", {}, el("div", { class: "empty-teach" }, el("strong", {}, "No approved answers yet"), " — add any you trust, or let the flywheel build them as you review."))); return; }
    list.replaceChildren(...entries.map((e) => el("li", {},
      el("span", { class: "fname" }, `${e.question} → ${(e.answer || "").slice(0, 60)}${(e.answer || "").length > 60 ? "…" : ""}  (v${e.version})`),
      el("button", { class: "btn danger sm", onclick: async () => { await api(`/api/workspaces/${wid}/qa/${e.index}`, { method: "DELETE" }); load(); } }, "Delete"))));
  }
  load();
  wrap.append(el("div", { class: "form" }, el("label", { class: "field" }, "Question", q), el("label", { class: "field" }, "Answer", a),
    el("div", { class: "btn-row" }, el("button", { class: "btn", onclick: async () => {
      if (!q.value.trim() || !a.value.trim()) return;
      await jpost(`/api/workspaces/${wid}/qa`, { question: q.value, answer: a.value }); q.value = a.value = ""; load();
    } }, "Add approved answer"))), list);
  return wrap;
}

boot();
