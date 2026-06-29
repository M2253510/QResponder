# Changelog

All notable changes to QRESPONDER. Format based on
[Keep a Changelog](https://keepachangelog.com/); this project uses semantic
versioning.

## [Unreleased]

### Added
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

[0.1.0]: https://github.com/scorpionus007/QResponder-GRC/releases/tag/v0.1.0
