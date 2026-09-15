"""Graph measurements over the canonical open-edge maze (PRD 17.7 step 5-6).

Everything here *measures*; nothing assumes. The loop count in particular is
computed as ``E - V + C`` from the actual edge set, because removing a wall does
not reliably add a useful loop -- it may reconnect a dead-end stub, or join two
cells that were already connected by a short detour.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ..model.geometry import Cell

Adjacency = dict[Cell, list[Cell]]


# --------------------------------------------------------------------------- #
# Connectivity
# --------------------------------------------------------------------------- #

def connected_components(adj: Adjacency) -> list[set[Cell]]:
    seen: set[Cell] = set()
    components: list[set[Cell]] = []
    for root in adj:
        if root in seen:
            continue
        component = {root}
        queue = deque([root])
        seen.add(root)
        while queue:
            cell = queue.popleft()
            for neighbor in adj[cell]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    component.add(neighbor)
                    queue.append(neighbor)
        components.append(component)
    return components


def reachable_from(adj: Adjacency, source: Cell) -> set[Cell]:
    if source not in adj:
        return set()
    seen = {source}
    queue = deque([source])
    while queue:
        cell = queue.popleft()
        for neighbor in adj[cell]:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return seen


def edge_count(adj: Adjacency) -> int:
    """Undirected edges; each appears twice in an adjacency map."""
    return sum(len(v) for v in adj.values()) // 2


def cyclomatic_number(adj: Adjacency) -> int:
    """``k = E - V + C`` -- the number of independent cycles.

    This is the *measured* loop count. A connected maze has ``C == 1``, so a
    spanning tree gives ``k == 0`` and each genuinely new loop adds one.
    """
    vertices = len(adj)
    if vertices == 0:
        return 0
    return edge_count(adj) - vertices + len(connected_components(adj))


# --------------------------------------------------------------------------- #
# Distances and paths
# --------------------------------------------------------------------------- #

def bfs_distances(adj: Adjacency, source: Cell) -> dict[Cell, int]:
    if source not in adj:
        return {}
    distances = {source: 0}
    queue = deque([source])
    while queue:
        cell = queue.popleft()
        for neighbor in adj[cell]:
            if neighbor not in distances:
                distances[neighbor] = distances[cell] + 1
                queue.append(neighbor)
    return distances


def shortest_path(adj: Adjacency, start: Cell, finish: Cell) -> list[Cell]:
    """One shortest start->finish path as a cell list, or ``[]`` if unreachable.

    Neighbour order is the fixed N/E/S/W order from the adjacency map, so the
    chosen path is deterministic among equally short ones.
    """
    if start not in adj or finish not in adj:
        return []
    if start == finish:
        return [start]
    previous: dict[Cell, Cell] = {start: start}
    queue = deque([start])
    while queue:
        cell = queue.popleft()
        for neighbor in adj[cell]:
            if neighbor in previous:
                continue
            previous[neighbor] = cell
            if neighbor == finish:
                path = [finish]
                while path[-1] != start:
                    path.append(previous[path[-1]])
                path.reverse()
                return path
            queue.append(neighbor)
    return []


# --------------------------------------------------------------------------- #
# Dead ends
# --------------------------------------------------------------------------- #

def degree(adj: Adjacency, cell: Cell) -> int:
    return len(adj.get(cell, ()))


def dead_end_cells(adj: Adjacency, *, exclude: tuple[Cell, ...] = ()) -> list[Cell]:
    """Degree-one traversable cells, excluding start/finish.

    PRD 17.8: the finish is never reported as an unwanted dead end and the start
    is excluded from dead-end asset placement even when its degree is one.
    """
    excluded = set(exclude)
    return sorted(
        cell for cell, neighbors in adj.items() if len(neighbors) == 1 and cell not in excluded
    )


def dead_end_depth(adj: Adjacency, cell: Cell) -> int:
    """Steps from a dead end to the nearest junction (degree >= 3).

    A depth of 1 is a single wasted step; a depth of 6 is a corridor a child
    walks down and has to walk back out of. When no junction is reachable the
    whole component is a corridor, and its size is the honest answer.
    """
    if degree(adj, cell) != 1:
        raise ValueError(f"{cell} is not a dead end (degree {degree(adj, cell)})")
    distances = bfs_distances(adj, cell)
    junctions = [d for c, d in distances.items() if len(adj[c]) >= 3]
    if junctions:
        return min(junctions)
    return max(distances.values()) if distances else 0


def junctions(adj: Adjacency) -> list[Cell]:
    return sorted(cell for cell, neighbors in adj.items() if len(neighbors) >= 3)


# --------------------------------------------------------------------------- #
# Corridor runs (candy spacing)
# --------------------------------------------------------------------------- #

def corridor_runs(adj: Adjacency) -> list[set[Cell]]:
    """Maximal connected groups of degree-two cells.

    Candy spacing is defined per run rather than globally: two sweets a step
    apart inside one straight corridor read as a single blob at 0.375 in, while
    two sweets a step apart across a junction read as a real choice.
    """
    corridor = {cell for cell, neighbors in adj.items() if len(neighbors) == 2}
    runs: list[set[Cell]] = []
    seen: set[Cell] = set()
    for cell in sorted(corridor):
        if cell in seen:
            continue
        run = {cell}
        seen.add(cell)
        queue = deque([cell])
        while queue:
            current = queue.popleft()
            for neighbor in adj[current]:
                if neighbor in corridor and neighbor not in seen:
                    seen.add(neighbor)
                    run.add(neighbor)
                    queue.append(neighbor)
        runs.append(run)
    return runs


def run_index(runs: list[set[Cell]]) -> dict[Cell, int]:
    return {cell: index for index, run in enumerate(runs) for cell in run}


# --------------------------------------------------------------------------- #
# Path-relevant reduction
# --------------------------------------------------------------------------- #

@dataclass
class Reduction:
    """The subgraph that any simple start->finish path is confined to."""

    adjacency: Adjacency
    removed: set[Cell]

    @property
    def size(self) -> int:
        return len(self.adjacency)


def path_relevant_subgraph(adj: Adjacency, start: Cell, finish: Cell) -> Reduction:
    """Strip every vertex that no simple start->finish path can use.

    A degree-one vertex other than ``start``/``finish`` cannot be an endpoint and
    cannot be an interior vertex (that would need two distinct neighbours), so it
    is unusable -- and removing it can expose another. Iterating to a fixed point
    peels off every dead-end tree and leaves the 2-core plus the corridors that
    attach ``start`` and ``finish`` to it. This is what makes exact enumeration
    tractable: the dead-end trees, which are most of a maze's cells, contribute
    nothing but DFS work.
    """
    working: Adjacency = {cell: list(neighbors) for cell, neighbors in adj.items()}
    keep = {start, finish}
    peeled: set[Cell] = set()
    frontier = deque(
        cell for cell, neighbors in working.items() if len(neighbors) <= 1 and cell not in keep
    )
    while frontier:
        cell = frontier.popleft()
        if cell not in working or cell in keep:
            continue
        if len(working[cell]) > 1:
            continue
        for neighbor in working[cell]:
            working[neighbor] = [c for c in working[neighbor] if c != cell]
            if len(working[neighbor]) <= 1 and neighbor not in keep:
                frontier.append(neighbor)
        del working[cell]
        peeled.add(cell)
    return Reduction(adjacency=working, removed=peeled)
