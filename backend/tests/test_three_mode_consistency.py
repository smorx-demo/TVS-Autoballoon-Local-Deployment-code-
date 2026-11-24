"""
One part, three inputs — drawing PDF, all-sheets DXF (converted by the app) and AP242 STEP paired with
the drawing — must give the same TVS extraction report: same balloons, zones, symbols, values,
general tolerances, text as shown, near-border flags and Summary scope rows.

Integration tests build the reports of both demo parts (tools.three_mode_reports, the code paths the
app runs on upload); unit tests pin each shared rule on synthetic input.
"""

from __future__ import annotations

import io
from pathlib import Path

import fitz
import openpyxl
import pytest

from app.services import tvs_extractor as T
from app.services.dxf_to_pdf import FONT_DIR
from app.services.step_pmi import read_pmi, stated_quantity, tolerance_text
from tests.eval_against_key import FIXTURES, evaluate
from tools.three_mode_reports import build, compare, extraction

PARTS = ["SMX-DEMO-HF-001", "SMX-DEMO-002"]
DEJAVU = str(FONT_DIR / "DejaVuSans.ttf")


def _inputs(part: str) -> tuple[Path, Path, Path]:
    return FIXTURES / f"{part}.pdf", FIXTURES / f"{part}_all_sheets.dxf", FIXTURES / f"{part}_PMI_AP242_v3.step"


@pytest.fixture(scope="module")
def reports_gt():
    return {p: build(*_inputs(p), general_tolerances=True) for p in PARTS}


@pytest.fixture(scope="module")
def reports_plain():
    return {p: build(*_inputs(p), general_tolerances=False) for p in PARTS}


def _summary_rows(xlsx: bytes) -> dict[str, tuple]:
    ws = openpyxl.load_workbook(io.BytesIO(xlsx), data_only=True)["Summary"]
    return {str(r[0]): tuple(r[1:3]) for r in ws.iter_rows(values_only=True) if r and r[0] is not None}


# ----------------------------------------------------------------------------- 1. consistency

@pytest.mark.parametrize("part", PARTS)
def test_three_modes_identical_with_general_tolerances(reports_gt, part):
    assert compare(reports_gt[part]) == []


@pytest.mark.parametrize("part", PARTS)
def test_three_modes_identical_without_general_tolerances(reports_plain, part):
    assert compare(reports_plain[part]) == []


# ----------------------------------------------------------------------------- 2. answer keys

@pytest.mark.parametrize("part", PARTS)
def test_every_mode_scores_like_the_drawing_against_the_key(reports_plain, part):
    """General tolerances OFF: each mode reproduces the drawing report's score feature by feature.
    (Gaps the drawing report itself has against the key are covered by test_eval_against_key.)"""
    key = FIXTURES / f"{part}_expected_extraction.xlsx"
    ref = evaluate(reports_plain[part]["PDF"], key)
    for mode in ("all_sheets", "STEP"):
        got = evaluate(reports_plain[part][mode], key)
        assert {k: (v["status"], v.get("failed")) for k, v in got["per_feature"].items()} == \
               {k: (v["status"], v.get("failed")) for k, v in ref["per_feature"].items()}, mode
        assert got["summary_diff"] == ref["summary_diff"], mode
        assert got["notes"]["found"] == ref["notes"]["found"], mode


# ISO 2768-m values of the open radii / chamfers (title block: ISO 2768-mK), by balloon
GT_EXPECTED = {
    "SMX-DEMO-HF-001": {25: (6, 0.5), 30: (1, 0.2), 35: (2, 0.2), 37: (3, 0.2), 40: (1, 0.2), 52: (12, 1)},
    "SMX-DEMO-002": {19: (1, 0.2), 20: (1, 0.2), 21: (2, 0.2), 25: (0.5, 0.2), 37: (1, 0.2), 54: (1, 0.2)},
}


