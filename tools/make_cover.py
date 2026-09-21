#!/usr/bin/env python3
"""Build the KDP paperback cover wrap for a book package.

A wrap is one PDF holding back cover, spine and front cover side by side, sized
to the trim plus bleed plus a spine whose width comes from the page count. The
page count is read out of the built interior rather than typed in, because a
spine measured against a stale number is a cover that arrives folded in the
wrong place and cannot be fixed in the file.

Art is supplied per face as a full-bleed image. Each face is scaled to *cover*
its panel, which crops a little off the long edge rather than leaving a band of
background; the crop lands inside the bleed, which is the part that gets
trimmed away.

The barcode area is cleared to white. Amazon prints its own barcode over the
lower outer corner of the back cover and only asks that the area be free of
anything that matters, so the file states the clearance rather than hoping the
artwork happens to have it.

Usage:
    uv run python tools/make_cover.py --book books/<book-id>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from maze_book.rendering.svg_to_pdf import (  # noqa: E402
    BODY_FONT,
    TITLE_FONT,
    register_fonts,
)

PT_PER_IN = 72.0

#: KDP bleed on the three outer edges of each face, and around the whole wrap.
BLEED_IN = 0.125

#: Caliper per page, in inches, by KDP paper stock. A 112-page interior on white
#: stock is a 0.25 in spine, which is thin but thick enough for KDP to allow
#: spine text (their floor is 100 pages).
CALIPER_IN = {"white": 0.002252, "cream": 0.0025, "color": 0.002347}

#: Amazon's barcode block, and how far it sits from the trimmed corner.
BARCODE_IN = (2.0, 1.2)
BARCODE_INSET_IN = 0.25

#: Spine text needs this much clear on each side of the spine fold.
SPINE_TEXT_CLEARANCE_IN = 0.0625


def load_book(book_dir: Path) -> dict:
    return json.loads((book_dir / "book.json").read_text(encoding="utf-8"))


def interior_page_count(pdf_path: Path) -> int:
    """Page count of the built interior, read from the PDF itself."""
    from pypdf import PdfReader

    if not pdf_path.is_file():
        raise SystemExit(
            f"interior not found at {pdf_path}\n"
            f"run `uv run maze-book book build <package>` first: the spine width "
            f"is derived from the real page count, not from book.json"
        )
    return len(PdfReader(str(pdf_path)).pages)


def draw_cover_image(
    canvas: pdfcanvas.Canvas,
    image_path: Path,
    *,
    box: tuple[float, float, float, float],
) -> None:
    """Draw ``image_path`` scaled to cover ``box``, centred, cropping the excess."""
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    with Image.open(image_path) as image:
        source_w, source_h = image.size
    scale = max(width / source_w, height / source_h)
    drawn_w, drawn_h = source_w * scale, source_h * scale

    canvas.saveState()
    canvas.rect(x0, y0, width, height, stroke=0, fill=0)
    path = canvas.beginPath()
    path.rect(x0, y0, width, height)
    canvas.clipPath(path, stroke=0, fill=0)
    canvas.drawImage(
        ImageReader(str(image_path)),
        x0 - (drawn_w - width) / 2.0,
        y0 - (drawn_h - height) / 2.0,
        width=drawn_w,
        height=drawn_h,
        mask=None,
    )
    canvas.restoreState()


def build_cover(book_dir: Path, out_path: Path, *, paper: str = "white") -> dict:
    register_fonts(REPO / "fonts")
    book = load_book(book_dir)
    meta = book["book"]
    cover = book.get("cover") or {}
    artifacts = REPO / (cover.get("sourceDir") or "artifacts")

    trim_w = float(book["print"]["trimWidthIn"])
    trim_h = float(book["print"]["trimHeightIn"])
    pages = interior_page_count(
        REPO / "output" / meta["id"] / "book-interior.pdf"
    )
    spine_in = pages * CALIPER_IN[paper]

    wrap_w = (trim_w * 2 + spine_in + BLEED_IN * 2) * PT_PER_IN
    wrap_h = (trim_h + BLEED_IN * 2) * PT_PER_IN
    bleed = BLEED_IN * PT_PER_IN
    spine = spine_in * PT_PER_IN
    face = trim_w * PT_PER_IN

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas = pdfcanvas.Canvas(
        str(out_path), pagesize=(wrap_w, wrap_h), invariant=1,
        initialFontName=BODY_FONT, initialFontSize=12,
    )
    canvas.setTitle(f"{meta['title']} -- cover")

    # Back cover: the left panel, running into the left and outer bleed.
    back_box = (0.0, 0.0, bleed + face, wrap_h)
    draw_cover_image(canvas, artifacts / cover["back"], box=back_box)

    # Front cover: the right panel.
    front_x = bleed + face + spine
    draw_cover_image(
        canvas, artifacts / cover["front"], box=(front_x, 0.0, wrap_w, wrap_h)
    )

    # Spine: a flat colour taken from the artwork rather than guessed, so the
    # fold does not show as a seam against either face.
    spine_colour = cover.get("spineColor") or "#5B2E91"
    canvas.setFillColor(spine_colour)
    canvas.rect(bleed + face, 0.0, spine, wrap_h, stroke=0, fill=1)

    if spine_in >= 0.1875:
        _draw_spine_text(canvas, meta, x=bleed + face, spine=spine, wrap_h=wrap_h)

    _clear_barcode(canvas, back_box, trim_h=trim_h)

    canvas.showPage()
    canvas.save()
    return {
        "pages": pages,
        "spineIn": round(spine_in, 4),
        "wrapIn": (round(wrap_w / PT_PER_IN, 4), round(wrap_h / PT_PER_IN, 4)),
        "path": str(out_path),
    }


def _draw_spine_text(
    canvas: pdfcanvas.Canvas, meta: dict, *, x: float, spine: float, wrap_h: float
) -> None:
    """Title up the spine, sized to the clearance the fold leaves."""
    usable = spine - 2 * SPINE_TEXT_CLEARANCE_IN * PT_PER_IN
    size = max(6.0, min(11.0, usable * 0.8))
    canvas.saveState()
    canvas.setFillColorRGB(1, 1, 1)
    canvas.translate(x + spine / 2.0, wrap_h / 2.0)
    canvas.rotate(90)
    canvas.setFont(TITLE_FONT, size)
    canvas.drawCentredString(0, -size / 3.0, meta["title"])
    canvas.restoreState()


def _clear_barcode(
    canvas: pdfcanvas.Canvas, back_box: tuple[float, float, float, float], *, trim_h: float
) -> None:
    """White out Amazon's barcode block in the back cover's lower spine-side corner.

    That corner is where KDP's own template puts it, and where this book's back
    cover art already leaves a gap -- the gap is just smaller than the block, so
    it is extended rather than moved.
    """
    _, _, back_right, _ = back_box
    width, height = (v * PT_PER_IN for v in BARCODE_IN)
    inset = BARCODE_INSET_IN * PT_PER_IN
    bleed = BLEED_IN * PT_PER_IN
    x = back_right - inset - width
    y = bleed + inset
    canvas.setFillColorRGB(1, 1, 1)
    canvas.rect(x, y, width, height, stroke=0, fill=1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--paper", choices=sorted(CALIPER_IN), default="white")
    args = parser.parse_args(argv)

    book_id = load_book(args.book)["book"]["id"]
    out = args.out or REPO / "output" / book_id / "cover" / "cover-wrap.pdf"
    result = build_cover(args.book, out, paper=args.paper)
    print(
        f"{result['pages']} pages -> spine {result['spineIn']} in; "
        f"wrap {result['wrapIn'][0]} x {result['wrapIn'][1]} in"
    )
    print(f"wrote {result['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
