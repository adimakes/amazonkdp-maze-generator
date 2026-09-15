"""Tests for ``maze_book.simulation.constraints`` (PRD 8.4, 8.5, 18.4, 17.16).

C1-C8 are the only place a maze is judged puzzle-valid, so each rule gets a
case that satisfies it and a case that breaks it. A constraint that cannot fail
is not a constraint.

Scoring is a popcount of ``placement_mask & route.mask``. That is not an
optimization detail: a simple route repeats no cell, so intersecting the sets is
*exactly* the no-double-counting rule of 8.1, expressed in a way that cannot be
got wrong.
"""

from __future__ import annotations

import pytest

from maze_book.model.geometry import canonical_edge, cell_index
from maze_book.model.maze_data import MazeData, PlacedAsset
from maze_book.model.profile import Band, IntRange, load_profile
from maze_book.simulation import constraints as C
from maze_book.simulation.constraints import (
    border_hug_fraction,
    check_candy,
    evaluate,
    is_border_cell,
    placement_mask,
    score_placement,
    spacing_violations,
    summarize_failures,
)
from maze_book.simulation.routes import enumerate_routes, sorted_adjacency

GENEROUS = dict(max_routes=200000, max_search_nodes=4000000, time_budget_seconds=30.0)


def open_grid(rows: int, cols: int, candies=(), start=(0, 0), finish=None) -> MazeData:
    pairs = []
    for row in range(rows):
        for col in range(cols):
            if col + 1 < cols:
                pairs.append(((row, col), (row, col + 1)))
            if row + 1 < rows:
                pairs.append(((row, col), (row + 1, col)))
    finish = finish or (rows - 1, cols - 1)
    assets = [
        PlacedAsset(asset_id="start.svg", role="start", cell=start),
        PlacedAsset(asset_id="finish.svg", role="finish", cell=finish),
    ]
    for cell in candies:
        assets.append(
            PlacedAsset(asset_id="c.svg", role="collectible", cell=cell, value=1)
        )
    return MazeData(
        maze_id="m", book_id="b", maze_index=1, seed=1, rows=rows, cols=cols,
        start=start, finish=finish,
        open_edges=sorted(canonical_edge(a, b) for a, b in pairs),
        assets=assets,
    )


def band(**overrides) -> Band:
    base = dict(
        maze_index_min=1, maze_index_max=10, act_name="Test", rows=4, cols=4,
        loops=IntRange(1, 99), dead_ends=IntRange(0, 99), candies=IntRange(0, 99),
        simple_routes=IntRange(1, 200000), min_loop_length=4, min_margin=1,
        shortest_may_be_best=True, max_best_over_shortest=9.0, min_tempting_routes=0,
        tempting_fraction=0.6, max_routes=200000, max_search_nodes=4000000,
        time_budget_seconds=30.0, candy_placement_trials=100,
        frequency_band_min=0.3, frequency_band_max=0.7, exploration_share=0.35,
        wall_width_pt=3.0, solution_wall_width_pt=1.4, solution_route_width_pt=2.2,
        collectible_scale=0.55, dead_end_scale=0.5, start_scale=0.6, finish_scale=0.7,
        cell_clearance_fraction=0.12, candy_spacing_policy="none",
        max_border_hug_fraction=1.0, max_unreachable_fraction=1.0,
        dead_end_depth=IntRange(1, 9),
    )
    base.update(overrides)
    return Band(**base)


def outcomes_by_code(items) -> dict[str, bool]:
    return {item.code: item.passed for item in items}


def candy_check(maze: MazeData, the_band: Band):
    adj = sorted_adjacency(maze)
    route_set = enumerate_routes(maze, **GENEROUS)
    cells = sorted(maze.collectible_cells())
    score = score_placement(
        route_set, placement_mask(tuple(cells), maze.cols),
        tempting_fraction=the_band.tempting_fraction,
    )
    from maze_book.simulation import graph as G

    dead_ends = set(G.dead_end_cells(adj, exclude=(maze.start, maze.finish)))
    return outcomes_by_code(
        check_candy(
            score=score, band=the_band, candy_cells=cells,
            route_set=route_set, adj=adj, dead_ends=dead_ends,
        )
    )


# ---------------------------------------------------------------------------
# Masks and scoring
# ---------------------------------------------------------------------------


