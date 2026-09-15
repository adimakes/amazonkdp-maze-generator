"""Acceptance rules, written down exactly once (PRD 8.5, 17.8, 18.2, 18.4).

The generator and the ``maze validate`` command must agree about what a good
maze is, so both call ``evaluate`` here. Duplicating a rule -- one copy that
accepts an attempt and another that checks the exported file -- is how a book
ends up with a maze that passes generation and fails print QA.

Rule codes follow the PRD: C1-C8 are the acceptance rules of 8.5/17.8, G* are
the graph-shape rules of 17.7 step 6 and 18.2, and E* are the extra print-safety
rules of 18.4. Every code lands in ``MazeAnalysis.constraints`` as a boolean, and
every failure adds a sentence to ``errors`` that names the measured value --
"C2 score-margin: best 6 minus second-best 5 is 1, need 2" is actionable;
"C2 failed" is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model.analysis import MazeAnalysis, RouteSummary
from ..model.geometry import Cell, cell_index
from ..model.maze_data import MazeData
from ..model.profile import Band
from . import graph
from .graph import Adjacency
from .routes import RouteSet, sorted_adjacency

#: Rule codes, in the order they are reported.
CODE_EXACT = "G1_exactEnumeration"
CODE_CONNECTED = "G2_startReachesFinish"
CODE_LOOPS = "G3_loopCount"
CODE_DEAD_ENDS = "G4_deadEndCount"
CODE_DEAD_END_DEPTH = "G5_deadEndDepth"
CODE_ROUTE_COUNT = "G6_simpleRouteCount"
CODE_UNIQUE_MAX = "C1_uniqueMaxScore"
CODE_MARGIN = "C2_scoreMargin"
CODE_NOT_SHORTEST = "C3_bestIsNotShortest"
CODE_LENGTH_RATIO = "C4_bestOverShortest"
CODE_TEMPTING = "C5_temptingRoutes"
CODE_CANDY_RANGE = "C6_candyCount"
CODE_CANDY_PLACEMENT = "C7_candyNotInDeadEndOrTerminal"
CODE_CANDY_SPACING = "C8_candySpacing"
CODE_BORDER_HUG = "E1_bestRouteEdgeHugging"
CODE_REACHABILITY = "E2_reachableFraction"

ALL_CODES = (
    CODE_EXACT, CODE_CONNECTED, CODE_LOOPS, CODE_DEAD_ENDS, CODE_DEAD_END_DEPTH,
    CODE_ROUTE_COUNT, CODE_UNIQUE_MAX, CODE_MARGIN, CODE_NOT_SHORTEST,
    CODE_LENGTH_RATIO, CODE_TEMPTING, CODE_CANDY_RANGE, CODE_CANDY_PLACEMENT,
    CODE_CANDY_SPACING, CODE_BORDER_HUG, CODE_REACHABILITY,
)

_EPSILON = 1e-9


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

@dataclass
class CandyScore:
    """What one candy placement does to the route field.

    Scores are popcounts of ``placement_mask & route.mask``: a route collects a
    sweet if and only if the sweet's cell is one of the route's cells, and a
    simple route repeats no cell, so nothing can be counted twice.
    """

    scores: list[int] = field(default_factory=list)
    best_total: int = 0
    best_index: int = -1
    best_count: int = 0
    second_best_total: int = 0
    tempting_indices: list[int] = field(default_factory=list)

    @property
    def unique_best(self) -> bool:
        return self.best_count == 1

    @property
    def margin(self) -> int:
        return self.best_total - self.second_best_total

    @property
    def tempting_count(self) -> int:
        return len(self.tempting_indices)


def placement_mask(cells: "set[Cell] | frozenset[Cell] | tuple[Cell, ...]", cols: int) -> int:
    mask = 0
    for cell in cells:
        mask |= 1 << cell_index(cell, cols)
    return mask


def score_placement(
    route_set: RouteSet, mask: int, *, tempting_fraction: float
) -> CandyScore:
    """Score every route against one candy placement.

    Ties are resolved by route index only to make ``best_index`` deterministic;
    a tie is itself a failure (C1), so the choice never decides a shipped maze.
    """
    result = CandyScore()
    if not route_set.routes:
        return result

    scores = [(mask & route.mask).bit_count() for route in route_set.routes]
    result.scores = scores
    best = max(scores)
    result.best_total = best
    result.best_count = scores.count(best)
    result.best_index = scores.index(best)

    if result.best_count == 1:
        result.second_best_total = max(
            (value for index, value in enumerate(scores) if index != result.best_index),
            default=0,
        )
    else:
        result.second_best_total = best

    threshold = tempting_fraction * best
    result.tempting_indices = [
        index
        for index, value in enumerate(scores)
        if index != result.best_index and value + _EPSILON >= threshold
    ]
    return result


# --------------------------------------------------------------------------- #
# Geometry helpers used by the rules
# --------------------------------------------------------------------------- #

def is_border_cell(cell: Cell, rows: int, cols: int) -> bool:
    """True when the cell touches the outer boundary of the grid.

    "Within one cell of the grid border" (18.4) is the outermost ring: those are
    the cells whose own edge is the boundary line. Reading it as the outer *two*
    rings would fail almost every 8x8 maze, since two rings are 75% of an 8x8
    grid, and the rule is about a route that visibly traces the frame.
    """
    row, col = cell
    return row == 0 or col == 0 or row == rows - 1 or col == cols - 1


def border_hug_fraction(route_cells: list[Cell], rows: int, cols: int) -> float:
    if not route_cells:
        return 0.0
    hugging = sum(1 for cell in route_cells if is_border_cell(cell, rows, cols))
    return hugging / len(route_cells)


def spacing_violations(
    candy_cells: list[Cell], adj: Adjacency, policy: str
) -> list[tuple[Cell, Cell]]:
    """Pairs of candies that sit too close under the active policy.

    ``no-adjacent-in-corridor-run`` is the Halloween rule: two sweets one step
    apart inside a single degree-two corridor read as one blob at 0.375 in, while
    the same spacing across a junction reads as a genuine fork.
    """
    if policy == "none":
        return []
    chosen = set(candy_cells)
    if policy == "no-adjacent":
        return sorted(
            (a, b)
            for a in chosen
            for b in adj.get(a, ())
            if b in chosen and a < b
        )
    if policy == "no-adjacent-in-corridor-run":
        runs = graph.run_index(graph.corridor_runs(adj))
        return sorted(
            (a, b)
            for a in chosen
            for b in adj.get(a, ())
            if b in chosen
            and a < b
            and a in runs
            and b in runs
            and runs[a] == runs[b]
        )
    raise ValueError(f"unknown candy spacing policy {policy!r}")


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #

@dataclass
class Outcome:
    """One rule's verdict."""

    code: str
    passed: bool
    message: str = ""


