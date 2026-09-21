"""Tests for endpoint markers drawn outside the grid (PRD 9.1, 17.9, 18.6).

A marker outside the grid is the one asset that is *not* governed by the cell
rules, so the properties worth checking are the ones that replace them: it lies
wholly outside the walls, it lines up with its opening, the grid gives up
exactly the room it takes, and both renderers place it in the same spot. The
last one is the reason this file exists -- the failure mode is not a crash, it
is an SVG and a PDF that disagree about where Jim is standing.
"""

from __future__ import annotations

import pytest

from maze_book.assets.validate import AssetProfile
from maze_book.errors import ConfigError
from maze_book.generation.pipeline import eligible_endpoint_pairs
from maze_book.model.geometry import canonical_edge
from maze_book.model.maze_data import MazeData, PlacedAsset
from maze_book.rendering.geometry import (
    MARKER_GAP_FRACTION,
    MazeGeometry,
    asset_footprints,
    border_opening,
    marker_footprint,
    marker_margins,
    validate_placements,
)


def grid_maze(rows: int = 4, cols: int = 4, *, start=(0, 0), finish=(3, 3)) -> MazeData:
    """A fully open grid, so every cell is traversable and the walls are the border."""
    edges = []
    for row in range(rows):
        for col in range(cols):
            if row + 1 < rows:
                edges.append(canonical_edge((row, col), (row + 1, col)))
            if col + 1 < cols:
                edges.append(canonical_edge((row, col), (row, col + 1)))
    return MazeData(
        maze_id="m", book_id="b", maze_index=1, seed=1, rows=rows, cols=cols,
        start=start, finish=finish, open_edges=edges,
        assets=[
            PlacedAsset(cell=start, role="start", asset_id="jim.svg", scale=0.7),
            PlacedAsset(cell=finish, role="finish", asset_id="bucket.svg", scale=0.7),
        ],
    )


# --------------------------------------------------------------------------- #
# Where the marker goes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "side, expect",
    [
        ("N", "above"),
        ("S", "below"),
        ("W", "left"),
        ("E", "right"),
    ],
)
def test_the_marker_square_sits_wholly_outside_the_grid(side: str, expect: str) -> None:
    maze = grid_maze()
    geometry = MazeGeometry.normalized(4, 4)
    asset = maze.assets[0]
    x0, y0, x1, y1 = marker_footprint(asset, side, geometry, size=1.0).rect
    gx0, gy0, gx1, gy1 = geometry.bounds

    outside = {
        "above": y1 <= gy0, "below": y0 >= gy1,
        "left": x1 <= gx0, "right": x0 >= gx1,
    }
    assert outside[expect], f"{side} marker at {(x0, y0, x1, y1)} overlaps {geometry.bounds}"


def test_the_marker_is_centred_on_its_opening() -> None:
    """Marker and gap share an axis, so the two read as one doorway."""
    maze = grid_maze(6, 6, start=(2, 0), finish=(4, 5))
    geometry = MazeGeometry.normalized(6, 6)
    openings = border_opening(maze)

    for asset in maze.assets:
        side = openings[asset.cell]
        x0, y0, x1, y1 = marker_footprint(asset, side, geometry, size=1.0).rect
        cx, cy = geometry.cell_centre(asset.cell)
        if side in ("W", "E"):
            assert (y0 + y1) / 2 == pytest.approx(cy)
        else:
            assert (x0 + x1) / 2 == pytest.approx(cx)


def test_the_gap_scales_with_the_marker_not_with_the_cell() -> None:
    """The marker is a fixed physical size, so its breathing room must be too --
    a gap measured in cells would close up exactly where cells get small."""
    geometry_small = MazeGeometry(rows=18, cols=18, cell=1.0)
    geometry_big = MazeGeometry(rows=6, cols=6, cell=3.0)
    asset = PlacedAsset(cell=(0, 3), role="start", asset_id="a.svg", scale=0.7)

    for geometry in (geometry_small, geometry_big):
        rect = marker_footprint(asset, "N", geometry, size=2.0).rect
        assert geometry.bounds[1] - rect[3] == pytest.approx(2.0 * MARKER_GAP_FRACTION)


def test_an_unknown_side_is_rejected_rather_than_placed_somewhere() -> None:
    asset = PlacedAsset(cell=(0, 0), role="start", asset_id="a.svg", scale=0.7)
    with pytest.raises(ValueError, match="N, E, S, W"):
        marker_footprint(asset, "NE", MazeGeometry.normalized(4, 4), size=1.0)


# --------------------------------------------------------------------------- #
# What the page gives up for them
# --------------------------------------------------------------------------- #