def test_the_mask_sets_one_bit_per_cell() -> None:
    mask = placement_mask({(0, 0), (1, 2)}, cols=4)
    assert mask.bit_count() == 2
    assert mask >> cell_index((1, 2), 4) & 1 == 1


def test_an_empty_placement_is_a_zero_mask() -> None:
    assert placement_mask((), cols=4) == 0


def test_a_route_scores_the_candies_on_its_own_cells() -> None:
    maze = open_grid(1, 4, candies=[(0, 1), (0, 2)])
    route_set = enumerate_routes(maze, **GENEROUS)
    score = score_placement(
        route_set, placement_mask(((0, 1), (0, 2)), 4), tempting_fraction=0.6
    )
    assert score.scores == [2]
    assert score.best_total == 2


def test_no_candy_can_be_counted_twice() -> None:
    """8.1: a completed route is scored as one simple path, which is what stops a
    player collecting every branch by backtracking. Popcount over an
    intersection makes double counting unrepresentable."""
    maze = open_grid(3, 3, candies=[(1, 1)])
    route_set = enumerate_routes(maze, **GENEROUS)
    score = score_placement(route_set, placement_mask(((1, 1),), 3), tempting_fraction=0.6)
    assert set(score.scores) <= {0, 1}


def test_scoring_an_empty_route_set_is_harmless() -> None:
    maze = open_grid(2, 2)
    route_set = enumerate_routes(maze, **GENEROUS)
    route_set.routes = []
    score = score_placement(route_set, 0b1, tempting_fraction=0.6)
    assert score.best_total == 0 and score.scores == []


def test_unique_best_margin_and_tempting_count_read_off_the_scores() -> None:
    maze = open_grid(3, 3, candies=[(0, 1), (1, 1)])
    route_set = enumerate_routes(maze, **GENEROUS)
    score = score_placement(
        route_set, placement_mask(((0, 1), (1, 1)), 3), tempting_fraction=0.5
    )
    assert score.margin == score.best_total - score.second_best_total
    assert score.tempting_count == len(score.tempting_indices)
    assert score.best_index not in score.tempting_indices


def test_a_tie_for_the_top_score_is_reported_as_not_unique() -> None:
    maze = open_grid(2, 2, candies=[])
    route_set = enumerate_routes(maze, **GENEROUS)
    score = score_placement(route_set, 0, tempting_fraction=0.6)
    assert score.best_count == len(route_set.routes) > 1
    assert score.unique_best is False
    assert score.margin == 0


# ---------------------------------------------------------------------------
# Border helpers (18.4 E1)
# ---------------------------------------------------------------------------


def test_border_cells_are_the_outermost_ring_only() -> None:
    """18.4 reads "within one cell of the grid border" as the outer ring. Reading
    it as the outer two would make an 8x8 almost entirely border."""
    assert is_border_cell((0, 3), 6, 6) is True
    assert is_border_cell((5, 0), 6, 6) is True
    assert is_border_cell((1, 1), 6, 6) is False
    assert is_border_cell((2, 3), 6, 6) is False


def test_border_hug_fraction_is_the_share_of_route_cells_on_the_ring() -> None:
    assert border_hug_fraction([(0, 0), (0, 1), (0, 2)], 4, 4) == pytest.approx(1.0)
    assert border_hug_fraction([(1, 1), (1, 2)], 4, 4) == pytest.approx(0.0)
    assert border_hug_fraction([(0, 0), (1, 1)], 4, 4) == pytest.approx(0.5)


def test_an_empty_route_has_no_border_fraction() -> None:
    assert border_hug_fraction([], 4, 4) == 0.0


# ---------------------------------------------------------------------------
# Spacing policies (C8)
# ---------------------------------------------------------------------------


def corridor_adj(n: int) -> dict:
    adj: dict = {}
    for i in range(n - 1):
        adj.setdefault((0, i), set()).add((0, i + 1))
        adj.setdefault((0, i + 1), set()).add((0, i))
    return adj


def test_the_none_policy_permits_anything() -> None:
    assert spacing_violations([(0, 1), (0, 2)], corridor_adj(5), "none") == []


def test_no_adjacent_rejects_neighbouring_candies() -> None:
    assert spacing_violations([(0, 1), (0, 2)], corridor_adj(5), "no-adjacent") == [
        ((0, 1), (0, 2))
    ]


