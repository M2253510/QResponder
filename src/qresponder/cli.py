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


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


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
    questionnaire: str = typer.Option(..., "--questionnaire", "-q", help="xlsx/docx/pdf file"),
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
    result = run_pipeline(questionnaire, kb, qa, cfg, scope_tags=scope, evidence_dir=evidence)
    # Always emit the safe Phase-0/1 artifacts.
    paths = write_all(result, out)

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
        wb = write_back(result, questionnaire, out)
        if wb.get("written"):
            typer.echo(f"  writeback: {wb['written']} ({wb.get('cells', 0)} cell(s))")
        elif wb.get("fallback"):
            typer.secho(
                f"  writeback skipped ({wb.get('reason')}); use the answered.* file above.",
                fg=typer.colors.YELLOW,
            )

    typer.echo("\nReview the draft (review.md) before using. Nothing was submitted.")


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
