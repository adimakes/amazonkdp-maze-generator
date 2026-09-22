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

#: How far the flat spine colour runs onto each cover, so a small bind shift
#: shows spine colour at a cover's edge rather than cover art on the spine.
SPINE_OVERLAP_IN = 0.06


def load_book(book_dir: Path) -> dict:
    return json.loads((book_dir / "input" / "book.json").read_text(encoding="utf-8"))


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
    artwork = book_dir / "input" / (cover.get("sourceDir") or "artwork")

    trim_w = float(book["print"]["trimWidthIn"])
    trim_h = float(book["print"]["trimHeightIn"])
    pages = interior_page_count(book_dir / "output" / "book-interior.pdf")
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
    draw_cover_image(canvas, artwork / cover["back"], box=back_box)

    # Front cover: the right panel.
    front_x = bleed + face + spine
    draw_cover_image(
        canvas, artwork / cover["front"], box=(front_x, 0.0, wrap_w, wrap_h)
    )

    # Spine: a flat colour taken from the artwork rather than guessed, so the
    # fold does not show as a seam against either face. It runs a little wide on
    # both sides, because a bind shift of a millimetre then puts spine colour on
    # the edge of a cover instead of front-cover art on the spine.
    spine_colour = cover.get("spineColor") or "#5B2E91"
    overlap = SPINE_OVERLAP_IN * PT_PER_IN
    canvas.setFillColor(spine_colour)
    canvas.rect(bleed + face - overlap, 0.0, spine + 2 * overlap, wrap_h, stroke=0, fill=1)

    if spine_in >= 0.1875:
        _draw_spine_text(canvas, meta, x=bleed + face, spine=spine, wrap_h=wrap_h)

    _place_real_mazes(canvas, book_dir, cover, box=back_box)
    _clear_barcode(canvas, back_box, trim_h=trim_h)

    canvas.showPage()
    canvas.save()
    return {
        "pages": pages,
        "spineIn": round(spine_in, 4),
        "wrapIn": (round(wrap_w / PT_PER_IN, 4), round(wrap_h / PT_PER_IN, 4)),
        "path": str(out_path),
    }


#: Where each sample card's maze sits inside the card, as fractions of the
#: card. Measured off the supplied artwork, and identical for all four cards.
CARD_MAZE_INSET = (0.177, 0.115, 0.668)  # x, y, side -- all of the card's width


def _place_real_mazes(
    canvas: pdfcanvas.Canvas,
    book_dir: Path,
    cover: dict,
    *,
    box: tuple[float, float, float, float],
) -> None:
    """Paste real pages from this book over the back cover's sample cards.

    The supplied artwork draws four invented mazes: coloured candy, round dots,
    a boy icon, on white cards. The interior is flat monochrome line art with
    silhouette icons. A parent compares the back cover to page 7 and concludes
    the samples were mocked up -- which is the complaint that costs stars, and
    the only honest fix is to show what is actually inside.

    It also makes the EASY / MEDIUM / HARD / EXPERT labels true, since the four
    samples are drawn from four different bands.
    """
    samples = cover.get("samples")
    cards = cover.get("cards")
    if not samples or not cards:
        return

    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    inset_x, inset_y, side = CARD_MAZE_INSET

    for card, maze_index in zip(cards, samples):
        image = _maze_sample(book_dir, int(maze_index))
        if image is None:
            continue
        # Card rectangles are fractions of the artwork, which fills the panel.
        cx0 = x0 + card[0] * width
        cy0 = y0 + (1.0 - card[3]) * height        # artwork y runs downward
        cw, ch = (card[2] - card[0]) * width, (card[3] - card[1]) * height
        size = side * cw
        px = cx0 + inset_x * cw
        py = cy0 + ch - inset_y * ch - size
        canvas.drawImage(
            ImageReader(image), px, py, width=size, height=size, mask=None
        )


def _maze_sample(book_dir: Path, maze_index: int):
    """One maze rendered as a square white image, or None if it is not built."""
    import io
    import subprocess
    import tempfile

    plan_path = book_dir / "build" / "page-plan.json"
    if not plan_path.is_file():
        return None
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    page = next(
        (p["pageNumber"] for p in plan["pages"]
         if p["kind"] == "maze" and p.get("mazeIndex") == maze_index),
        None,
    )
    if page is None:
        return None

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["pdftoppm", "-r", "260", "-png", "-f", str(page), "-l", str(page),
             str(book_dir / "output" / "book-interior.pdf"), f"{tmp}/s"],
            check=True, capture_output=True,
        )
        rendered = next(Path(tmp).glob("*.png"), None)
        if rendered is None:
            return None
        image = Image.open(rendered).convert("L")
        image.load()

    # Crop to the maze square the page layout defines, not to "all the ink":
    # the ink on that page starts at a decoration in the top band and ends below
    # the tally, so an ink-bounds crop puts tick boxes on the cover.
    from maze_book.model.book_config import load_book_config
    from maze_book.rendering.maze_page import MAZE_TOP_OFFSET_IN
    from maze_book.rendering.page import PageMetrics

    config = load_book_config(book_dir)
    metrics = PageMetrics.from_print_spec(config.print)
    live_x0, live_y0, live_x1, _ = metrics.live_box("right")
    square = config.layout.maze_square_in * PT_PER_IN
    left = live_x0 + ((live_x1 - live_x0) - square) / 2.0
    top = live_y0 + MAZE_TOP_OFFSET_IN * PT_PER_IN

    scale = image.width / (config.print.trim_width_in * PT_PER_IN)
    crop = tuple(
        int(round(value * scale))
        for value in (left, top, left + square, top + square)
    )
    cropped = image.crop(crop)
    buffer = io.BytesIO()
    cropped.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


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

    out = args.out or args.book / "output" / "book-cover.pdf"
    result = build_cover(args.book, out, paper=args.paper)
    print(
        f"{result['pages']} pages -> spine {result['spineIn']} in; "
        f"wrap {result['wrapIn'][0]} x {result['wrapIn'][1]} in"
    )
    print(f"wrote {result['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