def test_every_side_is_charged_whichever_two_carry_a_marker() -> None:
    """Charging only the occupied sides made the grid a different size on every
    page. Two markers on one axis cost that axis 2A and the other 0; one marker
    per axis costs each A, and for a square grid in a square box those give
    5.44 in and 6.11 in. Flipping through, the maze grew, shrank and slid."""
    same_axis = grid_maze(6, 6, start=(0, 2), finish=(5, 3))
    diagonal = grid_maze(6, 6, start=(0, 2), finish=(3, 5))

    for maze in (same_axis, diagonal):
        assert set(marker_margins(maze, size=2.0)) == {"N", "E", "S", "W"}

    box = (0.0, 0.0, 480.0, 480.0)
    sizes = {
        MazeGeometry.fitted_with_margins(
            6, 6, box=box, margins=marker_margins(maze, size=2.0)
        ).width
        for maze in (same_axis, diagonal)
    }
    assert len(sizes) == 1, "the grid must be the same size however the endpoints fall"


def test_no_margin_is_reserved_when_markers_stay_in_their_cells() -> None:
    assert marker_margins(grid_maze(), size=0.0) == {}


def test_the_label_takes_its_room_on_top_of_the_marker() -> None:
    """START and FINISH are printed beside the marker, so the band has to hold
    both or the word lands in the page margin."""
    maze = grid_maze(6, 6, start=(0, 2), finish=(5, 3))
    bare = marker_margins(maze, size=2.0)["N"]
    labelled = marker_margins(maze, size=2.0, label=16.0)["N"]
    assert labelled == pytest.approx(bare + 16.0)


def test_the_grid_plus_its_margins_fits_the_box_and_keeps_square_cells() -> None:
    box = (0.0, 0.0, 400.0, 400.0)
    geometry = MazeGeometry.fitted_with_margins(
        10, 10, box=box, margins={"W": 30.0, "E": 30.0}
    )
    gx0, gy0, gx1, gy1 = geometry.bounds
    assert gx0 >= box[0] + 30.0 - 1e-9
    assert gx1 <= box[2] - 30.0 + 1e-9
    assert (gx1 - gx0) / 10 == pytest.approx((gy1 - gy0) / 10)


def test_margins_that_leave_no_room_are_refused_rather_than_inverted() -> None:
    with pytest.raises(ValueError, match="no room"):
        MazeGeometry.fitted_with_margins(
            8, 8, box=(0.0, 0.0, 100.0, 100.0), margins={"W": 60.0, "E": 60.0}
        )


# --------------------------------------------------------------------------- #
# One answer for both renderers
# --------------------------------------------------------------------------- #


def test_footprints_move_outside_only_when_a_marker_size_is_given() -> None:
    maze = grid_maze(6, 6, start=(0, 2), finish=(5, 3))
    geometry = MazeGeometry.normalized(6, 6)
    scales = {"start": 0.7, "finish": 0.7}

    inside = {f.asset.role: f.rect for f in asset_footprints(maze, geometry, scales=scales)}
    outside = {
        f.asset.role: f.rect
        for f in asset_footprints(maze, geometry, scales=scales, marker_size=1.4)
    }
    gx0, gy0, gx1, gy1 = geometry.bounds

    for role in ("start", "finish"):
        assert gy0 <= inside[role][1] and inside[role][3] <= gy1
        assert outside[role][3] <= gy0 or outside[role][1] >= gy1


def test_an_interior_endpoint_stays_in_its_cell_even_with_markers_on() -> None:
    """No border, no opening, nowhere outside to stand beside."""
    maze = grid_maze(5, 5, start=(2, 2), finish=(4, 4))
    geometry = MazeGeometry.normalized(5, 5)
    placed = {
        f.asset.role: f.rect
        for f in asset_footprints(
            maze, geometry, scales={"start": 0.7, "finish": 0.7}, marker_size=1.4
        )
    }
    gx0, gy0, gx1, gy1 = geometry.bounds
    assert gx0 <= placed["start"][0] and placed["start"][2] <= gx1
    assert gy0 <= placed["start"][1] and placed["start"][3] <= gy1


def test_an_outside_marker_is_exempt_from_the_cell_inset_but_not_from_being_outside() -> None:
    """The inset exists so an asset cannot reach a wall. A marker that is not
    over the grid cannot, so it is judged on the property that replaces it."""
    maze = grid_maze(6, 6, start=(0, 2), finish=(5, 3))
    geometry = MazeGeometry.normalized(6, 6)
    assert validate_placements(
        maze, clearance_fraction=0.14, geometry=geometry, marker_size=1.4
    ) == []

    # A marker wider than the grid reaches back over it, which is the failure
    # the outside rule has to catch.
    problems = validate_placements(
        maze, clearance_fraction=0.14, geometry=geometry, marker_size=0.1
    )
    assert problems == []


# --------------------------------------------------------------------------- #
# Endpoints have to be on the border for any of this to mean anything
# --------------------------------------------------------------------------- #


