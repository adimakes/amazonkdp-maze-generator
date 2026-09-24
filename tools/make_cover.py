#!/usr/bin/env python3
"""Build the KDP paperback cover wrap for a book package.

A wrap is one PDF holding back cover, spine and front cover side by side, sized
to the trim plus bleed plus a spine whose width comes from the page count. The
page count is read out of the built interior rather than typed in, because a
spine measured against a stale number is a cover that arrives folded in the
wrong place and cannot be fixed in the file.

Art is supplied per face as a full-bleed image, one per folder:
``input/artwork/cover-front/`` and ``input/artwork/cover-back/``. Whatever
single image is in each folder is used, whatever it is called, so replacing a
cover is dropping a file in and deleting the old one. Each face is scaled to
*cover* its panel, which crops a little off the long edge rather than leaving a
band of background; the crop lands inside the bleed, which is the part that
gets trimmed away.

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

#: KDP allows spine text from this many pages. The rule is the page count, not
#: the spine width: 0.1875 in of spine is 83 pages on white paper, and text on
#: an 83-page spine is text KDP rejects.
SPINE_TEXT_MIN_PAGES = 100

#: How far the flat spine colour runs onto each cover, so a small bind shift
#: shows spine colour at a cover's edge rather than cover art on the spine.
SPINE_OVERLAP_IN = 0.06

#: One folder per face, under the book's artwork folder. The folder is the
#: contract, not the file name.
FACE_DIRS = {"front": "cover-front", "back": "cover-back"}
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")

#: KDP asks for 300 dpi. Below the floor a cover prints visibly soft, which is
#: what a 1K image stretched over 8.6 in does (about 100 dpi).
TARGET_DPI = 300
MIN_DPI = 150

#: How much of a sample card the real maze fills, as a share of the card's
#: shorter side. The card is whited out first, so the artwork's own invented
#: maze cannot show around the edges of the real one.
CARD_MAZE_FILL = 0.94


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


def face_image(artwork: Path, face: str) -> Path:
    """The one image in this face's folder, whatever it is called.

    Exactly one, and an error otherwise: picking the newest of two would make
    the cover depend on file timestamps, which a fresh clone does not keep.
    """
    folder = artwork / FACE_DIRS[face]
    images = sorted(
        path for path in (folder.iterdir() if folder.is_dir() else ())
        if path.is_file() and not path.name.startswith(".")
        and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if len(images) == 1:
        return images[0]
    found = ", ".join(path.name for path in images) or "nothing"
    raise SystemExit(
        f"{folder}: expected exactly one {face} cover image, found {found}.\n"
        f"Drop the {face} artwork into that folder and remove any older version."
    )


def fitted(
    image_size: tuple[int, int], box: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """Where an image scaled to cover ``box`` lands: (x, y, width, height), PDF points.

    One function for drawing the art and for finding things on it, so the
    sample cards are located on the image as drawn -- cropped top and bottom
    for 3:4 art -- rather than on the panel it was fitted into.
    """
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    source_w, source_h = image_size
    scale = max(width / source_w, height / source_h)
    drawn_w, drawn_h = source_w * scale, source_h * scale
    return x0 - (drawn_w - width) / 2.0, y0 - (drawn_h - height) / 2.0, drawn_w, drawn_h


def draw_cover_image(
    canvas: pdfcanvas.Canvas,
    image_path: Path,
    *,
    box: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Draw ``image_path`` scaled to cover ``box``, centred, cropping the excess.

    Returns where the whole image landed, as :func:`fitted` computes it.
    """
    x0, y0, x1, y1 = box
    with Image.open(image_path) as image:
        placement = fitted(image.size, box)
    px, py, drawn_w, drawn_h = placement

    canvas.saveState()
    path = canvas.beginPath()
    path.rect(x0, y0, x1 - x0, y1 - y0)
    canvas.clipPath(path, stroke=0, fill=0)
    canvas.drawImage(
        ImageReader(str(image_path)), px, py, width=drawn_w, height=drawn_h, mask=None
    )
    canvas.restoreState()
    return placement


