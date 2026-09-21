"""The minimal internal SVG->ReportLab converter (PRD 17.11, 18.9).

18.9 rejects ``svglib`` explicitly: "a second SVG code path is how SVG and PDF
output diverge". This module is the first path's other consumer, not a second
one. It reads ``assets.svg_subset`` -- the same parser the validator measures
with -- so a file that validates is by construction convertible, and anything
outside the subset raises here exactly as it would there.

Two coordinate conventions meet in this file and nowhere else. Maze geometry is
y-down with the origin at the top-left, matching SVG and the row-major-top-left
cell convention; PDF is y-up from the bottom-left. Every drawing helper takes the
page height and flips once, so no caller ever writes ``page_height - y`` itself.
That arithmetic scattered across renderers is how one element ends up mirrored
about the page centre while everything around it is right.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from reportlab.pdfgen.canvas import FILL_EVEN_ODD, FILL_NON_ZERO, Canvas

from ..assets.svg_subset import ParsedSvg
from ..errors import RenderingError
from .geometry import Point, Segment

BLACK = 0.0


class PdfFrame:
    """A y-down drawing surface over a y-up ReportLab canvas.

    Everything is drawn in DeviceGray black (18.8 requires DeviceGray only and
    pure 0.0 ink), so there is no colour argument to get wrong.
    """

    __slots__ = ("canvas", "page_height")

    def __init__(self, canvas: Canvas, page_height: float) -> None:
        self.canvas = canvas
        self.page_height = page_height

    def y(self, value: float) -> float:
        return self.page_height - value

    def point(self, point: Point) -> Point:
        return (point[0], self.page_height - point[1])

    # -- primitives ---------------------------------------------------------

    def segments(self, segments: Sequence[Segment], width: float) -> None:
        """Wall runs. Butt caps would leave a notch at every corner where two
        runs meet, so the joint is closed by squaring the ends instead."""
        if not segments or width <= 0.0:
            return
        canvas = self.canvas
        canvas.saveState()
        canvas.setStrokeGray(BLACK)
        canvas.setLineWidth(width)
        canvas.setLineCap(2)   # projecting square
        canvas.setLineJoin(0)  # miter
        path = canvas.beginPath()
        for x0, y0, x1, y1 in segments:
            path.moveTo(x0, self.y(y0))
            path.lineTo(x1, self.y(y1))
        canvas.drawPath(path, stroke=1, fill=0)
        canvas.restoreState()

    def polyline(self, points: Sequence[Point], width: float) -> None:
        """A solution route: round caps and joins, so a turn reads as a single
        continuous line rather than as two segments meeting at a chipped corner."""
        if len(points) < 2 or width <= 0.0:
            return
        canvas = self.canvas
        canvas.saveState()
        canvas.setStrokeGray(BLACK)
        canvas.setLineWidth(width)
        canvas.setLineCap(1)
        canvas.setLineJoin(1)
        path = canvas.beginPath()
        first = self.point(points[0])
        path.moveTo(*first)
        for point in points[1:]:
            path.lineTo(*self.point(point))
        canvas.drawPath(path, stroke=1, fill=0)
        canvas.restoreState()

    def dots(self, centres: Iterable[Point], radius: float) -> None:
        if radius <= 0.0:
            return
        canvas = self.canvas
        canvas.saveState()
        canvas.setFillGray(BLACK)
        for x, y in centres:
            canvas.circle(x, self.y(y), radius, stroke=0, fill=1)
        canvas.restoreState()

    def rect(self, box: tuple[float, float, float, float], width: float) -> None:
        x0, y0, x1, y1 = box
        canvas = self.canvas
        canvas.saveState()
        canvas.setStrokeGray(BLACK)
        canvas.setLineWidth(width)
        canvas.rect(x0, self.y(y1), x1 - x0, y1 - y0, stroke=1, fill=0)
        canvas.restoreState()

    # -- assets -------------------------------------------------------------

    def document(self, document: ParsedSvg, *, scale: float, offset: Point) -> None:
        """Draw a parsed asset, placed by ``scale`` and ``offset``.

        Subpaths are grouped by fill rule and each group drawn as one path, so an
        ``evenodd`` hole stays attached to the body it punctures. Drawing them
        separately would fill the hole with the body's own ink.
        """
        groups: dict[str, list] = {}
        for item in document.paths:
            groups.setdefault(item.fill_rule, []).append(item.subpath)
        if not groups:
            return

        canvas = self.canvas
        canvas.saveState()
        canvas.setFillGray(BLACK)
        previous_mode = canvas._fillMode
        try:
            for rule, subpaths in groups.items():
                canvas._fillMode = FILL_EVEN_ODD if rule == "evenodd" else FILL_NON_ZERO
                path = canvas.beginPath()
                for subpath in subpaths:
                    start = self._place(subpath.start, scale, offset)
                    path.moveTo(*start)
                    for segment in subpath.segments:
                        path.curveTo(
                            *self._place(segment.p1, scale, offset),
                            *self._place(segment.p2, scale, offset),
                            *self._place(segment.p3, scale, offset),
                        )
                    path.close()
                canvas.drawPath(path, stroke=0, fill=1)
        finally:
            canvas._fillMode = previous_mode
            canvas.restoreState()

    def _place(self, point: Point, scale: float, offset: Point) -> Point:
        return (
            point[0] * scale + offset[0],
            self.y(point[1] * scale + offset[1]),
        )


def place_document(
    frame: PdfFrame, document: ParsedSvg, box: tuple[float, float, float, float]
) -> None:
    """Draw an asset to fill ``box``, which must be square.

    The subset fixes a square viewBox precisely so the renderer can scale by the
    box it is given rather than by the illustrator's canvas; a non-square box
    here would scale the axes differently and silently distort the art.
    """
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    if abs(width - height) > 1e-6:
        raise RenderingError(f"asset placement box must be square, got {width:g}x{height:g}")
    vb_x, vb_y, vb_w, vb_h = document.view_box
    if abs(vb_w - vb_h) > 1e-9:
        raise RenderingError(f"asset viewBox must be square, got {vb_w:g}x{vb_h:g}")
    scale = width / vb_w if vb_w else 0.0
    frame.document(document, scale=scale, offset=(x0 - vb_x * scale, y0 - vb_y * scale))


# --------------------------------------------------------------------------- #
# Fonts
# --------------------------------------------------------------------------- #

#: 17.11: every bundled face must permit bundling, redistribution *and*
#: embedding -- exactly the rights a KDP interior needs. Bitstream Vera and the
#: SIL Open Font License both do; the licence text ships beside the files.
#:
#: Vera stays registered because it is the neutral fallback and the licence
#: proof that the rule is about rights rather than about one family. What the
#: book actually reads in is Nunito with Fredoka headings: rounded terminals, a
#: single-storey 'a' and a tall x-height, which is what a six-year-old reading
#: one sentence per page needs, and a hard 'l'/'I' distinction, which is what
#: stops "Ill" from being three identical strokes.
BUNDLED_FACES = {
    "Vera": "Vera.ttf",
    "Vera-Bold": "VeraBd.ttf",
    "Vera-Italic": "VeraIt.ttf",
    "Vera-BoldItalic": "VeraBI.ttf",
    "Nunito": "Nunito-Regular.ttf",
    "Nunito-Bold": "Nunito-Bold.ttf",
    "Nunito-ExtraBold": "Nunito-ExtraBold.ttf",
    "Fredoka-SemiBold": "Fredoka-SemiBold.ttf",
}

#: The three roles every page draws with, named once so a face change is one
#: edit rather than fourteen. Callers take these as defaults instead of naming a
#: family, which is what let "Vera" become fourteen separate decisions.
BODY_FONT = "Nunito"
BOLD_FONT = "Nunito-Bold"
TITLE_FONT = "Fredoka-SemiBold"

_registered: set[str] = set()


def register_fonts(fonts_dir: Path) -> dict[str, str]:
    """Register the bundled faces by absolute path.

    17.11: the renderer "MUST raise rather than fall back when a face is
    missing, so a stripped checkout fails loudly instead of shipping Helvetica".
    A silent fallback is the worst outcome available here -- the book builds,
    preflight passes on an embedded system font, and the substitution is only
    visible once a proof arrives in the post.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    fonts_dir = Path(fonts_dir)
    missing = [name for name in BUNDLED_FACES.values() if not (fonts_dir / name).is_file()]
    if missing:
        raise RenderingError(
            f"{len(missing)} bundled font file(s) missing from {fonts_dir}",
            details=sorted(missing),
        )

    for face, filename in BUNDLED_FACES.items():
        if face not in _registered:
            pdfmetrics.registerFont(TTFont(face, str((fonts_dir / filename).resolve())))
            _registered.add(face)

    from reportlab.pdfbase.pdfmetrics import registerFontFamily

    registerFontFamily(
        "Vera", normal="Vera", bold="Vera-Bold",
        italic="Vera-Italic", boldItalic="Vera-BoldItalic",
    )
    # Nunito ships here as upright weights only. Naming the bold face for the
    # italic slots keeps a stray <i> from silently resolving to a base-14 font,
    # which is the failure 17.11 exists to prevent.
    registerFontFamily(
        "Nunito", normal="Nunito", bold="Nunito-Bold",
        italic="Nunito", boldItalic="Nunito-Bold",
    )
    return dict(BUNDLED_FACES)
