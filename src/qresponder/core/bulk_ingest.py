"""Bulk, any-format file ingestion (Phase 8 C).

Validates and sandboxes many uploaded files into a workspace dir at once, expands
ZIPs into their members, rejects unsupported formats per-file with a reason (never
silently mis-ingested), and records tags + provenance via sidecars. Reading of the
content is deferred to the existing KB loaders — this is storage + validation only.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import yaml

from ..kb.tags import load_tag_sidecar, normalize_tags, write_tag_sidecar

PROVENANCE_SIDECAR = ".provenance.yaml"

# Canonical bulk-ingest allow-lists (shared by web uploads + connectors).
KB_INGEST_EXTS = {".txt", ".md", ".markdown", ".rst", ".csv", ".pdf", ".docx",
                  ".xlsx", ".xlsm", ".html", ".htm"}
EVIDENCE_INGEST_EXTS = KB_INGEST_EXTS | {".png", ".jpg", ".jpeg", ".pptx"}


def _safe_name(name: str) -> str | None:
    base = Path(name or "").name.strip()
    if not base or base.startswith(".") or base in {".", ".."}:
        return None
    return base


def _load_provenance(d: Path) -> dict:
    p = d / PROVENANCE_SIDECAR
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    return {}


def _write_provenance(d: Path, prov: dict) -> None:
    (d / PROVENANCE_SIDECAR).write_text(
        yaml.safe_dump(prov, sort_keys=True, allow_unicode=True), encoding="utf-8")


def ingest_files(items, dest_dir, allowed_exts: set[str], tags=None, date: str | None = None) -> dict:
    """items: iterable of (filename, bytes). Returns {accepted: [names], rejected:
    [{name, reason}], files: [...]}. ZIPs are expanded and their members ingested."""
    d = Path(dest_dir)
    d.mkdir(parents=True, exist_ok=True)
    tags = normalize_tags(tags)
    sidecar = load_tag_sidecar(d)
    prov = _load_provenance(d)
    accepted: list[str] = []
    rejected: list[dict] = []

    def _one(name: str, data: bytes, via: str):
        safe = _safe_name(name)
        if safe is None:
            rejected.append({"name": name, "reason": "invalid filename"})
            return
        ext = Path(safe).suffix.lower()
        if ext not in allowed_exts:
            rejected.append({"name": safe, "reason": f"unsupported type '{ext or '(none)'}'"})
            return
        (d / safe).write_bytes(data)
        if tags:
            sidecar[safe] = sorted(set(sidecar.get(safe, [])) | set(tags))
        prov[safe] = {"via": via, **({"added": date} if date else {})}
        accepted.append(safe)

    for name, data in items:
        if Path(name or "").suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    for member in zf.namelist():
                        if member.endswith("/"):
                            continue
                        _one(Path(member).name, zf.read(member), via=f"zip:{Path(name).name}")
            except zipfile.BadZipFile:
                rejected.append({"name": name, "reason": "corrupt zip"})
        else:
            _one(name, data, via="upload")

    if sidecar:
        write_tag_sidecar(d, sidecar)
    _write_provenance(d, prov)

    files = []
    sc = load_tag_sidecar(d)
    for fp in sorted(d.iterdir()):
        if fp.is_file() and not fp.name.startswith("."):
            files.append({"name": fp.name, "tags": sc.get(fp.name, []),
                          "via": prov.get(fp.name, {}).get("via")})
    return {"accepted": accepted, "rejected": rejected, "files": files}
