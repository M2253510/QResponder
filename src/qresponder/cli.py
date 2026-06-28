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
    tags: str = typer.Option(None, "--tags", help="Comma-separated tag scope, e.g. hipaa,soc2"),
    mode: str = typer.Option(None, "--mode", help="in_context | retrieval (overrides config)"),
    out: str = typer.Option("./out", "--out", help="Output directory"),
    batch_size: int = typer.Option(None, "--batch-size", help="Questions per answer call"),
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
    result = run_pipeline(questionnaire, kb, qa, cfg, scope_tags=scope)
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