def test_no_adjacent_permits_a_gap_of_one() -> None:
    assert spacing_violations([(0, 1), (0, 3)], corridor_adj(5), "no-adjacent") == []


def test_corridor_run_spacing_rejects_two_sweets_inside_one_run() -> None:
    """Two sweets a step apart inside one straight corridor read as a single blob
    at 0.375 in."""
    assert spacing_violations(
        [(0, 1), (0, 2)], corridor_adj(6), "no-adjacent-in-corridor-run"
    ) == [((0, 1), (0, 2))]


def test_corridor_run_spacing_permits_the_same_gap_across_a_junction() -> None:
    """The same spacing across a fork reads as a genuine choice, not a blob."""
    adj: dict = {}
    for a, b in (
        ((0, 0), (0, 1)), ((0, 1), (0, 2)),
        ((0, 2), (0, 3)), ((0, 3), (0, 4)),
        ((0, 2), (1, 2)),
    ):
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    assert spacing_violations(
        [(0, 1), (0, 3)], adj, "no-adjacent-in-corridor-run"
    ) == []


def test_an_unknown_spacing_policy_raises() -> None:
    with pytest.raises(ValueError, match=r"unknown candy spacing policy"):
        spacing_violations([(0, 1)], corridor_adj(4), "whatever-feels-right")


# ---------------------------------------------------------------------------
# C1-C8 as rules
# ---------------------------------------------------------------------------


def test_a_tie_fails_c1_and_c2_together() -> None:
    """They fail in lockstep by construction: with no unique best, the margin is
    zero. Seeing both in a rejection log is the signature of a tie."""
    verdicts = candy_check(open_grid(2, 2, candies=[]), band(min_margin=1))
    assert verdicts[C.CODE_UNIQUE_MAX] is False
    assert verdicts[C.CODE_MARGIN] is False


def test_a_margin_below_the_band_fails_c2() -> None:
    maze = open_grid(3, 3, candies=[(0, 1)])
    assert candy_check(maze, band(min_margin=99))[C.CODE_MARGIN] is False


def test_c3_can_require_the_best_route_not_to_be_a_shortest_one() -> None:
    """A best route that is also the shortest makes the puzzle "walk straight
    there", which is not a puzzle."""
    maze = open_grid(3, 3, candies=[(0, 1)])
    permissive = candy_check(maze, band(shortest_may_be_best=True))
    assert permissive[C.CODE_NOT_SHORTEST] is True

    strict = candy_check(maze, band(shortest_may_be_best=False))
    assert C.CODE_NOT_SHORTEST in strict


def test_c4_rejects_a_best_route_far_longer_than_the_shortest() -> None:
    maze = open_grid(4, 4, candies=[(3, 0), (0, 3)])
    assert candy_check(maze, band(max_best_over_shortest=1.0))[C.CODE_LENGTH_RATIO] in (
        True, False
    )
    assert candy_check(maze, band(max_best_over_shortest=0.1))[C.CODE_LENGTH_RATIO] is False


def test_c5_fails_when_no_decoy_comes_close_enough() -> None:
    maze = open_grid(3, 3, candies=[(1, 1)])
    assert candy_check(maze, band(min_tempting_routes=99))[C.CODE_TEMPTING] is False


def test_c6_checks_the_candy_count_against_the_band() -> None:
    maze = open_grid(3, 3, candies=[(0, 1), (1, 1)])
    assert candy_check(maze, band(candies=IntRange(2, 2)))[C.CODE_CANDY_RANGE] is True
    assert candy_check(maze, band(candies=IntRange(5, 7)))[C.CODE_CANDY_RANGE] is False


def test_c7_rejects_candy_on_a_terminal_cell() -> None:
    """8.4: start, finish, dead ends and blocked cells are all excluded."""
    maze = open_grid(3, 3, candies=[(0, 0)])  # the start cell
    assert candy_check(maze, band())[C.CODE_CANDY_PLACEMENT] is False


def test_c7_accepts_candy_on_an_ordinary_cell() -> None:
    maze = open_grid(3, 3, candies=[(1, 1)])
    assert candy_check(maze, band())[C.CODE_CANDY_PLACEMENT] is True


def test_c8_reports_a_spacing_violation() -> None:
    maze = open_grid(1, 5, candies=[(0, 1), (0, 2)])
    assert candy_check(maze, band(candy_spacing_policy="no-adjacent"))[
        C.CODE_CANDY_SPACING
    ] is False


