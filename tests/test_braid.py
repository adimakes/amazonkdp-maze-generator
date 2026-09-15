"""Tests for ``maze_book.generation.braid`` (PRD 7, 17.7, 18.1).

Braid-and-rebalance is why the profiles are satisfiable at all. A perfect maze
has far more dead ends than any profile allows; removing one adds a loop, and
loops run out long before dead ends do. So dead-end removal is paired with
*rebalancing* -- cutting a cycle edge whose endpoints both have degree >= 3,
which lowers k without creating a new dead end.

The invariants tested here are the ones the rest of the engine assumes:
connectivity is never broken, k is always the measured cyclomatic number, and a
failure is a *report*, not an exception, because the pipeline answers it by
reseeding rather than by aborting.
"""

from __future__ import annotations

import random

import pytest

from maze_book.generation.adapter_mazelib import GENERATOR_ID, base_generator
from maze_book.generation.braid import shape
from maze_book.model.geometry import canonical_edge
from maze_book.model.profile import Band, IntRange, load_profile
from maze_book.simulation import graph as G


def band(**overrides) -> Band:
    base = dict(
        maze_index_min=1, maze_index_max=10, act_name="Test", rows=8, cols=8,
        loops=IntRange(3, 4), dead_ends=IntRange(4, 5), candies=IntRange(5, 7),
        simple_routes=IntRange(1, 200000), min_loop_length=6, min_margin=1,
        shortest_may_be_best=False, max_best_over_shortest=1.6, min_tempting_routes=2,
        tempting_fraction=0.6, max_routes=200000, max_search_nodes=4000000,
        time_budget_seconds=20.0, candy_placement_trials=400,
        frequency_band_min=0.3, frequency_band_max=0.7, exploration_share=0.35,
        wall_width_pt=3.0, solution_wall_width_pt=1.4, solution_route_width_pt=2.2,
        collectible_scale=0.55, dead_end_scale=0.5, start_scale=0.6, finish_scale=0.7,
        cell_clearance_fraction=0.12, candy_spacing_policy="none",
        max_border_hug_fraction=0.5, max_unreachable_fraction=0.35,
        dead_end_depth=IntRange(1, 3),
    )
    base.update(overrides)
    return Band(**base)


def tree(rows: int, cols: int, seed: int) -> list:
    return base_generator(GENERATOR_ID).carve(rows, cols, seed)


def adjacency_of(rows: int, cols: int, edges) -> dict:
    adj: dict = {cell: set() for cell in
                 ((r, c) for r in range(rows) for c in range(cols))}
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    return adj


def run(rows=8, cols=8, seed=1, **band_kwargs):
    start, finish = (0, 0), (rows - 1, cols - 1)
    return shape(
        rows=rows, cols=cols, tree_edges=tree(rows, cols, seed),
        start=start, finish=finish,
        band=band(rows=rows, cols=cols, **band_kwargs), rng=random.Random(seed),
    )


# ---------------------------------------------------------------------------
# The starting point
# ---------------------------------------------------------------------------


def test_a_carved_tree_is_a_spanning_tree() -> None:
    edges = tree(8, 8, 1)
    adj = adjacency_of(8, 8, edges)
    assert len(edges) == 64 - 1
    assert G.cyclomatic_number(adj) == 0
    assert len(G.connected_components(adj)) == 1


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_a_spanning_tree_has_more_dead_ends_than_the_band_allows(seed: int) -> None:
    """This is the whole reason braiding exists. Asserted against the band's own
    ceiling rather than a magic number: what matters is that the carver always
    overshoots what the profile will accept, so every maze needs reshaping."""
    adj = adjacency_of(8, 8, tree(8, 8, seed))
    raw = len(G.dead_end_cells(adj, exclude=((0, 0), (7, 7))))
    assert raw > band().dead_ends.max


