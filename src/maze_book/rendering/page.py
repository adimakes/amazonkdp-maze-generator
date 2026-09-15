"""Page metrics: trim, margins, and the live area of a physical page (PRD 17.10).

All page geometry is in points, y-down from the top-left corner, matching
``rendering.geometry``. ``svg_to_pdf.PdfFrame`` performs the single flip to
ReportLab's y-up space.

Physical parity is the load-bearing idea (17.10): even page numbers are ``left``
(verso), odd are ``right`` (recto). Which physical edge the *inside* margin sits
on follows from that and nothing else, so it is derived here rather than passed
in -- a caller that had to remember the rule would eventually get one page wrong,
and a single mirrored page is the kind of defect that survives proofreading.

`print.gutterIn` is deliberately **not** added to `safeMarginsIn.inside`. The
cross-field check in ``model/book_config.py`` already computes the usable area as
``trim - inside - outside``, and a renderer using a different usable area than
the validator checks against is exactly the two-sites-disagree bug this codebase
keeps designing out. `inside` is the whole binding-side margin; preflight asserts
it is at least `gutterIn`, which is what gives that field its meaning.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model.book_config import PrintSpec

PT_PER_IN = 72.0

SIDE_LEFT = "left"
SIDE_RIGHT = "right"

Box = tuple[float, float, float, float]


def side_for_page(page_number: int) -> str:
    """17.10: even page numbers are left pages, odd are right pages."""
    return SIDE_LEFT if page_number % 2 == 0 else SIDE_RIGHT


@dataclass(frozen=True, slots=True)
class PageMetrics:
    """One book's page geometry, in points."""

    width: float
    height: float
    inside: float
    outside: float
    top: float
    bottom: float
    gutter: float

    @staticmethod
    def from_print_spec(spec: PrintSpec) -> "PageMetrics":
        margins = spec.safe_margins_in
        return PageMetrics(
            width=spec.trim_width_in * PT_PER_IN,
            height=spec.trim_height_in * PT_PER_IN,
            inside=margins.inside * PT_PER_IN,
            outside=margins.outside * PT_PER_IN,
            top=margins.top * PT_PER_IN,
            bottom=margins.bottom * PT_PER_IN,
            gutter=spec.gutter_in * PT_PER_IN,
        )

    @property
    def page_size(self) -> tuple[float, float]:
        """As ReportLab wants it: ``(width, height)`` in points."""
        return (self.width, self.height)

    def left_margin(self, side: str) -> float:
        return self.inside if side == SIDE_RIGHT else self.outside

    def right_margin(self, side: str) -> float:
        return self.outside if side == SIDE_RIGHT else self.inside

    def live_box(self, side: str) -> Box:
        """The area artwork may occupy, y-down from the page's top-left."""
        return (
            self.left_margin(side),
            self.top,
            self.width - self.right_margin(side),
            self.height - self.bottom,
        )

    def live_width(self, side: str) -> float:
        x0, _, x1, _ = self.live_box(side)
        return x1 - x0

    def live_height(self) -> float:
        return self.height - self.top - self.bottom

    def outside_x(self, side: str) -> float:
        """The x of the outside trim-ward edge of the live area."""
        x0, _, x1, _ = self.live_box(side)
        return x1 if side == SIDE_RIGHT else x0

    def safe_area_clearance(self, box: Box) -> float:
        """Smallest distance from ``box`` to any trim edge.

        18.8 requires no mark within 0.25 in of trim; this is what preflight and
        the page renderers measure against.
        """
        x0, y0, x1, y1 = box
        return min(x0, y0, self.width - x1, self.height - y1)
