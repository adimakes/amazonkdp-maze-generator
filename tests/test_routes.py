"""Tests for ``maze_book.simulation.routes`` (PRD 8.1, 8.2, 17.16).

The no-revisit rule is the whole basis of scoring: "physical erasing and
retrying is allowed for the child, but a completed route is scored as one simple
path. This prevents a player from collecting every branch by repeatedly
backtracking." So enumeration is checked against hand-built graphs whose simple
paths can be counted on paper.

The honesty flags matter as much as the count. If a cap trips, ``exact`` is False
and the attempt must be rejected -- a puzzle whose answer key is only probably
unique is not shippable.
"""

from __future__ import annotations

import pytest

from maze_book.model.geometry import canonical_edge
from maze_book.model.maze_data import MazeData
from maze_book.simulation.routes import (
    ABORT_MAX_ROUTES,
    ABORT_MAX_SEARCH_NODES,
    Route,
    enumerate_routes,
    sorted_adjacency,
)

GENEROUS = dict(max_routes=200000, max_search_nodes=4000000, time_budget_seconds=30.0)


def maze_from(rows: int, cols: int, pairs, start, finish) -> MazeData:
    return MazeData(
        maze_id="m", book_id="b", maze_index=1, seed=1, rows=rows, cols=cols,
        start=start, finish=finish,
        open_edges=sorted(canonical_edge(a, b) for a, b in pairs),
    )


def corridor(cols: int) -> MazeData:
    return maze_from(
        1, cols, [((0, c), (0, c + 1)) for c in range(cols - 1)], (0, 0), (0, cols - 1)
    )


def open_grid(rows: int, cols: int) -> MazeData:
    pairs = []
    for row in range(rows):
        for col in range(cols):
            if col + 1 < cols:
                pairs.append(((row, col), (row, col + 1)))
            if row + 1 < rows:
                pairs.append(((row, col), (row + 1, col)))
    return maze_from(rows, cols, pairs, (0, 0), (rows - 1, cols - 1))


# ---------------------------------------------------------------------------
# Adjacency
# ---------------------------------------------------------------------------


def test_adjacency_is_symmetric_and_in_a_declared_order() -> None:
    """Neighbours come back in fixed N, E, S, W order. MazeData.adjacency
    returns sets, whose iteration order is an implementation detail, and route
    order is part of the answer key -- so the graph handed to the search must
    have an order that is declared rather than incidental."""
    from maze_book.model.geometry import DIRECTIONS

    adj = sorted_adjacency(open_grid(3, 3))
    for cell, neighbours in adj.items():
        for neighbour in neighbours:
            assert cell in adj[neighbour]
        deltas = [(n[0] - cell[0], n[1] - cell[1]) for n in neighbours]
        assert deltas == sorted(deltas, key=DIRECTIONS.index)


def test_adjacency_covers_every_cell_even_when_isolated() -> None:
    maze = maze_from(2, 2, [((0, 0), (0, 1))], (0, 0), (0, 1))
    assert set(sorted_adjacency(maze)) == {(0, 0), (0, 1), (1, 0), (1, 1)}


# ---------------------------------------------------------------------------
# Counting simple paths on graphs you can count by hand
# ---------------------------------------------------------------------------


def test_a_corridor_has_exactly_one_route() -> None:
    result = enumerate_routes(corridor(5), **GENEROUS)
    assert result.count == 1
    assert result.exact is True
    assert result.routes[0].length == 5


def test_a_single_loop_offers_exactly_two_routes() -> None:
    """A cycle from start to the opposite corner: round one way or the other."""
    pairs = [
        ((0, 0), (0, 1)), ((0, 1), (0, 2)),
        ((0, 0), (1, 0)), ((1, 0), (1, 1)), ((1, 1), (1, 2)), ((1, 2), (0, 2)),
    ]
    result = enumerate_routes(maze_from(2, 3, pairs, (0, 0), (0, 2)), **GENEROUS)
    assert result.count == 2