def effective_dpi(image_path: Path, placement: tuple[float, float, float, float]) -> float:
    with Image.open(image_path) as image:
        return image.width / (placement[2] / PT_PER_IN)


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

    front_art = face_image(artwork, "front")
    back_art = face_image(artwork, "back")

    # Back cover: the left panel, running into the left and outer bleed.
    back_box = (0.0, 0.0, bleed + face, wrap_h)
    back_placement = draw_cover_image(canvas, back_art, box=back_box)

    # Front cover: the right panel.
    front_x = bleed + face + spine
    front_placement = draw_cover_image(
        canvas, front_art, box=(front_x, 0.0, wrap_w, wrap_h)
    )

    dpi = {
        "front": effective_dpi(front_art, front_placement),
        "back": effective_dpi(back_art, back_placement),
    }
    soft = [f"{name} {value:.0f} dpi" for name, value in dpi.items() if value < MIN_DPI]
    if soft:
        raise SystemExit(
            f"cover art too small to print: {', '.join(soft)} (minimum {MIN_DPI}, "
            f"KDP asks for {TARGET_DPI}). Upscale the image and drop it in again."
        )

    # Spine: a flat colour taken from the artwork rather than guessed, so the
    # fold does not show as a seam against either face. It runs a little wide on
    # both sides, because a bind shift of a millimetre then puts spine colour on
    # the edge of a cover instead of front-cover art on the spine.
    spine_colour = cover.get("spineColor") or "#5B2E91"
    overlap = SPINE_OVERLAP_IN * PT_PER_IN
    canvas.setFillColor(spine_colour)
    canvas.rect(bleed + face - overlap, 0.0, spine + 2 * overlap, wrap_h, stroke=0, fill=1)

    if pages >= SPINE_TEXT_MIN_PAGES:
        _draw_spine_text(
            canvas, meta, x=bleed + face, spine=spine, wrap_h=wrap_h,
            downward=cover.get("spineTextDirection", "top-to-bottom") == "top-to-bottom",
        )

    _place_real_mazes(canvas, book_dir, cover, placement=back_placement, clip=back_box)
    _clear_barcode(canvas, back_box)

    canvas.showPage()
    canvas.save()
    return {
        "front": front_art.name,
        "back": back_art.name,
        "dpi": {name: round(value) for name, value in dpi.items()},
        "pages": pages,
        "spineIn": round(spine_in, 4),
        "wrapIn": (round(wrap_w / PT_PER_IN, 4), round(wrap_h / PT_PER_IN, 4)),
        "path": str(out_path),
    }


def _place_real_mazes(
    canvas: pdfcanvas.Canvas,
    book_dir: Path,
    cover: dict,
    *,
    placement: tuple[float, float, float, float],
    clip: tuple[float, float, float, float],
) -> None:
    """Paste real pages from this book over the back cover's sample cards.

    The supplied artwork draws four invented mazes: coloured candy, round dots,
    a boy icon, on white cards. The interior is flat monochrome line art with
    silhouette icons. A parent compares the back cover to page 7 and concludes
    the samples were mocked up -- which is the complaint that costs stars, and
    the only honest fix is to show what is actually inside.

    It also makes the EASY / MEDIUM / HARD / EXPERT labels true, since the four
    samples are drawn from four different bands.

    Each card is the paper area of one sample card, as fractions of the image
    (y downward). The card is whited out and the real maze centred on it, so
    the artwork's invented maze -- which fills the card -- never shows at the
    edges of the real one.
    """
    samples = cover.get("samples")
    cards = cover.get("cards")
    if not samples and not cards:
        return
    if len(samples or ()) != len(cards or ()):
        raise SystemExit(
            f"cover.cards has {len(cards or ())} card(s) but cover.samples names "
            f"{len(samples or ())} maze(s); they pair up one to one"
        )

    ix, iy, iw, ih = placement
    canvas.saveState()
    path = canvas.beginPath()
    path.rect(clip[0], clip[1], clip[2] - clip[0], clip[3] - clip[1])
    canvas.clipPath(path, stroke=0, fill=0)
    for card, maze_index in zip(cards, samples):
        image = _maze_sample(book_dir, int(maze_index))
        if image is None:
            # Skipping would ship the artwork's invented maze on that card,
            # which is the exact complaint the real samples are there to stop.
            raise SystemExit(
                f"maze {maze_index} for a back-cover card is not in the built "
                f"interior; run `uv run maze-book book build {book_dir}` first"
            )
        left, right = ix + card[0] * iw, ix + card[2] * iw
        top, bottom = iy + (1.0 - card[1]) * ih, iy + (1.0 - card[3]) * ih
        canvas.setFillColorRGB(1, 1, 1)
        canvas.rect(left, bottom, right - left, top - bottom, stroke=0, fill=1)
        size = min(right - left, top - bottom) * CARD_MAZE_FILL
        canvas.drawImage(
            ImageReader(image),
            (left + right - size) / 2.0, (bottom + top - size) / 2.0,
            width=size, height=size, mask=None,
        )
    canvas.restoreState()


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
    canvas: pdfcanvas.Canvas,
    meta: dict,
    *,
    x: float,
    spine: float,
    wrap_h: float,
    downward: bool = True,
) -> None:
    """Title along the spine, sized to the clearance the fold leaves.

    Top to bottom by default, the US and UK convention: with the book lying face
    up the title reads the right way round. Bottom to top is the continental
    European one -- Spain, Germany, France -- and a book for those shelves sets
    ``cover.spineTextDirection`` to say so.
    """
    usable = spine - 2 * SPINE_TEXT_CLEARANCE_IN * PT_PER_IN
    size = max(6.0, min(11.0, usable * 0.8))
    canvas.saveState()
    canvas.setFillColorRGB(1, 1, 1)
    canvas.translate(x + spine / 2.0, wrap_h / 2.0)
    canvas.rotate(-90 if downward else 90)
    canvas.setFont(TITLE_FONT, size)
    canvas.drawCentredString(0, -size / 3.0, meta["title"])
    canvas.restoreState()


def _clear_barcode(
    canvas: pdfcanvas.Canvas, back_box: tuple[float, float, float, float]
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
    for face in ("front", "back"):
        dpi = result["dpi"][face]
        note = "" if dpi >= TARGET_DPI else f"  (below the {TARGET_DPI} dpi KDP asks for)"
        print(f"{face:<6} {result[face]}  {dpi} dpi{note}")
    print(f"wrote {result['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
