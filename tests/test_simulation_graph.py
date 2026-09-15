"""Tests for ``maze_book.simulation.graph`` (PRD 17.16).

These are the measurements every acceptance decision rests on, so they are
checked against graphs whose answer can be worked out by hand. The cyclomatic
number matters most: a profile asking for ``loops: 3..4`` is checked against
``k = E - V + C`` computed from the graph that actually exists, never against how
many edges the braider tried to add.
"""

from __future__ import annotations

import pytest

from maze_book.simulation.graph import (
    Reduction,
    bfs_distances,
    connected_components,
    corridor_runs,
    cyclomatic_number,
    dead_end_cells,
    dead_end_depth,
    degree,
    edge_count,
    junctions,
    path_relevant_subgraph,
    reachable_from,
    run_index,
    shortest_path,
)


def adjacency(*pairs) -> dict:
    """Build a symmetric adjacency from unordered pairs."""
    adj: dict = {}
    for a, b in pairs:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return adj


def path_graph(n: int) -> dict:
    return adjacency(*[((0, i), (0, i + 1)) for i in range(n - 1)])


def cycle_graph(n: int) -> dict:
    return adjacency(*[((0, i), (0, (i + 1) % n)) for i in range(n)])


# ---------------------------------------------------------------------------
# Components and reachability
# ---------------------------------------------------------------------------


def test_a_connected_graph_has_one_component() -> None:
    assert len(connected_components(path_graph(5))) == 1


def test_disjoint_pieces_are_separate_components() -> None:
    adj = adjacency(((0, 0), (0, 1)), ((5, 5), (5, 6)))
    components = connected_components(adj)
    assert len(components) == 2
    assert {len(c) for c in components} == {2}


def test_an_isolated_cell_counts_as_its_own_component() -> None:
    adj = adjacency(((0, 0), (0, 1)))
    adj[(9, 9)] = set()
    assert len(connected_components(adj)) == 2


def test_reachable_from_walks_the_whole_component_and_no_further() -> None:
    adj = adjacency(((0, 0), (0, 1)), ((0, 1), (0, 2)), ((5, 5), (5, 6)))
    assert reachable_from(adj, (0, 0)) == {(0, 0), (0, 1), (0, 2)}
    assert reachable_from(adj, (5, 5)) == {(5, 5), (5, 6)}


def test_reachable_from_an_unknown_cell_is_empty_or_itself() -> None:
    assert reachable_from(adjacency(((0, 0), (0, 1))), (9, 9)) <= {(9, 9)}


# ---------------------------------------------------------------------------
# Edge counting and the cyclomatic number
# ---------------------------------------------------------------------------


def test_edge_count_does_not_double_count_a_symmetric_adjacency() -> None:
    assert edge_count(path_graph(5)) == 4
    assert edge_count(cycle_graph(6)) == 6


def test_a_tree_has_no_loops() -> None:
    """k = E - V + C. A spanning tree has E = V - 1 and C = 1, so k = 0."""
    assert cyclomatic_number(path_graph(7)) == 0


def test_a_single_cycle_has_one_loop() -> None:
    assert cyclomatic_number(cycle_graph(6)) == 1


def test_each_extra_edge_adds_exactly_one_loop() -> None:
    adj = cycle_graph(8)
    assert cyclomatic_number(adj) == 1
    adj[(0, 0)].add((0, 4))
    adj[(0, 4)].add((0, 0))
    assert cyclomatic_number(adj) == 2
    adj[(0, 1)].add((0, 5))
    adj[(0, 5)].add((0, 1))
    assert cyclomatic_number(adj) == 3


def test_the_formula_accounts_for_disconnected_components() -> None:
    """Two separate cycles are two loops, not one -- which only comes out right
    if C is counted rather than assumed to be 1."""
    adj = adjacency(*[((0, i), (0, (i + 1) % 4)) for i in range(4)])
    adj.update(adjacency(*[((9, i), (9, (i + 1) % 4)) for i in range(4)]))
    assert len(connected_components(adj)) == 2
    assert cyclomatic_number(adj) == 2


