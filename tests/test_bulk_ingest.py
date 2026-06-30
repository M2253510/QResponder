"""Bulk any-format ingestion tests (Phase 8 C). Offline."""

import io
import json
import zipfile
from pathlib import Path

from qresponder.core.bulk_ingest import ingest_files
from qresponder.core.qa_import import extract_pairs, import_qa
from qresponder.kb.library import AnswerLibrary
from qresponder.kb.tags import load_tag_sidecar

FIX = Path(__file__).parent / "fixtures"

_KB_EXTS = {".txt", ".md", ".csv", ".pdf", ".docx", ".xlsx", ".html"}


def _zip_of(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_mixed_batch_ingests_supported_rejects_unsupported_and_expands_zip(tmp_path):
    xlsx = (FIX / "sample.xlsx").read_bytes()
    docx = (FIX / "sample.docx").read_bytes()
    items = [
        ("policy.md", b"Tags: soc2\n\nWe encrypt at rest."),
        ("controls.csv", b"control,status\nMFA,enabled\n"),
        ("matrix.xlsx", xlsx),
        ("plan.docx", docx),
        ("bundle.zip", _zip_of({"extra.md": b"More policy.", "skip.exe": b"MZ"})),
        ("malware.exe", b"MZ"),
    ]
    res = ingest_files(items, tmp_path / "kb", _KB_EXTS, tags=["soc2"])
    accepted = set(res["accepted"])
    assert {"policy.md", "controls.csv", "matrix.xlsx", "plan.docx", "extra.md"} <= accepted
    # The .exe (standalone and inside the zip) is rejected with a reason.
    rejected_names = {r["name"] for r in res["rejected"]}
    assert "malware.exe" in rejected_names and "skip.exe" in rejected_names
    assert all(r["reason"] for r in res["rejected"])
    # Provenance + tags recorded.
    assert load_tag_sidecar(tmp_path / "kb")["policy.md"] == ["soc2"]
    via = {f["name"]: f["via"] for f in res["files"]}
    assert via["extra.md"].startswith("zip:")
    assert via["policy.md"] == "upload"
    # Files are readable by the KB loader (any-format reading).
    from qresponder.kb.in_context import InContextKB
    ctx = InContextKB.load(tmp_path / "kb").assemble_context()
    assert "encrypt at rest" in ctx.lower()


def test_filename_traversal_rejected(tmp_path):
    res = ingest_files([("../../evil.md", b"x"), (".hidden", b"y")], tmp_path / "kb", _KB_EXTS)
    # Sanitized to basename 'evil.md' (accepted) — never escapes the dir;
    # the dotfile is rejected.
    assert (tmp_path / "kb" / "evil.md").exists()
    assert not (tmp_path / "evil.md").exists()
    assert any(r["name"] == ".hidden" for r in res["rejected"])


def test_qa_import_csv_json_xlsx_to_library(tmp_path):
    qa = tmp_path / "qa.yaml"
    csv_bytes = b"question,answer\nDo you encrypt at rest?,Yes AES-256\nDo you do MFA?,Yes\n"
    json_bytes = json.dumps([{"question": "Backups?", "answer": "Daily."}]).encode()
    res = import_qa([("pairs.csv", csv_bytes), ("more.json", json_bytes)], qa, approved_by="alice", tags=["soc2"])
    assert res["imported"] == 3
    lib = AnswerLibrary.load(qa)
    assert len(lib.entries) == 3
    assert all(e.approved_by == "alice" for e in lib.entries)


def test_qa_import_dedups_via_approve_one(tmp_path):
    qa = tmp_path / "qa.yaml"
    csv_bytes = b"question,answer\nDo you encrypt at rest?,Yes.\n"
    import_qa([("a.csv", csv_bytes)], qa)
    # Same question again -> approve_one dedups + version bumps, not a duplicate.
    import_qa([("b.csv", b"question,answer\nDo you encrypt at rest?,Yes AES-256.\n")], qa)
    lib = AnswerLibrary.load(qa)
    assert len(lib.entries) == 1
    assert lib.entries[0].version == 2


def test_extract_pairs_unsupported_raises():
    import pytest

    with pytest.raises(ValueError):
        extract_pairs("notes.pdf", b"%PDF")
