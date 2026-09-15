"""Tests for ``maze_book.model.maze_data`` -- the canonical open-edge maze
representation (PRD 17.5).

Covers graph access (``adjacency``, ``is_open``, ``traversable_cells``),
normalization (idempotency, role-ranked asset ordering), and the
``to_json_obj``/``from_json_obj`` round trip validated against
``schemas/maze-data.schema.json``.
"""

from __future__ import annotations

import jsonschema
import pytest

from maze_book.model.geometry import canonical_edge
from maze_book.model.json_io import read_json
from maze_book.model.maze_data import (
    ROLE_COLLECTIBLE,
    ROLE_DEAD_END,
    ROLE_FINISH,
    ROLE_START,
    MazeData,
    PlacedAsset,
    edges_from_adjacency,
    full_grid_edges,
)


# --------------------------------------------------------------------------- #
# full_grid_edges / adjacency
# --------------------------------------------------------------------------- #

def test_full_grid_edges_3x3_has_twelve_edges():
    edges = full_grid_edges(3, 3)
    # 3x3: 6 horizontal passages (3 rows x 2) + 6 vertical passages (3 cols x 2).
    assert len(edges) == 12
    assert len(set(edges)) == 12  # already deduplicated/canonical


def test_full_grid_edges_gives_interior_cell_degree_four_via_adjacency():
    maze = MazeData(
        maze_id="m", book_id="book", maze_index=1, seed=0,
        rows=3, cols=3, start=(0, 0), finish=(2, 2),
        open_edges=full_grid_edges(3, 3),
    )
    adj = maze.adjacency()
    # (1, 1) is the only fully-interior cell in a 3x3 grid.
    assert adj[(1, 1)] == {(0, 1), (1, 0), (1, 2), (2, 1)}
    # A corner has degree 2.
    assert adj[(0, 0)] == {(0, 1), (1, 0)}


def test_adjacency_excludes_blocked_cells_entirely():
    maze = MazeData(
        maze_id="m", book_id="book", maze_index=1, seed=0,
        rows=3, cols=3, start=(0, 0), finish=(2, 2),
        open_edges=full_grid_edges(3, 3),
        blocked_cells=[(1, 1)],
    )
    adj = maze.adjacency()
    assert (1, 1) not in adj
    # Neighbours of the blocked cell must not list it either.
    assert (1, 1) not in adj[(0, 1)]
    assert (1, 1) not in adj[(1, 0)]
    assert (1, 1) not in adj[(1, 2)]
    assert (1, 1) not in adj[(2, 1)]


def test_traversable_cells_excludes_blocked_cells():
    maze = MazeData(
        maze_id="m", book_id="book", maze_index=1, seed=0,
        rows=2, cols=2, start=(0, 0), finish=(1, 1),
        blocked_cells=[(0, 1)],
    )
    assert set(maze.traversable_cells()) == {(0, 0), (1, 0), (1, 1)}


def test_edges_from_adjacency_round_trips_full_grid_edges():
    rows, cols = 4, 3
    edges = full_grid_edges(rows, cols)
    maze = MazeData(
        maze_id="m", book_id="book", maze_index=1, seed=0,
        rows=rows, cols=cols, start=(0, 0), finish=(rows - 1, cols - 1),
        open_edges=edges,
    )
    reconstructed = edges_from_adjacency(maze.adjacency())
    assert reconstructed == edges


# --------------------------------------------------------------------------- #
# is_open / edge_set
# --------------------------------------------------------------------------- #

def test_is_open_agrees_with_edge_set_in_both_orders():
    maze = MazeData(
        maze_id="m", book_id="book", maze_index=1, seed=0,
        rows=2, cols=2, start=(0, 0), finish=(1, 1),
        open_edges=[canonical_edge((0, 0), (0, 1))],
    )
    assert maze.is_open((0, 0), (0, 1)) is True
    assert maze.is_open((0, 1), (0, 0)) is True
    assert maze.is_open((0, 0), (1, 0)) is False
    assert maze.is_open((1, 0), (0, 0)) is False
    assert canonical_edge((0, 0), (0, 1)) in maze.edge_set()


# --------------------------------------------------------------------------- #
# collectible_cells
# --------------------------------------------------------------------------- #

