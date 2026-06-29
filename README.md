# QRESPONDER

**Self-hostable, bring-your-own-model security-questionnaire automation.**

QRESPONDER drafts grounded, cited answers to vendor security questionnaires
(SIG, CAIQ, VSAQ, custom Excel/Word/PDF) from *your own* knowledge base — and
routes everything uncertain to a human. It runs entirely on your infrastructure
with any model: a local Llama/Qwen via Ollama/vLLM, or a cloud API. A security
tool that demands you upload your security posture to someone's cloud is a
contradiction — so QRESPONDER doesn't.

> **Status:** feature-complete engine (Phases 0–3). Ingest → AI-extract →
> Tier-1 library → retrieval/in-context answer → faithfulness-verify →
> cross-source conflict check → confidence → NEEDS_REVIEW → output + review.
> Phase 1: hybrid retrieval (BM25 + dense + RRF) → cross-encoder rerank,
> faithfulness/citation verification, eval harness. Phase 2: ambiguity surfacing,
> attachment resolution, format-perfect write-back, approve-back flywheel.
> Phase 3: cross-source conflict detection + launch hardening (golden eval, CI,
> demo). Phase 4: a local web review UI (`qresponder serve`) where every
> accept/edit trains the Answer Library. Two-adapter BYOM, `doctor` preflight,
> CLI, Docker, **77 tests, all offline**.

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

## Web review UI

Prefer a UI? Launch the local review app:

```bash
pip install -e ".[web]"     # FastAPI + uvicorn (or build the web Docker image)
qresponder serve            # → http://127.0.0.1:8000
```

