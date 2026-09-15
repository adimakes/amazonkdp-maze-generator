"""The one seam a third-party maze library is allowed to sit behind (PRD 17.7).

``mazelib`` gives us a perfect-maze carver and nothing else: no loops, no
dead-end budget, no notion of a route worth taking. Everything this repository
actually cares about is built on top. That makes the library a *base generator*
-- a source of spanning trees -- and this module is the contract it has to meet.

Only ``generation/adapter_mazelib.py`` may import ``mazelib``. If a second
module ever imports it, swapping carvers stops being a config change and becomes
a refactor, and the "one adapter" rule in the PRD has been lost.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..model.geometry import Cell, Edge


@runtime_checkable
class BaseMazeGenerator(Protocol):
    """Produces a spanning tree of the ``rows x cols`` grid graph.

    Implementations must be pure functions of their arguments: the same
    ``(rows, cols, seed)`` must always give the same edge list, because a book is
    reproducible only if every stage of it is.
    """

    #: Stable identifier recorded in maze JSON, e.g.
    #: ``"mazelib_recursive_backtracker"``. Book configs name a generator by
    #: this string, so it is part of the file format and must not drift.
    generator_id: str

    #: Version of the underlying implementation, recorded alongside the id so a
    #: regenerated book can be told apart from its predecessor.
    generator_version: str

    def carve(self, rows: int, cols: int, seed: int) -> list[Edge]:
        """Return the open edges of a spanning tree over every grid cell.

        The result must contain exactly ``rows * cols - 1`` canonical edges and
        connect every cell: a base generator that leaves a cell unreachable has
        produced a maze with no answer, and the pipeline rejects it.
        """
        ...


def validate_spanning_tree(rows: int, cols: int, edges: list[Edge]) -> None:
    """Raise ``GenerationError`` unless ``edges`` is a spanning tree of the grid.

    Called on every base generator's output. A carver that quietly returns a
    forest would surface much later as an unreachable finish, and by then the
    seed that produced it is three stages away.
    """
    from ..errors import GenerationError
    from ..model.geometry import canonical_edge, is_adjacent, in_bounds

    expected = rows * cols - 1
    seen: set[Edge] = set()
    for a, b in edges:
        if not (in_bounds(a, rows, cols) and in_bounds(b, rows, cols)):
            raise GenerationError(f"base generator produced out-of-bounds edge {a}-{b}")
        if not is_adjacent(a, b):
            raise GenerationError(f"base generator produced non-adjacent edge {a}-{b}")
        seen.add(canonical_edge(a, b))
    if len(seen) != expected:
        raise GenerationError(
            f"base generator produced {len(seen)} distinct edges, "
            f"a {rows}x{cols} spanning tree needs {expected}"
        )
    adjacency: dict[Cell, list[Cell]] = {
        (r, c): [] for r in range(rows) for c in range(cols)
    }
    for a, b in seen:
        adjacency[a].append(b)
        adjacency[b].append(a)
    from ..simulation.graph import connected_components

    components = connected_components(adjacency)
    if len(components) != 1:
        raise GenerationError(
            f"base generator produced {len(components)} components, expected 1"
        )
