"""Tests for ``maze_book.preflight`` (PRD 17.15, 18.8).

Each check gets a PDF built to fail it. That matters more here than elsewhere: a
preflight check that cannot fail is indistinguishable from one that always
passes, and the whole point of this layer is to be the thing that says no.

The external-tool contract is tested too. 17.15 requires production mode to fail
when a tool is missing, and the local diagnostic mode to report the skip --
never to count it as a pass, which is how an unchecked PDF gets announced as
print-ready.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from reportlab.pdfgen.canvas import Canvas

from maze_book.errors import PreflightError
from maze_book.model.analysis import MazeAnalysis
from maze_book.preflight.checks import (
    PreflightReport,
    check_best_possible_cross_check,
    check_color_and_ink,
    check_fonts,
    check_pages,
    check_parity,
    check_raster_and_transparency,
    check_safe_area,
    check_text_markers,
    extract_pages_text,
    missing_tools,
)


def reader_for(path: Path):
    from pypdf import PdfReader

    return PdfReader(str(path))


def make_pdf(
    path: Path, pages: int = 2, *, size=(612.0, 792.0), draw=None, initial_font="Vera"
) -> Path:
    """Build a PDF to order.

    ``initial_font`` is explicit because ReportLab writes it into every page's
    preamble whether or not anything draws with it -- which is how an unembedded
    base-14 face ends up in a file that never used one. The default is a bundled
    face, so a test about colour or rasterization does not accidentally also be
    a test about fonts.
    """
    if initial_font.startswith("Vera"):
        from maze_book.rendering.svg_to_pdf import register_fonts

        register_fonts(Path(__file__).resolve().parents[1] / "fonts")
    canvas = Canvas(str(path), pagesize=size, initialFontName=initial_font)
    for index in range(pages):
        if draw is not None:
            draw(canvas, index + 1)
        canvas.showPage()
    canvas.save()
    return path


def report_for(path: Path) -> PreflightReport:
    return PreflightReport(pdf=path)


def codes(report: PreflightReport) -> dict[str, bool]:
    return {r.name: r.passed for r in report.results}


# ---------------------------------------------------------------------------
# Page count, size, rotation
# ---------------------------------------------------------------------------


def test_a_correct_pdf_passes_the_page_checks(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "ok.pdf", pages=4)
    report = report_for(pdf)
    check_pages(report, reader_for(pdf), expected_count=4, width_pt=612.0, height_pt=792.0)
    assert all(codes(report).values())


def test_a_wrong_page_count_fails(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "short.pdf", pages=3)
    report = report_for(pdf)
    check_pages(report, reader_for(pdf), expected_count=4, width_pt=612.0, height_pt=792.0)
    assert codes(report)["page-count"] is False


def test_an_odd_page_count_fails_even_when_it_was_expected(tmp_path: Path) -> None:
    """18.8 requires both: the count the book planned *and* an even total. A
    printer folds sheets, not pages."""
    pdf = make_pdf(tmp_path / "odd.pdf", pages=3)
    report = report_for(pdf)
    check_pages(report, reader_for(pdf), expected_count=3, width_pt=612.0, height_pt=792.0)
    assert codes(report)["page-count"] is True
    assert codes(report)["page-count-even"] is False


def test_a_page_off_trim_by_a_point_fails(tmp_path: Path) -> None:
    """KDP rejects on trim, and a point is invisible on screen."""
    pdf = make_pdf(tmp_path / "wrong.pdf", pages=2, size=(613.0, 792.0))
    report = report_for(pdf)
    check_pages(report, reader_for(pdf), expected_count=2, width_pt=612.0, height_pt=792.0)
    assert codes(report)["page-size"] is False
    assert "613" in next(r.detail for r in report.results if r.name == "page-size")


def test_a_rotation_flag_fails(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "rot.pdf", pages=2)
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(str(pdf)).pages:
        page.rotate(90)
        writer.add_page(page)
    rotated = tmp_path / "rotated.pdf"
    with rotated.open("wb") as handle:
        writer.write(handle)

    report = report_for(rotated)
    check_pages(report, reader_for(rotated), expected_count=2, width_pt=612.0, height_pt=792.0)
    assert codes(report)["page-rotation"] is False


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


def test_a_base_14_font_is_reported_as_unembedded(tmp_path: Path) -> None:
    """Helvetica is never embedded: the press substitutes something metrically
    different and every line re-wraps."""
    def draw(canvas, page):
        canvas.setFont("Helvetica", 12)
        canvas.drawString(100, 100, "hello")

    pdf = make_pdf(tmp_path / "helv.pdf", pages=1, draw=draw, initial_font="Helvetica")
    report = report_for(pdf)
    check_fonts(report, reader_for(pdf))
    assert codes(report)["fonts-embedded"] is False
    assert "Helvetica" in next(r.detail for r in report.results)


def test_a_bundled_truetype_face_passes(tmp_path: Path, repo_root: Path) -> None:
    from maze_book.rendering.svg_to_pdf import register_fonts

    register_fonts(repo_root / "fonts")

    def draw(canvas, page):
        canvas.setFont("Vera", 12)
        canvas.drawString(100, 100, "hello")

    pdf = make_pdf(tmp_path / "vera.pdf", pages=1, draw=draw)
    report = report_for(pdf)
    check_fonts(report, reader_for(pdf))
    assert codes(report)["fonts-embedded"] is True


def test_a_page_that_draws_no_text_still_carries_its_initial_font(tmp_path: Path) -> None:
    """ReportLab's page preamble sets a font unconditionally, so "no text" does
    not mean "no font resource". With a bundled initial face that is harmless;
    with the Helvetica default it is an unembedded base-14 font in a file that
    never drew a character -- which is the defect this caught in the assembler."""
    bundled = make_pdf(tmp_path / "blank-vera.pdf", pages=1)
    report = report_for(bundled)
    check_fonts(report, reader_for(bundled))
    assert codes(report)["fonts-embedded"] is True

    default = make_pdf(tmp_path / "blank-helv.pdf", pages=1, initial_font="Helvetica")
    report = report_for(default)
    check_fonts(report, reader_for(default))
    assert codes(report)["fonts-embedded"] is False


# ---------------------------------------------------------------------------
# Colour, ink, raster, transparency
# ---------------------------------------------------------------------------


def test_devicegray_drawing_passes(tmp_path: Path) -> None:
    def draw(canvas, page):
        canvas.setFillGray(0.0)
        canvas.rect(100, 100, 50, 50, stroke=0, fill=1)

    pdf = make_pdf(tmp_path / "gray.pdf", pages=1, draw=draw)
    report = report_for(pdf)
    check_color_and_ink(report, reader_for(pdf))
    assert codes(report) == {"color-space-devicegray": True, "ink-pure-black": True}


def test_an_rgb_fill_fails_the_colour_check(tmp_path: Path) -> None:
    def draw(canvas, page):
        canvas.setFillColorRGB(0.8, 0.1, 0.1)
        canvas.rect(100, 100, 50, 50, stroke=0, fill=1)

    pdf = make_pdf(tmp_path / "rgb.pdf", pages=1, draw=draw)
    report = report_for(pdf)
    check_color_and_ink(report, reader_for(pdf))
    assert codes(report)["color-space-devicegray"] is False


def test_a_grey_tint_fails_the_ink_check(tmp_path: Path) -> None:
    """18.8: pure black only. A 60% tint halftones unpredictably on cream stock."""
    def draw(canvas, page):
        canvas.setFillGray(0.4)
        canvas.rect(100, 100, 50, 50, stroke=0, fill=1)

    pdf = make_pdf(tmp_path / "tint.pdf", pages=1, draw=draw)
    report = report_for(pdf)
    check_color_and_ink(report, reader_for(pdf))
    assert codes(report)["ink-pure-black"] is False


def test_an_embedded_raster_image_fails(tmp_path: Path) -> None:
    from PIL import Image

    png = tmp_path / "dot.png"
    Image.new("L", (8, 8), color=0).save(png)

    def draw(canvas, page):
        canvas.drawImage(str(png), 100, 100, width=20, height=20)

    pdf = make_pdf(tmp_path / "raster.pdf", pages=1, draw=draw)
    report = report_for(pdf)
    check_raster_and_transparency(report, reader_for(pdf))
    assert codes(report)["no-raster-images"] is False


def test_soft_alpha_fails_the_transparency_check(tmp_path: Path) -> None:
    def draw(canvas, page):
        canvas.setFillAlpha(0.5)
        canvas.setFillGray(0.0)
        canvas.rect(100, 100, 50, 50, stroke=0, fill=1)

    pdf = make_pdf(tmp_path / "alpha.pdf", pages=1, draw=draw)
    report = report_for(pdf)
    check_raster_and_transparency(report, reader_for(pdf))
    assert codes(report)["transparency-flattened"] is False


def test_plain_vector_drawing_passes_both(tmp_path: Path) -> None:
    def draw(canvas, page):
        canvas.setFillGray(0.0)
        canvas.rect(100, 100, 50, 50, stroke=0, fill=1)

    pdf = make_pdf(tmp_path / "vec.pdf", pages=1, draw=draw)
    report = report_for(pdf)
    check_raster_and_transparency(report, reader_for(pdf))
    assert all(codes(report).values())


# ---------------------------------------------------------------------------
# Parity, markers and the cross-check
# ---------------------------------------------------------------------------


def plan_obj(pages: list[dict]) -> dict:
    return {"totalPages": len(pages), "pages": pages}


def test_a_correct_plan_passes_the_parity_check() -> None:
    report = PreflightReport(pdf=Path("x.pdf"))
    check_parity(report, plan_obj([
        {"pageNumber": 6, "side": "left", "kind": "story", "sceneNumber": 1},
        {"pageNumber": 7, "side": "right", "kind": "maze", "mazeIndex": 1},
    ]))
    assert codes(report)["story-maze-parity"] is True


def test_a_story_page_on_an_odd_page_fails_parity() -> None:
    report = PreflightReport(pdf=Path("x.pdf"))
    check_parity(report, plan_obj([
        {"pageNumber": 7, "side": "right", "kind": "story", "sceneNumber": 1},
    ]))
    assert codes(report)["story-maze-parity"] is False


def test_a_side_contradicting_the_page_number_fails_parity() -> None:
    report = PreflightReport(pdf=Path("x.pdf"))
    check_parity(report, plan_obj([
        {"pageNumber": 6, "side": "right", "kind": "story", "sceneNumber": 1},
    ]))
    assert codes(report)["story-maze-parity"] is False


def test_missing_story_titles_are_reported() -> None:
    report = PreflightReport(pdf=Path("x.pdf"))
    check_text_markers(
        report,
        ["", "nothing here"],
        plan_obj=plan_obj([{"pageNumber": 2, "side": "left", "kind": "story", "sceneNumber": 1}]),
        scene_titles={1: "THE ATTIC HUNT"},
    )
    assert codes(report)["text-markers"] is False


def test_present_markers_pass() -> None:
    report = PreflightReport(pdf=Path("x.pdf"))
    check_text_markers(
        report,
        ["", "1\nTHE ATTIC HUNT\nbody", "3. Best possible: 5 candies"],
        plan_obj=plan_obj([
            {"pageNumber": 2, "side": "left", "kind": "story", "sceneNumber": 1},
            {"pageNumber": 3, "side": "right", "kind": "solutions"},
        ]),
        scene_titles={1: "THE ATTIC HUNT"},
    )
    assert codes(report)["text-markers"] is True


def analysis(index: int, best: int) -> MazeAnalysis:
    return MazeAnalysis(
        maze_id=f"m{index}", maze_index=index, exact=True, reachable=10,
        reachable_fraction=1.0, loop_count=3, simple_path_count=8, dead_end_cells=[],
        shortest_route_length=5, best_route_length=7, best_candy_total=best,
        second_best_candy_total=best - 1, unique_highest_candy=True, best_route=[],
    )


def test_the_cross_check_passes_when_the_printed_number_agrees() -> None:
    report = PreflightReport(pdf=Path("x.pdf"))
    check_best_possible_cross_check(
        report,
        ["", "Best possible: 12"],
        plan_obj=plan_obj([{"pageNumber": 2, "side": "left", "kind": "maze", "mazeIndex": 4}]),
        analyses={4: analysis(4, 12)},
    )
    assert codes(report)["best-possible-cross-check"] is True


def test_the_cross_check_catches_a_disagreement() -> None:
    """18.8 calls this mandatory and non-negotiable: a book whose printed target
    disagrees with its own answer key is a defect that reaches every copy."""
    report = PreflightReport(pdf=Path("x.pdf"))
    check_best_possible_cross_check(
        report,
        ["", "Best possible: 11"],
        plan_obj=plan_obj([{"pageNumber": 2, "side": "left", "kind": "maze", "mazeIndex": 4}]),
        analyses={4: analysis(4, 12)},
    )
    assert codes(report)["best-possible-cross-check"] is False
    assert "prints 11, analysis says 12" in report.results[0].detail


def test_a_maze_page_with_no_printed_number_at_all_fails() -> None:
    report = PreflightReport(pdf=Path("x.pdf"))
    check_best_possible_cross_check(
        report,
        ["", "no number here"],
        plan_obj=plan_obj([{"pageNumber": 2, "side": "left", "kind": "maze", "mazeIndex": 4}]),
        analyses={4: analysis(4, 12)},
    )
    assert codes(report)["best-possible-cross-check"] is False


# ---------------------------------------------------------------------------
# Safe area
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    "pdftoppm" in missing_tools(), reason="pdftoppm is not installed"
)
def test_a_clean_page_passes_the_safe_area_check(tmp_path: Path) -> None:
    def draw(canvas, page):
        canvas.setFillGray(0.0)
        canvas.rect(200, 300, 100, 100, stroke=0, fill=1)

    pdf = make_pdf(tmp_path / "clean.pdf", pages=2, draw=draw)
    report = report_for(pdf)
    check_safe_area(report, pdf, page_count=2, prefix=tmp_path / "raster" / "page")
    assert codes(report)["safe-area"] is True
    assert codes(report)["renders-every-page"] is True


@pytest.mark.skipif(
    "pdftoppm" in missing_tools(), reason="pdftoppm is not installed"
)
def test_ink_inside_the_trim_band_fails(tmp_path: Path) -> None:
    """Measured by rasterizing and looking, because the layout arithmetic is
    exactly what would be wrong if this check were needed."""
    def draw(canvas, page):
        canvas.setFillGray(0.0)
        canvas.rect(2, 400, 10, 50, stroke=0, fill=1)  # 2 pt from the left trim

    pdf = make_pdf(tmp_path / "bleed.pdf", pages=1, draw=draw)
    report = report_for(pdf)
    check_safe_area(report, pdf, page_count=1, prefix=tmp_path / "raster" / "page")
    assert codes(report)["safe-area"] is False
    assert "left band" in next(r.detail for r in report.results if r.name == "safe-area")


@pytest.mark.skipif(
    "pdftoppm" in missing_tools(), reason="pdftoppm is not installed"
)
def test_the_jpeg_previews_17_15_asks_for_are_written(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "x.pdf", pages=3)
    report = report_for(pdf)
    prefix = tmp_path / "out" / "validate-page"
    check_safe_area(report, pdf, page_count=3, prefix=prefix)
    assert sorted(p.name for p in prefix.parent.glob("*.jpg")) == [
        "validate-page-001.jpg", "validate-page-002.jpg", "validate-page-003.jpg",
    ]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_the_report_serializes_every_check_with_its_status(tmp_path: Path) -> None:
    report = PreflightReport(pdf=tmp_path / "book.pdf")
    report.add("a", True, "fine")
    report.add("b", False, "broken")
    report.add("c", True, "unavailable", skipped=True)

    path = tmp_path / "preflight.json"
    report.write(path)
    obj = json.loads(path.read_text(encoding="utf-8"))
    assert obj["passed"] is False
    assert obj["failed"] == ["b"]
    assert obj["skipped"] == ["c"]
    assert [c["status"] for c in obj["checks"]] == ["pass", "fail", "skipped"]


def test_raise_for_status_lists_every_failure(tmp_path: Path) -> None:
    report = PreflightReport(pdf=tmp_path / "book.pdf")
    report.add("a", False, "one")
    report.add("b", False, "two")
    with pytest.raises(PreflightError, match=r"2 preflight check\(s\) failed") as excinfo:
        report.raise_for_status()
    assert len(excinfo.value.details) == 2


def test_a_skipped_check_never_counts_as_a_failure_or_a_pass(tmp_path: Path) -> None:
    """A skip that reads as a pass is how an unchecked PDF gets announced as
    print-ready."""
    report = PreflightReport(pdf=tmp_path / "book.pdf")
    report.add("tool", True, "missing", skipped=True)
    assert report.passed is True
    assert report.skipped and not report.failures
    assert "1 skipped" in report.summary()


def test_extract_pages_text_splits_on_form_feeds(tmp_path: Path, repo_root: Path) -> None:
    from maze_book.rendering.svg_to_pdf import register_fonts

    register_fonts(repo_root / "fonts")

    def draw(canvas, page):
        canvas.setFont("Vera", 12)
        canvas.drawString(100, 700, f"PAGE MARKER {page}")

    pdf = make_pdf(tmp_path / "text.pdf", pages=3, draw=draw)
    pages = extract_pages_text(pdf, tmp_path / "text.txt")
    assert len(pages) == 3
    assert "PAGE MARKER 2" in pages[1]
