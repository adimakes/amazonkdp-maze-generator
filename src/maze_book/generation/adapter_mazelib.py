"""The only module in this repository that may import ``mazelib`` (PRD 17.7).

``mazelib`` models a maze as a ``(2*rows+1, 2*cols+1)`` numpy array of walls.
That representation is convenient for its own algorithms and wrong for ours: we
need open edges over a cell graph so that loops, dead-end depth and route
enumeration are all statements about the same object. The translation happens
here, once, and nothing downstream ever sees a wall array.
"""

from __future__ import annotations

from ..errors import GenerationError
from ..model.geometry import Edge, canonical_edge, sort_edges
from .base import validate_spanning_tree

GENERATOR_ID = "mazelib_recursive_backtracker"


def _mazelib_version() -> str:
    try:
        import mazelib

        return str(getattr(mazelib, "__version__", "unknown"))
    except Exception:  # pragma: no cover - import failure is reported by carve()
        return "unknown"


class MazelibRecursiveBacktracker:
    """Recursive-backtracking carver: long winding corridors, many dead ends.

    Recursive backtracking is the right base for this book because it produces
    *long* solution paths -- the braid stage can always remove structure, but it
    cannot invent a corridor that the carver never dug. The many dead ends it
    leaves are the raw material the rebalancer spends.
    """

    generator_id = GENERATOR_ID

    def __init__(self) -> None:
        self.generator_version = f"mazelib-{_mazelib_version()}"

    def carve(self, rows: int, cols: int, seed: int) -> list[Edge]:
        if rows < 2 or cols < 2:
            raise GenerationError(f"grid must be at least 2x2, got {rows}x{cols}")
        try:
            from mazelib import Maze
            from mazelib.generate.BacktrackingGenerator import BacktrackingGenerator
        except ImportError as exc:  # pragma: no cover - environment problem
            raise GenerationError(
                "mazelib is required to generate mazes", details=str(exc)
            ) from exc

        maze = Maze(seed=seed & 0x7FFFFFFF)
        maze.generator = BacktrackingGenerator(rows, cols)
        maze.generate()
        grid = maze.grid
        expected_shape = (2 * rows + 1, 2 * cols + 1)
        if tuple(grid.shape) != expected_shape:
            raise GenerationError(
                f"mazelib returned a {tuple(grid.shape)} grid, expected {expected_shape}"
            )

        # grid[2r+1][2c+1] is cell (r, c); the entry between two cell entries is
        # the passage, so a 0 there means the edge is open.
        edges: list[Edge] = []
        for r in range(rows):
            for c in range(cols):
                if c + 1 < cols and grid[2 * r + 1][2 * c + 2] == 0:
                    edges.append(canonical_edge((r, c), (r, c + 1)))
                if r + 1 < rows and grid[2 * r + 2][2 * c + 1] == 0:
                    edges.append(canonical_edge((r, c), (r + 1, c)))
        result = sort_edges(edges)
        validate_spanning_tree(rows, cols, result)
        return result


#: Registry of base generators, keyed by the string a book config uses.
BASE_GENERATORS = {GENERATOR_ID: MazelibRecursiveBacktracker}


def base_generator(generator_id: str):
    """Instantiate the base generator a book config names."""
    try:
        factory = BASE_GENERATORS[generator_id]
    except KeyError:
        raise GenerationError(
            f"unknown baseGenerator '{generator_id}'",
            details={"available": sorted(BASE_GENERATORS)},
        ) from None
    return factory()
