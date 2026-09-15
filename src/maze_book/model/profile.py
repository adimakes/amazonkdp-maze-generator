"""Difficulty profiles (PRD 17.4).

A profile is data: index bands mapped to grid size and the numeric ranges that
define "acceptable". Adding a difficulty level means adding a JSON file, never
editing source. ``defaults`` supplies every knob a band does not override, so a
band only states what makes it different.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ConfigError
from .json_io import read_json


@dataclass(frozen=True)
class IntRange:
    min: int
    max: int

    def contains(self, value: int) -> bool:
        return self.min <= value <= self.max

    def clamp(self, value: int) -> int:
        return max(self.min, min(self.max, value))

    def __iter__(self):
        return iter(range(self.min, self.max + 1))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.min}..{self.max}"


@dataclass(frozen=True)
class Band:
    """Fully resolved constraint set for one maze index."""

    maze_index_min: int
    maze_index_max: int
    act_name: str
    rows: int
    cols: int
    loops: IntRange
    dead_ends: IntRange
    candies: IntRange
    simple_routes: IntRange
    min_loop_length: int
    min_margin: int
    shortest_may_be_best: bool
    max_best_over_shortest: float
    min_tempting_routes: int
    tempting_fraction: float
    max_routes: int
    max_search_nodes: int
    time_budget_seconds: float
    candy_placement_trials: int
    frequency_band_min: float
    frequency_band_max: float
    exploration_share: float
    wall_width_pt: float
    solution_wall_width_pt: float
    solution_route_width_pt: float
    collectible_scale: float
    dead_end_scale: float
    start_scale: float
    finish_scale: float
    cell_clearance_fraction: float
    candy_spacing_policy: str
    max_border_hug_fraction: float
    max_unreachable_fraction: float
    dead_end_depth: IntRange | None = None


_REQUIRED = (
    "rows", "cols", "loops", "deadEnds", "candies", "simpleRoutes", "minLoopLength",
    "minMargin", "shortestMayBeBest", "maxBestOverShortest", "minTemptingRoutes",
    "temptingFraction", "maxRoutes", "maxSearchNodes", "timeBudgetSeconds",
    "candyPlacementTrials", "frequencyBand", "explorationShare", "wallWidthPt",
    "solutionWallWidthPt", "solutionRouteWidthPt", "collectibleScale", "deadEndScale",
    "startScale", "finishScale", "cellClearanceFraction", "candySpacingPolicy",
    "maxBorderHugFraction", "maxUnreachableFraction",
)


def _range(obj: dict[str, Any]) -> IntRange:
    rng = IntRange(int(obj["min"]), int(obj["max"]))
    if rng.min > rng.max:
        raise ConfigError(f"range min {rng.min} exceeds max {rng.max}")
    return rng


@dataclass
class Profile:
    profile_id: str
    description: str
    age_label: str
    raw: dict[str, Any]
    _bands: list[dict[str, Any]]

    @staticmethod
    def load(path: Path) -> "Profile":
        obj = read_json(path)
        defaults = obj.get("defaults", {})
        bands: list[dict[str, Any]] = []
        for band in obj["bands"]:
            merged = {**defaults, **band}
            missing = [key for key in _REQUIRED if key not in merged]
            if missing:
                raise ConfigError(
                    f"profile '{obj['profileId']}' band "
                    f"{band.get('mazeIndexMin')}-{band.get('mazeIndexMax')} "
                    f"is missing: {', '.join(missing)}"
                )
            bands.append(merged)
        bands.sort(key=lambda b: b["mazeIndexMin"])
        return Profile(
            profile_id=obj["profileId"],
            description=obj.get("description", ""),
            age_label=obj.get("ageLabel", ""),
            raw=obj,
            _bands=bands,
        )

    def band_for(self, maze_index: int) -> Band:
        """Resolve the band covering ``maze_index``.

        Bands must cover the index explicitly; falling back to "the last band"
        would silently mis-size a maze when a book grows past its profile.
        """
        for band in self._bands:
            if band["mazeIndexMin"] <= maze_index <= band["mazeIndexMax"]:
                return self._build(band)
        raise ConfigError(
            f"profile '{self.profile_id}' has no band covering maze index {maze_index}"
        )

    def covers(self, maze_count: int) -> None:
        """Raise unless every index in ``1..maze_count`` is covered exactly once."""
        seen: dict[int, int] = {}
        for band in self._bands:
            for index in range(band["mazeIndexMin"], band["mazeIndexMax"] + 1):
                seen[index] = seen.get(index, 0) + 1
        problems = []
        for index in range(1, maze_count + 1):
            count = seen.get(index, 0)
            if count == 0:
                problems.append(f"maze {index} is not covered by any band")
            elif count > 1:
                problems.append(f"maze {index} is covered by {count} bands")
        if problems:
            raise ConfigError(
                f"profile '{self.profile_id}' band coverage is invalid",
                details=problems[:10],
            )

    def _build(self, band: dict[str, Any]) -> Band:
        freq = band["frequencyBand"]
        depth = band.get("deadEndDepth")
        return Band(
            maze_index_min=int(band["mazeIndexMin"]),
            maze_index_max=int(band["mazeIndexMax"]),
            act_name=band.get("actName", ""),
            rows=int(band["rows"]),
            cols=int(band["cols"]),
            loops=_range(band["loops"]),
            dead_ends=_range(band["deadEnds"]),
            candies=_range(band["candies"]),
            simple_routes=_range(band["simpleRoutes"]),
            dead_end_depth=_range(depth) if depth else None,
            min_loop_length=int(band["minLoopLength"]),
            min_margin=int(band["minMargin"]),
            shortest_may_be_best=bool(band["shortestMayBeBest"]),
            max_best_over_shortest=float(band["maxBestOverShortest"]),
            min_tempting_routes=int(band["minTemptingRoutes"]),
            tempting_fraction=float(band["temptingFraction"]),
            max_routes=int(band["maxRoutes"]),
            max_search_nodes=int(band["maxSearchNodes"]),
            time_budget_seconds=float(band["timeBudgetSeconds"]),
            candy_placement_trials=int(band["candyPlacementTrials"]),
            frequency_band_min=float(freq["min"]),
            frequency_band_max=float(freq["max"]),
            exploration_share=float(band["explorationShare"]),
            wall_width_pt=float(band["wallWidthPt"]),
            solution_wall_width_pt=float(band["solutionWallWidthPt"]),
            solution_route_width_pt=float(band["solutionRouteWidthPt"]),
            collectible_scale=float(band["collectibleScale"]),
            dead_end_scale=float(band["deadEndScale"]),
            start_scale=float(band["startScale"]),
            finish_scale=float(band["finishScale"]),
            cell_clearance_fraction=float(band["cellClearanceFraction"]),
            candy_spacing_policy=str(band["candySpacingPolicy"]),
            max_border_hug_fraction=float(band["maxBorderHugFraction"]),
            max_unreachable_fraction=float(band["maxUnreachableFraction"]),
        )


def load_profile(profiles_dir: Path, profile_id: str) -> Profile:
    path = profiles_dir / f"{profile_id}.json"
    if not path.is_file():
        available = sorted(p.stem for p in profiles_dir.glob("*.json"))
        raise ConfigError(
            f"unknown profileId '{profile_id}'",
            details={"searched": str(path), "available": available},
        )
    return Profile.load(path)