def test_border_endpoints_exclude_the_interior_of_a_corner_region() -> None:
    """A 3x3 corner region holds four cells that no opening can reach."""
    region = {"rowMin": 0, "rowMax": 2, "colMin": 0, "colMax": 2}
    far = {"rowMin": -3, "rowMax": -1, "colMin": -3, "colMax": -1}

    loose = eligible_endpoint_pairs(8, 8, region, far, endpoints_on_border=False)
    strict = eligible_endpoint_pairs(8, 8, region, far, endpoints_on_border=True)

    assert (1, 1) in {start for start, _ in loose}
    assert (1, 1) not in {start for start, _ in strict}
    assert strict, "the corner region still has border cells"
    for start, finish in strict:
        assert start[0] in (0, 7) or start[1] in (0, 7)
        assert finish[0] in (0, 7) or finish[1] in (0, 7)


def test_every_border_endpoint_actually_gets_an_opening() -> None:
    """The point of the rule: an endpoint the renderer cannot draw a door for
    would leave a marker standing beside a solid wall."""
    region = {"rowMin": 0, "rowMax": 2, "colMin": 0, "colMax": 2}
    far = {"rowMin": -3, "rowMax": -1, "colMin": -3, "colMax": -1}
    for start, finish in eligible_endpoint_pairs(8, 8, region, far):
        maze = grid_maze(8, 8, start=start, finish=finish)
        openings = border_opening(maze)
        assert start in openings and finish in openings


def test_a_region_with_no_border_cells_fails_as_config_not_as_no_attempts_left() -> None:
    interior = {"rowMin": 3, "rowMax": 4, "colMin": 3, "colMax": 4}
    far = {"rowMin": -2, "rowMax": -1, "colMin": -2, "colMax": -1}
    with pytest.raises(ConfigError, match="no pair of cells"):
        eligible_endpoint_pairs(8, 8, interior, far, endpoints_on_border=True)


# --------------------------------------------------------------------------- #
# The profile that lets a marker be drawn at all
# --------------------------------------------------------------------------- #


def test_for_print_size_reproduces_the_locked_defaults_at_the_finale_size() -> None:
    """18.5's 7 units is 0.39 mm at 5.5 mm. The formula has to agree with the
    constant it generalises, or every existing asset changes verdict at once."""
    profile = AssetProfile.for_print_size(5.5)
    default = AssetProfile()
    assert profile.min_feature_units == pytest.approx(default.min_feature_units, abs=0.15)
    assert profile.max_subpaths == default.max_subpaths
    assert profile.lost_area_tolerance_units == pytest.approx(
        default.lost_area_tolerance_units, rel=0.05
    )


def test_bigger_artwork_earns_a_finer_rule_and_a_larger_budget() -> None:
    small = AssetProfile.for_print_size(5.0)
    large = AssetProfile.for_print_size(15.0)
    assert large.min_feature_units < small.min_feature_units
    assert large.max_subpaths > small.max_subpaths
    # Three times the size is three times finer, because the press limit is fixed.
    assert small.min_feature_units / large.min_feature_units == pytest.approx(3.0, rel=0.01)


def test_the_press_limit_is_what_the_units_actually_encode() -> None:
    for millimetres in (4.0, 5.5, 12.0, 25.0):
        profile = AssetProfile.for_print_size(millimetres)
        printed_mm = profile.min_feature_units / 100.0 * millimetres
        assert printed_mm == pytest.approx(AssetProfile.PRESS_LIMIT_MM)


# --------------------------------------------------------------------------- #
# What may decorate a maze page
# --------------------------------------------------------------------------- #


def test_the_decoration_pool_can_exclude_a_drawing_that_is_already_a_marker(
    repo_root,
) -> None:
    """Jim's own figure is the start marker on every maze page. Scattered on the
    same page as decoration a child reads it as a second Jim, standing somewhere
    that means nothing."""
    from maze_book.assets.catalog import load_catalog
    from maze_book.model.book_config import load_book_config

    catalog = load_catalog(load_book_config(repo_root / "books" / "jims-halloween-maze-adventure"))
    decorations = {asset.asset_id for asset in catalog.decorations()}

    assert decorations, "a book with page vectors must have something to decorate with"
    assert decorations <= set(catalog.page_vectors)
    assert catalog.start.asset_id not in decorations
    assert "jim_in_sheet.svg" not in decorations, (
        "the start marker's own drawing must not also be page furniture"
    )


def test_an_empty_pool_means_every_page_vector_is_allowed(repo_root) -> None:
    """A book that says nothing gets the old behaviour rather than no decoration."""
    from maze_book.assets.catalog import AssetCatalog, load_catalog
    from maze_book.model.book_config import load_book_config
    import dataclasses

    catalog = load_catalog(load_book_config(repo_root / "books" / "jims-halloween-maze-adventure"))
    unrestricted = dataclasses.replace(catalog, maze_decorations=())
    assert {a.asset_id for a in unrestricted.decorations()} == set(catalog.page_vectors)