def check_candy(
    *,
    score: CandyScore,
    band: Band,
    candy_cells: list[Cell],
    route_set: RouteSet,
    adj: Adjacency,
    dead_ends: set[Cell],
) -> list[Outcome]:
    """C1-C8. Split out so the placement search can call it on a candidate."""
    outcomes: list[Outcome] = []
    best_length = (
        route_set.routes[score.best_index].length if score.best_index >= 0 else 0
    )
    shortest = route_set.shortest_length

    outcomes.append(
        Outcome(
            CODE_UNIQUE_MAX,
            score.unique_best,
            f"{score.best_count} routes tie for the top score of {score.best_total}",
        )
    )
    outcomes.append(
        Outcome(
            CODE_MARGIN,
            score.margin >= band.min_margin,
            f"best {score.best_total} minus second-best {score.second_best_total} "
            f"is {score.margin}, need {band.min_margin}",
        )
    )
    if band.shortest_may_be_best:
        outcomes.append(Outcome(CODE_NOT_SHORTEST, True))
    else:
        outcomes.append(
            Outcome(
                CODE_NOT_SHORTEST,
                best_length > shortest,
                f"the best route is also a shortest route ({best_length} cells)",
            )
        )
    ratio_limit = band.max_best_over_shortest * shortest
    outcomes.append(
        Outcome(
            CODE_LENGTH_RATIO,
            best_length <= ratio_limit + _EPSILON,
            f"best route is {best_length} cells, more than "
            f"{band.max_best_over_shortest} x the shortest ({shortest})",
        )
    )
    outcomes.append(
        Outcome(
            CODE_TEMPTING,
            score.tempting_count >= band.min_tempting_routes,
            f"{score.tempting_count} rival routes reach "
            f"{band.tempting_fraction:.0%} of the best score of "
            f"{score.best_total}, need {band.min_tempting_routes}",
        )
    )
    outcomes.append(
        Outcome(
            CODE_CANDY_RANGE,
            band.candies.contains(len(candy_cells)),
            f"{len(candy_cells)} candies, need {band.candies.min}-{band.candies.max}",
        )
    )

    illegal = sorted(
        cell
        for cell in candy_cells
        if cell in dead_ends
        or cell == route_set.start
        or cell == route_set.finish
        or cell not in adj
    )
    outcomes.append(
        Outcome(
            CODE_CANDY_PLACEMENT,
            not illegal,
            f"candies on terminal, dead-end or untraversable cells: {illegal[:6]}",
        )
    )

    violations = spacing_violations(candy_cells, adj, band.candy_spacing_policy)
    outcomes.append(
        Outcome(
            CODE_CANDY_SPACING,
            not violations,
            f"{len(violations)} adjacent candy pairs under policy "
            f"'{band.candy_spacing_policy}': {violations[:4]}",
        )
    )
    return outcomes


