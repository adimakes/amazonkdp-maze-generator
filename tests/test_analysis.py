"""Tests for ``maze_book.model.analysis`` -- the canonical maze analysis
record (PRD 17.6).

Covers ``MazeAnalysis.to_json_obj()`` schema validation, the
``to_json_obj``/``from_json_obj`` round trip, and the "only the top N routes
carry their full cell list" mechanism that keeps large mazes' analysis JSON
from ballooning.
"""

from __future__ import annotations

import jsonschema

from maze_book.model.analysis import MazeAnalysis, RouteSummary
from maze_book.model.json_io import read_json


def _route_summaries(n: int) -> list[RouteSummary]:
    return [
        RouteSummary(
            route_index=i,
            length=10 + i,
            candy_total=i,
            cells=[(0, 0), (0, 1), (i, i)],
        )
        for i in range(n)
    ]


def _full_analysis(*, n_routes: int = 8) -> MazeAnalysis:
    return MazeAnalysis(
        maze_id="tiny-child-book-maze-001",
        maze_index=1,
        exact=True,
        reachable=9,
        reachable_fraction=1.0,
        loop_count=2,
        simple_path_count=4,
        dead_end_cells=[(1, 2), (2, 0)],
        shortest_route_length=5,
        best_route_length=7,
        best_candy_total=3,
        second_best_candy_total=2,
        unique_highest_candy=True,
        best_route=[(0, 0), (0, 1), (1, 1), (2, 1), (2, 2)],
        tempting_route_count=2,
        route_summaries=_route_summaries(n_routes),
        passed=True,
        errors=[],
        warnings=["a minor warning"],
        constraints={"c8_candy_reachable": True, "c1_unique_winner": True},
    )


# --------------------------------------------------------------------------- #
# Schema validation
# --------------------------------------------------------------------------- #

def test_maze_analysis_to_json_obj_validates_against_schema(repo_root):
    analysis = _full_analysis()
    obj = analysis.to_json_obj()
    schema = read_json(repo_root / "schemas" / "maze-analysis.schema.json")
    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(obj))
    assert errors == [], [e.message for e in errors]


def test_maze_analysis_minimal_default_fields_still_validate(repo_root):
    # tempting_route_count/route_summaries/constraints all default; the
    # result must still be schema-valid (defaults are not just Python-side
    # conveniences, they must produce a shippable JSON shape).
    analysis = MazeAnalysis(
        maze_id="m", maze_index=1, exact=True, reachable=4, reachable_fraction=1.0,
        loop_count=0, simple_path_count=1, dead_end_cells=[],
        shortest_route_length=3, best_route_length=3, best_candy_total=0,
        second_best_candy_total=0, unique_highest_candy=True,
        best_route=[(0, 0), (0, 1)],
    )
    obj = analysis.to_json_obj()
    schema = read_json(repo_root / "schemas" / "maze-analysis.schema.json")
    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(obj))
    assert errors == [], [e.message for e in errors]


# --------------------------------------------------------------------------- #
# Round trip
# --------------------------------------------------------------------------- #

def test_maze_analysis_round_trip_preserves_every_field():
    analysis = _full_analysis()
    rebuilt = MazeAnalysis.from_json_obj(analysis.to_json_obj())

    assert rebuilt.maze_id == analysis.maze_id
    assert rebuilt.maze_index == analysis.maze_index
    assert rebuilt.exact == analysis.exact
    assert rebuilt.reachable == analysis.reachable
    assert rebuilt.reachable_fraction == analysis.reachable_fraction
    assert rebuilt.loop_count == analysis.loop_count
    assert rebuilt.simple_path_count == analysis.simple_path_count
    assert rebuilt.dead_end_cells == analysis.dead_end_cells
    assert rebuilt.shortest_route_length == analysis.shortest_route_length
    assert rebuilt.best_route_length == analysis.best_route_length
    assert rebuilt.best_candy_total == analysis.best_candy_total
    assert rebuilt.second_best_candy_total == analysis.second_best_candy_total
    assert rebuilt.unique_highest_candy == analysis.unique_highest_candy
    assert rebuilt.best_route == analysis.best_route
    assert rebuilt.tempting_route_count == analysis.tempting_route_count
    assert rebuilt.passed == analysis.passed
    assert rebuilt.errors == analysis.errors
    assert rebuilt.warnings == analysis.warnings
    assert rebuilt.constraints == analysis.constraints
    assert len(rebuilt.route_summaries) == len(analysis.route_summaries)
    for original, rebuilt_summary in zip(analysis.route_summaries, rebuilt.route_summaries):
        assert rebuilt_summary.route_index == original.route_index
        assert rebuilt_summary.length == original.length
        assert rebuilt_summary.candy_total == original.candy_total


def test_maze_analysis_from_json_obj_defaults_missing_optional_fields():
    minimal = {
        "mazeId": "m", "mazeIndex": 1, "exact": True, "reachable": 4,
        "simplePathCount": 1, "shortestRouteLength": 3, "bestRouteLength": 3,
        "bestCandyTotal": 0, "secondBestCandyTotal": 0, "uniqueHighestCandy": True,
        "bestRoute": [[0, 0], [0, 1]],
    }
    analysis = MazeAnalysis.from_json_obj(minimal)
    assert analysis.reachable_fraction == 1.0
    assert analysis.loop_count == 0
    assert analysis.dead_end_cells == []
    assert analysis.tempting_route_count == 0
    assert analysis.route_summaries == []
    assert analysis.passed is False
    assert analysis.errors == []
    assert analysis.warnings == []
    assert analysis.constraints == {}


# --------------------------------------------------------------------------- #
# "Only the top N routes carry cells" mechanism
# --------------------------------------------------------------------------- #

def test_only_first_five_route_summaries_carry_cells_by_default():
    analysis = _full_analysis(n_routes=8)
    assert analysis.include_cells_for_top == 5
    obj = analysis.to_json_obj()
    route_summaries = obj["routeSummaries"]
    assert len(route_summaries) == 8
    for index, summary in enumerate(route_summaries):
        if index < 5:
            assert "cells" in summary, f"route {index} should carry cells"
        else:
            assert "cells" not in summary, f"route {index} should NOT carry cells"


def test_include_cells_for_top_is_configurable():
    analysis = _full_analysis(n_routes=8)
    analysis.include_cells_for_top = 2
    obj = analysis.to_json_obj()
    for index, summary in enumerate(obj["routeSummaries"]):
        assert ("cells" in summary) == (index < 2)


def test_route_summary_to_json_obj_omits_cells_when_none_even_if_included():
    summary = RouteSummary(route_index=0, length=5, candy_total=1, cells=None)
    obj = summary.to_json_obj(include_cells=True)
    assert "cells" not in obj


def test_route_summary_to_json_obj_omits_cells_when_not_included():
    summary = RouteSummary(route_index=0, length=5, candy_total=1, cells=[(0, 0), (0, 1)])
    obj = summary.to_json_obj(include_cells=False)
    assert "cells" not in obj


def test_route_summary_to_json_obj_includes_cells_when_included_and_present():
    summary = RouteSummary(route_index=0, length=5, candy_total=1, cells=[(0, 0), (0, 1)])
    obj = summary.to_json_obj(include_cells=True)
    assert obj["cells"] == [[0, 0], [0, 1]]


# --------------------------------------------------------------------------- #
# validation.constraints ordering
# --------------------------------------------------------------------------- #

def test_constraints_are_sorted_by_key_in_to_json_obj():
    analysis = _full_analysis()
    obj = analysis.to_json_obj()
    keys = list(obj["validation"]["constraints"].keys())
    assert keys == sorted(keys)