def test_an_empty_graph_has_no_loops() -> None:
    assert cyclomatic_number({}) == 0


def test_a_fully_open_grid_matches_the_closed_form() -> None:
    """For an r x c grid with every interior wall removed,
    E = r(c-1) + c(r-1), V = rc, C = 1."""
    rows, cols = 4, 5
    pairs = []
    for row in range(rows):
        for col in range(cols):
            if col + 1 < cols:
                pairs.append(((row, col), (row, col + 1)))
            if row + 1 < rows:
                pairs.append(((row, col), (row + 1, col)))
    adj = adjacency(*pairs)
    expected = (rows * (cols - 1) + cols * (rows - 1)) - rows * cols + 1
    assert cyclomatic_number(adj) == expected == 12


# ---------------------------------------------------------------------------
# Distances and shortest paths
# ---------------------------------------------------------------------------


def test_bfs_distances_are_hop_counts_from_the_source() -> None:
    distances = bfs_distances(path_graph(5), (0, 0))
    assert distances == {(0, 0): 0, (0, 1): 1, (0, 2): 2, (0, 3): 3, (0, 4): 4}


def test_bfs_omits_unreachable_cells() -> None:
    adj = adjacency(((0, 0), (0, 1)), ((5, 5), (5, 6)))
    assert (5, 5) not in bfs_distances(adj, (0, 0))


def test_shortest_path_returns_the_cells_in_order() -> None:
    assert shortest_path(path_graph(4), (0, 0), (0, 3)) == [
        (0, 0), (0, 1), (0, 2), (0, 3)
    ]


def test_shortest_path_takes_the_short_way_round_a_cycle() -> None:
    path = shortest_path(cycle_graph(8), (0, 0), (0, 2))
    assert len(path) == 3


def test_shortest_path_between_disconnected_cells_is_empty() -> None:
    adj = adjacency(((0, 0), (0, 1)), ((5, 5), (5, 6)))
    assert shortest_path(adj, (0, 0), (5, 5)) == []


def test_shortest_path_to_itself_is_a_single_cell() -> None:
    assert shortest_path(path_graph(4), (0, 1), (0, 1)) == [(0, 1)]


# ---------------------------------------------------------------------------
# Degree, dead ends and depth
# ---------------------------------------------------------------------------


def test_degree_counts_open_neighbours() -> None:
    adj = path_graph(3)
    assert degree(adj, (0, 0)) == 1
    assert degree(adj, (0, 1)) == 2
    assert degree(adj, (9, 9)) == 0


def test_dead_ends_are_the_degree_one_cells() -> None:
    """8.3: a non-terminal cell with graph degree one is a dead end."""
    assert set(dead_end_cells(path_graph(4))) == {(0, 0), (0, 3)}


def test_the_endpoints_can_be_excluded_from_the_dead_end_count() -> None:
    adj = path_graph(4)
    assert dead_end_cells(adj, exclude=((0, 0), (0, 3))) == []


def test_a_cycle_has_no_dead_ends() -> None:
    assert dead_end_cells(cycle_graph(5)) == []


def test_dead_end_depth_counts_steps_back_to_a_junction() -> None:
    """A stub hanging off a corridor: depth is how far a child walks before the
    mistake is obvious."""
    adj = adjacency(
        ((0, 0), (0, 1)), ((0, 1), (0, 2)), ((0, 2), (0, 3)),   # the corridor
        ((0, 1), (1, 1)),                                        # a depth-1 stub
        ((0, 2), (1, 2)), ((1, 2), (2, 2)),                      # a depth-2 stub
    )
    assert dead_end_depth(adj, (1, 1)) == 1
    assert dead_end_depth(adj, (2, 2)) == 2


def test_dead_end_depth_rejects_a_cell_that_is_not_a_dead_end() -> None:
    """Asking for the depth of a junction is a caller error, not a zero: a
    silent 0 would read as "a dead end right next to a junction"."""
    adj = adjacency(((0, 1), (0, 0)), ((0, 1), (0, 2)), ((0, 1), (1, 1)))
    with pytest.raises(ValueError, match=r"is not a dead end"):
        dead_end_depth(adj, (0, 1))


