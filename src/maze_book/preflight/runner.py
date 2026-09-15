"""Running the whole preflight gate over a built book (PRD 17.15, 18.8).

The runner's one job beyond sequencing is to keep the production path honest:
in ``strict`` mode a missing external tool is a *failure*, never a skip. A skip
that reads as a pass is how an unchecked PDF gets announced as print-ready, and
17.14 is explicit that "the production PDF MUST NOT be announced as successful
unless all required preflight checks pass".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..errors import PreflightError
from ..model.analysis import MazeAnalysis
from ..model.book_config import BookConfig
from ..model.json_io import read_json
from ..rendering.page import PT_PER_IN
from .checks import (
    PreflightReport,
    check_fonts,
    check_best_possible_cross_check,
    check_color_and_ink,
    check_pages,
    check_parity,
    check_qpdf,
    check_raster_and_transparency,
    check_safe_area,
    check_text_markers,
    extract_pages_text,
    missing_tools,
)


def run_preflight(
    pdf: Path,
    *,
    config: BookConfig,
    plan_obj: dict[str, Any],
    analyses: dict[int, MazeAnalysis],
    text_out: Path,
    raster_prefix: Path,
    strict: bool = True,
    expected_page_count: int | None = None,
) -> PreflightReport:
    from pypdf import PdfReader

    pdf = Path(pdf)
    report = PreflightReport(pdf=pdf)
    if not pdf.is_file():
        report.add("pdf-exists", False, f"{pdf} does not exist")
        return report
    report.add("pdf-exists", True, f"{pdf.stat().st_size / 1024:.0f} KiB")

    absent = missing_tools()
    if absent:
        report.add(
            "external-tools",
            not strict,
            f"missing: {', '.join(absent)} -- required in production mode (17.15)",
            skipped=not strict,
        )
    else:
        report.add("external-tools", True, "qpdf, pdftotext and pdftoppm available")

    reader = PdfReader(str(pdf))
    width_pt = config.print.trim_width_in * PT_PER_IN
    height_pt = config.print.trim_height_in * PT_PER_IN

    expected = (
        expected_page_count
        if expected_page_count is not None
        else config.layout.expected_page_count
    )
    check_pages(report, reader, expected_count=expected, width_pt=width_pt, height_pt=height_pt)
    check_fonts(report, reader)
    check_color_and_ink(report, reader)
    check_raster_and_transparency(report, reader)
    check_parity(report, plan_obj)

    # `gutterIn` is recorded separately from `safeMarginsIn.inside` and is not
    # added to it (see rendering/page.py); this is the assertion that gives the
    # field meaning rather than letting it sit unused.
    report.add(
        "gutter-within-inside-margin",
        config.print.safe_margins_in.inside + 1e-9 >= config.print.gutter_in,
        f"inside margin {config.print.safe_margins_in.inside} in vs gutter "
        f"{config.print.gutter_in} in",
    )

    if "pdftotext" not in absent:
        pages_text = extract_pages_text(pdf, text_out)
        titles = {scene.number: scene.title.upper() for scene in config.scenes}
        check_text_markers(report, pages_text, plan_obj=plan_obj, scene_titles=titles)
        check_best_possible_cross_check(
            report, pages_text, plan_obj=plan_obj, analyses=analyses
        )
    else:
        for name in ("text-markers", "best-possible-cross-check"):
            report.add(name, not strict, "pdftotext unavailable", skipped=not strict)

    if "pdftoppm" not in absent:
        check_safe_area(report, pdf, page_count=len(reader.pages), prefix=raster_prefix)
    else:
        for name in ("safe-area", "renders-every-page"):
            report.add(name, not strict, "pdftoppm unavailable", skipped=not strict)

    check_qpdf(report, pdf, strict=strict)
    return report


def check_editable_fonts(report: PreflightReport, editable: Path) -> None:
    """18.8 requires embedded fonts in *both* PDFs, so the proof file is checked
    too. It is the file a proofreader opens and a reviewer comments on; a
    substituted font there misleads the person whose job is to catch exactly
    that."""
    from pypdf import PdfReader

    if not editable.is_file():
        report.add("editable-pdf", True, "not produced (--no-editable)", skipped=True)
        return
    sub = PreflightReport(pdf=editable)
    check_fonts(sub, PdfReader(str(editable)))
    for result in sub.results:
        report.add(
            f"editable-{result.name}", result.passed, result.detail, skipped=result.skipped
        )


def preflight_from_outputs(
    *,
    config: BookConfig,
    paths,
    strict: bool = True,
) -> PreflightReport:
    """Preflight an already-built book from ``output/<book-id>/`` alone.

    17.13: ``book preflight`` "checks existing outputs without changing them".
    """
    plan_path = Path(paths.page_plan)
    if not plan_path.is_file():
        raise PreflightError(
            f"no page plan at {plan_path}", details=["run `book build` or `book assemble` first"]
        )
    plan_obj = read_json(plan_path)

    analyses: dict[int, MazeAnalysis] = {}
    for page in plan_obj["pages"]:
        if page["kind"] != "maze":
            continue
        index = page["mazeIndex"]
        analysis_path = Path(paths.analysis_json(index))
        if not analysis_path.is_file():
            raise PreflightError(
                f"missing analysis for maze {index}", details=[str(analysis_path)]
            )
        analyses[index] = MazeAnalysis.from_json_obj(read_json(analysis_path))

    report = run_preflight(
        Path(paths.interior),
        config=config,
        plan_obj=plan_obj,
        analyses=analyses,
        text_out=Path(paths.interior_text),
        raster_prefix=Path(paths.validate_page_prefix),
        strict=strict,
        expected_page_count=plan_obj.get("totalPages"),
    )
    check_editable_fonts(report, Path(paths.editable))
    return report
