# Changelog

All notable changes to QRESPONDER. Format based on
[Keep a Changelog](https://keepachangelog.com/); this project uses semantic
versioning.

## [Unreleased]

### Added
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
