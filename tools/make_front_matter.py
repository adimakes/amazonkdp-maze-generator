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
from reportlab.pdfgen import canvas as pdfcanvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from maze_book.rendering.svg_to_pdf import (  # noqa: E402
    BODY_FONT,
    TITLE_FONT,
    register_fonts as register_interior_fonts,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = REPO_ROOT / "fonts"
DEFAULT_BOOK_DIR = REPO_ROOT / "books" / "jims-halloween-maze-adventure"

# 8.5 x 11 in trim at 72 pt/in -> 612 x 792 pt (PRD 17.3 print.trimWidthIn/trimHeightIn).
PAGE_WIDTH_PT = 8.5 * 72.0
PAGE_HEIGHT_PT = 11.0 * 72.0

# PRD 17.3 print.safeMarginsIn for this book. "inside" is the spine-side
# margin, "outside" is the trim-edge-side margin (PRD 18.6/17.10).
SAFE_MARGINS_IN = {"inside": 0.625, "outside": 0.5, "top": 0.5, "bottom": 0.5}

GENERATOR_LABEL = "maze-book v1.0.0"


def register_fonts() -> None:
    """Register the interior's own faces, from the interior's own list.

    17.11: bundled faces, registered by absolute path, raising rather than
    falling back to an unconfigured system font. The list itself comes from
    ``rendering.svg_to_pdf`` rather than being restated here, because a front
    matter set in a different face from the pages behind it is exactly what a
    second copy of the list produces.
    """
    register_interior_fonts(FONTS_DIR)


def load_book(book_dir: Path) -> dict:
    """Load book.json for the given book package directory."""
    book_json_path = book_dir / "book.json"
    if not book_json_path.is_file():
        raise FileNotFoundError(f"book.json not found at {book_json_path}")
    with book_json_path.open(encoding="utf-8") as f:
        return json.load(f)


#: Used when a package supplies none. Deliberately nameless: a default that
#: mentions Jim would put one book's character into every other book's front
#: matter, which is the same mistake as branching on the book id in src/.
DEFAULT_HOW_TO_PLAY = [
    "Find a path from the start to the finish.",
    "You may not cross a wall.",
    "Collect as much as you can on the way.",
    "Tick one box for everything you collect.",
    "Write your total in the Total box.",
    "Compare it with the \u201cBest possible\u201d score.",
    "Answers are in the back of the book.",
]

DEFAULT_DEDICATION = ["For everyone who likes a good puzzle."]


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

    #: Fraction of the font size a capital rises above the baseline, and a
    #: descender drops below it. Generous on purpose: this is a bound, and the
    #: alternative is asking the font for metrics at every draw call.
    ASCENT_RATIO = 0.78
    DESCENT_RATIO = 0.25

    def _check_y(self, y: float, size: float = 0.0) -> None:
        """Bounds-check a *baseline*, allowing for the glyphs above and below it.

        Checking the baseline alone passes a heading whose capitals stand above
        the top margin, which is how the how-to-play page came to break the very
        margin this class exists to enforce.
        """
        top = y + size * self.ASCENT_RATIO
        bottom = y - size * self.DESCENT_RATIO
        if not (self.content_bottom - 1e-6 <= bottom and top <= self.content_top + 1e-6):
            raise ValueError(
                f"page {self.page_number}: a {size:g}pt line on baseline y={y:.2f}pt "
                f"reaches [{bottom:.2f}, {top:.2f}]pt, outside the safe area "
                f"[{self.content_bottom:.2f}, {self.content_top:.2f}]pt"
            )

    def centred(self, y: float, text: str, font: str, size: float, gray: float = 0.0) -> None:
        self._check_y(y, size)
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
        self._check_y(y, size)
        width = pdfmetrics.stringWidth(text, font, size)
        if width > self.content_width + 1e-6:
            raise ValueError(
                f"page {self.page_number}: line {text!r} ({width:.1f}pt) "
                f"exceeds the safe width ({self.content_width:.1f}pt)"
            )
        self.c.setFillGray(gray)
        self.c.setFont(font, size)
        self.c.drawString(self.content_left, y, text)

    def numbered(
        self, y: float, index: int, text: str, font: str, size: float, leading: float
    ) -> float:
        """A hanging-indent numbered item: the number in its own column, the
        text wrapped against a common left edge rather than under the number."""
        gutter = pdfmetrics.stringWidth("00. ", font, size)
        self._check_y(y, size)
        self.c.setFillGray(0.0)
        self.c.setFont(font, size)
        self.c.drawRightString(self.content_left + gutter - 6.0, y, f"{index}.")
        for line in wrap_text(text, font, size, self.content_width - gutter):
            self._check_y(y, size)
            self.c.drawString(self.content_left + gutter, y, line)
            y -= leading
        return y

    def centred_wrapped(
        self, y: float, text: str, font: str, size: float, leading: float, gray: float = 0.0
    ) -> float:
        """Draw text wrapped to the safe width, centred; returns the y below the last line."""
        for line in wrap_text(text, font, size, self.content_width):
            self.centred(y, line, font, size, gray)
            y -= leading
        return y


#: Height of one row of key icons, and the space around it.
KEY_ICON_PT = 34.0
KEY_ROW_GAP_PT = 16.0


def _draw_icon_key(
    c: pdfcanvas.Canvas, pw: "PageWriter", book_dir: Path, y: float
) -> float:
    """Print what counts and what does not, using the book's own artwork.

    The whole scoring loop rests on a child telling a wrapped sweet from a bat,
    and both are black silhouettes of similar weight. Size carries most of that
    distinction on the maze page; this page says it in words once, with the
    actual icons, on a page that was two-thirds blank anyway.
    """
    from maze_book.assets.catalog import load_catalog
    from maze_book.model.book_config import load_book_config
    from maze_book.model.profile import load_profile
    from maze_book.rendering.svg import AssetGeometryCache
    from maze_book.rendering.svg_to_pdf import PdfFrame, place_document

    catalog = load_catalog(load_book_config(book_dir))
    cache = AssetGeometryCache()
    frame = PdfFrame(c, PAGE_HEIGHT_PT)

    # Drawn at the ratio the maze pages actually use, because the key's job is
    # to teach that ratio. Printing both rows the same size would teach the
    # opposite of what the page then says.
    profile = load_profile(REPO_ROOT / "profiles", load_book_config(book_dir).book.profile_id)
    band = profile.bands[0]
    ratio = band.dead_end_scale / band.collectible_scale

    rows = (
        ("COLLECT THESE", sorted(catalog.collectibles, key=lambda a: a.asset_id), 1.0),
        ("THESE ARE JUST SPOOKY", sorted(catalog.dead_ends, key=lambda a: a.asset_id), ratio),
    )
    for heading, assets, scale in rows:
        if not assets:
            continue
        pw.left_aligned(y, heading, TITLE_FONT, 12)
        y -= KEY_ICON_PT + 8.0
        icon = KEY_ICON_PT * scale
        step = min(KEY_ICON_PT + 14.0, pw.content_width / max(len(assets), 1))
        for column, asset in enumerate(assets):
            x = pw.content_left + column * step + (KEY_ICON_PT - icon) / 2.0
            top = PAGE_HEIGHT_PT - y - KEY_ICON_PT + (KEY_ICON_PT - icon) / 2.0
            place_document(
                frame, cache.get(asset.path), (x, top, x + icon, top + icon)
            )
        y -= KEY_ROW_GAP_PT
    pw.left_aligned(
        y, "The spooky ones are smaller, and they are worth nothing.", BODY_FONT, 11
    )
    return y - 20.0


#: The character, under the title. A title page with nothing on it is the
#: first page anyone opens, and it was two lines of type on a blank sheet.
TITLE_FIGURE_IN = 2.4


def _draw_title_figure(c: pdfcanvas.Canvas, book_dir: Path, y: float) -> None:
    from maze_book.assets.catalog import load_catalog
    from maze_book.model.book_config import load_book_config
    from maze_book.rendering.svg import AssetGeometryCache
    from maze_book.rendering.svg_to_pdf import PdfFrame, place_document

    catalog = load_catalog(load_book_config(book_dir))
    size = TITLE_FIGURE_IN * 72.0
    x = (PAGE_WIDTH_PT - size) / 2.0
    place_document(
        PdfFrame(c, PAGE_HEIGHT_PT),
        AssetGeometryCache().get(catalog.start.path),
        (x, y, x + size, y + size),
    )


def build_front_matter(book_dir: Path, out_path: Path) -> None:
    register_fonts()
    book = load_book(book_dir)
    meta = book["book"]
    title = meta["title"]
    subtitle = meta.get("subtitle") or ""
    content_origin = meta.get("contentOrigin") or {}
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
        initialFontName=BODY_FONT,
        initialFontSize=12,
        initialLeading=14.4,
    )
    c.setTitle(title)
    c.setSubject(subtitle)

    # ---- Page 1: copyright (recto) ----
    pw = PageWriter(c, 1)
    y = pw.content_top - 220
    holder = meta.get("author") or title
    copyright_lines = [
        f"Copyright © {year} {holder}.",
        "All rights reserved.",
        f"This interior was generated deterministically by {GENERATOR_LABEL}.",
    ]
    # KDP's AI disclosure is made in the publishing form, and is required
    # whether or not this line is printed. Printing it as well is a choice the
    # package makes, not something the format requires.
    if meta.get("printContentOrigin", True):
        copyright_lines.append(content_origin_line(content_origin))
    for line in copyright_lines:
        y = pw.centred_wrapped(y, line, BODY_FONT, 10.5, 15)
        y -= 8
    c.showPage()

    # ---- Page 2: how to play (verso) ----
    pw = PageWriter(c, 2)
    y = pw.content_top - 20 * PageWriter.ASCENT_RATIO
    pw.centred(y, "HOW TO PLAY", TITLE_FONT, 20)
    y -= 38

    # Left-aligned, in a block. A centred numbered list is the clearest
    # self-published tell in a book, and it is harder to read besides: a child
    # following steps wants every number in the same column.
    how_to_play_steps = book.get("content", {}).get("howToPlay") or DEFAULT_HOW_TO_PLAY
    for index, step in enumerate(how_to_play_steps, start=1):
        y = pw.numbered(y, index, step, BODY_FONT, 12.5, 20)
        y -= 6

    y = _draw_icon_key(c, pw, book_dir, y - 26)
    c.showPage()

    # ---- Page 3: meet the character (recto) ----
    # NOTE (PRD 18.7): front matter MUST have an ODD page count and MUST
    # change two pages at a time -- a one-page change flips every story/maze
    # spread in the rest of the book. This is the third of three: copyright,
    # how to play, meet the character.
    #
    # There is no title page. KDP prints the cover from a separate file, so a
    # title page inside the interior repeats the cover on the first page a
    # reader turns to. It went with the dedication, two at a time, and the
    # dedication's line moved here.
    pw = PageWriter(c, 3)
    meet = book.get("content", {}).get("meetPage")
    if meet:
        y = PAGE_HEIGHT_PT * 0.30
        y = pw.centred_wrapped(y, meet["title"], TITLE_FONT, 24, 30)
        y -= 14
        for line in meet.get("lines", []):
            y = pw.centred_wrapped(y, line, BODY_FONT, 14, 20)
        _draw_title_figure(c, book_dir, y + 40.0)
    else:
        pw.centred_wrapped(PAGE_HEIGHT_PT * 0.5, title, TITLE_FONT, 24, 30)
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