def evaluate(
    maze: MazeData,
    route_set: RouteSet,
    band: Band,
    *,
    adjacency: Adjacency | None = None,
    include_cells_for_top: int = 5,
) -> MazeAnalysis:
    """Measure a maze, check every rule, and return the answer-key record.

    The returned ``MazeAnalysis`` is what gets written to disk, so an accepted
    maze ships with the evidence of *why* it was accepted, and a rejected attempt
    can be printed verbatim into a log.
    """
    adj = adjacency if adjacency is not None else sorted_adjacency(maze)
    dead_ends = set(graph.dead_end_cells(adj, exclude=(maze.start, maze.finish)))
    loop_count = graph.cyclomatic_number(adj)

    candy_values = maze.collectible_cells()
    candy_cells = sorted(candy_values)
    mask = placement_mask(tuple(candy_cells), maze.cols)
    score = score_placement(route_set, mask, tempting_fraction=band.tempting_fraction)

    outcomes: list[Outcome] = [
        Outcome(
            CODE_EXACT,
            route_set.exact,
            f"route enumeration stopped early on {route_set.abort_reason} after "
            f"{route_set.count} routes and {route_set.nodes_visited} nodes",
        ),
        Outcome(
            CODE_CONNECTED,
            route_set.count > 0,
            "no simple route connects start to finish",
        ),
        Outcome(
            CODE_LOOPS,
            band.loops.contains(loop_count),
            f"loop count k={loop_count}, need {band.loops.min}-{band.loops.max}",
        ),
        Outcome(
            CODE_DEAD_ENDS,
            band.dead_ends.contains(len(dead_ends)),
            f"{len(dead_ends)} dead ends, need "
            f"{band.dead_ends.min}-{band.dead_ends.max}",
        ),
    ]

    if band.dead_end_depth is None:
        outcomes.append(Outcome(CODE_DEAD_END_DEPTH, True))
    else:
        too_deep = sorted(
            (cell, graph.dead_end_depth(adj, cell))
            for cell in dead_ends
            if not band.dead_end_depth.contains(graph.dead_end_depth(adj, cell))
        )
        outcomes.append(
            Outcome(
                CODE_DEAD_END_DEPTH,
                not too_deep,
                f"dead ends outside depth {band.dead_end_depth.min}-"
                f"{band.dead_end_depth.max}: {too_deep[:6]}",
            )
        )

    outcomes.append(
        Outcome(
            CODE_ROUTE_COUNT,
            band.simple_routes.contains(route_set.count),
            f"{route_set.count} simple routes, need "
            f"{band.simple_routes.min}-{band.simple_routes.max} "
            f"({'too closed' if route_set.count < band.simple_routes.min else 'too open'})",
        )
    )

    if route_set.count:
        outcomes.extend(
            check_candy(
                score=score,
                band=band,
                candy_cells=candy_cells,
                route_set=route_set,
                adj=adj,
                dead_ends=dead_ends,
            )
        )
        best_cells = route_set.cells_for(score.best_index)
        hug = border_hug_fraction(best_cells, maze.rows, maze.cols)
        outcomes.append(
            Outcome(
                CODE_BORDER_HUG,
                hug <= band.max_border_hug_fraction + _EPSILON,
                f"the best route runs along the border for {hug:.0%} of its "
                f"length, ceiling {band.max_border_hug_fraction:.0%}",
            )
        )
    else:
        best_cells = []
        for code in (
            CODE_UNIQUE_MAX, CODE_MARGIN, CODE_NOT_SHORTEST, CODE_LENGTH_RATIO,
            CODE_TEMPTING, CODE_CANDY_RANGE, CODE_CANDY_PLACEMENT,
            CODE_CANDY_SPACING, CODE_BORDER_HUG,
        ):
            outcomes.append(Outcome(code, False, "no routes to score"))

    unreachable = 1.0 - route_set.reachable_fraction
    outcomes.append(
        Outcome(
            CODE_REACHABILITY,
            unreachable <= band.max_unreachable_fraction + _EPSILON,
            f"{unreachable:.0%} of traversable cells are unreachable from the "
            f"start, ceiling {band.max_unreachable_fraction:.0%}",
        )
    )

    errors = [
        f"{outcome.code}: {outcome.message}" for outcome in outcomes if not outcome.passed
    ]
    constraints = {outcome.code: outcome.passed for outcome in outcomes}

    analysis = MazeAnalysis(
        maze_id=maze.maze_id,
        maze_index=maze.maze_index,
        exact=route_set.exact,
        reachable=route_set.reachable_cells,
        reachable_fraction=route_set.reachable_fraction,
        loop_count=loop_count,
        simple_path_count=route_set.count,
        dead_end_cells=sorted(dead_ends),
        shortest_route_length=route_set.shortest_length,
        best_route_length=len(best_cells),
        best_candy_total=score.best_total,
        second_best_candy_total=score.second_best_total,
        unique_highest_candy=score.unique_best,
        best_route=best_cells,
        tempting_route_count=score.tempting_count,
        route_summaries=_summaries(route_set, score, include_cells_for_top),
        passed=not errors,
        errors=errors,
        constraints=constraints,
        include_cells_for_top=include_cells_for_top,
    )
    return analysis


def _summaries(
    route_set: RouteSet, score: CandyScore, include_cells_for_top: int
) -> list[RouteSummary]:
    """Route summaries ordered best-score-first, then shortest, then by index.

    QA reads the top of this list, so the intended answer is row one and its
    nearest rivals follow -- that is the ordering a proofreader needs to see the
    decision the child faces.
    """
    if not route_set.routes:
        return []
    order = sorted(
        range(route_set.count),
        key=lambda index: (
            -score.scores[index],
            route_set.routes[index].length,
            index,
        ),
    )
    summaries: list[RouteSummary] = []
    for rank, index in enumerate(order):
        route = route_set.routes[index]
        summaries.append(
            RouteSummary(
                route_index=index,
                length=route.length,
                candy_total=score.scores[index],
                cells=route.cells(route_set.start) if rank < include_cells_for_top else None,
            )
        )
    return summaries


def summarize_failures(analysis: MazeAnalysis) -> str:
    """One-line reason string for attempt logs."""
    if analysis.passed:
        return "passed"
    return "; ".join(analysis.errors)
