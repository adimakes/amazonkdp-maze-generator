"""Guided candy placement (PRD 17.8, 18.3).

Sampling candy positions at random and hoping the acceptance rules hold does not
work, and the arithmetic says why. With margin ``m`` and tempting fraction ``f``,
a rival route needs ``f*B <= S <= B - m``, so a legal placement requires
``B >= m / (1 - f)`` -- five sweets on the intended route for act 1, out of a
budget of five to seven. Blind placement almost never lands there.

So the search is guided. It picks one **intended** route, puts every sweet on it,
and chooses *which* of its cells by route frequency, which is what turns the
frequency weighting of 18.3 from a stylistic preference into the mechanism:

* Sweets on cells in the 30-70% frequency band are shared with many rival routes,
  so the rivals score well and the wrong choice stays tempting (C5).
* Sweets on low-frequency cells are nearly exclusive to the intended route, so
  they are what creates the gap between best and second-best (C2).

Putting every sweet on the intended route is also what makes C1 structural: a
rival can only tie by covering the whole candy set, and a simple path that covers
every cell of another simple path between the same endpoints is that path.

When no trial satisfies the profile, this module reports failure. It never
returns a placement that breaks a rule -- 17.8 requires the maze be regenerated
from a new attempt seed instead.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from ..model.geometry import Cell
from ..model.profile import Band
from . import graph
from .constraints import (
    CODE_BORDER_HUG,
    CODE_MARGIN,
    CODE_TEMPTING,
    CandyScore,
    Outcome,
    border_hug_fraction,
    check_candy,
    placement_mask,
    score_placement,
    spacing_violations,
)
from .graph import Adjacency
from .routes import RouteSet

#: How many swap-repairs to try on a near-miss before abandoning the trial. A
#: repair is one popcount pass over the routes, so this is cheap next to drawing
#: a fresh sample and re-checking from scratch.
_REPAIR_STEPS = 12

#: Targets tried in rotation. A wide shortlist keeps 400 trials from all being
#: spent on one unlucky route; a bounded one keeps the ordering meaningful.
_TARGET_SHORTLIST = 24

_REPAIRABLE = (CODE_MARGIN, CODE_TEMPTING)


@dataclass
class CandyPlacement:
    """One accepted placement, with the evidence for accepting it."""

    cells: list[Cell]
    target_route_index: int
    score: CandyScore
    trial: int
    repairs: int = 0

    @property
    def count(self) -> int:
        return len(self.cells)

    @property
    def best_is_target(self) -> bool:
        return self.score.best_index == self.target_route_index


@dataclass
class CandyResult:
    placement: CandyPlacement | None = None
    trials_used: int = 0
    elapsed_seconds: float = 0.0
    abort_reason: str | None = None
    #: rule code -> how many trials that rule was the thing standing in the way.
    failure_counts: dict[str, int] = field(default_factory=dict)
    target_count: int = 0

    @property
    def ok(self) -> bool:
        return self.placement is not None

    def reason(self) -> str:
        """Why placement failed, in a form worth putting in an attempt log."""
        if self.ok:
            return "placed"
        if self.abort_reason:
            return f"candy search aborted on {self.abort_reason}"
        if not self.target_count:
            return (
                "no route is eligible to be the intended answer "
                "(too short, too long, or hugging the border)"
            )
        blockers = sorted(self.failure_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        detail = ", ".join(f"{code} x{count}" for code, count in blockers[:4])
        return f"{self.trials_used} placement trials, all rejected: {detail}"


# --------------------------------------------------------------------------- #
# Candidate selection
# --------------------------------------------------------------------------- #

def eligible_cells(
    maze_start: Cell,
    maze_finish: Cell,
    adj: Adjacency,
    *,
    excluded: "set[Cell] | frozenset[Cell]" = frozenset(),
) -> set[Cell]:
    """Cells a sweet may legally occupy (17.8).

    Terminals are out because the start and finish already carry their own icons,
    and dead ends are out because a sweet down a dead end is a sweet no route can
    take -- it reads as an unreachable reward rather than a choice.
    """
    dead_ends = set(graph.dead_end_cells(adj, exclude=(maze_start, maze_finish)))
    return {
        cell
        for cell in adj
        if cell != maze_start
        and cell != maze_finish
        and cell not in dead_ends
        and cell not in excluded
    }


def eligible_target_routes(
    route_set: RouteSet, band: Band, rows: int, cols: int
) -> list[int]:
    """Route indices that may serve as the intended answer.

    Filtering here rather than after placement means a trial is never spent on a
    route that C3, C4 or E1 would reject no matter where the sweets go.
    """
    shortest = route_set.shortest_length
    limit = band.max_best_over_shortest * shortest
    out: list[int] = []
    for index, route in enumerate(route_set.routes):
        if not band.shortest_may_be_best and route.length <= shortest:
            continue
        if route.length > limit + 1e-9:
            continue
        cells = route.cells(route_set.start)
        if border_hug_fraction(cells, rows, cols) > band.max_border_hug_fraction + 1e-9:
            continue
        out.append(index)
    return out


def _pools(
    cells: list[Cell], frequency: dict[Cell, float], band: Band
) -> tuple[list[Cell], list[Cell], list[Cell]]:
    """Split target-route cells into the low / mid / high frequency pools."""
    low: list[Cell] = []
    mid: list[Cell] = []
    high: list[Cell] = []
    for cell in cells:
        freq = frequency.get(cell, 0.0)
        if freq < band.frequency_band_min:
            low.append(cell)
        elif freq <= band.frequency_band_max:
            mid.append(cell)
        else:
            high.append(cell)
    return low, mid, high


def _take(
    pool: list[Cell],
    wanted: int,
    chosen: list[Cell],
    adj: Adjacency,
    policy: str,
) -> None:
    """Move up to ``wanted`` spacing-compatible cells from ``pool`` into ``chosen``.

    Spacing is enforced while building rather than checked afterwards: a sample
    that has to be thrown away for adjacency wastes the whole trial, and C8 is a
    property of pairs, so it is decidable one cell at a time.
    """
    taken = 0
    for cell in list(pool):
        if taken >= wanted:
            break
        if spacing_violations(chosen + [cell], adj, policy):
            continue
        chosen.append(cell)
        pool.remove(cell)
        taken += 1


def _draw(
    rng: random.Random,
    target_cells: list[Cell],
    eligible: set[Cell],
    frequency: dict[Cell, float],
    band: Band,
    adj: Adjacency,
    wanted: int,
) -> tuple[list[Cell], list[Cell]]:
    """Draw one candidate placement plus the leftover cells available for repair."""
    usable = [cell for cell in target_cells if cell in eligible]
    low, mid, high = _pools(usable, frequency, band)
    for pool in (low, mid, high):
        rng.shuffle(pool)

    want_low = round(wanted * band.exploration_share)
    want_mid = wanted - want_low

    chosen: list[Cell] = []
    _take(mid, want_mid, chosen, adj, band.candy_spacing_policy)
    _take(low, want_low, chosen, adj, band.candy_spacing_policy)
    # Backfill in preference order: the bands are a weighting, not a quota, and a
    # placement one sweet short of the profile range fails C6 outright.
    for pool in (mid, low, high):
        if len(chosen) >= wanted:
            break
        _take(pool, wanted - len(chosen), chosen, adj, band.candy_spacing_policy)
    leftover = low + mid + high
    return chosen, leftover


# --------------------------------------------------------------------------- #
# Repair
# --------------------------------------------------------------------------- #

def _swap_candidates(
    chosen: list[Cell],
    leftover: list[Cell],
    frequency: dict[Cell, float],
    adj: Adjacency,
    policy: str,
    *,
    raise_margin: bool,
) -> "tuple[Cell, Cell] | None":
    """Pick one swap that moves the failing measure in the right direction.

    Margin comes from exclusivity, temptation from sharing, and route frequency
    measures exactly that -- so a near-miss is repaired by trading the most
    extreme chosen cell for the most extreme unchosen one, not by resampling.
    """
    if not chosen or not leftover:
        return None
    if raise_margin:
        out_cell = max(chosen, key=lambda c: (frequency.get(c, 0.0), c))
        ranked = sorted(leftover, key=lambda c: (frequency.get(c, 0.0), c))
    else:
        out_cell = min(chosen, key=lambda c: (frequency.get(c, 0.0), c))
        ranked = sorted(leftover, key=lambda c: (-frequency.get(c, 0.0), c))
    remaining = [c for c in chosen if c != out_cell]
    for in_cell in ranked:
        if frequency.get(in_cell, 0.0) == frequency.get(out_cell, 0.0):
            continue
        if spacing_violations(remaining + [in_cell], adj, policy):
            continue
        return out_cell, in_cell
    return None


# --------------------------------------------------------------------------- #
# The search
# --------------------------------------------------------------------------- #

def place_candy(
    *,
    route_set: RouteSet,
    band: Band,
    rng: random.Random,
    rows: int,
    cols: int,
    adjacency: Adjacency,
    excluded: "set[Cell] | frozenset[Cell]" = frozenset(),
    time_budget_seconds: float | None = None,
) -> CandyResult:
    """Search for a candy placement that satisfies the profile, or report failure."""
    started = time.perf_counter()
    result = CandyResult()
    if not route_set.routes:
        return result

    adj = adjacency
    eligible = eligible_cells(
        route_set.start, route_set.finish, adj, excluded=excluded
    )
    dead_ends = set(
        graph.dead_end_cells(adj, exclude=(route_set.start, route_set.finish))
    )
    frequency = route_set.cell_route_frequency()

    targets = eligible_target_routes(route_set, band, rows, cols)
    result.target_count = len(targets)
    if not targets:
        result.elapsed_seconds = time.perf_counter() - started
        return result

    # Try the routes with the most usable mid-band cells first: those have the
    # most room to satisfy the margin and temptation rules at once.
    def target_rank(index: int) -> tuple:
        cells = [
            cell
            for cell in route_set.cells_for(index)
            if cell in eligible
            and band.frequency_band_min <= frequency.get(cell, 0.0) <= band.frequency_band_max
        ]
        return (-len(cells), -route_set.routes[index].length, index)

    shortlist = sorted(targets, key=target_rank)[:_TARGET_SHORTLIST]
    budget = band.candies

    for trial in range(band.candy_placement_trials):
        if time_budget_seconds is not None:
            if time.perf_counter() - started > time_budget_seconds:
                result.abort_reason = "candyTimeBudgetSeconds"
                break
        result.trials_used = trial + 1
        target_index = shortlist[trial % len(shortlist)]
        target_cells = route_set.cells_for(target_index)
        wanted = rng.randint(budget.min, budget.max)

        chosen, leftover = _draw(
            rng, target_cells, eligible, frequency, band, adj, wanted
        )
        if len(chosen) < budget.min:
            result.failure_counts["C6_candyCount"] = (
                result.failure_counts.get("C6_candyCount", 0) + 1
            )
            continue

        placement = _finish_trial(
            chosen=chosen,
            leftover=leftover,
            target_index=target_index,
            trial=trial + 1,
            route_set=route_set,
            band=band,
            adj=adj,
            dead_ends=dead_ends,
            frequency=frequency,
            rows=rows,
            cols=cols,
            failure_counts=result.failure_counts,
        )
        if placement is not None:
            result.placement = placement
            break

    result.elapsed_seconds = time.perf_counter() - started
    return result


def _check(
    *,
    chosen: list[Cell],
    route_set: RouteSet,
    band: Band,
    adj: Adjacency,
    dead_ends: set[Cell],
    rows: int,
    cols: int,
) -> tuple[CandyScore, list[Outcome]]:
    mask = placement_mask(tuple(chosen), cols)
    score = score_placement(route_set, mask, tempting_fraction=band.tempting_fraction)
    outcomes = check_candy(
        score=score,
        band=band,
        candy_cells=sorted(chosen),
        route_set=route_set,
        adj=adj,
        dead_ends=dead_ends,
    )
    if score.best_index >= 0:
        hug = border_hug_fraction(
            route_set.cells_for(score.best_index), rows, cols
        )
        outcomes.append(
            Outcome(
                CODE_BORDER_HUG,
                hug <= band.max_border_hug_fraction + 1e-9,
                f"best route hugs the border for {hug:.0%}",
            )
        )
    return score, outcomes


def _finish_trial(
    *,
    chosen: list[Cell],
    leftover: list[Cell],
    target_index: int,
    trial: int,
    route_set: RouteSet,
    band: Band,
    adj: Adjacency,
    dead_ends: set[Cell],
    frequency: dict[Cell, float],
    rows: int,
    cols: int,
    failure_counts: dict[str, int],
) -> CandyPlacement | None:
    """Check one drawn placement, repairing near-misses in place."""
    working = list(chosen)
    spare = list(leftover)

    for repair in range(_REPAIR_STEPS + 1):
        score, outcomes = _check(
            chosen=working,
            route_set=route_set,
            band=band,
            adj=adj,
            dead_ends=dead_ends,
            rows=rows,
            cols=cols,
        )
        failed = [o for o in outcomes if not o.passed]
        if not failed:
            return CandyPlacement(
                cells=sorted(working),
                target_route_index=target_index,
                score=score,
                trial=trial,
                repairs=repair,
            )
        codes = {o.code for o in failed}
        if not codes <= set(_REPAIRABLE) or repair == _REPAIR_STEPS:
            for code in sorted(codes):
                failure_counts[code] = failure_counts.get(code, 0) + 1
            return None
        swap = _swap_candidates(
            working,
            spare,
            frequency,
            adj,
            band.candy_spacing_policy,
            raise_margin=CODE_MARGIN in codes,
        )
        if swap is None:
            for code in sorted(codes):
                failure_counts[code] = failure_counts.get(code, 0) + 1
            return None
        out_cell, in_cell = swap
        working = [c for c in working if c != out_cell] + [in_cell]
        spare = [c for c in spare if c != in_cell] + [out_cell]
    return None
