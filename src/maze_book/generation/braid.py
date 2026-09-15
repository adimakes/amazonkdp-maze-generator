"""Turning a perfect maze into the maze the book actually needs (PRD 17.7 step 4).

A recursive-backtracker maze is a spanning tree: exactly one route from start to
finish, and every wrong turn is a dead end. That is the wrong shape for a
candy-collecting puzzle, which needs *choices* -- several genuinely different
routes, one of which collects more sweets than the rest.

So the tree gets reshaped by four independent controls, each of which moves one
measurement in one direction:

======================  ==========================================  =========
want                    do                                           side effect
======================  ==========================================  =========
fewer / shallower       add an edge at a dead end (or inside its     loops +1
dead ends               corridor, which turns a corridor cell into
                        a junction and shortens the dead end)
more dead ends          cut a cycle edge next to a junction          loops -1
fewer loops             cut a cycle edge between two junctions       none
more loops              add an edge between two non-dead-end cells   none
======================  ==========================================  =========

Every measurement is taken from the graph, never predicted: the loop count is
``E - V + C`` and the dead-end count is a degree census. That matters because the
"obvious" prediction is wrong -- knocking out a wall does not reliably add a
loop, and joining two dead ends removes two of them at once.

**Girth is preserved by construction.** Every cycle in the finished maze contains
some edge; take the one added last. When it was added, the rest of the cycle
already existed (removals never create cycles), so the cycle is at least
``1 + dist(a, b)`` long, and an edge is only ever added when that is at least
``minLoopLength``. No separate girth check is needed, and none is performed.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model.geometry import Cell, Edge, canonical_edge, neighbors
from ..model.profile import Band
from ..simulation import graph as g

#: Hard iteration ceilings. Each control removes or adds one edge per step, and
#: a grid has O(rows * cols) edges, so these are "something is looping" guards
#: rather than tuning knobs.
_MAX_STEPS = 4000


@dataclass
class ShapeReport:
    """What the rebalancer did, and whether it landed inside the band."""

    edges: list[Edge]
    loop_count: int
    dead_ends: list[Cell]
    depths: list[int]
    additions: int = 0
    removals: int = 0
    stage: str = "ok"
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.stage == "ok"

    @property
    def dead_end_count(self) -> int:
        return len(self.dead_ends)

    @property
    def max_depth(self) -> int:
        return max(self.depths, default=0)

    def summary(self) -> str:
        return (
            f"k={self.loop_count} deadEnds={self.dead_end_count} "
            f"maxDepth={self.max_depth} +{self.additions}/-{self.removals}"
        )


class _Graph:
    """A mutable open-edge graph with the measurements the controls need.

    Adjacency is stored as sets so that an edge can be removed in constant time;
    ``sorted_neighbors`` is what callers use when order has to be deterministic.
    """

    __slots__ = ("rows", "cols", "adj", "keep")

    def __init__(self, rows: int, cols: int, edges: list[Edge], keep: frozenset[Cell]):
        self.rows = rows
        self.cols = cols
        self.keep = keep
        self.adj: dict[Cell, set[Cell]] = {
            (r, c): set() for r in range(rows) for c in range(cols)
        }
        for a, b in edges:
            self.adj[a].add(b)
            self.adj[b].add(a)

    # -- measurement ------------------------------------------------------

    def as_list_adjacency(self) -> dict[Cell, list[Cell]]:
        return {
            cell: [n for n in neighbors(cell, self.rows, self.cols) if n in linked]
            for cell, linked in self.adj.items()
        }

    def degree(self, cell: Cell) -> int:
        return len(self.adj[cell])

    def loop_count(self) -> int:
        return g.cyclomatic_number(self.as_list_adjacency())

    def dead_ends(self) -> list[Cell]:
        return sorted(
            cell
            for cell, linked in self.adj.items()
            if len(linked) == 1 and cell not in self.keep
        )

    def distances(self, source: Cell) -> dict[Cell, int]:
        return g.bfs_distances(self.as_list_adjacency(), source)

    def depth(self, cell: Cell) -> int:
        """Steps from a dead end to the nearest junction; see graph.dead_end_depth."""
        return g.dead_end_depth(self.as_list_adjacency(), cell)

    def corridor_toward_junction(self, tip: Cell) -> list[Cell]:
        """Cells from a dead end up to (not including) the first junction.

        ``[tip]`` for a nub beside a junction, ``[tip, next, ...]`` for a longer
        stub. This is the run of cells where an added edge would shorten the dead
        end instead of removing it.
        """
        run = [tip]
        previous = tip
        current = next(iter(self.adj[tip]))
        while len(self.adj[current]) == 2:
            run.append(current)
            following = [n for n in self.adj[current] if n != previous]
            if not following:
                break
            previous, current = current, following[0]
            if current == tip:  # a closed corridor loop; nothing more to walk
                break
        return run

    # -- mutation ---------------------------------------------------------

    def open_edge(self, a: Cell, b: Cell) -> None:
        self.adj[a].add(b)
        self.adj[b].add(a)

    def close_edge(self, a: Cell, b: Cell) -> None:
        self.adj[a].discard(b)
        self.adj[b].discard(a)

    def is_open(self, a: Cell, b: Cell) -> bool:
        return b in self.adj[a]

    def on_cycle(self, a: Cell, b: Cell) -> bool:
        """True when removing ``a-b`` keeps the graph connected."""
        self.close_edge(a, b)
        connected = b in g.reachable_from(self.as_list_adjacency(), a)
        self.open_edge(a, b)
        return connected

    def edges(self) -> list[Edge]:
        from ..model.geometry import sort_edges

        return sort_edges(
            canonical_edge(a, b) for a, linked in self.adj.items() for b in linked
        )


# --------------------------------------------------------------------------- #
# The four controls
# --------------------------------------------------------------------------- #

def _closable_pairs(graph: _Graph, cell: Cell, min_loop_length: int) -> list[Cell]:
    """Grid neighbours of ``cell`` that may be joined to it.

    The distance test is what keeps the added loop worth walking: joining two
    cells that are already three steps apart makes a four-cell cycle, which reads
    as a wide corridor rather than a choice.
    """
    distances = graph.distances(cell)
    out = []
    for n in neighbors(cell, graph.rows, graph.cols):
        if graph.is_open(cell, n):
            continue
        distance = distances.get(n)
        if distance is None or distance + 1 < min_loop_length:
            continue
        out.append(n)
    return out


def _dead_end_target(band: Band, base_dead_ends: int, rng) -> int:
    """Pick the dead-end count to aim for, given how many the tree arrived with.

    Aiming at the ceiling every time would give every maze in an act the same
    census, which reads as mechanical across ten facing pages. But aiming low is
    not free: each dead end is removed by *adding* an edge, and each addition
    adds a loop. Going from ``base_dead_ends`` down to ``t`` therefore costs about
    ``base_dead_ends - t`` loops, and if that exceeds the loop ceiling the maze
    cannot be saved afterwards -- shedding a loop needs an edge between two
    junctions, which is the one move that is genuinely scarce.

    So the floor is the arithmetic one, ``base_dead_ends - loops.max``, and the
    variety comes from the top few values above it.
    """
    affordable = max(band.dead_ends.min, base_dead_ends - band.loops.max)
    low = min(max(affordable, band.dead_ends.max - 3), band.dead_ends.max)
    return rng.randint(low, band.dead_ends.max)


def _fix_dead_ends(graph: _Graph, band: Band, target: int, rng) -> tuple[int, str]:
    """Bring the dead-end census inside the band, deepest offenders first.

    Two different problems are solved by the same control. Too *many* dead ends
    is fixed by joining a tip to a far-away cell, which removes it (and removes
    two at once when the far cell is itself a tip). Too *deep* a dead end is
    fixed by joining a cell *inside* its corridor to a far-away cell: the
    corridor cell becomes a junction, so the dead end survives but is shorter.
    That distinction is why a maze can have twenty dead ends and none of them
    longer than five steps.
    """
    additions = 0
    depth_max = band.dead_end_depth.max if band.dead_end_depth else None
    for _ in range(_MAX_STEPS):
        dead = graph.dead_ends()
        depths = {cell: graph.depth(cell) for cell in dead}
        too_many = len(dead) - target
        too_deep = (
            [cell for cell, d in depths.items() if d > depth_max]
            if depth_max is not None
            else []
        )
        if too_many <= 0 and not too_deep:
            return additions, ""

        if too_many > 0:
            # Removing a tip helps both problems; take the deepest first so the
            # survivors are the shallow, child-friendly ones.
            order = sorted(dead, key=lambda c: (-depths[c], c))
            choice = _remove_a_tip(graph, order, dead, band)
            if choice is not None:
                additions += 1
                continue
            # Every tip is boxed in. Shortening still helps the depth rule, so
            # fall through rather than give up here.
        if too_deep:
            order = sorted(too_deep, key=lambda c: (-depths[c], c))
            if _shorten_a_dead_end(graph, order, band, depth_max, rng):
                additions += 1
                continue
        return additions, (
            f"cannot reshape dead ends: {len(dead)} present "
            f"(band {band.dead_ends.min}-{band.dead_ends.max}), "
            f"deepest {max(depths.values(), default=0)}"
            + (f" (ceiling {depth_max})" if depth_max is not None else "")
        )
    return additions, "dead-end reshaping did not settle"


def _remove_a_tip(graph: _Graph, order: list[Cell], dead: list[Cell], band: Band) -> Cell | None:
    """Join one dead-end tip to a far cell, preferring joins that kill two."""
    dead_set = set(dead)
    best: tuple[int, Cell, Cell] | None = None
    for tip in order:
        for other in _closable_pairs(graph, tip, band.min_loop_length):
            kills = 2 if other in dead_set else 1
            if best is None or kills > best[0]:
                best = (kills, tip, other)
        if best is not None and best[0] == 2:
            break
    if best is None:
        return None
    _, tip, other = best
    graph.open_edge(tip, other)
    return tip


def _shorten_a_dead_end(graph: _Graph, order: list[Cell], band: Band, depth_max: int, rng) -> bool:
    """Turn a cell inside an over-long dead end into a junction."""
    for tip in order:
        run = graph.corridor_toward_junction(tip)
        # Index i in the run sits i steps from the tip; making it a junction
        # leaves a dead end of depth i. Depth 0 would remove the dead end, which
        # is _remove_a_tip's job, so start at 1 and prefer the deepest legal cut
        # for variety.
        for index in range(min(depth_max, len(run) - 1), 0, -1):
            cell = run[index]
            options = _closable_pairs(graph, cell, band.min_loop_length)
            if not options:
                continue
            graph.open_edge(cell, rng.choice(sorted(options)))
            return True
    return False


def _depths_in_band(graph: _Graph, band: Band) -> bool:
    if band.dead_end_depth is None:
        return True
    return all(band.dead_end_depth.contains(graph.depth(cell)) for cell in graph.dead_ends())


def _raise_dead_ends(graph: _Graph, band: Band, target: int, rng) -> tuple[int, str]:
    """Cut cycle edges to create dead ends until the census reaches ``target``.

    Cutting the edge between a junction and a corridor cell leaves that corridor
    cell with one neighbour: a new dead end whose depth is however far it is from
    the next junction the other way.

    The subtlety is that the cut also demotes the junction to degree two, and a
    dead end elsewhere may have been measuring its depth *to that junction*. Lose
    it and the depth jumps to whatever the next junction is -- in one probe, from
    inside a 1-5 band to seventeen. So every cut is made speculatively and undone
    unless all dead-end depths are still inside the band.
    """
    removals = 0
    depth_range = band.dead_end_depth
    for _ in range(_MAX_STEPS):
        dead = graph.dead_ends()
        if len(dead) >= target:
            return removals, ""
        candidates: list[tuple[int, Cell, Cell]] = []
        for cell in sorted(graph.adj):
            if graph.degree(cell) != 2 or cell in graph.keep:
                continue
            for other in sorted(graph.adj[cell]):
                if graph.degree(other) < 3:
                    continue
                if not graph.on_cycle(cell, other):
                    continue
                graph.close_edge(cell, other)
                depth = graph.depth(cell)
                graph.open_edge(cell, other)
                if depth_range is not None and not depth_range.contains(depth):
                    continue
                candidates.append((depth, cell, other))
        if not candidates:
            return removals, (
                f"only {len(dead)} dead ends, aiming for {target}, "
                "and no cycle edge can be cut into one"
            )
        # Shallow nubs first: they are the cheapest to place a decoration in and
        # the least frustrating to walk into.
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        window = candidates[: max(1, len(candidates) // 4)]
        rng.shuffle(window)
        for _depth, cell, other in window:
            graph.close_edge(cell, other)
            if _depths_in_band(graph, band):
                removals += 1
                break
            graph.open_edge(cell, other)
        else:
            return removals, (
                f"only {len(dead)} dead ends, aiming for {target}, and every "
                "available cut would deepen another dead end past the ceiling"
            )
    return removals, "dead-end carving did not settle"


def _tune_loops(graph: _Graph, band: Band, start: Cell, finish: Cell, rng) -> tuple[int, int, str]:
    """Move the measured loop count into the band without touching dead ends.

    Loops come off by cutting an edge whose two endpoints are both junctions --
    each drops to degree two at worst, so no dead end appears. Loops go on by
    joining two cells that both already have at least two neighbours, preferring
    a join whose new cycle crosses the current shortest route: a loop hanging off
    a side branch adds nothing a solver has to think about.
    """
    additions = removals = 0
    for _ in range(_MAX_STEPS):
        loops = graph.loop_count()
        if band.loops.contains(loops):
            return additions, removals, ""
        if loops > band.loops.max:
            cut = _cut_between_junctions(graph, band, rng)
            if cut is None:
                return additions, removals, (
                    f"{loops} loops, band ceiling {band.loops.max}, and no edge "
                    "between two junctions can be cut without making a dead end "
                    "or deepening one past the ceiling"
                )
            removals += 1
        else:
            if not _add_a_loop(graph, band, start, finish, rng):
                return additions, removals, (
                    f"{loops} loops, band floor {band.loops.min}, and no legal "
                    f"join is at least {band.min_loop_length} cells around"
                )
            additions += 1
    return additions, removals, "loop tuning did not settle"


def _cut_between_junctions(graph: _Graph, band: Band, rng) -> Edge | None:
    """Remove one loop without adding a dead end.

    Both endpoints keep at least two neighbours, so no dead end appears -- but a
    degree-three endpoint stops being a junction, and any dead end that was
    measuring its depth to it now measures to a further one. The cut is therefore
    made speculatively and undone unless every depth is still inside the band,
    the same guard the dead-end carver needs for the same reason.
    """
    candidates = [
        (a, b)
        for a in sorted(graph.adj)
        for b in sorted(graph.adj[a])
        if a < b and graph.degree(a) >= 3 and graph.degree(b) >= 3
    ]
    rng.shuffle(candidates)
    for a, b in candidates:
        if not graph.on_cycle(a, b):
            continue
        graph.close_edge(a, b)
        if _depths_in_band(graph, band):
            return canonical_edge(a, b)
        graph.open_edge(a, b)
    return None


def _add_a_loop(graph: _Graph, band: Band, start: Cell, finish: Cell, rng) -> bool:
    spine = set(g.shortest_path(graph.as_list_adjacency(), start, finish))
    preferred: list[tuple[Cell, Cell]] = []
    fallback: list[tuple[Cell, Cell]] = []
    for cell in sorted(graph.adj):
        if graph.degree(cell) < 2:
            continue
        distances = graph.distances(cell)
        for other in neighbors(cell, graph.rows, graph.cols):
            if other < cell or graph.is_open(cell, other) or graph.degree(other) < 2:
                continue
            distance = distances.get(other)
            if distance is None or distance + 1 < band.min_loop_length:
                continue
            (preferred if (cell in spine or other in spine) else fallback).append(
                (cell, other)
            )
    for pool in (preferred, fallback):
        if pool:
            a, b = rng.choice(pool)
            graph.open_edge(a, b)
            return True
    return False


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def shape(
    *,
    rows: int,
    cols: int,
    tree_edges: list[Edge],
    start: Cell,
    finish: Cell,
    band: Band,
    rng,
) -> ShapeReport:
    """Reshape a spanning tree until it matches ``band``, or report what blocked it.

    A failure here is not an error: the pipeline answers it by regenerating from
    a new attempt seed (PRD 17.8). Returning a report rather than raising keeps
    the reason attached to the attempt that produced it.
    """
    graph = _Graph(rows, cols, tree_edges, frozenset({start, finish}))
    additions = removals = 0
    target = _dead_end_target(band, len(graph.dead_ends()), rng)

    added, problem = _fix_dead_ends(graph, band, target, rng)
    additions += added
    if problem:
        return _report(graph, additions, removals, "dead-ends", problem)

    removed, problem = _raise_dead_ends(graph, band, target, rng)
    removals += removed
    if problem:
        return _report(graph, additions, removals, "dead-ends", problem)

    added, removed, problem = _tune_loops(graph, band, start, finish, rng)
    additions += added
    removals += removed
    if problem:
        return _report(graph, additions, removals, "loops", problem)

    report = _report(graph, additions, removals, "ok", "")
    # Loop tuning cannot create dead ends, but carving can overshoot the ceiling
    # when a single cut splits a corridor in two, so the census is re-checked
    # against the band here rather than trusted.
    if not band.dead_ends.contains(report.dead_end_count):
        report.stage = "dead-ends"
        report.detail = (
            f"settled at {report.dead_end_count} dead ends, "
            f"band {band.dead_ends.min}-{band.dead_ends.max}"
        )
    elif band.dead_end_depth is not None and report.depths:
        out_of_band = [d for d in report.depths if not band.dead_end_depth.contains(d)]
        if out_of_band:
            report.stage = "dead-ends"
            report.detail = (
                f"dead-end depths {sorted(set(out_of_band))} outside band "
                f"{band.dead_end_depth.min}-{band.dead_end_depth.max}"
            )
    return report


def _report(graph: _Graph, additions: int, removals: int, stage: str, detail: str) -> ShapeReport:
    dead = graph.dead_ends()
    return ShapeReport(
        edges=graph.edges(),
        loop_count=graph.loop_count(),
        dead_ends=dead,
        depths=[graph.depth(cell) for cell in dead],
        additions=additions,
        removals=removals,
        stage=stage,
        detail=detail,
    )
