# QRESPONDER

**Self-hostable, bring-your-own-model security-questionnaire automation.**

QRESPONDER drafts grounded, cited answers to vendor security questionnaires
(SIG, CAIQ, VSAQ, custom Excel/Word/PDF) from *your own* knowledge base — and
routes everything uncertain to a human. It runs entirely on your infrastructure
with any model: a local Llama/Qwen via Ollama/vLLM, or a cloud API. A security
tool that demands you upload your security posture to someone's cloud is a
contradiction — so QRESPONDER doesn't.

> **Status:** Phase 0 (core loop) **+ Phase 1 (accuracy hardening)**. Ingest →
> AI-extract questions → answer grounded from KB (+ approved Q&A) → cite +
> faithfulness-verify + confidence + NEEDS_REVIEW → write output + review report.
> Phase 1 adds hybrid retrieval (BM25 + dense + RRF) → cross-encoder rerank,
> faithfulness/citation verification, and an eval harness. Two-adapter BYOM,
> `doctor` preflight, CLI, Docker, mock-tested. Phase 2 (ambiguity, attachments,
> format-perfect write-back, flywheel) follows.

## Why it's honest by construction

- **Grounded:** answers come *only* from supplied KB context. Unsupported →
  `NEEDS_REVIEW` with `missing_info`. It never fabricates certifications,
  controls, audit results, or compliance status.
- **Cited:** every `ANSWERED` result carries at least one citation (source + snippet).
- **Human-gated:** output is a *draft*. There is no submit step.
- **Local-first privacy:** the fully-local path makes zero external network calls.
  No telemetry, ever. KB contents and keys are never logged.

## 60-second quickstart

```bash
pip install -e ".[anthropic]"      # or ".[openai]" for the OpenAI-compatible path
cp .env.example .env               # then edit .env
qresponder doctor                  # verify your model setup
qresponder answer \
  --questionnaire tests/fixtures/sample.xlsx \
  --kb tests/fixtures/kb \
  --qa qa.example.yaml \
  --out ./out
```

Outputs land in `./out`: `answered.xlsx`, `results.json`, and a human-first
`review.md` (NEEDS_REVIEW + LOW-confidence items first, grouped by reason).

## The two connection paths

Local vs cloud is just a different base URL. Edit `.env`:

**Cloud — Anthropic**
```
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-4-8
```

**Local — Ollama (fully offline)**
```
LLM_PROVIDER=openai_compat
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=llama3.1
```

The same `OpenAICompatProvider` also covers vLLM, LM Studio, OpenAI,
OpenRouter, Together, Groq, and Azure OpenAI — one class, three config values.

## `qresponder doctor`

Run it before anything else. It checks: endpoint reachable → model exists →
tiny completion succeeds → tiny JSON parse succeeds → (Phase 1) embeddings +
reranker load. It prints ✅ or a precise, actionable error.

## Retrieval mode (Phase 1)

For larger knowledge bases, switch from dumping the whole KB in-context to
hybrid retrieval — the single biggest quality jump over naive RAG:

```
qresponder answer -q f.xlsx --kb ./kb --mode retrieval        # or KB_MODE=retrieval in .env
```

It runs **BM25 (sparse) + dense embeddings in parallel**, fuses with
**Reciprocal Rank Fusion (k=60)**, retrieves the top 20, **reranks with a local
cross-encoder**, and passes the top 5 to the model — per question. BM25 nails
exact control names/acronyms ("MFA", "ISO 27001") that embeddings miss; dense
nails paraphrase. Install the local stack and note the first-run model download:

```
pip install -e ".[retrieval]"     # rank-bm25 + sentence-transformers (local, offline after download)
```

Models are configurable (`EMBEDDING_MODEL`, `RERANKER_MODEL`; `ms-marco-MiniLM-L-6-v2`
is a good CPU/zero-cost reranker). The local path makes **zero external network
calls** once models are cached.

## Faithfulness verification (Phase 1)

Every *generated* `ANSWERED` result is checked that each factual claim is
actually entailed by its cited snippet — not just topically related (the
"grounded-but-wrong" gap). Failures are downgraded to `NEEDS_REVIEW` /
`faithfulness_fail`. Tier-1 approved-library reuse is exempt (it's grounded by
human approval). Toggle with `VERIFY_FAITHFULNESS=true|false`. Confidence is
explainable, never a fake percentage:

- **HIGH** — Tier-1 approved-library reuse, *or* generated with faithfulness
  passed **and** a strong rerank score.
- **MEDIUM** — generated, answered, weaker/no retrieval signal.
- **LOW / NEEDS_REVIEW** — faithfulness failed, unsupported, ambiguous, weak
  retrieval, or parse error.

## Evaluate your model (Phase 1)

Turn "is my local Llama good enough?" into numbers:

```
qresponder eval --set eval.yaml --kb ./kb --qa qa.yaml --mode retrieval
```

It runs a golden set (`eval.example.yaml` for the format) through the real
answer path and reports **Recall@K** (was the expected source retrieved),
**faithfulness rate**, **answer correctness** (LLM-judge on key-fact coverage),
and **coverage** (% auto-answered vs % flagged, by reason). The correctness
judge should be calibrated against a small human-graded baseline — judges
hallucinate too.

## Honest accuracy stance

Best results come from a frontier API model or a large local model. Small local
models (7–8B) work too, but **flag more for review** — which is correct
behavior, not failure. The grounding + (Phase 1) hybrid-retrieval + reranker +
approved-library + faithfulness-check architecture is precisely what makes a
weaker model **degrade gracefully** (more review) instead of **failing
dangerously** (confident fabrication). "Connect any model" and "maximally
accurate" are not identical; the architecture closes the gap. Use
`qresponder eval` (Phase 1) to turn "is my Llama fine?" into a number.

## CLI

```
qresponder doctor
qresponder answer --questionnaire f.xlsx --kb ./kb [--qa qa.yaml] [--tags hipaa,soc2]
                  [--mode in_context|retrieval] [--out ./out] [--batch-size 12]
qresponder extract --questionnaire f.xlsx        # debug: dump extracted questions
qresponder eval --set eval.yaml                  # Phase 1
qresponder init                                  # scaffold .env / config / qa / eval
```

## Architecture

```
ingest (xlsx/docx/pdf → layout-aware IR)
   └─> extract (LLM call #1: IR → questions, with write-back anchors)
        └─> orchestrate (Tier-1 Answer Library first; else assemble cited KB context)
             └─> answer (LLM call #2, batched: grounded, cited, confidence, status)
                  └─> output (answered file + results.json + review.md)
```

Knowledge is tiered (§5): **Tier 1** human-approved Answer Library (the
authority) → **Tier 2** policies → **Tier 3** evidence vault. Retrieval is
tag-scoped so GDPR questions don't pull SOC 2 evidence.

## Phase roadmap

- **Phase 0** — core loop. ✅
- **Phase 1** — hybrid retrieval (BM25 + dense + RRF) → cross-encoder rerank;
  faithfulness/citation verification; tag-scoping; eval harness. ✅
- **Phase 2** — ambiguity/interpretation surfacing; attachment resolution;
  format-perfect write-back; hardened approved-answer flywheel.
- **Phase 3** — prior-submission mining + cross-source conflict detection; web
  UI; portal autofill; multi-tenant.

## License

Apache-2.0.
