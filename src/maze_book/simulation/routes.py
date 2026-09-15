"""Exact simple-route enumeration (PRD 17.7 step 7, 18.2).

The whole product rests on this file. "Choose the path that collects the most
candy" is only a puzzle if the set of start->finish routes is known *exactly*:
an approximate count can miss the route that ties with the intended answer, and
then the printed answer key is wrong in a book that has already been sold.

So this module never samples and never estimates. It enumerates, and when a
configured bound stops it early it reports ``exact=False`` and the caller MUST
reject the attempt rather than ship a maze whose answer key is merely probable.

Two ideas make exhaustive enumeration affordable:

1.  *Reduction.* Simple start->finish paths live entirely inside the 2-core plus
    the corridors leading to the endpoints, so every dead-end tree is peeled off
    first (``graph.path_relevant_subgraph``). Most cells in a braided maze are in
    those trees, and none of them can appear on a route.
2.  *Bitset reachability pruning.* Before descending into a neighbour, the finish
    must still be reachable through unvisited cells. The check is a BFS over
    machine integers -- one ``int`` per frontier, precomputed neighbour masks --
    so it costs a handful of word operations per level instead of a Python loop
    over cells.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..model.geometry import DIRECTIONS, Cell, cell_index, neighbors
from ..model.maze_data import MazeData
from . import graph
from .graph import Adjacency

#: Bound names, used verbatim in ``abort_reason`` and in error messages.
ABORT_MAX_ROUTES = "maxRoutes"
ABORT_MAX_SEARCH_NODES = "maxSearchNodes"
ABORT_TIME_BUDGET = "timeBudgetSeconds"

_NODES_PER_CLOCK_CHECK = 4096

#: (row delta, col delta) -> index into ``DIRECTIONS``.
_DELTA_TO_DIRECTION = {delta: index for index, delta in enumerate(DIRECTIONS)}


def sorted_adjacency(maze: MazeData) -> Adjacency:
    """Adjacency with neighbours in fixed N, E, S, W order.

    ``MazeData.adjacency`` returns sets, whose iteration order is an
    implementation detail. Route order is part of the answer key, so the graph
    handed to the search must have a declared order.
    """
    raw = maze.adjacency()
    return {
        cell: [n for n in neighbors(cell, maze.rows, maze.cols) if n in raw[cell]]
        for cell in raw
    }


@dataclass(frozen=True, slots=True)
class Route:
    """One simple start->finish path.

    Stored as a bitmask of visited cells plus the step directions rather than a
    cell list: scoring only ever needs the mask, and the direction bytes rebuild
    the exact cell sequence -- including which of two equal-length detours was
    taken -- for a few hundredth of the memory a list of tuples would cost.
    """

    mask: int
    directions: bytes

    @property
    def length(self) -> int:
        """Number of cells, which is one more than the number of steps."""
        return len(self.directions) + 1

    def cells(self, start: Cell) -> list[Cell]:
        row, col = start
        path = [start]
        for direction in self.directions:
            delta_row, delta_col = DIRECTIONS[direction]
            row += delta_row
            col += delta_col
            path.append((row, col))
        return path


@dataclass
class RouteSet:
    """Every simple route through one maze, plus the honesty flags."""

    start: Cell
    finish: Cell
    rows: int
    cols: int
    routes: list[Route] = field(default_factory=list)
    exact: bool = True
    abort_reason: str | None = None
    nodes_visited: int = 0
    elapsed_seconds: float = 0.0
    reachable_cells: int = 0
    traversable_cells: int = 0
    reduced_cells: int = 0

    # -- basic measures ---------------------------------------------------

    def __len__(self) -> int:
        return len(self.routes)

    @property
    def count(self) -> int:
        return len(self.routes)

    @property
    def lengths(self) -> list[int]:
        return [route.length for route in self.routes]

    @property
    def shortest_length(self) -> int:
        return min((route.length for route in self.routes), default=0)

    @property
    def longest_length(self) -> int:
        return max((route.length for route in self.routes), default=0)

    @property
    def reachable_fraction(self) -> float:
        if self.traversable_cells == 0:
            return 0.0
        return self.reachable_cells / self.traversable_cells

    def shortest_indices(self) -> list[int]:
        best = self.shortest_length
        return [i for i, route in enumerate(self.routes) if route.length == best]

    def cells_for(self, route_index: int) -> list[Cell]:
        return self.routes[route_index].cells(self.start)

    # -- candy guidance ---------------------------------------------------

    def cell_route_counts(self) -> dict[Cell, int]:
        """How many routes pass through each cell.

        Candy placement uses this directly: a cell on every route is a tax, a
        cell on one route is a secret, and the interesting sweets sit in the
        middle band where taking them means giving something else up.
        """
        counts: dict[int, int] = {}
        for route in self.routes:
            mask = route.mask
            while mask:
                lowest = mask & -mask
                index = lowest.bit_length() - 1
                counts[index] = counts.get(index, 0) + 1
                mask ^= lowest
        cols = self.cols
        return {divmod(index, cols): value for index, value in counts.items()}

    def cell_route_frequency(self) -> dict[Cell, float]:
        """``cell_route_counts`` normalized to 0..1; absent cells are 0.0."""
        total = self.count
        if total == 0:
            return {}
        return {cell: value / total for cell, value in self.cell_route_counts().items()}

    def route_cells_union(self) -> set[Cell]:
        cols = self.cols
        union = 0
        for route in self.routes:
            union |= route.mask
        cells: set[Cell] = set()
        while union:
            lowest = union & -union
            cells.add(divmod(lowest.bit_length() - 1, cols))
            union ^= lowest
        return cells


# --------------------------------------------------------------------------- #
# Enumeration
# --------------------------------------------------------------------------- #

def enumerate_routes(
    maze: MazeData,
    *,
    max_routes: int,
    max_search_nodes: int,
    time_budget_seconds: float,
    adjacency: Adjacency | None = None,
) -> RouteSet:
    """Enumerate every simple start->finish route, or explain why it stopped.

    Args:
        maze: the maze to search; only its graph, start and finish are read.
        max_routes: stop once this many routes have been found. Reaching it means
            the maze is too open to be scored honestly, not that the search is
            good enough.
        max_search_nodes: stop after this many DFS descents.
        time_budget_seconds: wall-clock ceiling for this one maze.
        adjacency: precomputed sorted adjacency, to avoid rebuilding it.

    Returns:
        A ``RouteSet``. ``exact`` is False exactly when ``abort_reason`` is set,
        and in that case ``routes`` holds only what was found before the bound
        tripped -- useful for diagnostics, never for an answer key.
    """
    adj = adjacency if adjacency is not None else sorted_adjacency(maze)
    cols = maze.cols
    start, finish = maze.start, maze.finish

    result = RouteSet(
        start=start,
        finish=finish,
        rows=maze.rows,
        cols=cols,
        traversable_cells=len(adj),
    )
    if start not in adj or finish not in adj:
        # A start or finish on a blocked cell is a configuration bug, but this
        # module reports rather than raises so the pipeline can log the attempt.
        return result

    reachable = graph.reachable_from(adj, start)
    result.reachable_cells = len(reachable)
    if finish not in reachable:
        return result
    if start == finish:
        result.routes.append(Route(mask=1 << cell_index(start, cols), directions=b""))
        result.reduced_cells = 1
        return result

    reduction = graph.path_relevant_subgraph(adj, start, finish)
    reduced = reduction.adjacency
    result.reduced_cells = len(reduced)

    # Dense integer ids over the reduced graph only, so the bitmasks stay narrow.
    order = sorted(reduced)
    index_of = {cell: i for i, cell in enumerate(order)}
    neighbor_ids: list[tuple[int, ...]] = [
        tuple(index_of[n] for n in sorted(reduced[cell])) for cell in order
    ]
    neighbor_mask: list[int] = [
        sum(1 << nid for nid in ids) for ids in neighbor_ids
    ]
    start_id = index_of[start]
    finish_id = index_of[finish]
    finish_bit = 1 << finish_id
    all_bits = (1 << len(order)) - 1

    # Direction byte for each ordered pair of reduced-graph ids.
    step_direction: dict[tuple[int, int], int] = {}
    for cell, cell_id in index_of.items():
        for neighbor_id in neighbor_ids[cell_id]:
            other = order[neighbor_id]
            delta = (other[0] - cell[0], other[1] - cell[1])
            step_direction[(cell_id, neighbor_id)] = _DELTA_TO_DIRECTION[delta]

    def finish_still_reachable(current: int, visited: int) -> bool:
        """Bitset BFS from ``current`` through cells absent from ``visited``."""
        allowed = (~visited & all_bits) | (1 << current)
        seen = 1 << current
        frontier = seen
        while frontier:
            expanded = 0
            bits = frontier
            while bits:
                lowest = bits & -bits
                expanded |= neighbor_mask[lowest.bit_length() - 1]
                bits ^= lowest
            frontier = expanded & allowed & ~seen
            if frontier & finish_bit:
                return True
            seen |= frontier
        return False

    routes = result.routes
    path: list[int] = [start_id]
    cursor: list[int] = [0]
    visited = 1 << start_id
    nodes = 0
    started = time.monotonic()
    deadline = started + time_budget_seconds
    abort: str | None = None

    while path and abort is None:
        node = path[-1]
        candidates = neighbor_ids[node]
        position = cursor[-1]
        if position >= len(candidates):
            visited &= ~(1 << node)
            path.pop()
            cursor.pop()
            continue
        cursor[-1] = position + 1
        candidate = candidates[position]
        candidate_bit = 1 << candidate
        if visited & candidate_bit:
            continue

        if candidate == finish_id:
            steps = bytes(
                step_direction[(path[i], path[i + 1])] for i in range(len(path) - 1)
            ) + bytes([step_direction[(node, candidate)]])
            routes.append(Route(mask=visited | candidate_bit, directions=steps))
            if len(routes) >= max_routes:
                abort = ABORT_MAX_ROUTES
            continue

        nodes += 1
        if nodes >= max_search_nodes:
            abort = ABORT_MAX_SEARCH_NODES
            break
        if nodes % _NODES_PER_CLOCK_CHECK == 0 and time.monotonic() > deadline:
            abort = ABORT_TIME_BUDGET
            break
        if not finish_still_reachable(candidate, visited | candidate_bit):
            continue

        visited |= candidate_bit
        path.append(candidate)
        cursor.append(0)

    result.nodes_visited = nodes
    result.elapsed_seconds = time.monotonic() - started
    if abort is not None:
        result.exact = False
        result.abort_reason = abort

    # Re-key masks from reduced-graph ids to whole-grid cell indices so that
    # every consumer speaks one coordinate language.
    grid_bit = [1 << cell_index(cell, cols) for cell in order]
    result.routes = [
        Route(mask=_remap_mask(route.mask, grid_bit), directions=route.directions)
        for route in routes
    ]
    return result


def _remap_mask(mask: int, grid_bit: list[int]) -> int:
    out = 0
    while mask:
        lowest = mask & -mask
        out |= grid_bit[lowest.bit_length() - 1]
        mask ^= lowest
    return out
