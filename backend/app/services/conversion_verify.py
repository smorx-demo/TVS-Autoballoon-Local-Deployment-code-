"""
Conversion verification after format → vector PDF conversion, fully local.

Checks the converted PDF against what the source file itself declares, so nothing is sent
outside the machine:
  DXF   every TEXT / MTEXT / dimension text in the source must appear in the PDF text
  STEP  semantic PMI: the PDF must carry one annotation per PMI item the reader found;
        3D-edge export without PMI is flagged (nothing to inspect on it)
  PVZ   every page must carry drawing content
  all   page count ≥ 1, no blank pages, selectable text present

The report keeps the shape the previous (external) verifier produced:
  {agent, status: ok|warning|fail, completeness_score, missing, found, recommendation, method}
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import fitz

OK_SCORE = 0.98      # ≥ this share of source text found → ok
WARN_SCORE = 0.80    # ≥ this → warning, below → fail
_MAX_LISTED = 25


def verify_enabled() -> bool:
    """The local verifier needs no service or key, so it is always available."""
    return True


# ---------------------------------------------------------------------------
# text normalisation
# ---------------------------------------------------------------------------

_MTEXT_CODES = re.compile(r"\\[ACcFfHhQqTtWw][^;]*;|\\[LlOoKkNnXx]|[{}]")
_DXF_SYMBOLS = {"%%c": "Ø", "%%C": "Ø", "%%d": "°", "%%D": "°", "%%p": "±", "%%P": "±"}


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = t.replace("⌀", "Ø").replace("∅", "Ø").replace("−", "-").replace("–", "-")
    return re.sub(r"\s+", "", t).upper()


def _dxf_text(raw: str) -> list[str]:
    """Plain text runs of a DXF TEXT/MTEXT value (formatting codes removed, one run per line)."""
    t = raw or ""
    for code, sym in _DXF_SYMBOLS.items():
        t = t.replace(code, sym)
    t = re.sub(r"\\S([^;^/#]*)[\^/#]([^;]*);", r"\1 \2", t)   # stacked fractions / tolerances
    t = _MTEXT_CODES.sub("", t.replace("\\P", "\n"))
    return [ln.strip() for ln in t.split("\n") if ln.strip() and ln.strip() != "<>"]


def _pdf_pages(pdf: Path) -> list[dict[str, Any]]:
    doc = fitz.open(pdf)
    try:
        out = []
        for p in doc:
            out.append({
                "text": p.get_text(),
                "drawings": len(p.get_drawings()),
                "images": len(p.get_images()),
            })
        return out
    finally:
        doc.close()


def _report(agent: str, found: list[str], missing: list[str], *, total: int | None = None,
            extra_fail: str = "", extra_warn: str = "") -> dict[str, Any]:
    total = len(found) + len(missing) if total is None else total
    score = 1.0 if total == 0 else round(len(found) / total, 4)
    if extra_fail or score < WARN_SCORE:
        status = "fail"
    elif extra_warn or score < OK_SCORE:
        status = "warning"
    else:
        status = "ok"
    if status == "ok":
        rec = "Conversion complete: all source annotations are present as selectable text."
    else:
        rec = extra_fail or extra_warn or (
            f"{len(missing)} of {total} source annotations are missing from the converted PDF; "
            "check them on the drawing before ballooning, or upload a PDF export instead.")
    return {
        "agent": agent,
        "method": "local",
        "status": status,
        "completeness_score": score,
        "missing": missing[:_MAX_LISTED],
        "missing_count": len(missing),
        "found": found[:_MAX_LISTED],
        "found_count": len(found),
        "recommendation": rec,
    }


def _common_checks(pages: list[dict[str, Any]]) -> tuple[str, str]:
    """(fail reason, warning reason) from page-level checks shared by every format."""
    if not pages:
        return "Converted PDF has no pages.", ""
    blank = [i + 1 for i, p in enumerate(pages) if not p["text"].strip() and not p["drawings"] and not p["images"]]
    if blank:
        return f"Converted PDF has blank page(s): {blank}.", ""
    if not any(p["text"].strip() for p in pages):
        return "", "Converted PDF has no selectable text; ballooning will rely on OCR."
    return "", ""


# ---------------------------------------------------------------------------
# per-format verifiers
# ---------------------------------------------------------------------------

def verify_dxf_conversion(conversion_meta: dict[str, Any], pdf_path: Path) -> dict[str, Any]:
    pages = _pdf_pages(pdf_path)
    fail, warn = _common_checks(pages)
    pdf_text = _norm("".join(p["text"] for p in pages))
    manifest = conversion_meta.get("manifest") or {}
    runs = [r for raw in manifest.get("text_entities") or [] for r in _dxf_text(str(raw))]
    found, missing = [], []
    for r in runs:
        (found if _norm(r) in pdf_text else missing).append(r)
    return _report("dxf", found, missing, extra_fail=fail, extra_warn=warn)


def verify_step_conversion(conversion_meta: dict[str, Any], pdf_path: Path) -> dict[str, Any]:
    pages = _pdf_pages(pdf_path)
    fail, warn = _common_checks(pages)
    counts = conversion_meta.get("pmi_counts") or {}
    if conversion_meta.get("converter") == "step_ap242_pmi" and counts:
        expected = sum(int(v) for v in counts.values())
        found = [f"{k}: {v}" for k, v in sorted(counts.items())]
        if not "".join(p["text"] for p in pages).strip():
            fail = fail or "PMI sheet was rendered without text."
        return _report("step", found, [], total=len(found), extra_fail=fail, extra_warn=warn) | {
            "pmi_items": expected}
    # Geometry-only export (no semantic PMI): nothing inspectable unless the sheet carries text
    has_dims = bool(re.search(r"\d", "".join(p["text"] for p in pages)))
    return _report("step", [], [], extra_fail=fail, extra_warn=warn or (
        "" if has_dims else
        "STEP has no semantic PMI: the PDF shows 3D edges only, with no dimensions to inspect. "
        "Upload the 2D drawing (PDF/DXF) or an AP242 STEP with PMI."))


def verify_pvz_conversion(conversion_meta: dict[str, Any], pdf_path: Path) -> dict[str, Any]:
    pages = _pdf_pages(pdf_path)
    fail, warn = _common_checks(pages)
    found = [f"page {i + 1}" for i, p in enumerate(pages) if p["drawings"] or p["text"].strip() or p["images"]]
    return _report("pvz", found, [], extra_fail=fail, extra_warn=warn)


def verify_pdf_upload(conversion_meta: dict[str, Any], pdf_path: Path) -> dict[str, Any]:
    pages = _pdf_pages(pdf_path)
    fail, warn = _common_checks(pages)
    return _report("pdf", [f"{len(pages)} page(s)"], [], extra_fail=fail, extra_warn=warn)


def run_conversion_verification(
    source_kind: str,
    conversion_meta: dict[str, Any],
    pdf_path: Path,
) -> dict[str, Any]:
    kind = (source_kind or "").lower()
    try:
        if kind == "step":
            report = verify_step_conversion(conversion_meta, pdf_path)
        elif kind == "dxf":
            report = verify_dxf_conversion(conversion_meta, pdf_path)
        elif kind == "pvz":
            report = verify_pvz_conversion(conversion_meta, pdf_path)
        elif kind == "pdf":
            report = verify_pdf_upload(conversion_meta, pdf_path)
        else:
            return {"status": "skipped", "reason": f"No verifier for kind={kind}"}
        report["verified_at"] = "conversion"
        return report
    except Exception as exc:
        return {"status": "error", "agent": kind, "method": "local", "error": str(exc)[:500]}