@pytest.mark.parametrize("part", PARTS)
@pytest.mark.parametrize("mode", ["PDF", "all_sheets", "STEP"])
def test_general_tolerance_values(reports_gt, part, mode):
    rows = extraction(reports_gt[part][mode])
    for no, (nom, tol) in GT_EXPECTED[part].items():
        r = rows[no]
        assert (r["Nominal"], r["Upper tolerance"], r["Lower tolerance"]) == (nom, tol, -tol), (mode, no)
        assert (r["Max limit"], r["Min limit"]) == (pytest.approx(nom + tol), pytest.approx(nom - tol)), (mode, no)
    s = _summary_rows(reports_gt[part][mode])
    assert "ISO 2768-mK" in str(s["General-tolerance standard used"][1])
    assert s["Auto-filled from the standard"][0] == len(GT_EXPECTED[part])


def test_unresolved_general_tolerance_is_blank_with_reason_in_every_mode(reports_gt):
    """2× R0.4 is below the ISO 2768 range (0.5 mm): blank, with the reason, in all modes."""
    for mode, x in reports_gt["SMX-DEMO-002"].items():
        r = next(r for r in extraction(x).values() if r["Text as shown on drawing"] == "2× R0.4")
        assert r["Upper tolerance"] is None and r["Lower tolerance"] is None, mode
        assert "below the range" in r["Notes"], mode
        assert _summary_rows(x)["Unresolved (blank, see Notes)"] == (1, str(r["Balloon No."])), mode


def test_step_notes_get_their_own_summary_row(reports_gt):
    s = _summary_rows(reports_gt["SMX-DEMO-002"]["STEP"])
    assert s["Notes & textual information"][0] == _summary_rows(reports_gt["SMX-DEMO-002"]["PDF"])["Notes & textual information"][0]
    assert s["STEP text notes (Notes tab)"][0] > 0


# ----------------------------------------------------------------------------- 3a. depth symbol ↧

def _page_lines(build_page) -> list[str]:
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    build_page(page)
    return [ln.text for ln in T.PageData(page, 0).lines]


def test_depth_symbol_codepoints_and_symbol_font():
    assert T.symbol_text("↧10") == "↧10"
    assert T.symbol_text("⤓10") == "↧10"               # ⤓ DOWNWARDS ARROW TO BAR
    assert T.symbol_text("x10", "GDT") == "↧10"             # AutoCAD gdt.shx / AMGDT letter
    assert T.symbol_text("x10", "AMGDT-Regular") == "↧10"
    assert T.symbol_text("x10", "DejaVuSans") == "x10"      # an ordinary font keeps its letters


def test_depth_glyph_in_pdf_text():
    lines = _page_lines(lambda p: p.insert_text((50, 100), "↧10", fontname="dv", fontfile=DEJAVU, fontsize=10))
    assert "↧10" in lines
    info = T.parse_dimension("↧10", ["+1", "0"])
    assert info["symbol"] == "↧" and info["nominal"] == 10


def _draw_depth_symbol(page: fitz.Page, x: float, base: float, em: float) -> None:
    """↧ drawn as lines: vertical stem, V arrowhead at its foot, bar under the tip."""
    top, foot = base - 0.75 * em, base
    sh = page.new_shape()
    sh.draw_line((x, top), (x, foot))
    sh.draw_line((x - 0.2 * em, foot - 0.25 * em), (x, foot))
    sh.draw_line((x + 0.2 * em, foot - 0.25 * em), (x, foot))
    sh.draw_line((x - 0.3 * em, foot), (x + 0.3 * em, foot))
    sh.finish(width=0.35, color=(0, 0, 0))
    sh.commit()


def test_depth_symbol_drawn_as_geometry():
    def page(p):
        _draw_depth_symbol(p, 52, 100, 10)
        p.insert_text((58, 100), "10", fontname="dv", fontfile=DEJAVU, fontsize=10)
    assert "↧10" in _page_lines(page)


def test_geometry_without_bar_is_not_a_depth_symbol():
    def page(p):
        sh = p.new_shape()
        sh.draw_line((52, 92.5), (52, 100))
        sh.finish(width=0.35, color=(0, 0, 0))
        sh.commit()
        p.insert_text((58, 100), "10", fontname="dv", fontfile=DEJAVU, fontsize=10)
    lines = _page_lines(page)
    assert "10" in lines and "↧10" not in lines


