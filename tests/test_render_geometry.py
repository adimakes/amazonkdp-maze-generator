"""Tests for ``maze_book.rendering.geometry`` (PRD 9.1, 17.9, 17.11).

The central property is that walls are the exact complement of open edges. It is
checked both on hand-built mazes, where the expected answer can be written down,
and as an invariant over generated ones, where it cannot -- the second is what
catches an off-by-one on a boundary that every small fixture happens to avoid.
"""

from __future__ import annotations

import pytest

from maze_book.errors import RenderingError
from maze_book.model.geometry import canonical_edge
from maze_book.model.maze_data import MazeData, PlacedAsset
from maze_book.rendering.geometry import (
    MazeGeometry,
    _runs,
    _unit_walls,
    asset_footprint,
    border_opening,
    raise_for_placements,
    route_polyline,
    safe_inset_rect,
    validate_placements,
    wall_segments,
)


def corridor(cols: int = 4) -> MazeData:
    """A 1 x ``cols`` corridor: every cell joined to the next."""
    return MazeData(
        maze_id="m", book_id="b", maze_index=1, seed=1, rows=1, cols=cols,
        start=(0, 0), finish=(0, cols - 1),
        open_edges=[canonical_edge((0, c), (0, c + 1)) for c in range(cols - 1)],
    )


def open_grid(rows: int, cols: int) -> MazeData:
    """Every interior wall removed -- the maximal-edge case."""
    edges = []
    for row in range(rows):
        for col in range(cols):
            if row + 1 < rows:
                edges.append(canonical_edge((row, col), (row + 1, col)))
            if col + 1 < cols:
                edges.append(canonical_edge((row, col), (row, col + 1)))
    return MazeData(
        maze_id="m", book_id="b", maze_index=1, seed=1, rows=rows, cols=cols,
        start=(0, 0), finish=(rows - 1, cols - 1), open_edges=edges,
    )


def total_unit_length(segments) -> float:
    return sum(max(abs(x1 - x0), abs(y1 - y0)) for x0, y0, x1, y1 in segments)


# ---------------------------------------------------------------------------
# MazeGeometry
# ---------------------------------------------------------------------------


def test_normalized_geometry_is_one_unit_per_cell_from_the_origin() -> None:
    geometry = MazeGeometry.normalized(3, 5)
    assert geometry.bounds == (0.0, 0.0, 5.0, 3.0)
    assert geometry.cell_rect((0, 0)) == (0.0, 0.0, 1.0, 1.0)
    assert geometry.cell_rect((2, 4)) == (4.0, 2.0, 5.0, 3.0)
    assert geometry.cell_centre((1, 2)) == (2.5, 1.5)


def test_cells_are_row_major_from_the_top_left() -> None:
    """Row grows downward, matching 17.5's coordinate system and SVG's y-down."""
    geometry = MazeGeometry.normalized(4, 4)
    assert geometry.cell_centre((0, 0))[1] < geometry.cell_centre((3, 0))[1]
    assert geometry.cell_centre((0, 0))[0] < geometry.cell_centre((0, 3))[0]


def test_fitted_fills_a_square_box_for_a_square_grid() -> None:
    geometry = MazeGeometry.fitted(10, 10, box=(20.0, 30.0, 120.0, 130.0))
    assert geometry.cell == pytest.approx(10.0)
    assert geometry.bounds == pytest.approx((20.0, 30.0, 120.0, 130.0))


def test_fitted_keeps_cells_square_and_centres_a_non_square_grid() -> None:
    """A stretched cell makes one corridor read as wider than the next, so the
    grid is letterboxed rather than distorted."""
    geometry = MazeGeometry.fitted(10, 20, box=(0.0, 0.0, 100.0, 100.0))
    assert geometry.cell == pytest.approx(5.0)
    assert geometry.width == pytest.approx(100.0)
    assert geometry.height == pytest.approx(50.0)
    assert geometry.origin == pytest.approx((0.0, 25.0))


