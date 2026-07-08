# Changelog

All notable changes to QRESPONDER. Format based on
[Keep a Changelog](https://keepachangelog.com/); this project uses semantic
versioning.

## [Unreleased]

### Added
- **PDF write-back — every ingested format now writes back.** Previously only
  `.xlsx/.xlsm` and `.docx` were filled in place; PDF questionnaires got answers
  only in the separate outputs. `answer --writeback` (and the web Export) now
  produce `<name>.answered.pdf` that keeps the original pages untouched and appends
  a grounded **Responses** section — each question with its answer (or a red review
  marker) and its source. Adds `pypdf` + `fpdf2` to the base install. Needs no
  per-answer anchors, so it works on any PDF.

- **One-command hosted stack.** `docker-compose.hosted.yml` + a `Caddyfile` run the app
  internally with **Caddy for automatic Let's Encrypt HTTPS** in front — set
  `QR_DOMAIN`, `QR_ACME_EMAIL`, `QRESPONDER_AUTH_TOKEN` and `docker compose -f
  docker-compose.hosted.yml up -d`. The app is kept off the public interface (only
  Caddy is exposed) and gated by the token. `docs/hosting.md` rewritten with a turnkey
  GCP/VPC walkthrough (create VM → firewall → DNS → one command).

## [0.2.0] - 2026-07-06

First open-source release: the full local-first dashboard, all seven source
connectors (real APIs + OAuth + token refresh), one-command Docker + a published
GHCR image, optional hosted auth, and complete docs — over the grounded engine.

### Added
- **Open-source & run-anywhere (Phase 16).** One-command Docker: a multi-stage
  `Dockerfile` (non-root, web+retrieval, `HEALTHCHECK` on a new always-open `/healthz`)
  + `docker-compose.yml` (`docker compose up -d` → `http://localhost:8000`, persistent
  workspaces volume, host-loopback port, optional bundled-Ollama `local` profile). CI
  now builds the image and runs a **container smoke test**; a `release.yml` builds +
  pushes a **multi-arch (amd64+arm64) image to GHCR** and cuts a GitHub Release on a
  tag. Governance/docs: `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue/PR templates,
  `ARCHITECTURE.md`, and a `docs/` set (install, usage, connectors + **live-verify
  checklist**, security/privacy, hosting); README overhauled (hero, badges, quickstart).
- **Optional hosted auth (Phase 16).** `QRESPONDER_AUTH_TOKEN` — when set, the app +
  API require it (Bearer header or `?token=` → httponly cookie, constant-time compare);
  **unset by default** so the local `127.0.0.1` experience is unchanged. The `serve`
  CLI prints a loud warning when binding non-loopback without it. `docs/hosting.md`
  covers reverse proxy + TLS.

### Fixed
- **All connectors hardened to real APIs (Phase 16).** Notion (`api.notion.com/v1`,
  `Notion-Version`, `start_cursor`), Google Drive (Drive v3 `files.list` pageToken +
  export/media), SharePoint/OneDrive (Microsoft Graph, `@odata.nextLink`) now run their
  real pagination/download against an **injectable HTTP fetcher**, so the wiring is
  exercised offline with real-API-shaped mocks. Added **Microsoft OAuth** (SharePoint +
  OneDrive), **token auto-refresh** on a 401 during sync, and `test_connection()` on
  every connector. Filenames from connectors now preserve real extensions.
- **`typeshape.coerce_to_options`** uses a word-boundary match so a short option ("No")
  no longer maps inside a longer word ("know"/"not"). Grounding-safe as before.

### Added
- **Home dashboard, icon-rail nav, and KB Insights (Phase 15; front-end + a small
  additive report).** A left **icon rail** (Home · Upload · Ask · Knowledge Base ·
  Connections · Insights · Settings — aria-labelled, tooltip, keyboard-navigable)
  replaces the top text nav; the model-status pill + workspace switcher move to a
  content top bar. A real first-run **Home** (`GET …/home`): a local-first assurance
  banner (our actual guarantee — *your documents never leave this machine*), a hero
  KB-upload zone, a **Setup checklist** with a progress ring that reflects real state
  (KB non-empty · asked · ≥1 run), and quick tiles. **Gated empty states** — Upload
  and Ask require a non-empty KB and say why ("answers must be grounded"), with the
  next action. New **KB Insights** knowledge-gap report (`qresponder kb-insights`,
  `GET …/insights`, CSV/JSON export): from the workspace's run history it surfaces
  what the KB couldn't answer — gaps by reason with example questions, keyword gap
  themes, the per-run abstain-rate trend, and the most-reused Tier-1 answers. Local
  read only, no DB/telemetry; Phase-13 tokens throughout. Explicitly NOT built: an
  external Answer Hub, per-user/messaging analytics, SSO/user-management/billing.
- **In-UI Connections manager (Prowler-style).** Configure every source from the
  browser — **Knowledge Base ▸ Documents & sources**: click **+ Add Connection**,
  pick the source, fill only the fields it needs, **Test connection** (must pass
  before Save), and it appears in a Connections list (status pill · last-synced ·
  Test · **Sync now** · Remove). **The credential boundary is enforced:** a
  connection is a non-secret record (`{id, type, label, config, status, last_synced}`);
  secrets/tokens live in a separate server-side store keyed by id and are **never**
  returned to the browser, echoed after entry, or logged. OAuth sources use the
  redirect flow (token exchanged + stored server-side); token sources take a field
  POSTed once over loopback and stored server-side. New: `core/connections.py`
  (`ConnectionStore`, `build_connector`), connector `test_connection()`, and
  `GET/POST/PATCH/DELETE …/connections`, `…/connections/test`,
  `…/connections/{id}/test`, `…/connections/{id}/sync`,
  `…/connections/{type}/authorize`. Connectors fetch only on explicit Test/Sync,
  never during answering; SSRF guard on URL inputs; extras-gated + offline-tested.
- **OAuth login for connectors (Notion, Google Drive, Confluence).** Sign in with
  the provider in the browser instead of pasting a personal token. Standard
  Authorization Code + PKCE (`connectors/oauth.py`) — the **client secret and the
  access token stay server-side** and never reach the browser. New endpoints
  `GET /api/oauth/{provider}/start`, `GET /api/oauth/callback`, `GET /api/oauth/status`,
  `DELETE /api/oauth/{provider}`; `/connect` prefers the stored OAuth token over a
  static `.env` token; the Connect panel shows a **Sign in with …** button and a
  signed-in chip. Requires a one-time registered OAuth app per provider
  (`*_client_id` / `*_client_secret` in `.env`, redirect URI `…/api/oauth/callback`).
  Connectors still run only on explicit connect, never during answering. Also fixed
  `/connect` to accept array tags from the UI.
- **Design-system overhaul (front-end only; engine unchanged).** A single design-
  token layer (`tokens.css`) — cool ink neutral ramp with real elevation, a
  disciplined violet→blue accent ramp (subtle fills/hover/active; gradient reserved
  for the one primary action + the signature), calibrated semantic ramps
  (ok/warn/danger/info) that drive confidence colors, an AA-checked text ramp
  (fixes the low-contrast "dead" greys), a modular type scale with mono reserved for
  source tags/console/IDs, and spacing/radius/elevation/motion scales (with
  `prefers-reduced-motion` honored). `styles.css` rebuilt on tokens only — polished
  buttons/inputs/cards/pills/tables/modals/toasts/tabs/tiles, loading skeletons,
  real empty states, visible focus rings, and the signature **provenance rail** that
  ties grounded answers and citations to their source. Applied across every screen.
- **Inline Regenerate.** `POST /api/workspaces/{id}/regenerate` re-runs one question
  through the **same** `run_ask`/orchestrate path (shared with Ask), with an optional
  **style-only** `guidance` note that overrides the preset but only reaches
  `prompts.style_block`. It cannot force an answer — `snippet_supported` +
  faithfulness + abstain still apply. The Ask screen gets Regenerate + Save-to-library
  (trains via `approve_one`).
- **More named connectors.** Confluence, Notion, SharePoint, and OneDrive over the
  Phase-10B `Connector` interface (shared `TokenConnector` base): credentials
  server-side (`.env`), ingest via the existing bulk path (validation / sandboxing /
  provenance / tags), extras-gated + lazy-imported, injectable clients so tests run
  offline, SSRF-guarded base URLs, bounded by `max_items`. `qresponder connect
  confluence|notion|sharepoint|onedrive`, a **Connect a source** panel, and
  `GET /api/connectors` (fields + configured state, never the credential).
  Connectors run **only** on explicit `connect`, never during answering.
- **Ask screen (dashboard).** Surfaces the existing Ask endpoint
  (`POST /api/workspaces/{id}/ask` → `run_ask`, the same grounded path) as a UI
  screen: a question box, the same live provider/model picker as Upload (configured
  only, unreachable blocks, keys server-side), and an optional tag scope + per-source
  include/exclude (Phase-10C). Renders the answer **only with its grounding** — a
  confidence chip, expandable citations, and the audit chain (retrieved → cited →
  faithfulness → confidence) — and renders the honest abstention (`needs_review` +
  missing info) when the KB doesn't support it, never a fabricated answer. UI only;
  no new backend/answering logic. New nav order: **Upload · Knowledge Base · Ask ·
  Settings**.
- **Polished dashboard UI (Phase 11).** A dark command-center web UI over the
  existing engine — **reimplements no answering logic**; the single grounded path
  stays the only path. Persistent top nav (**Upload · Knowledge Base · Ask ·
  Settings**),
  a live model-status pill (name only, never a key), no login/account.
  - **Upload** — per-batch live provider/model picker (configured providers only;
    unreachable blocks the run, no mock fallback), Phase-7 presets (style only),
    multi-file drag-drop with per-file type validation, Recent Uploads.
  - **Live processing** — batch card + progress bar, an **"AI Thinking"** SSE
    console driven by the *real* pipeline events (no faked competitor log lines),
    live stat tiles, **Download all results (.zip)** + audit pack.
  - **Batch results** — per-file rows (DONE / NEEDS REVIEW) with answered/flagged/
    KB-direct/model-calls/~tokens(est) and a per-file download of the filled
    original. New additive endpoints `GET /api/runs/{id}/files` +
    `…/files/{stem}/download`; batch `_file_summary` gains exact `model_calls` and
    a labeled `tokens_est`.
  - **Knowledge Base** — three tabs. **Entries** (Total/Categories cards, search +
    category filter, Add/Edit modal routed through the library write, Import, and
    new `GET …/qa/export?fmt=csv|json`). **Flagged** (cross-file groups → Provide
    Answer resolves everywhere + trains once; new `GET …/flagged/export` round-trip
    CSV and `POST …/flagged/sync` to clear library-matched items). **Duplicates**
    (`kb-check` scan; new opt-in `POST …/kb-check/merge` version-bumps the
    canonical, never deletes; contradictions shown for manual resolution).
  - **Review markers (Part F)** — `answered.xlsx` and write-back now render the
    unresolved-cell marker in **red italic**; ANSWERED cells untouched, the
    original never overwritten, validations/media preserved.
  - No CDN / external fonts / telemetry; 127.0.0.1 default with the exposure
    warning; no API key in any response. Additive-only models; grounding /
    faithfulness / citations unchanged; Tier-1 exempt and unoverridden.
- **Ask mode (Phase 10 A).** `qresponder ask "<question>"` (and `POST
  /api/workspaces/{id}/ask`) — one natural-language question through the **exact
  same grounded path** as a questionnaire (`run_ask` reuses `orchestrate`): answer
  + citations + explainable confidence + full audit trail; abstains when
  unsupported. Honors the Phase-8 provider/model selection (no mock fallback);
  key stays server-side.
- **Source connectors (Phase 10 B).** A `connectors/` package (`Connector` base →
  `SourceDoc`, ingest via the Phase-8 bulk-ingest path). **Folder** import
  (zero-dep, path-contained) and a **website crawler** (bounded by depth/max-pages,
  same-domain, per-request timeout, **SSRF guard** rejecting localhost/private/
  link-local/metadata; injectable fetcher) via `qresponder connect folder|website`
  and `POST …/connect`. Optional **Google Drive** behind a `connectors` extra.
  Connectors run **only on explicit `connect` — never during answering**; the
  answering path makes no fetch.
- **Per-run source include/exclude (Phase 10 C).** `--include-source` /
  `--exclude-source` (by source name or tag) on `answer`/`ask` + run/ask forms;
  applied at retrieval/candidate selection and library scoping (reuses
  tag-scoping). Effective `sources_used` / `sources_excluded` recorded in the
  audit. If excluding a source removes the grounding, the answer **abstains** —
  it never fabricates from another source.
- **Completion / analytics (Phase 10 D).** `qresponder stats --workspace` + a web
  Analytics panel (`GET …/stats`) — aggregates the workspace's own run
  `results.json` files into completion rate, auto-answer rate by confidence,
  flagged-by-reason, and a configurable, explicitly-labeled **time-saved
  estimate** (`stats_minutes_per_question`, default 10). Local read only — no DB,
  no telemetry, nothing sent anywhere.
- **Provider flexibility + live model picker (Phase 8 A/B).** Keys for OpenAI /
  Gemini / DeepSeek / Anthropic (own `.env` key each) + local; OpenAI-compatible
  adapter routes the cloud ones by base URL, Anthropic native. `llm/models.py`
  fetches **live** model lists per provider (server-side, key-gated, cached, with
  a reason on failure — never hardcoded); `llm/providers.py` registry + routing.
  `GET /api/providers` (no key) and extended `GET /api/status` (active + reason);
  `qresponder models`, `--provider/--model` on `answer`; per-run + workspace-default
  model selection. **No silent mock fallback** — an unreachable/unconfigured
  provider blocks the run with a clear error; the mock is test-only.
- **Bulk any-format ingestion (Phase 8 C).** Many files in one upload for KB and
  evidence (`core/bulk_ingest.py`) — PDF/DOCX/XLSX/CSV/TXT/MD/HTML **plus
  ZIP-of-those** (expanded + ingested); each file validated, filename-sanitized
  (no traversal), tagged, and given provenance; unsupported types rejected
  **per file with a reason** (no abort). Approved answers from CSV/JSON/XLSX/MD/
  two-column DOCX (`core/qa_import.py`) routed through `approve_one` (dedup/
  version). KB loader extended to read HTML/XLSX/CSV.
- **Live processing dashboard (Phase 8 D).** `run_pipeline`/`orchestrate` emit
  structured progress events (file_started, parsed, question_started, retrieved,
  tier1_reuse/generated/attachment, faithfulness, flagged, question_done,
  file_done, error) via an `on_event` callback — thin, around the existing engine.
  `GET /api/runs/{id}/events` (snapshot) + `/stream` (SSE); a background
  `/api/workspaces/{id}/batch-stream`. A vanilla command-center dashboard (tracker
  + monospace "AI thinking" console, no CDN) renders the grounded path live and
  links the filled-originals ZIP on completion.
- **Cross-file flagged aggregation + one-click resolve (Phase 8 E).** A **Flagged**
  tab groups identical/near-duplicate unresolved questions across all of a
  workspace's runs (dedup similarity). Resolve once → the answer is inserted into
  every affected file's results **and** routed through `approve_one` (one
  versioned library entry, not N). Idempotent; respects write-back safety.
  `GET/POST /api/workspaces/{id}/flagged[/resolve]`.
- **Competitor-parity phase (Phase 7).**
  - **Answer-style presets** — built-in `concise`/`detailed`/`formal` + custom
    per-workspace presets; `--preset` / run form / workspace default. Style-only:
    subordinate to grounding (can't fabricate or drop citations); recorded in the
    audit trail.
  - **`qresponder kb-check`** — scans the Answer Library against itself for
    internal contradictions + near-duplicates (conservative; reuses the conflict
    heuristics). Read-only by default; `--merge-duplicates` version-bumps via
    approve_one and never deletes. Web endpoint per workspace.
  - **Visible review markers** — `NEEDS_REVIEW` cells are filled with
    `⚠ NEEDS REVIEW: <reason>` (in answered.xlsx and write-back) instead of left
    blank; toggle with `--no-review-markers`. ANSWERED cells untouched; a new
    universal non-empty guard means write-back never overwrites a pre-filled cell.
  - **Answer-type enforcement** — ANSWERED answers are shaped to their
    `answer_type` (select/dropdown → an allowed option via the Part F coercion
    path; yes/no left as grounded). Format-only: never fabricates an option,
    never forces an unmappable answer, never turns an abstention into an answer.
- **Provably-superior phase (A–G).**
  - **A — Provable accuracy:** RAGAS-aligned eval (faithfulness, answer relevancy,
    context precision/recall, correctness) + retrieval Recall@K/MRR, a calibration
    table (measured correctness per confidence bucket — proves HIGH means HIGH),
    and abstention as a first-class metric. Reproducible mock baseline + CI gate.
  - **B — Audit / evidence pack:** every answer carries a persisted `AuditTrail`
    (retrieved → cited → faithfulness → confidence rationale → human action);
    `qresponder audit` + web endpoint emit audit.json + audit.md (+zip).
  - **C — Injection resistance (SafeRAG):** all untrusted content wrapped in DATA
    blocks under a standing "data not instructions" system note; an injection
    detector flags `injection_suspected` without ever obeying the directive.
  - **D — Batch + ZIP:** `answer --batch` / web batch — isolated per-file runs,
    summary, and a single zip; one bad file never sinks the batch.
  - **E — CSV round-trip + dedup + SME routing:** export-flagged/import-answers
    (trains the library + flips the run; re-syncs duplicates via Tier-1);
    near-duplicate grouping (answer once, apply to all); tag→owner routing.
  - **F — Excel data-validation/dropdown preservation** in write-back (+ coerce
    to the allowed option).
  - **G — Consistency over time** (`history_conflict` vs prior submissions),
    **compound-question decomposition** (subanswers; flag if any part unsupported),
    and **query normalization** (acronym expansion + boilerplate strip) for recall.
- **Setup wizard + multi-workspace asset management (Phase 5)** — named,
  isolated workspaces (`core/workspace.py`; each with its own kb/, evidence/,
  qa.yaml, settings.yaml, runs/) under `WORKSPACES_DIR`. New web endpoints to
  create/list/rename/delete workspaces; upload/validate/tag KB & evidence
  (extension allow-list, sanitized filenames, `.tags.yaml` sidecar read by the
  KB/evidence loaders); CRUD approved answers; read/update per-workspace engine
  settings (no provider/key fields); live `/api/doctor` connection check;
  workspace-scoped runs. A guided setup wizard, workspace switcher, and Settings
  page in the (still vanilla, no-CDN) frontend. KB loaders now extract text from
  PDF/DOCX via the ingest loaders. The provider/key remain in `.env` only — never
  accepted, stored, or returned by any endpoint.
- **Local web review UI (Phase 4)** — `qresponder serve` launches a FastAPI app
  (vanilla HTML/CSS/JS, no build, no CDN, no telemetry; binds 127.0.0.1, no
  keys in the browser). Upload → run → review queue (confidence chips, citations,
  per-reason panels: interpretation picker, attachment confirm, library-candidate
  accept/reject, conflict reconcile) → export. **Every accept/edit trains the
  Answer Library via the flywheel**; edits train on the edited text; re-accepting
  is idempotent. New `web` extra; FastAPI/uvicorn lazy-imported.
- `core.flywheel.approve_one` — single-entry flywheel shared by the CLI batch
  `approve` and the web per-item accept.

## [0.1.0] — 2026-06-29

First release. Local-first, bring-your-own-model security-questionnaire
automation: grounded, cited, faithfulness-verified, human-gated. Feature-complete
engine (Phases 0–3). 70 tests, all offline (MockProvider), temperature 0.0.

### Added

**Core loop (Phase 0)**
- Format-agnostic ingestion (xlsx/docx/pdf) → layout-aware IR.
- LLM question extraction and grounded, cited answering with a defensive
  strict-JSON parser; two-adapter BYOM (`AnthropicProvider`,
  `OpenAICompatProvider`) + `MockProvider`; `doctor` preflight.
- Tiered knowledge: Tier-1 Answer Library (authority) → in-context KB; tag-scoping.
- Outputs: `answered.xlsx`, `results.json`, human-first `review.md`. CLI + Docker.

**Accuracy hardening (Phase 1)**
- Hybrid retrieval: BM25 + dense + RRF (k=60) → cross-encoder rerank (20→5),
  structure-aware chunking, `--mode retrieval` (all local).
- Faithfulness / citation verification (entailment, not topicality); Tier-1
  exempt. Explainable confidence (HIGH only via faithfulness + strong grounding,
  in both modes). Eval harness: Recall@K, faithfulness, correctness, coverage,
  and a score-distribution / suggested-threshold report.
- Library band-split matcher (auto-reuse vs human-confirm candidate);
  fabricated-citation rejection; question-id de-duplication.

**Scrut differentiators (Phase 2)**
- Ambiguity surfacing — one grounded draft per interpretation.
- Attachment resolution from an evidence vault (with a solo-match floor).
- Format-perfect write-back into a copy of the original (merged-anchor- and
  style-safe; falls back rather than dropping embedded media; never overwrites a
  non-empty cell or the original).
- Flywheel: `qresponder approve` grows the Answer Library with versioned,
  de-duplicated approved entries.

**Knowledge architecture + launch readiness (Phase 3)**
- Cross-source conflict detection: flags contradictory answers (cheap heuristics
  + optional conservative LLM-judge) against the Library and other answers;
  never auto-resolves; never flags the approved Tier-1 answer.
- Shipped golden `eval.yaml`, demo script + Makefile, CI workflow, CONTRIBUTING,
  good-first-issues.

[0.1.0]: https://github.com/scorpionus007/QResponder/releases/tag/v0.1.0