def test_inferred_depth_follows_the_drawing_in_paired_step(reports_gt):
    """A section depth on the drawing (HF-001) is ↧ in every mode; a value that merely equals a thread
    depth in 3D (DEMO-002 '14 +0.1/0', no ↧ on the drawing or in the key) is not."""
    for mode, x in reports_gt["SMX-DEMO-HF-001"].items():
        rows = extraction(x)
        assert [rows[n]["Symbol"] for n in (50, 51)] == ["↧", "↧"], mode
    for mode, x in reports_gt["SMX-DEMO-002"].items():
        r = next(r for r in extraction(x).values() if r["Text as shown on drawing"] == "14 +0.1/0")
        assert r["Symbol"] is None, mode


# ----------------------------------------------------------------------------- 3b. zone on a zone line

def test_zone_is_the_zone_of_the_merged_callout_centre():
    """DEMO-002's 3× M6 callout straddles the A3/A4 column line: its zone is the zone of the centre of
    the box around all its text runs (both lines), never of its first word or its leader end."""
    from app.services.tvs_extraction_report import _PageCtx

    src = FIXTURES / "SMX-DEMO-002.pdf"
    ext = T.extract_drawing(src)
    f = next(f for f in ext.features if f.kind == "thread" and f.text.startswith("3× M6"))
    doc = fitz.open(src)
    ctx = _PageCtx(doc[f.sheet - 1], f.sheet)
    runs = [ln for ln in T.PageData(doc[f.sheet - 1], f.sheet - 1).lines if f.rect.intersects(ln.rect)]
    assert len(runs) >= 2                                            # two text lines merged
    assert ctx.zone(f.rect.x0, f.center.y) != ctx.zone(f.rect.x1, f.center.y)   # straddles the line
    assert ctx.zone(f.center.x, f.center.y) == "A4"


def test_zone_identical_in_all_modes(reports_gt):
    for part, reps in reports_gt.items():
        ex = {m: extraction(x) for m, x in reps.items()}
        for n, r in ex["PDF"].items():
            assert {ex[m][n]["Zone"] for m in ex} == {r["Zone"]}, (part, n)


# ----------------------------------------------------------------------------- 3c. quantity prefix

def test_stated_quantity():
    assert stated_quantity("2x R0.4 undercut corner radii", 0.4) == 2
    assert stated_quantity("4x DIA9 CSK", 9.0) == 4
    assert stated_quantity("R0.4", 0.4) == 1
    assert stated_quantity("2x R0.5", 0.4) == 1         # quantity of another value
    assert stated_quantity("1.2x R0.4", 0.4) == 1       # not a quantity


def test_step_only_text_carries_the_quantity():
    texts = [it.text for it in read_pmi(FIXTURES / "SMX-DEMO-002_PMI_AP242_v3.step").items]
    assert "2× R0.4" in texts and "R0.4" not in texts


def test_paired_text_is_the_drawing_text(reports_gt):
    for part, reps in reports_gt.items():
        pdf, step = extraction(reps["PDF"]), extraction(reps["STEP"])
        for n, r in pdf.items():
            assert step[n]["Text as shown on drawing"] == r["Text as shown on drawing"], (part, n)


# ----------------------------------------------------------------------------- 3d. trailing zeros

@pytest.mark.parametrize("hi, lo, places, text", [
    (-0.007, -0.02, None, "-0.007/-0.020"),     # ISO 129-1: same decimals on both deviations
    (0.021, 0.0, None, "+0.021/0"),
    (0.1, 0.0, None, "+0.1/0"),
    (0.2, -0.2, None, "±0.2"),
    (0.1, -0.1, 3, "±0.100"),                   # PMI value-format qualifier NR2 x.3
    (0.051, 0.026, None, "+0.051/+0.026"),
])
def test_tolerance_text(hi, lo, places, text):
    assert tolerance_text(hi, lo, places) == text


def test_step_only_keeps_trailing_zeros():
    texts = [it.text for it in read_pmi(FIXTURES / "SMX-DEMO-002_PMI_AP242_v3.step").items]
    assert "Ø25 g6 -0.007/-0.020" in texts


# ----------------------------------------------------------------------------- 3e. run merging

def _two_run_word(gap_em: float) -> list[str]:
    def page(p):
        size = 17.0
        p.insert_text((50, 100), "SmorX", fontname="dv", fontfile=DEJAVU, fontsize=size, color=(0, 0, 0))
        w = fitz.Font(fontfile=DEJAVU).text_length("SmorX", fontsize=size)
        p.insert_text((50 + w + gap_em * size, 100), ".ai", fontname="dv", fontfile=DEJAVU, fontsize=size,
                      color=(0.75, 0, 0))
    return _page_lines(page)


