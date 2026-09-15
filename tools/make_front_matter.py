#!/usr/bin/env python3
"""Deterministic front-matter PDF generator for maze-book packages.

Builds a fixed 5-page front-matter PDF (title, copyright, how-to-play,
dedication, half-title) for a book package. Title, subtitle and content-origin
disclosure are read out of that book's ``book.json`` (never hard-coded), so
this script stays reusable for any book package under ``books/<book-id>/``
(see CLAUDE.md: "Never add book-specific branching to src/"; the same
principle applies to this standalone tool).

PRD anchors:
  * 17.3  book.json contract -- book.title, book.subtitle, book.contentOrigin.
  * 17.10/18.6 page geometry -- 8.5 x 11 in trim, safe margins
    (inside 0.625in / outside 0.5in / top 0.5in / bottom 0.5in), recto
    (odd) pages have the spine on the left, verso (even) pages on the right.
  * 17.11 fonts MUST be the bundled Bitstream Vera family, registered by
    absolute path, raising rather than silently falling back to Helvetica
    when a face is missing.
  * 18.7  front matter MUST have an ODD page count so scene 1's story page
    lands on an even/left page, and any change to front-matter length MUST
    happen two pages at a time (a one-page change flips every spread in the
    book). This script emits 5 pages: a 4-page core (title / copyright /
    how-to-play / dedication) plus the half-title page below, which is what
    makes the count odd. If you ever add or remove a page here, add/remove
    TWO, never one -- see the NOTE at page 5 below.

Usage:
    uv run python tools/make_front_matter.py
    uv run python tools/make_front_matter.py --book books/some-other-book --out /tmp/fm.pdf
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas

REPO_ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = REPO_ROOT / "fonts"
DEFAULT_BOOK_DIR = REPO_ROOT / "books" / "jims-halloween-maze-adventure"

# 8.5 x 11 in trim at 72 pt/in -> 612 x 792 pt (PRD 17.3 print.trimWidthIn/trimHeightIn).
PAGE_WIDTH_PT = 8.5 * 72.0
PAGE_HEIGHT_PT = 11.0 * 72.0

# PRD 17.3 print.safeMarginsIn for this book. "inside" is the spine-side
# margin, "outside" is the trim-edge-side margin (PRD 18.6/17.10).
SAFE_MARGINS_IN = {"inside": 0.625, "outside": 0.5, "top": 0.5, "bottom": 0.5}

# PRD 17.11: bundle + register the Bitstream Vera family by absolute path;
# raise rather than fall back to an unconfigured system font.
FONT_FILES = {
    "Vera": "Vera.ttf",
    "Vera-Bold": "VeraBd.ttf",
    "Vera-Italic": "VeraIt.ttf",
}

GENERATOR_LABEL = "maze-book v1.0.0"


def register_fonts() -> None:
    """Register the bundled Bitstream Vera faces by absolute path.

    Raises FileNotFoundError if a required face is missing. MUST NOT fall
    back to Helvetica or any other unconfigured system font (PRD 17.11).
    """
    for font_name, filename in FONT_FILES.items():
        font_path = FONTS_DIR / filename
        if not font_path.is_file():
            raise FileNotFoundError(
                f"Required font face '{font_name}' not found at {font_path}. "
                "This script MUST NOT fall back to an unconfigured system "
                "font (PRD 17.11) -- restore the bundled Bitstream Vera "
                "fonts under fonts/ before running it."
            )
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))


def load_book(book_dir: Path) -> dict:
    """Load book.json for the given book package directory."""
    book_json_path = book_dir / "book.json"
    if not book_json_path.is_file():
        raise FileNotFoundError(f"book.json not found at {book_json_path}")
    with book_json_path.open(encoding="utf-8") as f:
        return json.load(f)


def _humanize_origin_value(value: str | None) -> str:
    """Turn a kebab-case contentOrigin value into readable prose.

    Examples (see PRD 17.3 book.contentOrigin):
        "human"                           -> "human"
        "ai-generated-and-vector-cleaned" -> "AI-generated and vector-cleaned"
        "not-applicable"                  -> "not-applicable"
    """
    if not value:
        return "not specified"
    clauses = value.split("-and-")
    clauses = [
        re.sub(r"(?<![A-Za-z])ai(?![A-Za-z])", "AI", clause, flags=re.IGNORECASE)
        for clause in clauses
    ]
    return " and ".join(clauses)


def content_origin_line(content_origin: dict) -> str:
    """Build the copyright-page content-origin disclosure line from book.json."""
    text_origin = _humanize_origin_value(content_origin.get("text"))
    images_origin = _humanize_origin_value(content_origin.get("images"))
    return f"Text: {text_origin}. Images: {images_origin}."


def margins_for_page(page_number: int) -> tuple[float, float, float, float]:
    """Return (left, right, top, bottom) safe-area margins in points.

    Page 1 is a right/recto page. Physical parity (PRD 17.10): odd page
    numbers are recto (right) pages, even page numbers are verso (left)
    pages. On a recto page the spine ("inside") is on the left; on a verso
    page the spine is on the right (PRD 18.6).
    """
    inside_pt = SAFE_MARGINS_IN["inside"] * 72.0
    outside_pt = SAFE_MARGINS_IN["outside"] * 72.0
    top_pt = SAFE_MARGINS_IN["top"] * 72.0
    bottom_pt = SAFE_MARGINS_IN["bottom"] * 72.0
    is_recto = page_number % 2 == 1
    if is_recto:
        left, right = inside_pt, outside_pt
    else:
        left, right = outside_pt, inside_pt
    return left, right, top_pt, bottom_pt


def wrap_text(text: str, font_name: str, font_size: float, max_width: float) -> list[str]:
    """Greedily wrap text into lines no wider than max_width for font/size."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


