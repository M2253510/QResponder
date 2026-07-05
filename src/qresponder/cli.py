"""QRESPONDER command-line interface (§13.3).

Commands: doctor · answer · extract · eval (Phase 1) · init.
Logging is info-level counts only — never KB text or keys (§4.7, §18).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import typer


def _force_utf8() -> None:
    """Make stdout/stderr UTF-8 with a safe fallback so non-ASCII glyphs (✅, —)
    never crash on a legacy Windows code page (cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


_force_utf8()

from .config import load_config
from .kb.tags import parse_tags

app = typer.Typer(
    add_completion=False,
    help="Local-first, bring-your-own-model security-questionnaire automation.",
    no_args_is_help=True,
)

connect_app = typer.Typer(help="Ingest a source into a workspace (explicit; never during answering).")
app.add_typer(connect_app, name="connect")


def _ws_kb_dir(cfg, workspace: str):
    from .core.workspace import WorkspaceError, WorkspaceStore

    try:
        ws = WorkspaceStore(cfg.workspaces_dir).get(workspace)
    except WorkspaceError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)
    return ws


def _report_ingest(res: dict):
    typer.secho(f"Ingested {len(res['accepted'])} file(s):", fg=typer.colors.GREEN)
    for n in res["accepted"]:
        typer.echo(f"  + {n}")
    for r in res.get("rejected", []):
        typer.secho(f"  - {r['name']}: {r['reason']}", fg=typer.colors.YELLOW)


