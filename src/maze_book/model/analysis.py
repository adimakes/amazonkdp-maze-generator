"""The canonical maze analysis record (PRD 17.6).

``MazeAnalysis`` is the machine-readable answer key and the evidence that a maze
was accepted for the right reasons. ``exact`` is the honesty flag: when route
enumeration hit a cap it is ``False``, and a maze whose answer key is only
*probably* unique must never ship.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .geometry import Cell

SCHEMA_VERSION = "1.0"


@dataclass
class RouteSummary:
    route_index: int
    length: int
    candy_total: int
    cells: list[Cell] | None = None

    def to_json_obj(self, *, include_cells: bool) -> dict[str, Any]:
        obj: dict[str, Any] = {
            "routeIndex": self.route_index,
            "length": self.length,
            "candyTotal": self.candy_total,
        }
        if include_cells and self.cells is not None:
            obj["cells"] = [[r, c] for r, c in self.cells]
        return obj


@dataclass
class MazeAnalysis:
    maze_id: str
    maze_index: int
    exact: bool
    reachable: int
    reachable_fraction: float
    loop_count: int
    simple_path_count: int
    dead_end_cells: list[Cell]
    shortest_route_length: int
    best_route_length: int
    best_candy_total: int
    second_best_candy_total: int
    unique_highest_candy: bool
    best_route: list[Cell]
    tempting_route_count: int = 0
    route_summaries: list[RouteSummary] = field(default_factory=list)
    passed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    constraints: dict[str, bool] = field(default_factory=dict)

    #: Route cells are large (a 18x18 maze can have >100k routes); only the top
    #: routes carry their full cell list into the JSON.
    include_cells_for_top: int = 5

    def to_json_obj(self) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "mazeId": self.maze_id,
            "mazeIndex": self.maze_index,
            "exact": self.exact,
            "reachable": self.reachable,
            "reachableFraction": round(self.reachable_fraction, 6),
            "loopCount": self.loop_count,
            "simplePathCount": self.simple_path_count,
            "deadEndCells": [[r, c] for r, c in self.dead_end_cells],
            "shortestRouteLength": self.shortest_route_length,
            "bestRouteLength": self.best_route_length,
            "bestCandyTotal": self.best_candy_total,
            "secondBestCandyTotal": self.second_best_candy_total,
            "uniqueHighestCandy": self.unique_highest_candy,
            "bestRoute": [[r, c] for r, c in self.best_route],
            "temptingRouteCount": self.tempting_route_count,
            "routeSummaries": [
                summary.to_json_obj(include_cells=index < self.include_cells_for_top)
                for index, summary in enumerate(self.route_summaries)
            ],
            "validation": {
                "passed": self.passed,
                "errors": list(self.errors),
                "warnings": list(self.warnings),
                "constraints": dict(sorted(self.constraints.items())),
            },
        }

    @staticmethod
    def from_json_obj(obj: dict[str, Any]) -> "MazeAnalysis":
        validation = obj.get("validation", {})
        return MazeAnalysis(
            maze_id=obj["mazeId"],
            maze_index=obj["mazeIndex"],
            exact=obj["exact"],
            reachable=obj["reachable"],
            reachable_fraction=obj.get("reachableFraction", 1.0),
            loop_count=obj.get("loopCount", 0),
            simple_path_count=obj["simplePathCount"],
            dead_end_cells=[(r, c) for r, c in obj.get("deadEndCells", [])],
            shortest_route_length=obj["shortestRouteLength"],
            best_route_length=obj["bestRouteLength"],
            best_candy_total=obj["bestCandyTotal"],
            second_best_candy_total=obj["secondBestCandyTotal"],
            unique_highest_candy=obj["uniqueHighestCandy"],
            best_route=[(r, c) for r, c in obj["bestRoute"]],
            tempting_route_count=obj.get("temptingRouteCount", 0),
            route_summaries=[
                RouteSummary(
                    route_index=s["routeIndex"],
                    length=s["length"],
                    candy_total=s["candyTotal"],
                    cells=[(r, c) for r, c in s["cells"]] if "cells" in s else None,
                )
                for s in obj.get("routeSummaries", [])
            ],
            passed=validation.get("passed", False),
            errors=list(validation.get("errors", [])),
            warnings=list(validation.get("warnings", [])),
            constraints=dict(validation.get("constraints", {})),
        )
