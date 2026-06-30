"""S3/SH1: the package must import with retrieval-only deps absent, so the default
(non-retrieval) Docker image and the in-context path work without
sentence-transformers / rank-bm25 / numpy / torch. Those are imported lazily,
only when retrieval is actually used.

The subprocess inherits the parent's import path (so it works in a bare source
checkout where qresponder is only on PYTHONPATH, not pip-installed); if the
package still can't be found, the test skips with a clear message rather than
false-failing for the wrong reason.
"""

import os
import subprocess
import sys
import textwrap

import pytest


def test_no_top_level_retrieval_deps():
    code = textwrap.dedent(
        """
        import importlib.util, sys
        if importlib.util.find_spec("qresponder") is None:
            print("NO_QRESPONDER")
            sys.exit(0)
        # Block heavy/retrieval-only deps: importing any of these now raises.
        for m in ("sentence_transformers", "rank_bm25", "numpy", "torch"):
            sys.modules[m] = None
        import importlib
        for mod in (
            "qresponder.cli",
            "qresponder.core.orchestrate",
            "qresponder.core.pipeline",
            "qresponder.core.confidence",
            "qresponder.core.faithfulness",
            "qresponder.core.interpretations",
            "qresponder.core.attachments",
            "qresponder.core.conflicts",
            "qresponder.core.flywheel",
            "qresponder.core.workspace",
            "qresponder.core.safety",
            "qresponder.core.batch",
            "qresponder.core.csvio",
            "qresponder.core.normalize",
            "qresponder.core.decompose",
            "qresponder.core.history",
            "qresponder.core.presets",
            "qresponder.core.kb_health",
            "qresponder.core.typeshape",
            "qresponder.core.bulk_ingest",
            "qresponder.core.qa_import",
            "qresponder.core.stats",
            "qresponder.connectors.base",
            "qresponder.connectors.folder",
            "qresponder.connectors.website",
            "qresponder.connectors.gdrive",
            "qresponder.kb.retrieval",
            "qresponder.kb.evidence",
            "qresponder.llm.embeddings",
            "qresponder.llm.reranker",
            "qresponder.llm.providers",
            "qresponder.llm.models",
            "qresponder.eval.runner",
            "qresponder.output.writeback",
            "qresponder.output.writer",
            "qresponder.output.audit",
        ):
            importlib.import_module(mod)
        print("IMPORT_OK")
        """
    )
    # Give the child the same import path the parent has (covers bare checkouts
    # where pytest puts src/ on sys.path without a pip install).
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)

    if "NO_QRESPONDER" in r.stdout:
        pytest.skip("qresponder not importable in subprocess; cannot run import-guard")
    assert "IMPORT_OK" in r.stdout, f"stdout={r.stdout!r} stderr={r.stderr!r}"