def test_collectible_cells_defaults_none_value_to_one():
    maze = MazeData(
        maze_id="m", book_id="book", maze_index=1, seed=0,
        rows=2, cols=2, start=(0, 0), finish=(1, 1),
        assets=[
            PlacedAsset(asset_id="candy.svg", role=ROLE_COLLECTIBLE, cell=(0, 1), value=None),
            PlacedAsset(asset_id="candy2.svg", role=ROLE_COLLECTIBLE, cell=(1, 0), value=5),
        ],
    )
    cells = maze.collectible_cells()
    assert cells == {(0, 1): 1, (1, 0): 5}


def test_collectible_cells_ignores_non_collectible_assets():
    maze = MazeData(
        maze_id="m", book_id="book", maze_index=1, seed=0,
        rows=2, cols=2, start=(0, 0), finish=(1, 1),
        assets=[
            PlacedAsset(asset_id="start.svg", role=ROLE_START, cell=(0, 0)),
            PlacedAsset(asset_id="finish.svg", role=ROLE_FINISH, cell=(1, 1)),
        ],
    )
    assert maze.collectible_cells() == {}


# --------------------------------------------------------------------------- #
# normalize -- idempotency and role-ranked ordering
# --------------------------------------------------------------------------- #

def _scrambled_maze() -> MazeData:
    return MazeData(
        maze_id="m", book_id="book", maze_index=1, seed=0,
        rows=3, cols=3, start=(0, 0), finish=(2, 2),
        open_edges=[
            canonical_edge((1, 1), (1, 2)),
            canonical_edge((0, 0), (0, 1)),
            canonical_edge((0, 0), (0, 1)),  # duplicate
            canonical_edge((0, 0), (1, 0)),
        ],
        blocked_cells=[(2, 0), (0, 2), (2, 0)],  # duplicate
        assets=[
            PlacedAsset(asset_id="dead_end.svg", role=ROLE_DEAD_END, cell=(1, 0)),
            PlacedAsset(asset_id="start.svg", role=ROLE_START, cell=(0, 0)),
            PlacedAsset(asset_id="candy_b.svg", role=ROLE_COLLECTIBLE, cell=(1, 1)),
            PlacedAsset(asset_id="finish.svg", role=ROLE_FINISH, cell=(2, 2)),
            PlacedAsset(asset_id="candy_a.svg", role=ROLE_COLLECTIBLE, cell=(0, 1)),
        ],
    )


def test_normalize_sorts_and_dedups_edges_and_blocked_cells():
    maze = _scrambled_maze()
    maze.normalize()
    assert maze.open_edges == sorted(set(maze.open_edges))
    assert len(maze.open_edges) == 3  # the duplicate was removed
    assert maze.blocked_cells == [(0, 2), (2, 0)]  # sorted, deduped


def test_normalize_orders_assets_by_role_rank_then_cell_then_id():
    maze = _scrambled_maze()
    maze.normalize()
    roles_in_order = [a.role for a in maze.assets]
    assert roles_in_order == [
        ROLE_START,
        ROLE_FINISH,
        ROLE_COLLECTIBLE,
        ROLE_COLLECTIBLE,
        ROLE_DEAD_END,
    ]
    # The two collectibles are tied on role rank, so they must be ordered by
    # cell: (0, 1) before (1, 1).
    collectibles = [a for a in maze.assets if a.role == ROLE_COLLECTIBLE]
    assert [a.cell for a in collectibles] == [(0, 1), (1, 1)]


def test_normalize_is_idempotent():
    maze = _scrambled_maze()
    maze.normalize()
    first_edges = list(maze.open_edges)
    first_blocked = list(maze.blocked_cells)
    first_asset_ids = [a.asset_id for a in maze.assets]

    maze.normalize()
    assert maze.open_edges == first_edges
    assert maze.blocked_cells == first_blocked
    assert [a.asset_id for a in maze.assets] == first_asset_ids


# --------------------------------------------------------------------------- #
# PlacedAsset.to_json_obj / from_json_obj
# --------------------------------------------------------------------------- #

def test_placed_asset_to_json_obj_omits_value_when_none():
    asset = PlacedAsset(asset_id="a.svg", role=ROLE_DEAD_END, cell=(1, 2), value=None)
    obj = asset.to_json_obj()
    assert "value" not in obj
    assert obj["cell"] == [1, 2]


def test_placed_asset_to_json_obj_includes_value_when_set():
    asset = PlacedAsset(asset_id="a.svg", role=ROLE_COLLECTIBLE, cell=(1, 2), value=3)
    obj = asset.to_json_obj()
    assert obj["value"] == 3