class PageWriter:
    """Draws text for one page, confined to that page's safe area.

    Every draw call is checked against the page's safe-area box computed
    from SAFE_MARGINS_IN + margins_for_page(); a line that would land
    outside it raises ValueError instead of silently violating the safe
    area (PRD 17.10 safe margins, hard requirement in this script's brief).
    """

    def __init__(self, c: pdfcanvas.Canvas, page_number: int):
        self.c = c
        self.page_number = page_number
        left, right, top, bottom = margins_for_page(page_number)
        self.content_left = left
        self.content_right = PAGE_WIDTH_PT - right
        self.content_width = self.content_right - self.content_left
        self.content_top = PAGE_HEIGHT_PT - top
        self.content_bottom = bottom
        self.center_x = self.content_left + self.content_width / 2.0

    def _check_y(self, y: float) -> None:
        if not (self.content_bottom - 1e-6 <= y <= self.content_top + 1e-6):
            raise ValueError(
                f"page {self.page_number}: y={y:.2f}pt is outside the safe "
                f"area [{self.content_bottom:.2f}, {self.content_top:.2f}]pt"
            )

    def centred(self, y: float, text: str, font: str, size: float, gray: float = 0.0) -> None:
        self._check_y(y)
        width = pdfmetrics.stringWidth(text, font, size)
        if width > self.content_width + 1e-6:
            raise ValueError(
                f"page {self.page_number}: line {text!r} ({width:.1f}pt) "
                f"exceeds the safe width ({self.content_width:.1f}pt)"
            )
        self.c.setFillGray(gray)
        self.c.setFont(font, size)
        self.c.drawCentredString(self.center_x, y, text)

    def left_aligned(self, y: float, text: str, font: str, size: float, gray: float = 0.0) -> None:
        self._check_y(y)
        width = pdfmetrics.stringWidth(text, font, size)
        if width > self.content_width + 1e-6:
            raise ValueError(
                f"page {self.page_number}: line {text!r} ({width:.1f}pt) "
                f"exceeds the safe width ({self.content_width:.1f}pt)"
            )
        self.c.setFillGray(gray)
        self.c.setFont(font, size)
        self.c.drawString(self.content_left, y, text)

    def centred_wrapped(
        self, y: float, text: str, font: str, size: float, leading: float, gray: float = 0.0
    ) -> float:
        """Draw text wrapped to the safe width, centred; returns the y below the last line."""
        for line in wrap_text(text, font, size, self.content_width):
            self.centred(y, line, font, size, gray)
            y -= leading
        return y


