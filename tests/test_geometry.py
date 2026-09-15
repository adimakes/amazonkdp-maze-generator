"""Tests for ``maze_book.model.geometry`` grid primitives (PRD 17.5)."""

from __future__ import annotations

import pytest

from maze_book.model.geometry import (
    canonical_edge,
    cell_index,
    in_bounds,
    index_cell,
    is_adjacent,
    neighbors,
    resolve_region,
    sort_edges,
)


# --------------------------------------------------------------------------- #
# canonical_edge / is_adjacent
# --------------------------------------------------------------------------- #

def test_canonical_edge_is_order_independent():
    a, b = (1, 2), (1, 3)
    assert canonical_edge(a, b) == canonical_edge(b, a)


def test_canonical_edge_orders_lexicographically_smaller_first():
    # (1, 2) < (1, 3) lexicographically.
    assert canonical_edge((1, 3), (1, 2)) == ((1, 2), (1, 3))
    # (0, 5) < (1, 5) lexicographically (row dominates).
    assert canonical_edge((1, 5), (0, 5)) == ((0, 5), (1, 5))


@pytest.mark.parametrize(
    "a, b",
    [
        ((0, 0), (0, 0)),  # identical
        ((0, 0), (1, 1)),  # diagonal
        ((0, 0), (2, 0)),  # two apart
        ((0, 0), (0, 2)),  # two apart, other axis
    ],
)
def test_canonical_edge_raises_value_error_when_not_adjacent(a, b):
    with pytest.raises(ValueError):
        canonical_edge(a, b)


def test_is_adjacent_true_only_for_orthogonal_neighbors():
    assert is_adjacent((2, 2), (2, 3)) is True
    assert is_adjacent((2, 2), (1, 2)) is True
    assert is_adjacent((2, 2), (2, 2)) is False
    assert is_adjacent((2, 2), (3, 3)) is False
    assert is_adjacent((2, 2), (4, 2)) is False


# --------------------------------------------------------------------------- #
# neighbors / in_bounds
# --------------------------------------------------------------------------- #

def test_in_bounds_basic():
    assert in_bounds((0, 0), rows=4, cols=5) is True
    assert in_bounds((3, 4), rows=4, cols=5) is True
    assert in_bounds((-1, 0), rows=4, cols=5) is False
    assert in_bounds((0, -1), rows=4, cols=5) is False
    assert in_bounds((4, 0), rows=4, cols=5) is False
    assert in_bounds((0, 5), rows=4, cols=5) is False


def test_neighbors_mid_grid_yields_all_four_in_nesw_order():
    # (2, 2) in a 5x5 grid is fully interior.
    result = list(neighbors((2, 2), rows=5, cols=5))
    assert result == [(1, 2), (2, 3), (3, 2), (2, 1)]


@pytest.mark.parametrize(
    "corner, rows, cols, expected",
    [
        # top-left: only E and S are in-bounds, in that (N,E,S,W) order.
        ((0, 0), 4, 5, [(0, 1), (1, 0)]),
        # top-right: only S and W are in-bounds.
        ((0, 4), 4, 5, [(1, 4), (0, 3)]),
        # bottom-left: only N and E are in-bounds.
        ((3, 0), 4, 5, [(2, 0), (3, 1)]),
        # bottom-right: only N and W are in-bounds.
        ((3, 4), 4, 5, [(2, 4), (3, 3)]),
    ],
)
def test_neighbors_at_all_four_corners(corner, rows, cols, expected):
    assert list(neighbors(corner, rows=rows, cols=cols)) == expected


def test_neighbors_only_yields_in_bounds_cells_on_a_1x1_grid():
    assert list(neighbors((0, 0), rows=1, cols=1)) == []


# --------------------------------------------------------------------------- #
# cell_index / index_cell
# --------------------------------------------------------------------------- #

def test_cell_index_and_index_cell_round_trip_full_grid():
    rows, cols = 5, 7
    seen_indices = set()
    for r in range(rows):
        for c in range(cols):
            idx = cell_index((r, c), cols)
            assert index_cell(idx, cols) == (r, c)
            seen_indices.add(idx)
    # Every index in range(rows * cols) is hit exactly once (a bijection).
    assert seen_indices == set(range(rows * cols))


def test_cell_index_matches_row_major_formula():
    assert cell_index((0, 0), cols=7) == 0
    assert cell_index((4, 6), cols=7) == 4 * 7 + 6 == 34
    assert cell_index((1, 0), cols=7) == 7


# --------------------------------------------------------------------------- #
# sort_edges
# --------------------------------------------------------------------------- #

def test_sort_edges_dedups_and_orders_independent_of_input_order():
    e1 = ((0, 0), (0, 1))
    e2 = ((0, 1), (1, 1))
    e3 = ((1, 0), (1, 1))
    expected = [e1, e2, e3]

    # Feed in various shuffles, each with a duplicate thrown in.
    assert sort_edges([e3, e1, e2, e1]) == expected
    assert sort_edges([e2, e2, e3, e1]) == expected
    assert sort_edges([e1, e2, e3]) == expected


def test_sort_edges_returns_a_list_not_a_set_or_generator():
    result = sort_edges([((0, 0), (0, 1))])
    assert isinstance(result, list)


# --------------------------------------------------------------------------- #
# resolve_region
# --------------------------------------------------------------------------- #

def test_resolve_region_negative_bounds_are_slice_style():
    # An 8x8 grid, region {-3, -1, -3, -1} should resolve to the bottom-right
    # 3x3 block: rows/cols 5, 6, 7 (i.e. -3 -> 5, -1 -> 7).
    region = {"rowMin": -3, "rowMax": -1, "colMin": -3, "colMax": -1}
    cells = resolve_region(region, rows=8, cols=8)
    assert len(cells) == 9
    assert set(cells) == {(r, c) for r in (5, 6, 7) for c in (5, 6, 7)}


def test_resolve_region_positive_bounds_are_inclusive():
    region = {"rowMin": 0, "rowMax": 1, "colMin": 0, "colMax": 1}
    cells = resolve_region(region, rows=8, cols=8)
    assert set(cells) == {(0, 0), (0, 1), (1, 0), (1, 1)}


def test_resolve_region_clamps_out_of_range_bounds_instead_of_raising():
    # Grossly out-of-range bounds on both ends must be clamped to the grid,
    # not raise -- the docstring promises clamping.
    region = {"rowMin": -1000, "rowMax": 1000, "colMin": -1000, "colMax": 1000}
    cells = resolve_region(region, rows=8, cols=8)
    assert set(cells) == {(r, c) for r in range(8) for c in range(8)}


def test_resolve_region_swaps_min_and_max_when_inverted():
    # rowMin (5) > rowMax (2) in the raw region -- after resolving (both are
    # already non-negative, so unchanged) and clamping, min/max must be
    # swapped rather than yielding an empty or nonsensical range.
    region = {"rowMin": 5, "rowMax": 2, "colMin": 0, "colMax": 0}
    cells = resolve_region(region, rows=8, cols=8)
    assert set(cells) == {(r, 0) for r in range(2, 6)}


def test_resolve_region_never_raises_for_any_integer_bounds():
    region = {"rowMin": -999, "rowMax": 999, "colMin": 999, "colMax": -999}
    # Should not raise; just clamp and (if needed) swap.
    cells = resolve_region(region, rows=4, cols=4)
    assert len(cells) == 16