def test_placed_asset_to_json_obj_rounds_scale_and_rotation_to_six_dp():
    asset = PlacedAsset(
        asset_id="a.svg", role=ROLE_DEAD_END, cell=(0, 0),
        scale=1 / 3, rotation_deg=10 / 3,
    )
    obj = asset.to_json_obj()
    assert obj["scale"] == round(1 / 3, 6)
    assert obj["rotationDeg"] == round(10 / 3, 6)


def test_placed_asset_from_json_obj_round_trips():
    asset = PlacedAsset(asset_id="a.svg", role=ROLE_COLLECTIBLE, cell=(3, 4), value=7, scale=0.5, rotation_deg=90.0)
    rebuilt = PlacedAsset.from_json_obj(asset.to_json_obj())
    assert rebuilt == asset


# --------------------------------------------------------------------------- #
# to_json_obj / from_json_obj round trip + schema validation
# --------------------------------------------------------------------------- #

def _full_maze() -> MazeData:
    return MazeData(
        maze_id="tiny-child-book-maze-001",
        book_id="tiny-child-book",
        maze_index=1,
        seed=12345,
        rows=3,
        cols=3,
        start=(0, 0),
        finish=(2, 2),
        open_edges=full_grid_edges(3, 3),
        blocked_cells=[],
        assets=[
            PlacedAsset(asset_id="assets/beginning-vectors/start.svg", role=ROLE_START, cell=(0, 0), scale=0.6),
            PlacedAsset(asset_id="assets/ending-vectors/finish.svg", role=ROLE_FINISH, cell=(2, 2), scale=0.7),
            PlacedAsset(
                asset_id="assets/maze-vectors/collectibles/candy_01.svg",
                role=ROLE_COLLECTIBLE, cell=(0, 1), value=1, scale=0.55,
            ),
            PlacedAsset(
                asset_id="assets/maze-vectors/dead-end/dead_end_01.svg",
                role=ROLE_DEAD_END, cell=(1, 0), scale=0.5,
            ),
        ],
        profile_id="child_6_7",
        attempt=1,
        generator_id="mazelib_recursive_backtracker",
        generator_version="1.0.0",
    )


def test_maze_data_to_json_obj_validates_against_schema(repo_root):
    maze = _full_maze()
    obj = maze.to_json_obj()
    schema = read_json(repo_root / "schemas" / "maze-data.schema.json")
    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(obj))
    assert errors == [], [e.message for e in errors]


def test_maze_data_round_trip_preserves_normalized_form():
    maze = _full_maze()
    obj = maze.to_json_obj()
    rebuilt = MazeData.from_json_obj(obj)
    obj_again = rebuilt.to_json_obj()
    assert obj_again == obj


def test_maze_data_round_trip_preserves_every_field():
    maze = _full_maze()
    rebuilt = MazeData.from_json_obj(maze.to_json_obj())
    assert rebuilt.maze_id == maze.maze_id
    assert rebuilt.book_id == maze.book_id
    assert rebuilt.maze_index == maze.maze_index
    assert rebuilt.seed == maze.seed
    assert rebuilt.rows == maze.rows
    assert rebuilt.cols == maze.cols
    assert rebuilt.start == maze.start
    assert rebuilt.finish == maze.finish
    assert set(rebuilt.open_edges) == set(maze.open_edges)
    assert set(rebuilt.blocked_cells) == set(maze.blocked_cells)
    assert rebuilt.profile_id == maze.profile_id
    assert rebuilt.attempt == maze.attempt
    assert rebuilt.generator_id == maze.generator_id
    assert rebuilt.generator_version == maze.generator_version
    assert {a.asset_id for a in rebuilt.assets} == {a.asset_id for a in maze.assets}


def test_maze_data_from_json_obj_defaults_missing_optional_fields():
    # from_json_obj is explicitly defensive (.get() throughout) for fields
    # that are optional in the writer's output, unlike book_config's loader.
    minimal = {
        "mazeId": "m", "bookId": "book", "mazeIndex": 1, "seed": 0,
        "grid": {"rows": 2, "cols": 2}, "start": [0, 0], "finish": [1, 1],
        "openEdges": [],
    }
    maze = MazeData.from_json_obj(minimal)
    assert maze.blocked_cells == []
    assert maze.assets == []
    assert maze.profile_id == ""
    assert maze.attempt == 1
    assert maze.generator_id == ""
    assert maze.generator_version == ""