def test_a_dead_end_branch_adds_no_routes() -> None:
    """A branch that goes nowhere cannot appear on a simple path to the finish,
    which is exactly why the reduction can prune it."""
    plain = enumerate_routes(corridor(4), **GENEROUS).count
    pairs = [((0, c), (0, c + 1)) for c in range(3)] + [((0, 1), (1, 1))]
    with_branch = enumerate_routes(
        maze_from(2, 4, pairs, (0, 0), (0, 3)), **GENEROUS
    ).count
    assert with_branch == plain == 1


def test_the_two_by_two_open_grid_has_two_routes() -> None:
    assert enumerate_routes(open_grid(2, 2), **GENEROUS).count == 2


def test_the_three_by_three_open_grid_has_twelve_corner_to_corner_routes() -> None:
    """A known value for self-avoiding corner-to-corner walks on a 3x3 lattice."""
    assert enumerate_routes(open_grid(3, 3), **GENEROUS).count == 12


def test_the_four_by_four_open_grid_has_184_routes() -> None:
    """OEIS A007764's lattice-path sequence: 2, 12, 184, 8512 for n = 2..5."""
    assert enumerate_routes(open_grid(4, 4), **GENEROUS).count == 184


def test_a_disconnected_finish_yields_no_routes() -> None:
    maze = maze_from(1, 4, [((0, 0), (0, 1))], (0, 0), (0, 3))
    result = enumerate_routes(maze, **GENEROUS)
    assert result.count == 0
    assert result.exact is True  # honestly zero, not aborted


def test_start_equal_to_finish_is_a_single_trivial_route() -> None:
    maze = maze_from(1, 3, [((0, 0), (0, 1)), ((0, 1), (0, 2))], (0, 0), (0, 0))
    assert enumerate_routes(maze, **GENEROUS).count <= 1


# ---------------------------------------------------------------------------
# The no-revisit rule
# ---------------------------------------------------------------------------


def test_no_route_visits_a_cell_twice() -> None:
    result = enumerate_routes(open_grid(4, 4), **GENEROUS)
    for index in range(result.count):
        cells = result.cells_for(index)
        assert len(cells) == len(set(cells)), cells


def test_every_route_starts_at_the_start_and_ends_at_the_finish() -> None:
    maze = open_grid(3, 4)
    result = enumerate_routes(maze, **GENEROUS)
    for index in range(result.count):
        cells = result.cells_for(index)
        assert cells[0] == maze.start
        assert cells[-1] == maze.finish


def test_every_consecutive_pair_of_a_route_is_an_open_edge() -> None:
    maze = open_grid(3, 3)
    opens = maze.edge_set()
    result = enumerate_routes(maze, **GENEROUS)
    for index in range(result.count):
        cells = result.cells_for(index)
        for a, b in zip(cells, cells[1:]):
            assert canonical_edge(a, b) in opens


def test_all_routes_are_distinct() -> None:
    result = enumerate_routes(open_grid(3, 3), **GENEROUS)
    seen = {tuple(result.cells_for(i)) for i in range(result.count)}
    assert len(seen) == result.count


# ---------------------------------------------------------------------------
# Route storage
# ---------------------------------------------------------------------------


def test_a_route_rebuilds_its_cells_from_the_direction_bytes() -> None:
    """Routes store a bitmask plus step directions rather than a cell list:
    scoring only needs the mask, and the bytes rebuild the exact sequence --
    including which of two equal-length detours was taken."""
    result = enumerate_routes(corridor(4), **GENEROUS)
    route = result.routes[0]
    assert route.cells((0, 0)) == [(0, 0), (0, 1), (0, 2), (0, 3)]
    assert route.length == len(route.directions) + 1


def test_the_mask_has_one_bit_per_visited_cell() -> None:
    result = enumerate_routes(open_grid(3, 3), **GENEROUS)
    for route in result.routes:
        assert route.mask.bit_count() == route.length


def test_route_lengths_agree_with_the_cell_lists() -> None:
    result = enumerate_routes(open_grid(3, 3), **GENEROUS)
    assert result.lengths == [len(result.cells_for(i)) for i in range(result.count)]


def test_shortest_indices_point_at_the_shortest_routes() -> None:
    result = enumerate_routes(open_grid(3, 3), **GENEROUS)
    shortest = result.shortest_length
    assert all(result.routes[i].length == shortest for i in result.shortest_indices)
    assert shortest == 5  # Manhattan distance on a 3x3, in cells


