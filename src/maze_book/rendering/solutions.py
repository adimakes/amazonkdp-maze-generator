"""Solution pages: a 3x3 grid of answer thumbnails (PRD 18.6, 8.5).

18.6: "9 mazes per page in a 3x3 grid, each thumbnail 2.1 x 2.1 in; best route
drawn as a 1.5 pt solid line with rounded joins against 0.75 pt walls; candies on
the best route drawn as small filled dots, never full icons (icons are illegible
at that scale); caption "<n>. Best possible: <total> candies" under each thumbnail, in
the same words the maze page used, so a child comparing the two is
comparing the same thing."

The dots matter. A collectible icon is designed to read at 5.5 mm in a full-size
maze; on a 2.1 in thumbnail of an 18x18 grid each cell is under 3 mm, and the
icon becomes a smudge that hides the very route line it sits on. A dot says
"a candy was here" without competing with the answer.

8.5 also fixes what the back matter shows: the highest-candy route, its total,
and the maze number -- nothing else. Alternative routes and per-route scores stay
in the analysis JSON for QA.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..model.analysis import MazeAnalysis
from ..model.maze_data import MazeData
from .geometry import MazeGeometry, route_polyline, wall_segments
from .page import PT_PER_IN, Box, PageMetrics
from .svg_to_pdf import BODY_FONT, PdfFrame

THUMBNAIL_IN = 2.1
CAPTION_GAP_PT = 13.0
CAPTION_SIZE = 9.5
#: 18.6's thumbnail weights. They are *not* the profile's solutionWallWidthPt /
#: solutionRouteWidthPt, and the difference is deliberate: those size a
#: full-size solution, where the maze is 6.75 in. Rendered at 2.1 in an 18x18
#: cell is under 3 mm, and the profile's 1.2-1.4 pt wall against its 1.8-1.9 pt
#: route leaves the answer line barely heavier than the maze it runs through,
#: with the candy dots lost in the ink. 18.6 requires these to be defaults and
#: to be overridable, which is what the draw functions' parameters are for.
WALL_WIDTH_PT = 0.75
ROUTE_WIDTH_PT = 1.5
COLUMNS = 3
ROWS = 3


@dataclass(frozen=True, slots=True)
class SolutionSlot:
    """One thumbnail's box and the baseline of its caption."""

    box: Box
    caption_centre: tuple[float, float]


@dataclass(frozen=True, slots=True)
class SolutionPageLayout:
    side: str
    slots: tuple[SolutionSlot, ...]


def plan_solutions_page(
    *, metrics: PageMetrics, side: str, count: int, per_page: int = COLUMNS * ROWS
) -> SolutionPageLayout:
    """Lay out up to ``per_page`` thumbnails; ``count`` says how many are used.

    A short final page keeps the grid's column positions rather than centring the
    remainder, so the last page reads as part of the same table.
    """
    live_x0, live_y0, live_x1, live_y1 = metrics.live_box(side)
    columns = COLUMNS
    rows = max(1, -(-per_page // columns))

    thumbnail = THUMBNAIL_IN * PT_PER_IN
    cell_w = (live_x1 - live_x0) / columns
    block_h = thumbnail + CAPTION_GAP_PT + CAPTION_SIZE
    spare = (live_y1 - live_y0) - rows * block_h
    row_gap = max(0.0, spare / (rows + 1))

    slots: list[SolutionSlot] = []
    for index in range(min(count, per_page)):
        row, column = divmod(index, columns)
        cx = live_x0 + cell_w * (column + 0.5)
        top = live_y0 + row_gap + row * (block_h + row_gap)
        box = (cx - thumbnail / 2.0, top, cx + thumbnail / 2.0, top + thumbnail)
        slots.append(
            SolutionSlot(box=box, caption_centre=(cx, box[3] + CAPTION_GAP_PT + CAPTION_SIZE))
        )
    return SolutionPageLayout(side=side, slots=tuple(slots))


def caption_for(maze_index: int, analysis: MazeAnalysis) -> str:
    total = analysis.best_candy_total
    unit = "candy" if total == 1 else "candies"
    return f"{maze_index}. Best possible: {total} {unit}"


def draw_solution_thumbnail(
    frame: PdfFrame,
    maze: MazeData,
    analysis: MazeAnalysis,
    slot: SolutionSlot,
    *,
    caption_font: str = BODY_FONT,
    wall_width: float = WALL_WIDTH_PT,
    route_width: float = ROUTE_WIDTH_PT,
) -> None:
    geometry = MazeGeometry.fitted(maze.rows, maze.cols, box=slot.box)

    frame.segments(wall_segments(maze, geometry), wall_width)

    route = list(analysis.best_route)
    if route:
        candies = maze.collectible_cells()
        on_route = [cell for cell in route if cell in candies]
        if on_route:
            # Sized off the cell, not fixed: at 18x18 a fixed dot swallows the
            # corridor it marks, and at 8x8 it disappears.
            frame.dots(
                (geometry.cell_centre(cell) for cell in on_route),
                max(0.9, geometry.cell * 0.17),
            )
        frame.polyline(route_polyline(route, geometry), route_width)

    canvas = frame.canvas
    canvas.saveState()
    canvas.setFillGray(0.0)
    canvas.setFont(caption_font, CAPTION_SIZE)
    canvas.drawCentredString(
        slot.caption_centre[0], frame.y(slot.caption_centre[1]),
        caption_for(maze.maze_index, analysis),
    )
    canvas.restoreState()


def draw_solutions_page(
    frame: PdfFrame,
    entries: Sequence[tuple[MazeData, MazeAnalysis]],
    layout: SolutionPageLayout,
    *,
    caption_font: str = BODY_FONT,
    wall_width: float = WALL_WIDTH_PT,
    route_width: float = ROUTE_WIDTH_PT,
) -> None:
    for (maze, analysis), slot in zip(entries, layout.slots):
        draw_solution_thumbnail(
            frame, maze, analysis, slot, caption_font=caption_font,
            wall_width=wall_width, route_width=route_width,
        )


def solution_page_count(maze_count: int, per_page: int = COLUMNS * ROWS) -> int:
    return -(-maze_count // per_page) if maze_count else 0
