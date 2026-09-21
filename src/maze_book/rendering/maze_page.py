"""The maze page: recto, odd, with its tally strip (PRD 18.6).

18.6 fixes the measurements: a 6.75 in maze square horizontally centred in the
live area with its top edge 0.75 in below the top margin; a tally strip directly
below it, 6.75 in wide and 1.35 in tall, holding one 0.28 in tick box per
available candy, a 0.9 x 0.5 in "Total" write-in box, and the printed "Best
possible" number; and the maze number at 11 pt in the outside bottom corner.

Layout is computed as data (``MazePageLayout``) before anything is drawn. That
split is what lets the tests assert a box position to the point without
rendering a PDF and reading it back, and it is what lets the tally adapt to a
candy count from 5 to 18 without the drawing code branching.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Sequence

from ..assets.catalog import AssetCatalog
from ..errors import RenderingError
from ..model.analysis import MazeAnalysis
from ..model.maze_data import MazeData
from ..model.profile import Band
from .geometry import (
    MazeGeometry,
    asset_footprints,
    border_opening,
    marker_margins,
    wall_segments,
)
from .page import PT_PER_IN, Box, PageMetrics
from .svg import AssetGeometryCache
from .svg_to_pdf import BODY_FONT, BOLD_FONT, PdfFrame, place_document

#: 18.6 measurements, in inches.
MAZE_TOP_OFFSET_IN = 0.75
TALLY_HEIGHT_IN = 1.35
TICK_BOX_IN = 0.28
TOTAL_BOX_IN = (0.9, 0.5)
TALLY_COLUMN_IN = 1.2
MAZE_SQUARE_RIGHT_TALLY_IN = 5.9

MAZE_NUMBER_SIZE = 11.0
TALLY_LABEL_SIZE = 9.5
TALLY_VALUE_SIZE = 11.0

TICK_GAP_PT = 5.0
TICK_ROW_GAP_PT = 6.0

#: Side of a maze-page decoration, in inches, and the air it keeps around
#: itself. Small enough to read as decoration rather than as part of the
#: puzzle -- a pumpkin the size of a cell would look like something to collect.
DECOR_SIZE_IN = 0.46
DECOR_MARGIN_PT = 10.0

#: The two words a child looks for first.
MARKER_LABELS = {"start": "START", "finish": "FINISH"}
MARKER_LABEL_SIZE = 9.0
MARKER_LABEL_GAP = 3.5


@dataclass(frozen=True, slots=True)
class MazePageLayout:
    """Every box on a maze page, in y-down page points."""

    side: str
    maze_box: Box
    tally_box: Box
    tick_boxes: tuple[Box, ...]
    total_box: Box
    total_label_origin: tuple[float, float]
    best_label_origin: tuple[float, float]
    tick_label_origin: tuple[float, float]
    number_origin: tuple[float, float] | None
    geometry: MazeGeometry
    #: Every slot a decoration may occupy. Which ones get filled is drawn from
    #: the page's seed stream, so position varies as well as icon.
    decor_boxes: tuple[Box, ...] = ()
    #: How many of those slots to fill.
    decoration_count: int = 0
    #: Side of the endpoint markers drawn outside the grid, in points. 0 keeps
    #: them inside their cells.
    marker_size: float = 0.0

    def bounding_box(self) -> Box:
        boxes = [self.maze_box, self.tally_box]
        return (
            min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes),
        )


def plan_maze_page(
    maze: MazeData,
    *,
    metrics: PageMetrics,
    side: str,
    candy_count: int,
    maze_square_in: float,
    tally_position: str = "below",
    show_maze_number: bool = True,
    outside_marker_fraction: float = 0.0,
    decoration_count: int = 0,
) -> MazePageLayout:
    if tally_position not in ("below", "right"):
        raise RenderingError(
            f"layout.tallyPosition {tally_position!r} is not 'below' or 'right'"
        )

    live_x0, live_y0, live_x1, live_y1 = metrics.live_box(side)
    square = maze_square_in * PT_PER_IN
    if tally_position == "right":
        # 18.6: the right column shrinks the maze; "below" stays the default
        # because maze size matters more than layout novelty.
        square = min(square, MAZE_SQUARE_RIGHT_TALLY_IN * PT_PER_IN)

    top = live_y0 + MAZE_TOP_OFFSET_IN * PT_PER_IN
    if tally_position == "below":
        maze_x0 = live_x0 + ((live_x1 - live_x0) - square) / 2.0
        maze_box = (maze_x0, top, maze_x0 + square, top + square)
        tally_box = (
            maze_box[0], maze_box[3],
            maze_box[2], maze_box[3] + TALLY_HEIGHT_IN * PT_PER_IN,
        )
    else:
        column = TALLY_COLUMN_IN * PT_PER_IN
        maze_box = (live_x0, top, live_x0 + square, top + square)
        tally_box = (live_x1 - column, top, live_x1, top + square)

    if tally_box[3] > live_y1 + 1e-6:
        raise RenderingError(
            f"maze page furniture is {tally_box[3] - live_y1:.1f} pt taller than the "
            f"live area; reduce layout.mazeSquareIn or the margins"
        )

    # The markers come out of the maze square, not out of the page margin:
    # anything drawn beyond this box is outside the safe area preflight checks.
    marker_size = outside_marker_fraction * square

    ticks, total_box, labels = _plan_tally(tally_box, candy_count, tally_position)

    number_origin = None
    if show_maze_number:
        number_origin = (metrics.outside_x(side), live_y1)

    decor_boxes = _plan_decorations(
        count=decoration_count,
        maze_box=maze_box,
        tally_box=tally_box,
        live_left=live_x0,
        live_right=live_x1,
        live_top=live_y0,
        live_bottom=live_y1,
        folio_side=side if show_maze_number else None,
    )

    return MazePageLayout(
        side=side,
        maze_box=maze_box,
        tally_box=tally_box,
        tick_boxes=ticks,
        total_box=total_box,
        total_label_origin=labels[0],
        best_label_origin=labels[1],
        tick_label_origin=labels[2],
        number_origin=number_origin,
        decor_boxes=decor_boxes,
        decoration_count=decoration_count,
        geometry=MazeGeometry.fitted_with_margins(
            maze.rows, maze.cols, box=maze_box,
            margins=marker_margins(
                maze, size=marker_size,
                label=MARKER_LABEL_SIZE + 2 * MARKER_LABEL_GAP if marker_size else 0.0,
            ),
        ),
        marker_size=marker_size,
    )


def _plan_decorations(
    *,
    count: int,
    maze_box: Box,
    tally_box: Box,
    live_left: float,
    live_right: float,
    live_top: float,
    live_bottom: float,
    folio_side: str | None,
) -> tuple[Box, ...]:
    """Every slot a decoration may occupy, in the bands the furniture leaves empty.

    Decorations go in the air above the maze and below the tally, never beside
    the grid: a pumpkin level with the walls reads as part of the puzzle, and a
    child who tries to route through it has been misled by the page rather than
    by the maze.

    All the slots are returned, not the first two. Choosing *which* is the
    caller's job, from the page's own seed stream -- a fixed pair repeated fifty
    times is a border whether the two corners match or not, and only the icon
    changing is not variety.
    """
    if count <= 0:
        return ()
    size = DECOR_SIZE_IN * PT_PER_IN

    def band(top: float, bottom: float) -> float | None:
        """Where a decoration sits in a horizontal band, or None if it will not fit."""
        if bottom - top < size + DECOR_MARGIN_PT:
            return None
        return top + (bottom - top - size) / 2.0

    slots: list[Box] = []
    columns = (live_left, (live_left + live_right) / 2.0 - size / 2.0, live_right - size)

    above = band(live_top, maze_box[1])
    if above is not None:
        slots.extend((x, above, x + size, above + size) for x in columns)

    # The folio sits in one bottom corner, not across the whole band. Clipping
    # the band above it left 0.36 in where a decoration needs 0.60, so the
    # bottom row silently vanished and every page wore its two decorations in a
    # line along the top -- the same border the slots were spread out to avoid,
    # turned on its side. Only the folio's own column is dropped.
    below = band(tally_box[3], live_bottom)
    if below is not None:
        folio_column = columns[-1] if folio_side == "right" else columns[0]
        slots.extend(
            (x, below, x + size, below + size)
            for x in columns
            if folio_column is None or x != folio_column
        )
    return tuple(slots)


#: Most tick boxes on one row before wrapping. Seventeen boxes across a strip
#: fit, but they wrap 12 and 5, which reads as a full row and an afterthought
#: rather than as one score to fill in. Nine and eight look like a pair.
MAX_TICKS_PER_ROW = 9

#: Room between the last tick box and the Total box: the word "Total" is 26 pt
#: wide at 11 pt, and it is drawn right-aligned against the box.
TOTAL_LABEL_ROOM_PT = 40.0


def _plan_tally(
    box: Box, candy_count: int, position: str
) -> tuple[tuple[Box, ...], Box, tuple[tuple[float, float], ...]]:
    """Tick boxes, the Total box, and the three text origins.

    One reading line: the label, then the boxes, then Total on the same
    baseline immediately after the last box, so the child's eye finishes the row
    where the answer goes. "Best possible" drops to its own line underneath,
    because printed next to the boxes it reads as part of the tally and a child
    ticks against it.

    The ticks wrap at ``MAX_TICKS_PER_ROW`` rather than at whatever the width
    allows, so an 18-candy finale reads as two even rows instead of one long one
    and a remainder.
    """
    x0, y0, x1, y1 = box
    tick = TICK_BOX_IN * PT_PER_IN
    total_w, total_h = TOTAL_BOX_IN[0] * PT_PER_IN, TOTAL_BOX_IN[1] * PT_PER_IN

    label_gap = TALLY_LABEL_SIZE + 4.0
    tick_label_origin = (x0, y0 + label_gap)
    ticks_top = y0 + label_gap + 4.0

    width = x1 - x0
    if position == "below":
        room = int((width - total_w - 40.0 + TICK_GAP_PT) // (tick + TICK_GAP_PT))
        per_row = max(1, min(MAX_TICKS_PER_ROW, room))
    else:
        per_row = max(1, int((width + TICK_GAP_PT) // (tick + TICK_GAP_PT)))

    boxes: list[Box] = []
    for index in range(candy_count):
        row, column = divmod(index, per_row)
        bx = x0 + column * (tick + TICK_GAP_PT)
        by = ticks_top + row * (tick + TICK_ROW_GAP_PT)
        boxes.append((bx, by, bx + tick, by + tick))

    rows = max(1, -(-candy_count // per_row))
    ticks_bottom = ticks_top + rows * tick + (rows - 1) * TICK_ROW_GAP_PT

    if position == "below":
        # Total sits at the end of the first tick row, which is where the eye
        # already is once the last box is ticked. The gap has to hold the word
        # as well as the box, or "Total" prints over the last tick.
        total_x = x0 + per_row * (tick + TICK_GAP_PT) + TOTAL_LABEL_ROOM_PT
        total_x = min(total_x, x1 - total_w)
        total_y = ticks_top + (tick - total_h) / 2.0
        total_box = (total_x, total_y, total_x + total_w, total_y + total_h)
        total_label_origin = (total_x - 6.0, total_y + total_h / 2.0 + 3.5)
        best_label_origin = (x0, ticks_bottom + TALLY_VALUE_SIZE + 6.0)
    else:
        total_box = (x0, y1 - total_h, x0 + min(total_w, width), y1)
        total_label_origin = (x0, y1 - total_h - 6.0)
        best_label_origin = (x0, y1 - total_h - 20.0)

    return tuple(boxes), total_box, (total_label_origin, best_label_origin, tick_label_origin)


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #


def draw_maze_page(
    frame: PdfFrame,
    maze: MazeData,
    analysis: MazeAnalysis,
    layout: MazePageLayout,
    *,
    catalog: AssetCatalog,
    band: Band,
    cache: AssetGeometryCache,
    show_best_possible: bool = True,
    body_font: str = BODY_FONT,
    bold_font: str = BOLD_FONT,
    decoration_rng: random.Random | None = None,
) -> None:
    geometry = layout.geometry

    # wallWidthPt is an absolute typographic weight, never a fraction of a cell.
    # 17.4 gives it per band precisely so the line keeps the same physical weight
    # as the grid grows from 8x8 to 18x18; a wall that scaled with the cell would
    # go spindly exactly where the maze gets hardest to read.
    frame.segments(wall_segments(maze, geometry), band.wall_width_pt)

    scales = {
        "start": band.start_scale, "finish": band.finish_scale,
        "collectible": band.collectible_scale, "dead-end": band.dead_end_scale,
    }
    placements = asset_footprints(
        maze, geometry, scales=scales, marker_size=layout.marker_size
    )
    for footprint in placements:
        document = cache.get(catalog.path_for(footprint.asset.asset_id))
        place_document(frame, document, footprint.rect)

    if layout.marker_size > 0.0:
        _label_markers(frame, maze, geometry, placements, font=bold_font)

    _draw_decorations(frame, layout, catalog=catalog, cache=cache, rng=decoration_rng)

    _draw_tally(
        frame, layout, analysis,
        show_best_possible=show_best_possible, body_font=body_font, bold_font=bold_font,
    )

    if layout.number_origin is not None:
        _draw_maze_number(frame, layout, maze.maze_index, font=body_font)


def _label_markers(
    frame: PdfFrame,
    maze: MazeData,
    geometry: MazeGeometry,
    placements: Sequence,
    *,
    font: str,
) -> None:
    """Print START and FINISH beside the two markers.

    A drawing of a boy is not the word "start", and a six-year-old reading the
    page for the first time should not have to work out which figure is Jim and
    which is scenery. The word goes on the side of the marker facing away from
    the grid, so it never sits between the marker and its opening.
    """
    openings = border_opening(maze)
    for footprint in placements:
        label = MARKER_LABELS.get(footprint.asset.role)
        side = openings.get(footprint.asset.cell)
        if label is None or side is None:
            continue
        x0, y0, x1, y1 = footprint.rect
        centre_x = (x0 + x1) / 2.0
        if side == "N":
            y = y0 - MARKER_LABEL_GAP
        elif side == "S":
            y = y1 + MARKER_LABEL_SIZE + MARKER_LABEL_GAP
        else:
            # Under the marker on the east and west sides: beside it would run
            # into the page margin on the outer edge.
            y = y1 + MARKER_LABEL_SIZE + MARKER_LABEL_GAP
        frame.canvas.saveState()
        frame.canvas.setFillGray(0.0)
        frame.canvas.setFont(font, MARKER_LABEL_SIZE)
        frame.canvas.drawCentredString(centre_x, frame.y(y), label)
        frame.canvas.restoreState()


def _draw_decorations(
    frame: PdfFrame,
    layout: MazePageLayout,
    *,
    catalog: AssetCatalog,
    cache: AssetGeometryCache,
    rng: random.Random | None,
) -> None:
    """Fill the planned slots from the page-vector folder, without repeating.

    Drawing from a stream derived for this maze alone means page 12 keeps its
    pumpkin when page 13 is regenerated -- the same reason every other
    stochastic step in the build has its own purpose.
    """
    wanted = min(layout.decoration_count, len(layout.decor_boxes))
    if wanted <= 0 or rng is None:
        return
    choices = catalog.decorations()
    if not choices:
        return
    boxes = rng.sample(list(layout.decor_boxes), k=wanted)
    picks = rng.sample(choices, k=min(wanted, len(choices)))
    for box, asset in zip(boxes, picks):
        place_document(frame, cache.get(asset.path), box)


def _draw_tally(
    frame: PdfFrame,
    layout: MazePageLayout,
    analysis: MazeAnalysis,
    *,
    show_best_possible: bool,
    body_font: str,
    bold_font: str,
) -> None:
    canvas = frame.canvas

    for box in layout.tick_boxes:
        frame.rect(box, 0.9)
    frame.rect(layout.total_box, 1.1)

    canvas.saveState()
    canvas.setFillGray(0.0)

    canvas.setFont(body_font, TALLY_LABEL_SIZE)
    canvas.drawString(
        layout.tick_label_origin[0], frame.y(layout.tick_label_origin[1]),
        "Color in one box for every candy you collect",
    )

    canvas.setFont(body_font, TALLY_VALUE_SIZE)
    canvas.drawRightString(
        layout.total_label_origin[0], frame.y(layout.total_label_origin[1]), "Total"
    )

    if show_best_possible:
        canvas.setFont(bold_font, TALLY_VALUE_SIZE)
        canvas.drawString(
            layout.best_label_origin[0], frame.y(layout.best_label_origin[1]),
            f"Best possible: {analysis.best_candy_total} "
            f"{'candy' if analysis.best_candy_total == 1 else 'candies'}",
        )
    canvas.restoreState()


def _draw_maze_number(
    frame: PdfFrame, layout: MazePageLayout, index: int, *, font: str
) -> None:
    x, y = layout.number_origin
    canvas = frame.canvas
    canvas.saveState()
    canvas.setFillGray(0.0)
    canvas.setFont(font, MAZE_NUMBER_SIZE)
    # "Maze 18", not "18". A bare number in the folio position reads as a page
    # number, and this book has none -- so a reader told to turn to 41 goes to
    # the wrong place, and a reader looking for maze 41 never thinks to use it.
    label = f"Maze {index}"
    if layout.side == "right":
        canvas.drawRightString(x, frame.y(y), label)
    else:
        canvas.drawString(x, frame.y(y), label)
    canvas.restoreState()