# ---------------------------------------------------------------------------
# Frequencies and unions
# ---------------------------------------------------------------------------


def test_cell_route_counts_never_exceed_the_route_count() -> None:
    result = enumerate_routes(open_grid(3, 3), **GENEROUS)
    counts = result.cell_route_counts()
    assert counts and max(counts.values()) <= result.count


def test_the_endpoints_are_on_every_route() -> None:
    maze = open_grid(3, 3)
    result = enumerate_routes(maze, **GENEROUS)
    counts = result.cell_route_counts()
    assert counts[maze.start] == counts[maze.finish] == result.count


def test_frequency_is_the_count_normalized_to_one() -> None:
    """The 30-70% band the candy placer favours is expressed in this number: a
    cell on every route is free candy, a cell on one route is punishing."""
    maze = open_grid(3, 3)
    result = enumerate_routes(maze, **GENEROUS)
    frequency = result.cell_route_frequency()
    assert frequency[maze.start] == pytest.approx(1.0)
    assert all(0.0 <= value <= 1.0 for value in frequency.values())


def test_the_union_is_every_cell_any_route_touches() -> None:
    maze = open_grid(3, 3)
    result = enumerate_routes(maze, **GENEROUS)
    union = result.route_cells_union()
    assert union == set(result.cell_route_counts())
    assert maze.start in union


# ---------------------------------------------------------------------------
# Caps and the exactness flag
# ---------------------------------------------------------------------------


def test_hitting_the_route_cap_sets_exact_false_and_names_the_reason() -> None:
    """8.2: "explicit exact: false status if a limit interrupts enumeration".
    An attempt in that state must be rejected, never scored."""
    result = enumerate_routes(
        open_grid(5, 5), max_routes=10, max_search_nodes=4000000, time_budget_seconds=30.0
    )
    assert result.exact is False
    assert result.abort_reason == ABORT_MAX_ROUTES
    assert result.count <= 10


def test_hitting_the_node_cap_sets_exact_false() -> None:
    result = enumerate_routes(
        open_grid(5, 5), max_routes=200000, max_search_nodes=50, time_budget_seconds=30.0
    )
    assert result.exact is False
    assert result.abort_reason == ABORT_MAX_SEARCH_NODES


def test_a_zero_time_budget_aborts_rather_than_running_forever() -> None:
    result = enumerate_routes(
        open_grid(6, 6), max_routes=200000, max_search_nodes=4000000,
        time_budget_seconds=0.0,
    )
    assert result.exact is False
    assert result.abort_reason is not None


def test_exact_is_true_exactly_when_no_reason_was_recorded() -> None:
    fine = enumerate_routes(open_grid(3, 3), **GENEROUS)
    assert fine.exact is True and fine.abort_reason is None

    capped = enumerate_routes(
        open_grid(5, 5), max_routes=5, max_search_nodes=4000000, time_budget_seconds=30.0
    )
    assert capped.exact is False and capped.abort_reason is not None


def test_a_completed_search_records_how_much_work_it_did() -> None:
    result = enumerate_routes(open_grid(3, 3), **GENEROUS)
    assert result.nodes_visited > 0
    assert result.elapsed_seconds >= 0.0


def test_reachable_fraction_reports_how_much_of_the_grid_is_usable() -> None:
    full = enumerate_routes(open_grid(3, 3), **GENEROUS)
    assert full.reachable_fraction == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_enumeration_is_deterministic_in_order_and_content() -> None:
    maze = open_grid(4, 4)
    first = enumerate_routes(maze, **GENEROUS)
    second = enumerate_routes(maze, **GENEROUS)
    assert [(r.mask, r.directions) for r in first.routes] == [
        (r.mask, r.directions) for r in second.routes
    ]


def test_precomputed_adjacency_gives_the_same_answer() -> None:
    maze = open_grid(3, 3)
    shared = sorted_adjacency(maze)
    assert (
        enumerate_routes(maze, adjacency=shared, **GENEROUS).count
        == enumerate_routes(maze, **GENEROUS).count
    )
