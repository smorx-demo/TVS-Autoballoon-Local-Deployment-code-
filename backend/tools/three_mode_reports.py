"""
Generate the TVS extraction report of one part in its three input modes and compare them.

    python -m tools.three_mode_reports OUT_DIR drawing.pdf drawing_all_sheets.dxf part_PMI_AP242.step
           [--no-general-tolerances]

Modes (the same code paths the app runs on upload):
  PDF          the drawing PDF
  all-sheets   the all-sheets DXF, converted to PDF by the app's converter
  STEP         the AP242 STEP, paired with the drawing PDF

Writes OUT_DIR/<stem>_{PDF,all_sheets,STEP}_TVS_extraction_report.xlsx and
OUT_DIR/<stem>_three_mode_comparison.xlsx; prints every difference (0 = consistent).
"""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

import openpyxl

# Columns that must be identical in every mode, row by row (Balloon No. is the key)
SAME = ["Sheet", "Zone", "View", "Feature type", "Symbol", "Nominal", "Upper tolerance", "Lower tolerance",
        "Max limit", "Min limit", "Primary datum", "Secondary datum", "Tertiary datum",
        "Boxed / reference flag", "Critical flag", "Text as shown on drawing",
        "Fit / thread / finish / GD&T details (incl. datums)"]
XY_MM = 3.0
# Summary sections whose rows must agree (STEP adds its own sections, which are not compared)
SUMMARY_SECTIONS = ("Features by type", "Scope coverage", "Balloons by zone", "General tolerances")


def build(pdf: Path, dxf: Path, step: Path, *, general_tolerances: bool = True) -> dict[str, bytes]:
    from app.services.step_report import build_step_report, drawing_pdf, step_report_xlsx
    from app.services.tvs_extraction_report import build_tvs_extraction_from_pdf

    out = {"PDF": build_tvs_extraction_from_pdf(pdf, general_tolerances=general_tolerances)}
    with tempfile.TemporaryDirectory() as td:
        conv = drawing_pdf(dxf, Path(td))
        out["all_sheets"] = build_tvs_extraction_from_pdf(conv, {"files": [{"name": dxf.name}]},
                                                          general_tolerances=general_tolerances)
    rep = build_step_report(step, pdf)
    out["STEP"] = step_report_xlsx(rep, {}, pdf, general_tolerances=general_tolerances)
    return out


def extraction(xlsx: bytes) -> dict[int, dict]:
    ws = openpyxl.load_workbook(io.BytesIO(xlsx), data_only=True)["Extraction"]
    it = ws.iter_rows(values_only=True)
    hdr = list(next(it))
    rows = [dict(zip(hdr, r)) for r in it if any(v is not None for v in r)]
    return {int(r["Balloon No."]): r for r in rows}


def summary(xlsx: bytes) -> dict[tuple[str, str], tuple]:
    """(section, label) → (count, detail) for the compared Summary sections."""
    ws = openpyxl.load_workbook(io.BytesIO(xlsx), data_only=True)["Summary"]
    out, section = {}, ""
    for r in ws.iter_rows(values_only=True):
        a = r[0] if r else None
        if a is None:
            continue
        b = r[1] if len(r) > 1 else None
        c = r[2] if len(r) > 2 else None
        if b is None and c is None:
            section = str(a)
            continue
        if section.startswith(SUMMARY_SECTIONS):
            out[(section.split(" (")[0], str(a))] = (b, c)
    return out


def _cell(v):
    return None if v in (None, "") else v


def compare(reports: dict[str, bytes]) -> list[tuple]:
    """Every difference between the modes: (where, balloon/label, field, {mode: value})."""
    ex = {m: extraction(x) for m, x in reports.items()}
    diffs = []
    ref = "PDF"
    all_no = sorted(set().union(*(set(v) for v in ex.values())))
    for n in all_no:
        have = {m: ex[m].get(n) for m in ex}
        if any(v is None for v in have.values()):
            diffs.append(("Extraction", n, "row present", {m: v is not None for m, v in have.items()}))
            continue
        for k in SAME:
            vals = {m: _cell(have[m].get(k)) for m in have}
            if len({repr(v) for v in vals.values()}) > 1:
                diffs.append(("Extraction", n, k, vals))
        x0, y0 = have[ref].get("X (mm)"), have[ref].get("Y (mm)")
        for m, r in have.items():
            if m == ref or x0 is None:
                continue
            if r.get("X (mm)") is None or abs(r["X (mm)"] - x0) > XY_MM or abs(r["Y (mm)"] - y0) > XY_MM:
                diffs.append(("Extraction", n, "X/Y", {ref: (x0, y0), m: (r.get("X (mm)"), r.get("Y (mm)"))}))
    sm = {m: summary(x) for m, x in reports.items()}
    for key in sorted(set().union(*(set(v) for v in sm.values()))):
        vals = {m: sm[m].get(key) for m in sm}
        if len({repr(v) for v in vals.values()}) > 1:
            diffs.append(("Summary", key[1], key[0], vals))
    return diffs


def main(argv: list[str]) -> int:
    gt = "--no-general-tolerances" not in argv
    args = [a for a in argv if not a.startswith("--")]
    out_dir, pdf, dxf, step = Path(args[0]), Path(args[1]), Path(args[2]), Path(args[3])
    out_dir.mkdir(parents=True, exist_ok=True)
    reports = build(pdf, dxf, step, general_tolerances=gt)
    for mode, x in reports.items():
        (out_dir / f"{pdf.stem}_{mode}_TVS_extraction_report.xlsx").write_bytes(x)
    diffs = compare(reports)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Differences"
    ws.append(["Where", "Balloon No. / label", "Field", *reports])
    for where, n, k, vals in diffs:
        ws.append([where, str(n), k, *[str(vals.get(m)) for m in reports]])
    wb.save(out_dir / f"{pdf.stem}_three_mode_comparison.xlsx")
    print(f"{pdf.stem}: {len(diffs)} difference(s) across {', '.join(reports)}")
    for where, n, k, vals in diffs:
        print(f"  {where:<10} {str(n):<45} {k:<22} " + " | ".join(f"{m}={v!r}" for m, v in vals.items()))
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