def test_contains_cell_rejects_out_of_range_coordinates() -> None:
    geometry = MazeGeometry.normalized(3, 3)
    assert geometry.contains_cell((2, 2))
    assert not geometry.contains_cell((3, 0))
    assert not geometry.contains_cell((-1, 0))


# ---------------------------------------------------------------------------
# Run merging
# ---------------------------------------------------------------------------


def test_runs_groups_consecutive_integers() -> None:
    assert list(_runs([0, 1, 2, 5, 6, 9])) == [(0, 3), (5, 2), (9, 1)]


def test_runs_is_order_insensitive_and_empty_safe() -> None:
    assert list(_runs([6, 1, 5, 0, 2, 9])) == [(0, 3), (5, 2), (9, 1)]
    assert list(_runs([])) == []


# ---------------------------------------------------------------------------
# Wall derivation
# ---------------------------------------------------------------------------


def test_a_closed_grid_is_walled_on_every_cell_side() -> None:
    maze = MazeData(
        maze_id="m", book_id="b", maze_index=1, seed=1, rows=2, cols=2,
        start=(0, 0), finish=(1, 1), open_edges=[],
    )
    horizontal, vertical = _unit_walls(maze, openings={})
    assert len(horizontal) == 3 * 2  # 3 grid lines x 2 columns
    assert len(vertical) == 3 * 2


def test_an_open_grid_keeps_only_its_border() -> None:
    maze = open_grid(4, 4)
    horizontal, vertical = _unit_walls(maze, openings={})
    assert len(horizontal) == 8   # top and bottom rows only
    assert len(vertical) == 8     # left and right columns only


def test_every_interior_wall_is_exactly_the_absence_of_an_open_edge() -> None:
    maze = corridor(5)
    horizontal, vertical = _unit_walls(maze, openings={})
    for col in range(1, maze.cols):
        assert (0, col) not in vertical, f"corridor is walled at column {col}"
    assert (0, 0) in vertical and (0, 5) in vertical


def test_wall_segments_merge_collinear_runs() -> None:
    """A straight border drawn per cell side is a row of abutting rectangles
    whose joins show as seams at print resolution."""
    maze = open_grid(4, 4)
    segments = wall_segments(maze, MazeGeometry.normalized(4, 4), open_endpoints=False)
    assert len(segments) == 4  # one per side of the border
    assert total_unit_length(segments) == pytest.approx(16.0)


def test_merging_preserves_total_wall_length() -> None:
    maze = corridor(6)
    geometry = MazeGeometry.normalized(1, 6)
    horizontal, vertical = _unit_walls(maze, openings={})
    segments = wall_segments(maze, geometry, open_endpoints=False)
    assert total_unit_length(segments) == pytest.approx(len(horizontal) + len(vertical))


def test_wall_segments_scale_with_the_geometry_rather_than_being_recomputed() -> None:
    maze = open_grid(3, 3)
    unit = wall_segments(maze, MazeGeometry.normalized(3, 3), open_endpoints=False)
    scaled = wall_segments(
        maze, MazeGeometry.fitted(3, 3, box=(0.0, 0.0, 30.0, 30.0)), open_endpoints=False
    )
    assert total_unit_length(scaled) == pytest.approx(10.0 * total_unit_length(unit))


def test_segments_are_normalized_left_to_right_and_top_to_bottom() -> None:
    for x0, y0, x1, y1 in wall_segments(corridor(4), MazeGeometry.normalized(1, 4)):
        assert x0 <= x1 and y0 <= y1


# ---------------------------------------------------------------------------
# Border openings
# ---------------------------------------------------------------------------


def test_an_endpoint_on_the_border_opens_through_the_outward_side() -> None:
    maze = corridor(4)  # start (0,0), finish (0,3), single row
    openings = border_opening(maze)
    assert openings[(0, 0)] == "W"
    assert openings[(0, 3)] == "E"