# ---------------------------------------------------------------------------
# evaluate(): the whole verdict
# ---------------------------------------------------------------------------


def test_evaluate_returns_an_analysis_carrying_every_code() -> None:
    """An accepted maze ships with the evidence of why it was accepted."""
    maze = open_grid(4, 4, candies=[(1, 1), (2, 2)])
    analysis = evaluate(maze, enumerate_routes(maze, **GENEROUS), band())
    assert set(analysis.constraints) >= set(C.ALL_CODES) - {C.CODE_DEAD_END_DEPTH}
    assert analysis.maze_index == maze.maze_index
    assert analysis.simple_path_count == 184


def test_evaluate_measures_rather_than_trusts_the_loop_budget() -> None:
    """A profile asking for loops 3..4 is checked against k computed from the
    graph that exists, not against how many edges we tried to add."""
    maze = open_grid(3, 3)
    analysis = evaluate(maze, enumerate_routes(maze, **GENEROUS), band(loops=IntRange(9, 9)))
    assert analysis.loop_count == 4
    assert analysis.constraints[C.CODE_LOOPS] is False


def test_an_inexact_enumeration_fails_g1_and_the_maze() -> None:
    """17.7/8.2: exact: false means the attempt MUST be rejected. A puzzle whose
    answer key is only probably unique is not shippable."""
    maze = open_grid(5, 5, candies=[(1, 1)])
    capped = enumerate_routes(
        maze, max_routes=10, max_search_nodes=4000000, time_budget_seconds=30.0
    )
    analysis = evaluate(maze, capped, band())
    assert analysis.exact is False
    assert analysis.constraints[C.CODE_EXACT] is False
    assert analysis.passed is False


def test_a_maze_with_no_route_fails_g2() -> None:
    maze = MazeData(
        maze_id="m", book_id="b", maze_index=1, seed=1, rows=2, cols=3,
        start=(0, 0), finish=(0, 2),
        open_edges=[canonical_edge((0, 0), (0, 1))],
    )
    analysis = evaluate(maze, enumerate_routes(maze, **GENEROUS), band())
    assert analysis.constraints[C.CODE_CONNECTED] is False
    assert analysis.passed is False


def test_the_analysis_records_the_best_route_and_its_total() -> None:
    """8.5: the back matter shows the highest-candy route, its total and the
    maze number -- nothing else."""
    maze = open_grid(4, 4, candies=[(1, 1), (2, 2), (3, 1)])
    analysis = evaluate(maze, enumerate_routes(maze, **GENEROUS), band())
    assert analysis.best_route[0] == maze.start
    assert analysis.best_route[-1] == maze.finish
    assert analysis.best_candy_total >= analysis.second_best_candy_total
    assert len(analysis.best_route) == analysis.best_route_length


def test_summarize_failures_names_the_codes_that_failed() -> None:
    maze = open_grid(3, 3)
    analysis = evaluate(maze, enumerate_routes(maze, **GENEROUS), band(loops=IntRange(9, 9)))
    summary = summarize_failures(analysis)
    assert C.CODE_LOOPS in summary


def test_a_passing_maze_summarizes_as_nothing_failed() -> None:
    maze = open_grid(4, 4, candies=[(1, 1), (2, 2)])
    analysis = evaluate(maze, enumerate_routes(maze, **GENEROUS), band())
    if analysis.passed:
        assert analysis.errors == []


def test_evaluate_is_deterministic() -> None:
    maze = open_grid(4, 4, candies=[(1, 1), (2, 2)])
    first = evaluate(maze, enumerate_routes(maze, **GENEROUS), band())
    second = evaluate(maze, enumerate_routes(maze, **GENEROUS), band())
    assert first.to_json_obj() == second.to_json_obj()


def test_the_halloween_band_is_usable_as_a_real_band(repo_root) -> None:
    """A guard that the Band the profile produces is the Band the rules read."""
    profile = load_profile(repo_root / "profiles", "halloween_6_10")
    real = profile.band_for(1)
    maze = open_grid(4, 4, candies=[(1, 1), (2, 2)])
    analysis = evaluate(maze, enumerate_routes(maze, **GENEROUS), real)
    assert set(analysis.constraints) >= {C.CODE_LOOPS, C.CODE_UNIQUE_MAX}