def test_two_colour_word_is_one_word():
    assert "SmorX.ai" in _two_run_word(0.0)
    assert "SmorX.ai" in _two_run_word(0.1)


def test_runs_with_a_real_gap_stay_apart():
    lines = _two_run_word(0.6)
    assert "SmorX.ai" not in lines


def test_title_block_note_has_no_stray_space(reports_gt):
    for part, reps in reports_gt.items():
        for mode, x in reps.items():
            ws = openpyxl.load_workbook(io.BytesIO(x), data_only=True)["Notes"]
            texts = [str(r[5]) for r in ws.iter_rows(min_row=2, values_only=True) if r[5]]
            assert not any("SmorX .ai" in t for t in texts), (part, mode)
            assert any("SmorX.ai" in t for t in texts), (part, mode)


# ----------------------------------------------------------------------------- 3f. near-border flag

def test_paired_step_keeps_the_near_border_flag(reports_gt):
    for part, reps in reports_gt.items():
        pdf, step = extraction(reps["PDF"]), extraction(reps["STEP"])
        near = {n for n, r in pdf.items() if "Placed near" in str(r["Notes"] or "")}
        assert near
        assert near == {n for n, r in step.items() if "Placed near" in str(r["Notes"] or "")}, part
        label = "Annotations near border / title block"
        assert _summary_rows(reps["STEP"])[label] == _summary_rows(reps["PDF"])[label], part


def test_step_only_near_border_is_not_applicable():
    from app.services.step_report import build_step_report, step_report_xlsx

    rep = build_step_report(FIXTURES / "SMX-DEMO-002_PMI_AP242_v3.step")
    s = _summary_rows(step_report_xlsx(rep, {}))
    assert s["Annotations near border / title block"][0] == "N/A (3D model)"


# ----------------------------------------------------------------------------- DXF conversion

def test_dxf_text_uses_its_style_font_and_colour(tmp_path):
    """Converted text keeps the font the DXF style names (bundled DejaVu here), so it has the width
    the drawing was laid out with on any machine; colour follows the entity."""
    from app.services.dxf_to_pdf import dxf_to_pdf

    out = tmp_path / "hf.pdf"
    dxf_to_pdf(FIXTURES / "SMX-DEMO-HF-001_all_sheets.dxf", out)
    conv = {s["text"]: s for b in fitz.open(out)[0].get_text("dict")["blocks"] for ln in b.get("lines", [])
            for s in ln["spans"]}
    orig = {s["text"]: s for b in fitz.open(FIXTURES / "SMX-DEMO-HF-001.pdf")[0].get_text("dict")["blocks"]
            for ln in b.get("lines", []) for s in ln["spans"]}
    assert conv["SmorX"]["font"].startswith("DejaVuSans-Bold")
    assert conv[".ai"]["color"] != 0
    for t in ("SmorX", ".ai"):
        assert conv[t]["bbox"][2] == pytest.approx(orig[t]["bbox"][2], abs=0.5)


def test_arc_distance_does_not_depend_on_segment_count():
    """A point on an arc is on it whether the arc is drawn with 2 or 3 Bézier segments (the DXF
    converter and the original CAD export split the same arc differently)."""
    import math

    r, c = 80.0, fitz.Point(150, 150)

    def on(t):
        return fitz.Point(c.x + r * math.cos(t), c.y + r * math.sin(t))

    def arc(n):
        ts = [math.pi / 2 * k / n for k in range(n + 1)]
        samples = [on(ts[0])]
        for t0, t1 in zip(ts, ts[1:]):  # as PageData samples a Bézier: its midpoint and end
            samples += [on((t0 + t1) / 2), on(t1)]
        return T.Curve(0, [on(t) for t in ts], fitz.Rect(150, 150, 230, 230), False, False, samples, 1.0)

    tip = on(0.3)
    for n in (2, 3):
        assert arc(n).dist_to(tip) < 0.5, n
    assert min(math.hypot(q.x - tip.x, q.y - tip.y) for q in arc(2).samples) > 6   # samples alone miss it