@connect_app.command("folder")
def connect_folder(
    path: str = typer.Argument(..., help="Local/mounted directory of docs"),
    workspace: str = typer.Option(..., "--workspace", help="Workspace id"),
    tags: str = typer.Option(None, "--tags"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """Ingest a local folder of documents into a workspace's KB."""
    from .connectors.base import ingest_connector
    from .connectors.folder import FolderConnector

    cfg = load_config(config_path)
    ws = _ws_kb_dir(cfg, workspace)
    res = ingest_connector(FolderConnector(path, tags=parse_tags(tags)), ws.kb_dir, tags=parse_tags(tags))
    _report_ingest(res)


@connect_app.command("website")
def connect_website(
    url: str = typer.Argument(..., help="Start URL"),
    workspace: str = typer.Option(..., "--workspace", help="Workspace id"),
    depth: int = typer.Option(1, "--depth", help="Crawl depth (same-domain)"),
    max_pages: int = typer.Option(20, "--max-pages"),
    allow_private: bool = typer.Option(False, "--allow-private", help="Disable the SSRF guard (danger)"),
    tags: str = typer.Option(None, "--tags"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """Crawl a website (bounded, same-domain, SSRF-guarded) into a workspace's KB."""
    from .connectors.base import ConnectorError, ingest_connector
    from .connectors.website import WebsiteConnector

    cfg = load_config(config_path)
    ws = _ws_kb_dir(cfg, workspace)
    conn = WebsiteConnector(url, depth=depth, max_pages=max_pages, allow_private=allow_private,
                            tags=parse_tags(tags))
    try:
        res = ingest_connector(conn, ws.kb_dir, tags=parse_tags(tags))
    except ConnectorError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)
    _report_ingest(res)


@connect_app.command("gdrive")
def connect_gdrive(
    folder_id: str = typer.Argument(..., help="Google Drive folder id"),
    workspace: str = typer.Option(..., "--workspace"),
    tags: str = typer.Option(None, "--tags"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """Ingest a Google Drive folder (optional; needs the 'connectors' extra + OAuth)."""
    from .connectors.base import ConnectorError, ingest_connector
    from .connectors.gdrive import GoogleDriveConnector

    cfg = load_config(config_path)
    ws = _ws_kb_dir(cfg, workspace)
    try:
        res = ingest_connector(GoogleDriveConnector(folder_id, tags=parse_tags(tags)), ws.kb_dir,
                               tags=parse_tags(tags))
    except ConnectorError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)
    _report_ingest(res)


def _connect_saas(cfg, workspace: str, conn, tags: str):
    """Shared run+report for the credentialed SaaS connectors (Phase 12)."""
    from .connectors.base import ConnectorError, ingest_connector

    ws = _ws_kb_dir(cfg, workspace)
    try:
        res = ingest_connector(conn, ws.kb_dir, tags=parse_tags(tags))
    except ConnectorError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)
    _report_ingest(res)


@connect_app.command("confluence")
def connect_confluence(
    space: str = typer.Argument(..., help="Confluence space key"),
    workspace: str = typer.Option(..., "--workspace"),
    tags: str = typer.Option(None, "--tags"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """Ingest a Confluence space (token/base_url from server config; explicit only)."""
    from .connectors.confluence import ConfluenceConnector

    cfg = load_config(config_path)
    _connect_saas(cfg, workspace, ConfluenceConnector(
        space, token=cfg.confluence_token, base_url=cfg.confluence_base_url,
        email=cfg.confluence_email, tags=parse_tags(tags)), tags)


@connect_app.command("notion")
def connect_notion(
    database: str = typer.Argument(..., help="Notion database id"),
    workspace: str = typer.Option(..., "--workspace"),
    tags: str = typer.Option(None, "--tags"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """Ingest a Notion database (token from server config; explicit only)."""
    from .connectors.notion import NotionConnector

    cfg = load_config(config_path)
    _connect_saas(cfg, workspace, NotionConnector(database, token=cfg.notion_token, tags=parse_tags(tags)), tags)


@connect_app.command("sharepoint")
def connect_sharepoint(
    site: str = typer.Argument(..., help="SharePoint site id"),
    workspace: str = typer.Option(..., "--workspace"),
    tags: str = typer.Option(None, "--tags"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """Ingest a SharePoint site's document library (MS Graph token; explicit only)."""
    from .connectors.sharepoint import SharePointConnector

    cfg = load_config(config_path)
    _connect_saas(cfg, workspace, SharePointConnector(site, token=cfg.microsoft_token, tags=parse_tags(tags)), tags)


@connect_app.command("onedrive")
def connect_onedrive(
    folder: str = typer.Argument("", help="OneDrive folder path (blank = root)"),
    workspace: str = typer.Option(..., "--workspace"),
    tags: str = typer.Option(None, "--tags"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """Ingest a OneDrive folder (MS Graph token; explicit only)."""
    from .connectors.onedrive import OneDriveConnector

    cfg = load_config(config_path)
    _connect_saas(cfg, workspace, OneDriveConnector(folder, token=cfg.microsoft_token, tags=parse_tags(tags)), tags)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _resolve_provider(cfg, provider, model):
    """Build the chosen provider explicitly (no silent mock fallback). Returns
    None when no --provider/--model is given (run_pipeline uses config default)."""
    if not (provider or model):
        return None
    from .llm.base import ProviderError
    from .llm.providers import canonical, is_configured, make_provider_for

    p = canonical(provider or cfg.llm_provider)
    if not is_configured(cfg, p):
        typer.secho(f"Provider '{p}' is not configured — set its key in .env.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    try:
        return make_provider_for(cfg, p, model)
    except ProviderError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)


@app.command()
def doctor(
    config_path: str = typer.Option("config.yaml", "--config", help="Path to config.yaml"),
    retrieval: bool = typer.Option(
        False, "--retrieval", help="Also verify embeddings + reranker load"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Verify your model setup (both paths)."""
    _setup_logging(verbose)
    from .llm.doctor import run_doctor

    cfg = load_config(config_path)
    typer.echo(f"QRESPONDER doctor — provider: {cfg.llm_provider}")
    checks = run_doctor(cfg, check_retrieval=True if retrieval else None)
    all_ok = True
    for c in checks:
        mark = "✅" if c.ok else "❌"
        typer.echo(f"  {mark} {c.name}: {c.detail}")
        all_ok = all_ok and c.ok
    if all_ok:
        typer.secho("\nAll checks passed.", fg=typer.colors.GREEN)
    else:
        typer.secho("\nSome checks failed — see details above.", fg=typer.colors.RED)
        raise typer.Exit(code=1)


@app.command()
def answer(
    questionnaire: str = typer.Option(None, "--questionnaire", "-q", help="xlsx/docx/pdf file"),
    batch: list[str] = typer.Option(None, "--batch", help="Dir or glob(s) of questionnaires (repeatable)"),
    kb: str = typer.Option(None, "--kb", help="Knowledge base directory (policies/evidence)"),
    qa: str = typer.Option(None, "--qa", help="Answer Library YAML (Tier 1)"),
    evidence: str = typer.Option(None, "--evidence", help="Evidence vault dir for attachment resolution"),
    tags: str = typer.Option(None, "--tags", help="Comma-separated tag scope, e.g. hipaa,soc2"),
    mode: str = typer.Option(None, "--mode", help="in_context | retrieval (overrides config)"),
    out: str = typer.Option("./out", "--out", help="Output directory"),
    batch_size: int = typer.Option(None, "--batch-size", help="Questions per answer call"),
    writeback: bool = typer.Option(
        False, "--writeback", help="Also fill answers into a copy of the original file"
    ),
    preset: str = typer.Option(None, "--preset", help="Answer-style preset: concise|detailed|formal|<custom>"),
    provider: str = typer.Option(None, "--provider", help="openai|gemini|deepseek|anthropic|local (overrides config)"),
    model: str = typer.Option(None, "--model", help="Exact model id (see `qresponder models`)"),
    include_source: list[str] = typer.Option(None, "--include-source", help="Only use these sources (name or tag; repeatable)"),
    exclude_source: list[str] = typer.Option(None, "--exclude-source", help="Exclude these sources (name or tag; repeatable)"),
    review_markers: bool = typer.Option(
        True, "--review-markers/--no-review-markers", help="Mark NEEDS_REVIEW cells visibly (default on)"
    ),
    config_path: str = typer.Option("config.yaml", "--config"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Draft grounded, cited answers for a questionnaire."""
    _setup_logging(verbose)
    from .core.pipeline import run_pipeline
    from .output.writer import write_all

    cfg = load_config(config_path)
    if mode:
        cfg.kb_mode = mode
    if batch_size:
        cfg.batch_size = batch_size
    if cfg.kb_mode == "retrieval":
        typer.secho(
            "Retrieval mode: hybrid BM25+dense + RRF + cross-encoder rerank "
            "(local models download on first run).",
            fg=typer.colors.YELLOW,
        )

    scope = parse_tags(tags)

    # --- Batch mode (Part D): process many files in isolation, then zip. ---
    if batch:
        from .core.batch import resolve_questionnaires, run_batch, zip_batch

        files = resolve_questionnaires(list(batch))
        if not files:
            typer.secho("No supported questionnaires matched --batch.", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        summary = run_batch(files, kb, qa, cfg, out, scope_tags=scope, evidence_dir=evidence)
        zpath = zip_batch(out)
        typer.secho(
            f"\nBatch: {summary['succeeded']}/{summary['n_files']} succeeded, "
            f"{summary['failed']} failed.", fg=typer.colors.GREEN,
        )
        for f in summary["files"]:
            if f["ok"]:
                s = f["summary"]
                typer.echo(f"  {f['file']}: {s['answered']} answered, {s['flagged']} flagged")
            else:
                typer.secho(f"  {f['file']}: FAILED — {f['error']}", fg=typer.colors.YELLOW)
        typer.echo(f"  summary: {Path(out) / 'batch_summary.json'}")
        typer.echo(f"  zip:     {zpath}")
        return

    if not questionnaire:
        typer.secho("Provide --questionnaire <file> or --batch <dir|glob>.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    from .core.presets import resolve as resolve_preset

    style = resolve_preset(preset)
    if preset and style is None:
        typer.secho(f"Unknown preset '{preset}'; using default style.", fg=typer.colors.YELLOW)

    # Build the selected provider explicitly — no silent mock fallback.
    provider_obj = _resolve_provider(cfg, provider, model)

    result = run_pipeline(questionnaire, kb, qa, cfg, scope_tags=scope, evidence_dir=evidence,
                          preset=preset if style else None, style=style, provider=provider_obj,
                          include_sources=include_source, exclude_sources=exclude_source)
    # Always emit the safe Phase-0/1 artifacts.
    paths = write_all(result, out, review_markers=review_markers)

    from .models import Status

    answered = sum(1 for r in result.results if r.status == Status.ANSWERED)
    flagged = len(result.results) - answered
    typer.echo("")
    typer.secho(
        f"{len(result.results)} questions · {answered} answered · {flagged} need review",
        fg=typer.colors.GREEN,
    )
    for name, p in paths.items():
        typer.echo(f"  {name}: {p}")

    # Optional format-perfect write-back into a COPY of the original (C3). Auto
    # when answer anchors are present for an xlsx/docx source.
    from .output.writeback import has_answer_anchors, write_back

    src_ext = Path(questionnaire).suffix.lower()
    do_writeback = writeback or (src_ext in {".xlsx", ".xlsm", ".docx"} and has_answer_anchors(result))
    if do_writeback:
        wb = write_back(result, questionnaire, out, review_markers=review_markers)
        if wb.get("written"):
            typer.echo(f"  writeback: {wb['written']} ({wb.get('cells', 0)} cell(s))")
        elif wb.get("fallback"):
            typer.secho(
                f"  writeback skipped ({wb.get('reason')}); use the answered.* file above.",
                fg=typer.colors.YELLOW,
            )

    typer.echo("\nReview the draft (review.md) before using. Nothing was submitted.")


@app.command()
def ask(
    question: str = typer.Argument(..., help="A natural-language question"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace id (uses its kb/qa/evidence)"),
    kb: str = typer.Option(None, "--kb", help="Knowledge base directory (if no --workspace)"),
    qa: str = typer.Option(None, "--qa", help="Answer Library YAML"),
    evidence: str = typer.Option(None, "--evidence", help="Evidence vault dir"),
    tags: str = typer.Option(None, "--tags", help="Tag scope"),
    mode: str = typer.Option(None, "--mode", help="in_context | retrieval"),
    preset: str = typer.Option(None, "--preset"),
    provider: str = typer.Option(None, "--provider"),
    model: str = typer.Option(None, "--model"),
    include_source: list[str] = typer.Option(None, "--include-source"),
    exclude_source: list[str] = typer.Option(None, "--exclude-source"),
    as_json: bool = typer.Option(False, "--json", help="Emit the full AnswerResult as JSON"),
    config_path: str = typer.Option("config.yaml", "--config"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Ask one question — the same grounded path as a questionnaire, on one item."""
    _setup_logging(verbose)
    from .core.pipeline import run_ask
    from .core.presets import resolve as resolve_preset

    cfg = load_config(config_path)
    if mode:
        cfg.kb_mode = mode
    kb_dir, qa_path, ev_dir = kb, qa, evidence
    if workspace:
        from .core.workspace import WorkspaceError, WorkspaceStore

        try:
            ws = WorkspaceStore(cfg.workspaces_dir).get(workspace)
        except WorkspaceError as exc:
            typer.secho(str(exc), fg=typer.colors.RED)
            raise typer.Exit(code=1)
        kb_dir, qa_path, ev_dir = str(ws.kb_dir), str(ws.qa_path), str(ws.evidence_dir)

    provider_obj = _resolve_provider(cfg, provider, model)
    style = resolve_preset(preset)
    r = run_ask(question, kb_dir, qa_path, cfg, scope_tags=parse_tags(tags),
                provider=provider_obj, evidence_dir=ev_dir,
                preset=preset if style else None, style=style,
                include_sources=include_source, exclude_sources=exclude_source)

    if as_json:
        typer.echo(r.model_dump_json(indent=2))
        return
    from .models import Status

    if r.status == Status.ANSWERED:
        typer.secho(f"[{r.confidence.value.upper()}] {r.answer}", fg=typer.colors.GREEN)
        for c in r.citations:
            snip = c.snippet if len(c.snippet) <= 160 else c.snippet[:157] + "..."
            typer.echo(f"  cite [{c.source}]: {snip}")
    else:
        typer.secho(f"[NEEDS REVIEW] {r.missing_info or 'Not supported by the knowledge base.'}",
                    fg=typer.colors.YELLOW)


@app.command()
def extract(
    questionnaire: str = typer.Option(..., "--questionnaire", "-q"),
    config_path: str = typer.Option("config.yaml", "--config"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Debug: extract and print the question list as JSON."""
    _setup_logging(verbose)
    from .core.extract import extract_questions
    from .ingest.base import load_document
    from .llm.base import make_provider

    cfg = load_config(config_path)
    doc = load_document(questionnaire)
    questions = extract_questions(doc, make_provider(cfg))
    typer.echo(json.dumps([q.model_dump() for q in questions], indent=2))


@app.command()
def eval(  # noqa: A001 - intentional command name
    set_path: str = typer.Option("eval.yaml", "--set", help="Golden eval YAML"),
    kb: str = typer.Option(None, "--kb", help="Knowledge base directory"),
    qa: str = typer.Option(None, "--qa", help="Answer Library YAML (Tier 1)"),
    mode: str = typer.Option(None, "--mode", help="in_context | retrieval (overrides config)"),
    config_path: str = typer.Option("config.yaml", "--config"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Measure your model on a golden set: Recall@K, faithfulness, correctness, coverage."""
    _setup_logging(verbose)
    from .eval.runner import format_report, run_eval

    cfg = load_config(config_path)
    if mode:
        cfg.kb_mode = mode
    report = run_eval(set_path, kb, qa, cfg)
    typer.echo(format_report(report))


@app.command()
def approve(
    results: str = typer.Option(..., "--results", help="Reviewed results.json"),
    qa: str = typer.Option(..., "--qa", help="Answer Library YAML to grow (created if missing)"),
    by: str = typer.Option(None, "--by", help="Approver name recorded on entries"),
    tags: str = typer.Option(None, "--tags", help="Comma-separated tags for approved entries"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Flywheel: approve reviewed answers into the Answer Library (versioned, de-duped)."""
    _setup_logging(verbose)
    from .core.flywheel import approve as approve_results

    stats = approve_results(results, qa, approved_by=by, extra_tags=parse_tags(tags))
    typer.secho(
        f"Approved {stats['added']} new + {stats['updated']} updated entr(y/ies); "
        f"library now {stats['total']} total.",
        fg=typer.colors.GREEN,
    )


@app.command()
def audit(
    run: str = typer.Option(..., "--run", help="A run output dir containing results.json"),
    zip_pack: bool = typer.Option(False, "--zip", help="Bundle all artifacts into evidence_pack.zip"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Export the audit / evidence pack (audit.json + audit.md) for a run."""
    _setup_logging(verbose)
    from .models import QuestionnaireResult
    from .output.audit import bundle_zip, write_audit

    rp = Path(run) / "results.json"
    if not rp.exists():
        typer.secho(f"No results.json in {run}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    result = QuestionnaireResult.model_validate_json(rp.read_text(encoding="utf-8"))
    paths = write_audit(result, run)
    typer.secho("Evidence pack written:", fg=typer.colors.GREEN)
    typer.echo(f"  audit.json: {paths['json']}")
    typer.echo(f"  audit.md:   {paths['md']}")
    if zip_pack:
        typer.echo(f"  zip:        {bundle_zip(run)}")


@app.command()
def models(
    provider: str = typer.Option(None, "--provider", help="openai|gemini|deepseek|anthropic|local (default: all configured)"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """List live models from each configured provider (server-side, key-gated)."""
    from .llm.models import list_models
    from .llm.providers import PROVIDER_SPECS, is_configured

    cfg = load_config(config_path)
    names = [provider] if provider else list(PROVIDER_SPECS)
    for name in names:
        spec = PROVIDER_SPECS.get(name)
        if spec is None:
            typer.secho(f"{name}: unknown provider", fg=typer.colors.RED)
            continue
        if not is_configured(cfg, name):
            typer.secho(f"{spec['label']}: not configured", fg=typer.colors.YELLOW)
            continue
        ml = list_models(name, cfg)
        if ml.reason:
            typer.secho(f"{spec['label']}: {ml.reason}", fg=typer.colors.RED)
        else:
            typer.secho(f"{spec['label']} ({len(ml.models)} models):", fg=typer.colors.GREEN)
            for m in ml.models:
                typer.echo(f"  {m.id}")


@app.command()
def stats(
    workspace: str = typer.Option(..., "--workspace", help="Workspace id"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """Local completion / auto-answer analytics from a workspace's run outputs."""
    from .core.stats import workspace_stats

    cfg = load_config(config_path)
    ws = _ws_kb_dir(cfg, workspace)
    s = workspace_stats(ws.runs_dir, cfg.stats_minutes_per_question)
    typer.secho(f"Workspace '{workspace}' — {s['n_runs']} run(s), {s['total_questions']} question(s)",
                fg=typer.colors.GREEN)
    typer.echo(f"  completion rate : {s['completion_rate'] * 100:.1f}%  "
               f"({s['answered']} answered / {s['flagged']} flagged)")
    typer.echo(f"  auto-answer (HIGH+MED): {s['auto_answer_rate_high_med'] * 100:.1f}%  "
               f"(high={s['auto_answer_by_confidence']['high']} med={s['auto_answer_by_confidence']['medium']})")
    if s["flagged_by_reason"]:
        typer.echo("  flagged by reason:")
        for reason, c in s["flagged_by_reason"].items():
            typer.echo(f"    - {reason}: {c}")
    typer.echo(f"  time saved: ~{s['time_saved_minutes']} min  ({s['time_saved_note']})")


@app.command(name="kb-insights")
def kb_insights_cmd(
    workspace: str = typer.Option(..., "--workspace", help="Workspace id"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """What your knowledge base can't yet answer — gaps from this workspace's runs."""
    from .core.insights import kb_insights

    cfg = load_config(config_path)
    ws = _ws_kb_dir(cfg, workspace)
    r = kb_insights(ws.runs_dir)
    typer.secho(f"KB Insights — {r['n_runs']} run(s), {r['total_questions']} question(s), "
                f"{r['abstain_rate'] * 100:.1f}% abstained", fg=typer.colors.GREEN)
    if r["gaps_by_reason"]:
        typer.echo("  Gaps by reason (what the KB couldn't answer):")
        for g in r["gaps_by_reason"]:
            typer.echo(f"    - {g['reason']}: {g['count']}  e.g. {g['examples'][0] if g['examples'] else ''}")
    if r["gap_themes"]:
        typer.echo("  Top gap themes: " + ", ".join(f"{t['theme']} ({t['count']})" for t in r["gap_themes"]))
    if r["reused_tier1"]:
        typer.echo(f"  Most-reused approved answers: {len(r['reused_tier1'])} (add more where gaps cluster)")
    typer.secho(f"  → {r['note']}", fg=typer.colors.YELLOW)


@app.command(name="kb-check")
def kb_check_cmd(
    qa: str = typer.Option("qa.yaml", "--qa", help="Answer Library YAML to scan"),
    merge_duplicates: bool = typer.Option(False, "--merge-duplicates", help="Version-bump duplicate canonicals (never deletes)"),
    config_path: str = typer.Option("config.yaml", "--config"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Scan the Answer Library for internal contradictions + near-duplicates (read-only)."""
    _setup_logging(verbose)
    from .core.kb_health import check_library
    from .core.kb_health import merge_duplicates as do_merge

    cfg = load_config(config_path)
    report = check_library(qa, config=cfg)
    typer.secho(f"Library health — {report['n_entries']} entries", fg=typer.colors.GREEN)
    if report["clean"]:
        typer.secho("  ✓ No contradictions or duplicates found.", fg=typer.colors.GREEN)
    for c in report["contradictions"]:
        typer.secho(f"  ✗ CONTRADICTION (sim {c['similarity']}):", fg=typer.colors.RED)
        typer.echo(f"      [{c['a_index']}] {c['question_a']} → {c['answer_a']}")
        typer.echo(f"      [{c['b_index']}] {c['question_b']} → {c['answer_b']}")
    for d in report["duplicates"]:
        typer.secho(f"  ⚠ DUPLICATE (sim {d['similarity']}): "
                    f"[{d['a_index']}] {d['question_a']}  ≈  [{d['b_index']}] {d['question_b']}",
                    fg=typer.colors.YELLOW)
    if merge_duplicates:
        m = do_merge(qa, config=cfg)
        typer.secho(f"  merged {m['merged']} duplicate canonical(s) (version-bumped, none deleted).",
                    fg=typer.colors.GREEN)


@app.command(name="export-flagged")
def export_flagged_cmd(
    run: str = typer.Option(..., "--run", help="Run dir containing results.json"),
    out: str = typer.Option("flagged.csv", "--out", help="Output CSV path"),
    by_owner: bool = typer.Option(False, "--by-owner", help="Split into per-owner CSVs"),
):
    """Export flagged items to CSV for an SME to fill (category,question,answer,reason)."""
    from .core.csvio import export_flagged
    from .models import QuestionnaireResult

    result = QuestionnaireResult.model_validate_json(
        (Path(run) / "results.json").read_text(encoding="utf-8"))
    paths = export_flagged(result, out, by_owner=by_owner)
    typer.secho(f"Wrote {len(paths)} CSV(s):", fg=typer.colors.GREEN)
    for p in paths:
        typer.echo(f"  {p}")


@app.command(name="import-answers")
def import_answers_cmd(
    csv_path: str = typer.Option(..., "--csv", help="Filled flagged CSV"),
    qa: str = typer.Option(..., "--qa", help="Answer Library to grow"),
    run: str = typer.Option(None, "--run", help="Run dir whose results.json to update"),
    by: str = typer.Option("csv-import", "--by", help="Approver name"),
):
    """Import filled CSV answers: train the library + flip the run's items to ANSWERED."""
    from .core.csvio import import_answers
    from .models import QuestionnaireResult

    result = None
    rp = None
    if run:
        rp = Path(run) / "results.json"
        result = QuestionnaireResult.model_validate_json(rp.read_text(encoding="utf-8"))
    stats = import_answers(csv_path, qa, result=result, approved_by=by)
    if result is not None and rp is not None:
        rp.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    typer.secho(
        f"Imported {stats['imported']} answer(s); re-synced {stats['resynced']} "
        "still-flagged item(s) against the updated library.", fg=typer.colors.GREEN,
    )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (default localhost)"),
    port: int = typer.Option(8000, "--port", help="Port"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """Launch the local web review UI (FastAPI). Defaults to 127.0.0.1."""
    cfg = load_config(config_path)
    try:
        import uvicorn

        from .web.app import create_app
    except ImportError:
        typer.secho(
            'The web UI needs extra deps. Install with: pip install "qresponder[web]"',
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    if host not in ("127.0.0.1", "localhost"):
        typer.secho(
            f"WARNING: binding {host} exposes the UI beyond localhost. It has NO AUTH "
            "and handles your security posture — put auth/a reverse proxy in front first.",
            fg=typer.colors.YELLOW,
        )
    model = cfg.anthropic_model if cfg.llm_provider == "anthropic" else cfg.llm_model
    typer.secho(f"QRESPONDER review UI — provider: {cfg.llm_provider} ({model})", fg=typer.colors.GREEN)
    typer.echo(f"  http://{host}:{port}  (keys stay server-side; nothing leaves this host)")
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="info")


_INIT_FILES = {
    ".env": """LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-opus-4-8
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=llama3.1
KB_MODE=in_context
VERIFY_FAITHFULNESS=true
BATCH_SIZE=12
""",
    "qa.yaml": """- question: "Do you encrypt data at rest?"
  answer: "Yes. All customer data at rest is encrypted using AES-256."
  tags: [soc2, encryption]
  approved_by: security-team
  version: 1
""",
}


@app.command()
def init():
    """Scaffold .env and qa.yaml in the current directory."""
    created = []
    for name, content in _INIT_FILES.items():
        p = Path(name)
        if p.exists():
            typer.echo(f"  skip {name} (exists)")
            continue
        p.write_text(content, encoding="utf-8")
        created.append(name)
        typer.echo(f"  wrote {name}")
    if created:
        typer.secho("Edit .env with your model settings, then run `qresponder doctor`.", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