def test_dead_end_depth_in_a_junctionless_component_is_its_length() -> None:
    """A component with no junction at all is one long corridor, and the honest
    answer to "how far back to a choice?" is its whole length."""
    assert dead_end_depth(path_graph(5), (0, 0)) == 4


# ---------------------------------------------------------------------------
# Junctions and corridor runs
# ---------------------------------------------------------------------------


def test_junctions_are_the_cells_with_three_or_more_ways_out() -> None:
    adj = adjacency(((0, 1), (0, 0)), ((0, 1), (0, 2)), ((0, 1), (1, 1)))
    assert junctions(adj) == [(0, 1)]


def test_a_corridor_has_no_junctions() -> None:
    assert junctions(path_graph(6)) == []


def test_corridor_runs_split_at_junctions() -> None:
    """C8's spacing rule is "no two candies adjacent in the same corridor run",
    so the runs have to be the stretches between choices."""
    adj = adjacency(
        ((0, 0), (0, 1)), ((0, 1), (0, 2)),      # left corridor into the junction
        ((0, 2), (0, 3)), ((0, 3), (0, 4)),      # right corridor out
        ((0, 2), (1, 2)), ((1, 2), (2, 2)),      # branch down
    )
    runs = corridor_runs(adj)
    # Runs are maximal groups of *degree-two* cells, so the junction (0,2) is in
    # none of them and the degree-one tips are excluded too. What is left are
    # the three single mid-corridor cells.
    assert all((0, 2) not in run for run in runs)
    assert sorted(sorted(run) for run in runs) == [[(0, 1)], [(0, 3)], [(1, 2)]]


def test_run_index_maps_each_corridor_cell_to_its_run() -> None:
    index = run_index(corridor_runs(path_graph(4)))
    assert index[(0, 1)] == index[(0, 2)]
    # The degree-one tips belong to no run, so spacing never applies to them.
    assert (0, 0) not in index and (0, 3) not in index


def test_cells_in_different_runs_get_different_indexes() -> None:
    adj = adjacency(
        ((0, 0), (0, 1)), ((0, 1), (0, 2)),
        ((0, 2), (0, 3)), ((0, 3), (0, 4)),
        ((0, 2), (1, 2)), ((1, 2), (2, 2)),
    )
    index = run_index(corridor_runs(adj))
    assert index[(0, 1)] != index[(0, 3)] != index[(1, 2)]


# ---------------------------------------------------------------------------
# Path-relevant reduction
# ---------------------------------------------------------------------------


def test_the_reduction_drops_cells_no_route_can_use() -> None:
    """Enumeration is exponential in the number of cells it has to consider, so
    pruning the cells that cannot appear on any simple start->finish path is
    what makes exact enumeration affordable on an 18x18."""
    adj = adjacency(
        ((0, 0), (0, 1)), ((0, 1), (0, 2)),      # the corridor start -> finish
        ((0, 1), (1, 1)), ((1, 1), (2, 1)),      # a dead-end branch
    )
    reduction = path_relevant_subgraph(adj, (0, 0), (0, 2))
    assert isinstance(reduction, Reduction)
    assert (2, 1) not in reduction.adjacency
    assert reduction.size < len(adj)


def test_the_reduction_keeps_both_endpoints() -> None:
    reduction = path_relevant_subgraph(path_graph(5), (0, 0), (0, 4))
    assert (0, 0) in reduction.adjacency and (0, 4) in reduction.adjacency


def test_a_reduction_keeps_every_cell_of_a_pure_corridor() -> None:
    reduction = path_relevant_subgraph(path_graph(5), (0, 0), (0, 4))
    assert reduction.size == 5


def test_the_reduction_keeps_both_arms_of_a_loop() -> None:
    """Both arms are usable by some route, so neither may be pruned -- doing so
    would silently drop half the answers."""
    reduction = path_relevant_subgraph(cycle_graph(6), (0, 0), (0, 3))
    assert reduction.size == 6
