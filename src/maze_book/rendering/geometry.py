"""Normalized maze geometry: the one place walls are derived (PRD 9.1, 17.11).

``MazeData`` stores *open edges* -- pairs of adjacent cells with no wall between
them. Every renderer needs the complement: where the walls actually are. That
inversion happens here and nowhere else. Two inference sites is how the SVG and
the PDF come to disagree about a single wall, and the disagreement is invisible
until a child hits a dead end that the answer key says is a corridor.

The pipeline is::

    MazeData -> MazeGeometry (normalized) -> SVG page coordinates
                                          -> PDF page coordinates
                                          -> solution thumbnail coordinates

"Normalized" means one cell is one unit and the grid's top-left corner is the
origin, so placement can be validated (17.9: "in normalized maze coordinates
before SVG or PDF rendering") without knowing the trim size. ``fitted`` then maps
that square onto a page box; every renderer scales the *same* segments rather
than recomputing them at its own scale.

Coordinates are y-down throughout, matching both SVG and the row-major-top-left
cell convention. ReportLab is y-up, and ``svg_to_pdf`` is the single place that
flip happens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from ..model.geometry import Cell, canonical_edge
from ..model.maze_data import MazeData, PlacedAsset

Point = tuple[float, float]

#: A wall run: ``(x0, y0, x1, y1)``, always left-to-right or top-to-bottom.
Segment = tuple[float, float, float, float]

ROLE_START = "start"
ROLE_FINISH = "finish"
ROLE_COLLECTIBLE = "collectible"
ROLE_DEAD_END = "dead-end"


@dataclass(frozen=True, slots=True)
class MazeGeometry:
    """Maps cells to a planar box. One cell is ``cell`` units square.

    The grid is centred in the box it is fitted to, and cells stay square even
    when the grid is not: a 10x18 maze drawn into a square page box gets bars of
    white either side rather than stretched cells, because a stretched cell
    makes a corridor read as wider than the one beside it.
    """

    rows: int
    cols: int
    origin: Point = (0.0, 0.0)
    cell: float = 1.0

    @staticmethod
    def normalized(rows: int, cols: int) -> "MazeGeometry":
        return MazeGeometry(rows=rows, cols=cols)

    @staticmethod
    def fitted(rows: int, cols: int, *, box: tuple[float, float, float, float]) -> "MazeGeometry":
        """Largest square-celled grid that fits ``box = (x0, y0, x1, y1)``, centred."""
        x0, y0, x1, y1 = box
        cell = min((x1 - x0) / cols, (y1 - y0) / rows)
        width, height = cell * cols, cell * rows
        return MazeGeometry(
            rows=rows,
            cols=cols,
            origin=(x0 + ((x1 - x0) - width) / 2.0, y0 + ((y1 - y0) - height) / 2.0),
            cell=cell,
        )

    @staticmethod
    def fitted_with_margins(
        rows: int,
        cols: int,
        *,
        box: tuple[float, float, float, float],
        margins: "dict[str, float]",
    ) -> "MazeGeometry":
        """Fit the grid into ``box`` less the room its outside markers need.

        Markers drawn outside the grid come out of the same 6.75 in square as
        the grid, or they would sit in the page margin and fail the safe-area
        check. The margins are absolute, not a share of a cell, because an
        endpoint marker is not a cell: it should be the same size on the 8x8
        opener as on the 18x18 finale, and a child should recognise it without
        first working out which maze they are on.

        Only the sides that carry a marker are charged for, so a maze whose
        endpoints open north and south keeps its full width.
        """
        x0, y0, x1, y1 = box
        inner = (
            x0 + margins.get("W", 0.0), y0 + margins.get("N", 0.0),
            x1 - margins.get("E", 0.0), y1 - margins.get("S", 0.0),
        )
        if inner[2] - inner[0] <= 0.0 or inner[3] - inner[1] <= 0.0:
            raise ValueError("marker margins leave no room for the grid")
        return MazeGeometry.fitted(rows, cols, box=inner)

    @property
    def width(self) -> float:
        return self.cell * self.cols

    @property
    def height(self) -> float:
        return self.cell * self.rows

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (self.origin[0], self.origin[1], self.origin[0] + self.width, self.origin[1] + self.height)

    def cell_rect(self, cell: Cell) -> tuple[float, float, float, float]:
        row, col = cell
        x = self.origin[0] + col * self.cell
        y = self.origin[1] + row * self.cell
        return (x, y, x + self.cell, y + self.cell)

    def cell_centre(self, cell: Cell) -> Point:
        x0, y0, x1, y1 = self.cell_rect(cell)
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

    def contains_cell(self, cell: Cell) -> bool:
        row, col = cell
        return 0 <= row < self.rows and 0 <= col < self.cols


# --------------------------------------------------------------------------- #
# Walls
# --------------------------------------------------------------------------- #


def border_opening(maze: MazeData) -> dict[Cell, str]:
    """Which outer wall, if any, each endpoint opens through (PRD 7: "outer
    boundaries render as walls except where the renderer intentionally creates a
    start/finish opening").

    Purely cosmetic: the opening is a gap in the drawn border, never an edge in
    the graph, so it cannot change traversability or the answer key. An endpoint
    in the interior gets no opening, and a corner cell opens through the side
    that points away from the other endpoint, so entry and exit read as opposite
    ends of a journey rather than as two doors on the same wall.
    """
    openings: dict[Cell, str] = {}
    for cell, other in ((maze.start, maze.finish), (maze.finish, maze.start)):
        row, col = cell
        candidates: list[str] = []
        if row == 0:
            candidates.append("N")
        if row == maze.rows - 1:
            candidates.append("S")
        if col == 0:
            candidates.append("W")
        if col == maze.cols - 1:
            candidates.append("E")
        if not candidates:
            continue
        away = {
            "N": other[0] - row, "S": row - other[0],
            "W": other[1] - col, "E": col - other[1],
        }
        openings[cell] = max(candidates, key=lambda side: (away[side], side))
    return openings


def _unit_walls(maze: MazeData, *, openings: dict[Cell, str]) -> tuple[set, set]:
    """Wall presence as two sets of unit edges on the grid lattice.

    ``horizontal`` holds ``(row, col)`` for the wall on the *top* side of cell
    ``(row, col)``; ``row == rows`` means the bottom boundary. ``vertical`` holds
    ``(row, col)`` for the wall on the *left* side; ``col == cols`` is the right
    boundary.
    """
    open_edges = maze.edge_set()
    horizontal: set[tuple[int, int]] = set()
    vertical: set[tuple[int, int]] = set()

    for row in range(maze.rows + 1):
        for col in range(maze.cols):
            if row == 0 or row == maze.rows:
                inner = (0, col) if row == 0 else (maze.rows - 1, col)
                side = "N" if row == 0 else "S"
                if openings.get(inner) == side:
                    continue
                horizontal.add((row, col))
            elif canonical_edge((row - 1, col), (row, col)) not in open_edges:
                horizontal.add((row, col))

    for col in range(maze.cols + 1):
        for row in range(maze.rows):
            if col == 0 or col == maze.cols:
                inner = (row, 0) if col == 0 else (row, maze.cols - 1)
                side = "W" if col == 0 else "E"
                if openings.get(inner) == side:
                    continue
                vertical.add((row, col))
            elif canonical_edge((row, col - 1), (row, col)) not in open_edges:
                vertical.add((row, col))

    return horizontal, vertical


def wall_segments(
    maze: MazeData, geometry: MazeGeometry, *, open_endpoints: bool = True
) -> list[Segment]:
    """Every wall, as collinear runs merged into the longest possible segments.

    Merging is not cosmetic. Drawn as one segment per cell side, a straight wall
    is a row of abutting rectangles whose joins show as faint seams at print
    resolution, and the PDF carries four times the operators it needs. Merged,
    a corridor wall is a single line with two ends.
    """
    openings = border_opening(maze) if open_endpoints else {}
    horizontal, vertical = _unit_walls(maze, openings=openings)
    x0, y0 = geometry.origin
    size = geometry.cell
    segments: list[Segment] = []

    for row in range(maze.rows + 1):
        for start_col, run in _runs(col for r, col in sorted(horizontal) if r == row):
            y = y0 + row * size
            segments.append((x0 + start_col * size, y, x0 + (start_col + run) * size, y))

    for col in range(maze.cols + 1):
        for start_row, run in _runs(row for row, c in sorted(vertical) if c == col):
            x = x0 + col * size
            segments.append((x, y0 + start_row * size, x, y0 + (start_row + run) * size))

    return segments


def _runs(values: Iterable[int]) -> Iterator[tuple[int, int]]:
    """Consecutive integers as ``(start, length)`` pairs."""
    start: int | None = None
    previous: int | None = None
    for value in sorted(values):
        if start is None:
            start = previous = value
            continue
        if value == previous + 1:
            previous = value
            continue
        yield start, previous - start + 1
        start = previous = value
    if start is not None and previous is not None:
        yield start, previous - start + 1


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


def route_polyline(cells: Sequence[Cell], geometry: MazeGeometry) -> list[Point]:
    """A solution route as cell centres. Renderers draw it; none re-derives it."""
    return [geometry.cell_centre(cell) for cell in cells]


# --------------------------------------------------------------------------- #
# Asset placement (PRD 17.9)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Footprint:
    """Where one asset's viewBox lands, in the same units as its geometry."""

    asset: PlacedAsset
    rect: tuple[float, float, float, float]

    @property
    def width(self) -> float:
        return self.rect[2] - self.rect[0]


def asset_footprint(
    asset: PlacedAsset, geometry: MazeGeometry, *, scale: float | None = None
) -> Footprint:
    """The square an asset occupies: ``scale`` of the cell, centred in it.

    Assets are square because the subset fixes the viewBox at 100x100 (18.5);
    the renderer scales by cell size rather than by the illustrator's canvas,
    which is the whole reason that rule exists.
    """
    factor = asset.scale if scale is None else scale
    cx, cy = geometry.cell_centre(asset.cell)
    half = geometry.cell * factor / 2.0
    return Footprint(asset=asset, rect=(cx - half, cy - half, cx + half, cy + half))


#: How far outside the grid a marker sits, as a fraction of one cell. Wide
#: enough that the marker reads as standing *beside* the opening rather than
#: plugging it, narrow enough that it still reads as attached to this maze.
MARKER_GAP_FRACTION = 0.14


def marker_footprint(
    asset: PlacedAsset, side: str, geometry: MazeGeometry, *, size: float
) -> Footprint:
    """The square an endpoint marker occupies *outside* the grid, by its opening.

    Drawn inside its cell, the start marker sits in a corridor a child is meant
    to walk through, and on an interior cell it is simply lost in the middle of
    the maze. Outside, it says the thing the page is for: here is where Jim
    stands, and the gap in the wall next to him is the way in.

    ``size`` is absolute, in the geometry's own units, and the square is centred
    on the opening's axis so marker and gap line up however large the grid is.
    """
    x0, y0, x1, y1 = geometry.bounds
    cx, cy = geometry.cell_centre(asset.cell)
    half = size / 2.0
    gap = size * MARKER_GAP_FRACTION

    if side == "N":
        rect = (cx - half, y0 - gap - size, cx + half, y0 - gap)
    elif side == "S":
        rect = (cx - half, y1 + gap, cx + half, y1 + gap + size)
    elif side == "W":
        rect = (x0 - gap - size, cy - half, x0 - gap, cy + half)
    elif side == "E":
        rect = (x1 + gap, cy - half, x1 + gap + size, cy + half)
    else:  # pragma: no cover - border_opening only ever returns the four sides
        raise ValueError(f"{side!r} is not one of N, E, S, W")
    return Footprint(asset=asset, rect=rect)


def marker_margins(maze: MazeData, *, size: float) -> dict[str, float]:
    """Room to reserve outside each side of the grid, in the geometry's units."""
    if size <= 0.0:
        return {}
    allowance = size * (1.0 + MARKER_GAP_FRACTION)
    openings = border_opening(maze)
    margins: dict[str, float] = {}
    for asset in maze.assets:
        if asset.role in (ROLE_START, ROLE_FINISH) and asset.cell in openings:
            margins[openings[asset.cell]] = allowance
    return margins


def asset_footprints(
    maze: MazeData,
    geometry: MazeGeometry,
    *,
    scales: dict[str, float],
    marker_size: float = 0.0,
) -> list[Footprint]:
    """Where every asset is drawn, decided once for all renderers.

    Both the PDF page and the SVG draw from this list. Letting each work out on
    its own whether an endpoint marker goes inside or outside its cell is the
    same mistake as letting each infer walls: two answers to one question, and
    the one that is wrong is whichever the reader is not looking at.
    """
    openings = border_opening(maze) if marker_size > 0.0 else {}
    placements: list[Footprint] = []
    for asset in maze.assets:
        side = openings.get(asset.cell) if asset.role in (ROLE_START, ROLE_FINISH) else None
        if side is None:
            placements.append(
                asset_footprint(asset, geometry, scale=scales.get(asset.role, asset.scale))
            )
        else:
            placements.append(marker_footprint(asset, side, geometry, size=marker_size))
    return placements


def safe_inset_rect(
    cell: Cell, geometry: MazeGeometry, clearance_fraction: float
) -> tuple[float, float, float, float]:
    """The cell shrunk by ``clearance_fraction`` on every side."""
    x0, y0, x1, y1 = geometry.cell_rect(cell)
    inset = geometry.cell * clearance_fraction
    return (x0 + inset, y0 + inset, x1 - inset, y1 - inset)


def validate_placements(
    maze: MazeData,
    *,
    clearance_fraction: float,
    geometry: MazeGeometry | None = None,
    marker_size: float = 0.0,
) -> list[str]:
    """Check every placement against 17.9. Returns violations, does not raise.

    Run on the *normalized* geometry, before any renderer exists, so a bad
    placement is caught once rather than once per output format.

    The footprint rules collapse into a strong one: an asset centred in its cell
    and kept inside the safe inset cannot reach a wall, an opening, or a
    neighbouring cell's asset, because the inset is strictly inside the cell and
    the cells tile without overlap. What remains is checking that the inset
    actually holds, that no cell carries two assets, and that no asset claims a
    cell the graph does not have -- which is how a "decorative" asset would
    otherwise end up asserting something about traversability.
    """
    geometry = geometry or MazeGeometry.normalized(maze.rows, maze.cols)
    problems: list[str] = []
    openings = border_opening(maze) if marker_size > 0.0 else {}

    traversable = set(maze.traversable_cells())
    adjacency = maze.adjacency()
    occupied: dict[Cell, PlacedAsset] = {}

    for asset in maze.assets:
        label = f"{asset.role} asset {asset.asset_id!r} at {asset.cell}"

        if not geometry.contains_cell(asset.cell):
            problems.append(f"{label} is outside the {maze.rows}x{maze.cols} grid")
            continue
        if asset.cell not in traversable:
            problems.append(f"{label} sits on a cell that is not traversable")

        previous = occupied.get(asset.cell)
        if previous is not None:
            problems.append(
                f"{label} shares a cell with {previous.role} asset {previous.asset_id!r}; "
                f"two footprints in one cell overlap by construction"
            )
        else:
            occupied[asset.cell] = asset

        if asset.scale <= 0.0:
            problems.append(f"{label} has non-positive scale {asset.scale}")
            continue

        # An endpoint marker that is drawn outside the grid is not competing for
        # room with a corridor, so the safe inset is not the rule it has to
        # keep. What it does have to keep is being outside: a marker that
        # reached back over the grid would cover the opening it is pointing at.
        if asset.cell in openings and asset.role in (ROLE_START, ROLE_FINISH):
            size = marker_size if marker_size > 0.0 else geometry.cell * asset.scale
            rect = marker_footprint(asset, openings[asset.cell], geometry, size=size).rect
            gx0, gy0, gx1, gy1 = geometry.bounds
            if not (rect[2] <= gx0 + 1e-9 or rect[0] >= gx1 - 1e-9
                    or rect[3] <= gy0 + 1e-9 or rect[1] >= gy1 - 1e-9):
                problems.append(
                    f"{label} is drawn outside the grid but its square still overlaps it"
                )
            continue

        footprint = asset_footprint(asset, geometry)
        safe = safe_inset_rect(asset.cell, geometry, clearance_fraction)
        if (
            footprint.rect[0] < safe[0] - 1e-9 or footprint.rect[1] < safe[1] - 1e-9
            or footprint.rect[2] > safe[2] + 1e-9 or footprint.rect[3] > safe[3] + 1e-9
        ):
            limit = 1.0 - 2.0 * clearance_fraction
            problems.append(
                f"{label} has scale {asset.scale:g}, which overflows the cell's safe "
                f"inset; with clearance {clearance_fraction:g} the ceiling is "
                f"{limit:.3g}"
            )

        if asset.role == ROLE_DEAD_END and len(adjacency.get(asset.cell, ())) > 1:
            problems.append(
                f"{label} marks a cell of degree {len(adjacency.get(asset.cell, ()))}; "
                f"a dead-end marker on a junction covers a real choice"
            )

    for role, cell in ((ROLE_START, maze.start), (ROLE_FINISH, maze.finish)):
        placed = [a for a in maze.assets if a.role == role]
        if len(placed) != 1:
            problems.append(f"expected exactly one {role} asset, found {len(placed)}")
        elif placed[0].cell != cell:
            problems.append(
                f"{role} asset is at {placed[0].cell} but MazeData.{role} is {cell}"
            )

    return problems


def raise_for_placements(
    maze: MazeData,
    *,
    clearance_fraction: float,
    marker_size: float = 0.0,
    geometry: MazeGeometry | None = None,
) -> None:
    from ..errors import RenderingError

    problems = validate_placements(
        maze,
        clearance_fraction=clearance_fraction,
        marker_size=marker_size,
        geometry=geometry,
    )
    if problems:
        raise RenderingError(
            f"maze {maze.maze_index} has {len(problems)} asset placement violation(s)",
            details=problems,
        )