def build_front_matter(book_dir: Path, out_path: Path) -> None:
    register_fonts()
    book = load_book(book_dir)
    title = book["book"]["title"]
    subtitle = book["book"].get("subtitle") or ""
    content_origin = book["book"].get("contentOrigin") or {}
    year = date.today().year

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # invariant=1: no embedded creation timestamp / doc id noise, so the same
    # book.json always produces the same PDF bytes (this script is meant to
    # be deterministic).
    #
    # initialFontName="Vera": ReportLab always emits one harmless,
    # glyph-free "BT /F.. Tf .. TL ET" boilerplate at the top of every
    # page's content stream using this setting, which defaults to
    # "Helvetica" and would otherwise leave an unused but still
    # system-substituted /Helvetica entry in every page's font Resources.
    # Pointing it at our own embedded "Vera" face instead means Helvetica
    # never appears anywhere in the document, not even unused (PRD 17.11:
    # MUST NOT fall back to an unconfigured system font).
    c = pdfcanvas.Canvas(
        str(out_path),
        pagesize=(PAGE_WIDTH_PT, PAGE_HEIGHT_PT),
        invariant=1,
        initialFontName="Vera",
        initialFontSize=12,
        initialLeading=14.4,
    )
    c.setTitle(title)
    c.setSubject(subtitle)

    # ---- Page 1: title page (recto) ----
    pw = PageWriter(c, 1)
    y = PAGE_HEIGHT_PT * 0.46
    y = pw.centred_wrapped(y, title, "Vera-Bold", 30, 36)
    y -= 18
    if subtitle:
        pw.centred_wrapped(y, subtitle, "Vera", 15, 21)
    c.showPage()

    # ---- Page 2: copyright page (verso) ----
    pw = PageWriter(c, 2)
    y = pw.content_top - 220
    copyright_lines = [
        f"Copyright © {year} {title}.",
        "All rights reserved.",
        f"This interior was generated deterministically by {GENERATOR_LABEL}.",
        content_origin_line(content_origin),
    ]
    for line in copyright_lines:
        y = pw.centred_wrapped(y, line, "Vera", 10.5, 15)
        y -= 8
    c.showPage()

    # ---- Page 3: how to play (recto) ----
    pw = PageWriter(c, 3)
    y = pw.content_top - 6
    pw.centred(y, "HOW TO PLAY", "Vera-Bold", 20)
    y -= 40
    how_to_play_steps = [
        "Follow a path from Jim to the candy bucket.",
        "You may not pass through a wall.",
        "Collect as much candy as you can on the way.",
        "Tick one box for every candy you collect.",
        "Write your total in the Total box.",
        "Compare your total with the “Best possible” score.",
        "Answers are in the back of the book.",
    ]
    for i, step in enumerate(how_to_play_steps, start=1):
        y = pw.centred_wrapped(y, f"{i}. {step}", "Vera", 12.5, 20)
        y -= 6
    c.showPage()

    # ---- Page 4: dedication / from-the-author page (verso) ----
    pw = PageWriter(c, 4)
    y = PAGE_HEIGHT_PT * 0.56
    dedication_lines = [
        "For every trick-or-treater who loves a good puzzle,",
        "and for the grown-ups who walk the block beside them.",
        "Happy hunting, and happy Halloween.",
    ]
    for line in dedication_lines:
        y = pw.centred_wrapped(y, line, "Vera-Italic", 13, 20)
    c.showPage()

    # ---- Page 5: half-title page (recto) ----
    # NOTE (PRD 18.7): front matter MUST change two pages at a time -- a
    # one-page change flips every story/maze spread in the rest of the
    # book. Pages 1-4 above are a natural 4-page core; this half-title page
    # is the deliberate 5th page that makes the front matter's page count
    # ODD (so scene 1's story page lands on an even/left page, per 18.7).
    # If this page is ever dropped, drop or add one MORE page alongside it
    # -- never change this file's page count by exactly one.
    pw = PageWriter(c, 5)
    pw.centred_wrapped(PAGE_HEIGHT_PT * 0.5, title, "Vera-Bold", 24, 30)
    c.showPage()

    c.save()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--book",
        type=Path,
        default=DEFAULT_BOOK_DIR,
        help="Book package directory containing book.json (default: %(default)s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output PDF path (default: <book>/front-matter.pdf)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    book_dir: Path = args.book
    out_path: Path = args.out if args.out is not None else book_dir / "front-matter.pdf"
    build_front_matter(book_dir, out_path)
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