# ---------------------------------------------------------------------------
# Invariants that must hold whatever the outcome
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_the_graph_stays_connected(seed: int) -> None:
    """Every cell traversable and reachable is assumed by every later stage."""
    report = run(seed=seed)
    adj = adjacency_of(8, 8, report.edges)
    assert len(G.connected_components(adj)) == 1
    assert len(G.reachable_from(adj, (0, 0))) == 64


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_the_reported_loop_count_is_the_measured_cyclomatic_number(seed: int) -> None:
    """17.7: loops are measured, never assumed."""
    report = run(seed=seed)
    adj = adjacency_of(8, 8, report.edges)
    assert report.loop_count == G.cyclomatic_number(adj)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_the_reported_dead_ends_are_the_degree_one_cells(seed: int) -> None:
    report = run(seed=seed)
    adj = adjacency_of(8, 8, report.edges)
    expected = set(G.dead_end_cells(adj, exclude=((0, 0), (7, 7))))
    assert set(report.dead_ends) == expected
    assert report.dead_end_count == len(expected)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_every_edge_stays_a_legal_grid_edge(seed: int) -> None:
    report = run(seed=seed)
    for a, b in report.edges:
        assert canonical_edge(a, b) == (a, b)
        assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_edges_are_unique(seed: int) -> None:
    report = run(seed=seed)
    assert len(set(report.edges)) == len(report.edges)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_the_endpoints_are_never_turned_into_dead_ends_to_be_marked(seed: int) -> None:
    """start and finish are excluded from the dead-end set by construction: a
    dead-end marker on the finish would be a lie."""
    report = run(seed=seed)
    assert (0, 0) not in report.dead_ends
    assert (7, 7) not in report.dead_ends


# ---------------------------------------------------------------------------
# Hitting the band
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(1, 13))
def test_a_successful_shape_lands_inside_both_bands(seed: int) -> None:
    report = run(seed=seed)
    if not report.ok:
        return
    target = band()
    assert target.loops.min <= report.loop_count <= target.loops.max
    assert target.dead_ends.min <= report.dead_end_count <= target.dead_ends.max


@pytest.mark.parametrize("seed", range(1, 13))
def test_a_successful_shape_respects_the_dead_end_depth_ceiling(seed: int) -> None:
    """Every wrong turn stays short: the depth ceiling is what keeps a dead end
    a moment's mistake rather than a long walk back."""
    report = run(seed=seed)
    if not report.ok:
        return
    assert all(1 <= depth <= 3 for depth in report.depths), report.depths


def test_the_rebalancer_actually_succeeds_most_of_the_time() -> None:
    """If most attempts failed, the profiles would be unsatisfiable in practice
    even though each individual invariant held."""
    successes = sum(1 for seed in range(1, 41) if run(seed=seed).ok)
    assert successes >= 20, f"only {successes}/40 shapes landed in band"


def test_shape_adds_and_removes_edges_rather_than_only_adding() -> None:
    """18.1: naively removing a dead end adds a loop, which blows the budget
    almost at once. Rebalancing is what makes the two bands satisfiable
    together, so a run that only ever added edges would mean it never ran."""
    reports = [run(seed=seed) for seed in range(1, 21)]
    assert any(report.removals > 0 for report in reports)
    assert all(report.additions >= 0 for report in reports)


# ---------------------------------------------------------------------------
# Failure is a report, not an exception
# ---------------------------------------------------------------------------


def test_an_impossible_band_reports_rather_than_raising() -> None:
    """17.8: the pipeline answers a shape failure by regenerating from a new
    attempt seed, so the reason has to come back attached to the attempt."""
    report = run(loops=IntRange(400, 500), dead_ends=IntRange(0, 0))
    assert report.ok is False
    assert report.stage and report.detail
    assert isinstance(report.edges, list)


def test_the_failure_detail_says_what_blocked_it() -> None:
    report = run(dead_ends=IntRange(60, 62))
    if not report.ok:
        assert any(word in report.detail for word in ("dead end", "loop", "cut", "edge"))


def test_a_report_is_never_an_exception_across_many_seeds() -> None:
    for seed in range(1, 25):
        report = run(seed=seed, loops=IntRange(1, 2), dead_ends=IntRange(1, 2))
        assert report.stage in ("ok",) or not report.ok


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_shaping_is_deterministic_for_a_fixed_rng_seed() -> None:
    first = run(seed=7)
    second = run(seed=7)
    assert first.edges == second.edges
    assert (first.loop_count, first.dead_end_count) == (
        second.loop_count, second.dead_end_count
    )


def test_different_seeds_give_different_shapes() -> None:
    assert run(seed=1).edges != run(seed=2).edges


# ---------------------------------------------------------------------------
# Against the real profile
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("index,size", [(1, 8), (11, 10), (21, 12)])
def test_the_real_halloween_bands_are_reachable(repo_root, index: int, size: int) -> None:
    profile = load_profile(repo_root / "profiles", "halloween_6_10")
    real = profile.band_for(index)
    successes = 0
    for seed in range(1, 25):
        report = shape(
            rows=size, cols=size, tree_edges=tree(size, size, seed),
            start=(0, 0), finish=(size - 1, size - 1),
            band=real, rng=random.Random(seed),
        )
        if report.ok:
            successes += 1
            assert real.loops.min <= report.loop_count <= real.loops.max
            assert real.dead_ends.min <= report.dead_end_count <= real.dead_ends.max
    assert successes > 0, f"no shape landed in band for maze {index}"