> _Record the demo: run `qresponder serve`, do one run, and capture the review
> screen → `docs/review-ui.png`. (Not committed — it's a per-environment asset.)_

**The review loop (this is the product):**
1. **New run** — upload the questionnaire; point at your `kb`, optional
   `evidence` vault, and `qa.yaml` Answer Library; set tags/mode. The active
   provider/model is shown read-only — **your API key never reaches the browser.**
2. **Review queue** — each answer shows a confidence chip (green/amber/red), a
   status/reason badge, and expandable citations. Flagged items get the right
   panel: an **interpretation picker** (ambiguous), an **attachment confirm**
   (evidence files), a **library-candidate** accept/reject, or a **conflict**
   side-by-side to reconcile.
3. **Accept / Edit + Accept** — and here's the flywheel: **every accept trains
   your Answer Library**, and an *edited* answer trains on the edited text, not
   the draft. The item shows an "added to library" badge. Coverage compounds with
   use, independent of the model.
4. **Export** — writes `answered.xlsx` + `results.json` + `review.md` and fills a
   copy of your original template (falling back if it has embedded media).
   Nothing is auto-submitted — the human gate is the whole point.

**Security note:** the UI binds **`127.0.0.1` by default and has no auth** — it
handles your security posture, so don't expose it on a network without putting
authentication / a reverse proxy in front first. `--host`/`--port` override the
bind (you'll get a warning if you bind beyond localhost). Like the rest of the
local path, the page loads **zero external assets** (no CDN, no web fonts) and
sends no telemetry.

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

**Docker:** the default image (`docker build -t qresponder .`) supports
in-context mode. For `--mode retrieval` in-container, build the retrieval image:

```
docker build --build-arg EXTRAS=anthropic,openai,retrieval -t qresponder:retrieval .
```

## Faithfulness verification (Phase 1)

Every *generated* `ANSWERED` result is checked that each factual claim is
actually entailed by its cited snippet — not just topically related (the
"grounded-but-wrong" gap). Failures are downgraded to `NEEDS_REVIEW` /
`faithfulness_fail`. Tier-1 approved-library reuse is exempt (it's grounded by
human approval). Toggle with `VERIFY_FAITHFULNESS=true|false`. Confidence is
explainable, never a fake percentage:

- **HIGH** — Tier-1 approved-library reuse, *or* generated with faithfulness
  passed **and** strong grounding. "Strongly grounded" means a strong
  cross-encoder rerank score (retrieval mode) **or** a high answer↔cited-snippet
  similarity (in-context mode) — so HIGH is reachable in **both** modes, never
  blocked merely by the absence of a reranker.
- **MEDIUM** — generated, answered, weak/uncertain grounding signal.
- **LOW / NEEDS_REVIEW** — faithfulness failed, unsupported, ambiguous, weak
  retrieval, or parse error.

The "strong" cutoff is reranker-dependent (some emit logits, some 0–1 sigmoids):
tune `strong_rerank_score` (retrieval) and `strong_grounding_score` (in-context)
via `qresponder eval`, which reports the score distribution for answered vs
flagged items and a suggested threshold.

## Evaluate your model (Phase 1)

Turn "is my local Llama good enough?" into numbers:

```
qresponder eval --set eval.yaml --kb ./kb --qa qa.yaml --mode retrieval
```

It runs a golden set through the real answer path and reports **Recall@K** (was
the expected source retrieved), **faithfulness rate**, **answer correctness**
(LLM-judge on key-fact coverage), and **coverage** (% auto-answered vs % flagged,
by reason). The correctness judge should be calibrated against a small
human-graded baseline — judges hallucinate too.

A golden `eval.yaml` (20 SIG/CAIQ-style questions) ships in-repo:

```
qresponder eval --set eval.yaml --kb tests/fixtures/kb --qa qa.example.yaml
```

**Reproducible baseline** (deterministic `LLM_PROVIDER=mock`, in-context — this
measures pipeline *structure*, not model quality; real correctness depends on
your model):

```
items: 20 · faithfulness: 100% · auto-answered: 85% · flagged: 15% (3 unsupported)
correctness (key-fact coverage): 47% · suggested grounding threshold: 0.77
```

Swap in your model (Anthropic or a local Ollama) to get *your* accuracy numbers.

## Phase 2 — the differentiators

**Ambiguity review.** ~1/3 of questionnaire items are ambiguous ("describe your
encryption practices" = at rest / in transit / backups / endpoints). QRESPONDER
never silently picks one reading: it drafts a grounded answer per interpretation
and flags the item `NEEDS_REVIEW` / `ambiguous` with the candidates listed in
`review.md` for you to choose.

**Attachment resolution.** Point at an evidence vault and "attach your SOC 2
report" resolves to the actual file:

```
qresponder answer -q f.xlsx --kb ./kb --evidence ./evidence   # or EVIDENCE_DIR
```

A clear winner (above a score floor and beating the runner-up by a margin) is
set as the answer's attachment; otherwise the top candidates are listed for
one-click confirmation. It never attaches a file below the margin without
flagging.

**Format-perfect write-back.** Fill answers into a *copy* of your original
template (`<name>.answered.xlsx`/`.docx`), never the original:

```
qresponder answer -q f.xlsx --kb ./kb --writeback
```

It writes to the top-left cell of merged ranges, sets values only (preserving
shared styles), and — because openpyxl can drop embedded images/charts on save —
**falls back to the separate `answered.xlsx` rather than risk stripping your
diagrams** when the workbook contains media. Only confident (ANSWERED) cells are
filled; review items are left blank. The Phase-0/1 outputs (`answered.*`,
`results.json`, `review.md`) are always produced as the safe artifact.

**The flywheel.** Approve reviewed answers back into the Answer Library so Tier-1
coverage compounds and accuracy climbs with use — independent of the model:

```
# after editing results.json during review:
qresponder approve --results out/results.json --qa qa.yaml --by you --tags soc2
```

Accepted answers become versioned approved entries; re-approving the same
question bumps its version and updates the answer instead of duplicating.

**Cross-source conflict detection.** Contradictory answers are the #1 reason a
questionnaire gets kicked back. QRESPONDER compares each answer against the
Answer Library and the other answers in the run (only for similar questions),
and flags clear contradictions — opposite yes/no, different control values
(TLS/AES versions, retention periods) — as `NEEDS_REVIEW` / `conflict` with the
conflicting source named. It's conservative (no false-positive noise), never
auto-resolves (both sides surfaced), and never flags or overrides an approved
Tier-1 answer.

## Try it in 30 seconds (no API key)

```
make demo          # or: bash scripts/demo.sh
```

Runs the full pipeline on the sample with the deterministic mock provider and
writes `demo_out/` — `answered.xlsx`, `results.json`, `review.md`, and the
filled-in copy `sample.answered.xlsx`.

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
                  [--mode in_context|retrieval] [--evidence ./evidence] [--writeback]
                  [--out ./out] [--batch-size 12]
qresponder extract --questionnaire f.xlsx        # debug: dump extracted questions
qresponder eval --set eval.yaml [--kb ./kb] [--qa qa.yaml] [--mode retrieval]
qresponder approve --results out/results.json --qa qa.yaml [--by NAME] [--tags ...]
qresponder serve [--host 127.0.0.1] [--port 8000]   # local web review UI
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
  format-perfect write-back; approved-answer flywheel. ✅
- **Phase 3** — cross-source conflict detection + launch hardening (golden eval,
  CI, demo, docs). ✅
- **Phase 4** — local web review UI (`qresponder serve`): upload → run → review
  queue with accept/edit/pick-interpretation/confirm-attachment/reconcile-conflict
  → export, with every accept training the Answer Library. ✅

**Deliberately out of scope** (with rationale): Tier-4 prior-submission mining
(the flywheel already promotes accepted answers to higher-authority Tier-1);
portal autofill (brittle anti-automation treadmill); multi-tenant/hosted SaaS
(off-mission for a self-hostable OSS tool).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and
[good first issues](docs/GOOD_FIRST_ISSUES.md). All tests run offline; keep them
that way. Changes: [CHANGELOG.md](CHANGELOG.md).

## License

Apache-2.0.
