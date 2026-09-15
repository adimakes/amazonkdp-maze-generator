"""Grid primitives shared by every layer.

A cell is ``(row, col)``, zero-based, row-major from the top-left
(PRD 17.5 ``coordinateSystem: "row-major-top-left"``).

An *edge* is an unordered pair of orthogonally adjacent cells with no wall
between them. Edges are always stored in canonical form -- the lexicographically
smaller cell first -- so that the same passage has exactly one representation and
``MazeData`` JSON is byte-comparable.
"""

from __future__ import annotations

from typing import Iterable, Iterator

Cell = tuple[int, int]
Edge = tuple[Cell, Cell]

#: (row delta, col delta) for the four orthogonal directions, in a fixed order.
DIRECTIONS: tuple[tuple[int, int], ...] = ((-1, 0), (0, 1), (1, 0), (0, -1))
DIRECTION_NAMES: tuple[str, ...] = ("N", "E", "S", "W")


def canonical_edge(a: Cell, b: Cell) -> Edge:
    """Return ``(a, b)`` ordered lexicographically.

    Raises:
        ValueError: if the cells are not orthogonally adjacent.
    """
    if not is_adjacent(a, b):
        raise ValueError(f"cells {a} and {b} are not orthogonally adjacent")
    return (a, b) if a <= b else (b, a)


def is_adjacent(a: Cell, b: Cell) -> bool:
    """True when ``a`` and ``b`` share a side (never diagonally, never equal)."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1


def in_bounds(cell: Cell, rows: int, cols: int) -> bool:
    return 0 <= cell[0] < rows and 0 <= cell[1] < cols


def neighbors(cell: Cell, rows: int, cols: int) -> Iterator[Cell]:
    """Yield in-bounds orthogonal neighbours in fixed N, E, S, W order."""
    r, c = cell
    for dr, dc in DIRECTIONS:
        nr, nc = r + dr, c + dc
        if 0 <= nr < rows and 0 <= nc < cols:
            yield (nr, nc)


def cell_index(cell: Cell, cols: int) -> int:
    """Flatten a cell to a single integer, for bitset work."""
    return cell[0] * cols + cell[1]


def index_cell(index: int, cols: int) -> Cell:
    return divmod(index, cols)


def sort_edges(edges: Iterable[Edge]) -> list[Edge]:
    """Deduplicate and sort edges into canonical serialization order."""
    return sorted(set(edges))


def resolve_region(
    region: dict[str, int], rows: int, cols: int
) -> list[Cell]:
    """Resolve a start/finish region to the list of cells it covers.

    Negative bounds count back from the far edge, Python-slice style: ``-1`` is
    the last row/column, ``-3`` the third from last (PRD 17.3). The resolved
    window is clamped to the grid and MUST be non-empty.
    """

    def resolve(value: int, limit: int) -> int:
        return value + limit if value < 0 else value

    row_min = max(0, min(rows - 1, resolve(region["rowMin"], rows)))
    row_max = max(0, min(rows - 1, resolve(region["rowMax"], rows)))
    col_min = max(0, min(cols - 1, resolve(region["colMin"], cols)))
    col_max = max(0, min(cols - 1, resolve(region["colMax"], cols)))
    if row_min > row_max:
        row_min, row_max = row_max, row_min
    if col_min > col_max:
        col_min, col_max = col_max, col_min
    return [
        (r, c)
        for r in range(row_min, row_max + 1)
        for c in range(col_min, col_max + 1)
    ]