def test_an_interior_endpoint_gets_no_opening() -> None:
    maze = open_grid(5, 5)
    maze.start = (2, 2)
    assert (2, 2) not in border_opening(maze)


def test_the_opening_removes_exactly_one_unit_of_border() -> None:
    maze = corridor(4)
    closed = total_unit_length(wall_segments(maze, MazeGeometry.normalized(1, 4), open_endpoints=False))
    opened = total_unit_length(wall_segments(maze, MazeGeometry.normalized(1, 4)))
    assert closed - opened == pytest.approx(2.0)  # one unit each for start and finish


def test_an_opening_never_becomes_an_edge_in_the_graph() -> None:
    """The opening is a gap in the drawn border, not a passage: it must not
    change traversability or the answer key."""
    maze = corridor(4)
    before = maze.edge_set()
    wall_segments(maze, MazeGeometry.normalized(1, 4))
    assert maze.edge_set() == before


def test_a_corner_endpoint_opens_away_from_the_other_endpoint() -> None:
    maze = open_grid(6, 6)
    maze.start = (0, 0)
    maze.finish = (5, 5)
    openings = border_opening(maze)
    assert openings[(0, 0)] in {"N", "W"}
    assert openings[(5, 5)] in {"S", "E"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_route_polyline_is_the_sequence_of_cell_centres() -> None:
    geometry = MazeGeometry.normalized(1, 3)
    assert route_polyline([(0, 0), (0, 1), (0, 2)], geometry) == [
        (0.5, 0.5), (1.5, 0.5), (2.5, 0.5),
    ]


def test_route_polyline_of_a_single_cell_is_one_point() -> None:
    assert len(route_polyline([(0, 0)], MazeGeometry.normalized(2, 2))) == 1


# ---------------------------------------------------------------------------
# Asset footprints and placement validation
# ---------------------------------------------------------------------------


def asset(cell, role="collectible", scale=0.6, asset_id="a.svg") -> PlacedAsset:
    return PlacedAsset(asset_id=asset_id, role=role, cell=cell, scale=scale)


def placed_maze(assets: list[PlacedAsset], rows: int = 3, cols: int = 3) -> MazeData:
    maze = open_grid(rows, cols)
    maze.assets = assets
    return maze


def test_footprint_is_a_centred_square_of_scale_times_the_cell() -> None:
    geometry = MazeGeometry.normalized(3, 3)
    footprint = asset_footprint(asset((1, 1), scale=0.5), geometry)
    assert footprint.rect == pytest.approx((1.25, 1.25, 1.75, 1.75))
    assert footprint.width == pytest.approx(0.5)


def test_footprint_scales_with_the_cell_not_the_illustrators_canvas() -> None:
    small = asset_footprint(asset((0, 0), scale=0.6), MazeGeometry.normalized(2, 2))
    large = asset_footprint(
        asset((0, 0), scale=0.6), MazeGeometry.fitted(2, 2, box=(0.0, 0.0, 20.0, 20.0))
    )
    assert large.width == pytest.approx(10.0 * small.width)


def test_safe_inset_shrinks_the_cell_on_every_side() -> None:
    assert safe_inset_rect((0, 0), MazeGeometry.normalized(2, 2), 0.12) == pytest.approx(
        (0.12, 0.12, 0.88, 0.88)
    )


def test_a_well_formed_placement_set_reports_no_violations() -> None:
    maze = placed_maze([
        asset((0, 0), role="start", asset_id="start.svg"),
        asset((2, 2), role="finish", asset_id="finish.svg"),
        asset((1, 1), scale=0.6),
    ])
    assert validate_placements(maze, clearance_fraction=0.12) == []


def test_an_oversized_asset_overflows_the_safe_inset() -> None:
    maze = placed_maze([
        asset((0, 0), role="start", asset_id="start.svg"),
        asset((2, 2), role="finish", asset_id="finish.svg"),
        asset((1, 1), scale=0.9),
    ])
    problems = validate_placements(maze, clearance_fraction=0.12)
    assert any("overflows the cell's safe inset" in p for p in problems)
    assert any("ceiling is 0.76" in p for p in problems)


def test_the_scale_ceiling_is_exactly_one_minus_twice_the_clearance() -> None:
    for scale, expected in ((0.76, True), (0.761, False)):
        maze = placed_maze([
            asset((0, 0), role="start", asset_id="start.svg"),
            asset((2, 2), role="finish", asset_id="finish.svg"),
            asset((1, 1), scale=scale),
        ])
        ok = not any(
            "safe inset" in p for p in validate_placements(maze, clearance_fraction=0.12)
        )
        assert ok is expected, scale


def test_two_assets_in_one_cell_are_rejected() -> None:
    maze = placed_maze([
        asset((0, 0), role="start", asset_id="start.svg"),
        asset((2, 2), role="finish", asset_id="finish.svg"),
        asset((1, 1), asset_id="candy.svg"),
        asset((1, 1), asset_id="bat.svg", role="dead-end"),
    ])
    problems = validate_placements(maze, clearance_fraction=0.12)
    assert any("shares a cell" in p for p in problems)


def test_an_asset_outside_the_grid_is_rejected() -> None:
    maze = placed_maze([
        asset((0, 0), role="start", asset_id="start.svg"),
        asset((2, 2), role="finish", asset_id="finish.svg"),
        asset((9, 9)),
    ])
    assert any("outside the 3x3 grid" in p for p in validate_placements(maze, clearance_fraction=0.12))


def test_an_asset_on_an_untraversable_cell_is_rejected() -> None:
    """A decorative asset must not assert anything about traversability."""
    maze = placed_maze([
        asset((0, 0), role="start", asset_id="start.svg"),
        asset((2, 2), role="finish", asset_id="finish.svg"),
        asset((1, 1)),
    ])
    maze.blocked_cells = [(1, 1)]
    assert any(
        "not traversable" in p for p in validate_placements(maze, clearance_fraction=0.12)
    )


def test_a_dead_end_marker_on_a_junction_is_rejected() -> None:
    """A dead-end marker on a cell with real choices covers one of them."""
    maze = placed_maze([
        asset((0, 0), role="start", asset_id="start.svg"),
        asset((2, 2), role="finish", asset_id="finish.svg"),
        asset((1, 1), role="dead-end", asset_id="bat.svg"),
    ])
    problems = validate_placements(maze, clearance_fraction=0.12)
    assert any("marks a cell of degree 4" in p for p in problems)


def test_a_dead_end_marker_on_a_degree_one_cell_is_accepted() -> None:
    maze = corridor(4)
    maze.assets = [
        asset((0, 0), role="start", asset_id="start.svg"),
        asset((0, 3), role="finish", asset_id="finish.svg"),
    ]
    assert validate_placements(maze, clearance_fraction=0.12) == []


def test_a_missing_or_misplaced_endpoint_asset_is_rejected() -> None:
    maze = placed_maze([asset((0, 0), role="start", asset_id="start.svg")])
    problems = validate_placements(maze, clearance_fraction=0.12)
    assert any("expected exactly one finish asset, found 0" in p for p in problems)

    maze = placed_maze([
        asset((1, 1), role="start", asset_id="start.svg"),
        asset((2, 2), role="finish", asset_id="finish.svg"),
    ])
    problems = validate_placements(maze, clearance_fraction=0.12)
    assert any("but MazeData.start is (0, 0)" in p for p in problems)


def test_raise_for_placements_reports_every_violation_at_once() -> None:
    maze = placed_maze([asset((9, 9), scale=0.95)])
    with pytest.raises(RenderingError, match=r"asset placement violation") as excinfo:
        raise_for_placements(maze, clearance_fraction=0.12)
    assert len(excinfo.value.details) >= 2


def test_raise_for_placements_is_silent_on_a_valid_maze() -> None:
    maze = placed_maze([
        asset((0, 0), role="start", asset_id="start.svg"),
        asset((2, 2), role="finish", asset_id="finish.svg"),
    ])
    raise_for_placements(maze, clearance_fraction=0.12)  # must not raise


def test_placement_validation_is_scale_invariant() -> None:
    """Validating in normalized coordinates means one verdict, not one per
    output format."""
    maze = placed_maze([
        asset((0, 0), role="start", asset_id="start.svg"),
        asset((2, 2), role="finish", asset_id="finish.svg"),
        asset((1, 1), scale=0.9),
    ])
    normalized = validate_placements(maze, clearance_fraction=0.12)
    page = validate_placements(
        maze, clearance_fraction=0.12,
        geometry=MazeGeometry.fitted(3, 3, box=(0.0, 0.0, 486.0, 486.0)),
    )
    assert len(normalized) == len(page) == 1


# ---------------------------------------------------------------------------
# The invariant, over topology nobody hand-wrote
# ---------------------------------------------------------------------------


def _random_maze(seed: int, rows: int, cols: int) -> MazeData:
    """A spanning tree from the real generator, then a few extra edges opened.

    Hand-built fixtures agree with whatever assumption the fixture author had.
    Generated topology is what catches a boundary case none of them happened to
    contain.
    """
    import random

    from maze_book.generation.adapter_mazelib import GENERATOR_ID, base_generator

    tree = set(base_generator(GENERATOR_ID).carve(rows, cols, seed))
    candidates = []
    for row in range(rows):
        for col in range(cols):
            if row + 1 < rows:
                candidates.append(canonical_edge((row, col), (row + 1, col)))
            if col + 1 < cols:
                candidates.append(canonical_edge((row, col), (row, col + 1)))
    extra = [edge for edge in candidates if edge not in tree]
    random.Random(seed + 1000).shuffle(extra)
    return MazeData(
        maze_id="m", book_id="b", maze_index=1, seed=seed, rows=rows, cols=cols,
        start=(0, 0), finish=(rows - 1, cols - 1),
        open_edges=sorted(tree | set(extra[: max(1, len(extra) // 8)])),
    )


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_walls_are_the_exact_complement_of_open_edges_on_generated_mazes(seed: int) -> None:
    maze = _random_maze(seed, 7, 9)
    horizontal, vertical = _unit_walls(maze, openings={})
    opens = maze.edge_set()
    for row in range(maze.rows):
        for col in range(maze.cols):
            if row + 1 < maze.rows:
                walled = (row + 1, col) in horizontal
                assert walled is (canonical_edge((row, col), (row + 1, col)) not in opens)
            if col + 1 < maze.cols:
                walled = (row, col + 1) in vertical
                assert walled is (canonical_edge((row, col), (row, col + 1)) not in opens)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_merging_never_loses_or_invents_wall_on_generated_mazes(seed: int) -> None:
    maze = _random_maze(seed, 7, 9)
    horizontal, vertical = _unit_walls(maze, openings={})
    segments = wall_segments(maze, MazeGeometry.normalized(maze.rows, maze.cols), open_endpoints=False)
    assert total_unit_length(segments) == pytest.approx(len(horizontal) + len(vertical))


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_border_is_closed_apart_from_the_two_endpoint_openings(seed: int) -> None:
    maze = _random_maze(seed, 7, 9)
    openings = border_opening(maze)
    horizontal, vertical = _unit_walls(maze, openings=openings)
    border = (
        sum(1 for row, _ in horizontal if row in (0, maze.rows))
        + sum(1 for _, col in vertical if col in (0, maze.cols))
    )
    perimeter = 2 * (maze.rows + maze.cols)
    assert border == perimeter - len(openings)
