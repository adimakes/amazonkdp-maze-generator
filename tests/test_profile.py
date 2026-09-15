"""Tests for ``maze_book.model.profile`` (PRD 17.4).

Covers: schema validation of every shipped profile file, default/override
merge correctness in ``Profile.band_for``, the "no silent fallback" contract
for out-of-range maze indexes, ``Profile.covers`` gap/overlap detection, a
handful of cross-profile invariants, and a literal regression check of the
``halloween_6_10`` profile against the normative six-act table in PRD 17.4.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from maze_book.errors import ConfigError
from maze_book.model.profile import IntRange, Profile, load_profile


def _profile_paths(repo_root: Path) -> list[Path]:
    paths = sorted((repo_root / "profiles").glob("*.json"))
    assert paths, "expected at least one profile JSON file under profiles/"
    return paths


def _difficulty_schema(repo_root: Path) -> dict:
    schema_path = repo_root / "schemas" / "difficulty-profile.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_every_profile_file_validates_against_schema(repo_root: Path) -> None:
    schema = _difficulty_schema(repo_root)
    validator = jsonschema.Draft202012Validator(schema)
    for path in _profile_paths(repo_root):
        obj = json.loads(path.read_text(encoding="utf-8"))
        errors = sorted(validator.iter_errors(obj), key=lambda e: list(e.path))
        assert errors == [], f"{path.name}: {[e.message for e in errors]}"


# ---------------------------------------------------------------------------
# Loading and coverage of every shipped profile
# ---------------------------------------------------------------------------


def test_every_profile_loads_and_covers_its_own_range(repo_root: Path) -> None:
    for path in _profile_paths(repo_root):
        profile = Profile.load(path)
        maze_count = max(band["mazeIndexMax"] for band in profile.raw["bands"])
        profile.covers(maze_count)  # must not raise


def test_load_profile_resolves_by_id_under_profiles_dir(repo_root: Path) -> None:
    profile = load_profile(repo_root / "profiles", "child_6_7")
    assert profile.profile_id == "child_6_7"
    assert profile.age_label == "6-7"
    assert profile.description  # non-empty in the shipped file
    profile.covers(30)


def test_load_profile_unknown_id_raises_configerror_with_available_list(repo_root: Path) -> None:
    with pytest.raises(ConfigError, match=r"unknown profileId 'does-not-exist'") as excinfo:
        load_profile(repo_root / "profiles", "does-not-exist")
    details = excinfo.value.details
    assert isinstance(details, dict)
    assert "available" in details
    assert "child_6_7" in details["available"]
    assert "halloween_6_10" in details["available"]
    assert "searched" in details


# ---------------------------------------------------------------------------
# band_for: defaults/override merge correctness
# ---------------------------------------------------------------------------


def test_band_for_child_6_7_merges_defaults_and_overrides(repo_root: Path) -> None:
    profile = load_profile(repo_root / "profiles", "child_6_7")

    band1 = profile.band_for(5)  # "First Steps" (1-10): no wallWidthPt override
    assert band1.rows == 6
    assert band1.cols == 6
    assert band1.wall_width_pt == 3.5  # defaulted through from `defaults`
    assert band1.shortest_may_be_best is True  # defaulted through
    assert band1.loops == IntRange(2, 3)

    band2 = profile.band_for(15)  # "Getting Braver" (11-20): overrides wallWidthPt
    assert band2.rows == 8
    assert band2.wall_width_pt == 3.25  # explicit band override
    assert band2.shortest_may_be_best is True  # still defaulted through

    band3 = profile.band_for(25)  # "All The Way" (21-30): overrides shortestMayBeBest + wallWidthPt
    assert band3.rows == 10
    assert band3.shortest_may_be_best is False  # explicit band override
    assert band3.wall_width_pt == 3.0  # explicit band override


def test_band_for_halloween_6_10_merges_defaults_and_overrides(repo_root: Path) -> None:
    profile = load_profile(repo_root / "profiles", "halloween_6_10")

    act1 = profile.band_for(5)  # "Getting Ready" (1-10)
    assert act1.min_margin == 2  # explicit override (defaults.minMargin is 1)
    assert act1.max_best_over_shortest == 1.6  # defaulted through (no band overrides it)
    assert act1.time_budget_seconds == 20.0  # defaulted through
    assert act1.candy_placement_trials == 400  # defaulted through
    assert act1.dead_end_depth == IntRange(1, 3)

    act5 = profile.band_for(45)  # "The Long Way Home" (41-48)
    assert act5.time_budget_seconds == 45.0  # explicit override
    assert act5.candy_placement_trials == 600  # explicit override
    assert act5.max_best_over_shortest == 1.6  # still defaulted through

    finale = profile.band_for(50)  # "Finale" (49-50)
    assert finale.solution_wall_width_pt == 1.2  # explicit override, only band that sets it
    assert act1.solution_wall_width_pt == 1.4  # defaulted through (defaults value)


# ---------------------------------------------------------------------------
# band_for: no silent fallback for an out-of-range index
# ---------------------------------------------------------------------------


def test_band_for_raises_configerror_above_covered_range(repo_root: Path) -> None:
    profile = load_profile(repo_root / "profiles", "child_6_7")  # covers 1..30
    with pytest.raises(
        ConfigError,
        match=r"profile 'child_6_7' has no band covering maze index 31",
    ):
        profile.band_for(31)


def test_band_for_raises_configerror_below_covered_range(repo_root: Path) -> None:
    profile = load_profile(repo_root / "profiles", "halloween_6_10")  # covers 1..50
    with pytest.raises(ConfigError, match=r"has no band covering maze index 0"):
        profile.band_for(0)


# ---------------------------------------------------------------------------
# covers(): gap and overlap detection on hand-built profiles
# ---------------------------------------------------------------------------


def _bare_profile(bands: list[dict]) -> Profile:
    """A minimal in-memory Profile for exercising covers() in isolation.

    covers() only reads mazeIndexMin/mazeIndexMax off each band dict, so the
    bands need no other keys and band_for()/_build() are never invoked.
    """
    return Profile(profile_id="synthetic", description="", age_label="", raw={}, _bands=bands)


def test_covers_raises_for_a_gap() -> None:
    profile = _bare_profile(
        [
            {"mazeIndexMin": 1, "mazeIndexMax": 5},
            {"mazeIndexMin": 7, "mazeIndexMax": 10},  # gap at maze 6
        ]
    )
    with pytest.raises(ConfigError, match="band coverage is invalid") as excinfo:
        profile.covers(10)
    assert "maze 6 is not covered by any band" in excinfo.value.details


def test_covers_raises_for_an_overlap() -> None:
    profile = _bare_profile(
        [
            {"mazeIndexMin": 1, "mazeIndexMax": 5},
            {"mazeIndexMin": 3, "mazeIndexMax": 10},  # overlap at mazes 3-5
        ]
    )
    with pytest.raises(ConfigError, match="band coverage is invalid") as excinfo:
        profile.covers(10)
    problems = excinfo.value.details
    assert "maze 3 is covered by 2 bands" in problems
    assert "maze 4 is covered by 2 bands" in problems
    assert "maze 5 is covered by 2 bands" in problems


def test_covers_passes_for_exact_contiguous_coverage() -> None:
    profile = _bare_profile(
        [
            {"mazeIndexMin": 1, "mazeIndexMax": 5},
            {"mazeIndexMin": 6, "mazeIndexMax": 10},
        ]
    )
    profile.covers(10)  # must not raise


# ---------------------------------------------------------------------------
# Cross-profile invariants
# ---------------------------------------------------------------------------


def test_every_band_in_every_profile_respects_the_grid_ceiling(repo_root: Path) -> None:
    """PRD 17.4: grids are capped at 18x18 (0.375in is the practical floor for
    a child drawing with a pencil at the book's 8.5x11 trim)."""
    for path in _profile_paths(repo_root):
        profile = Profile.load(path)
        for band_raw in profile.raw["bands"]:
            band = profile.band_for(band_raw["mazeIndexMin"])
            assert 2 <= band.rows <= 18, f"{profile.profile_id}/{band.act_name}: rows={band.rows}"
            assert 2 <= band.cols <= 18, f"{profile.profile_id}/{band.act_name}: cols={band.cols}"


def test_every_band_loops_min_is_at_least_3_except_child_6_7_first_steps(repo_root: Path) -> None:
    """PRD 17.4 prose: loop budget k 'MUST stay within 3-12 ... below 3 there
    are too few genuinely different routes for the puzzle to exist'. The one
    documented exception is child_6_7's very first band, deliberately eased
    for the youngest readers (loops 2-3)."""
    for path in _profile_paths(repo_root):
        profile = Profile.load(path)
        for band_raw in profile.raw["bands"]:
            band = profile.band_for(band_raw["mazeIndexMin"])
            if profile.profile_id == "child_6_7" and band.act_name == "First Steps":
                assert band.loops == IntRange(2, 3)
                continue
            assert band.loops.min >= 3, f"{profile.profile_id}/{band.act_name}: loops.min={band.loops.min}"


# ---------------------------------------------------------------------------
# halloween_6_10 vs the normative PRD 17.4 act table
# ---------------------------------------------------------------------------

# (rows, cols, candies, minMargin, wallWidthPt) -- verified to match exactly.
_HALLOWEEN_PRD_GRID_CANDY_MARGIN_WALL: dict[str, tuple[int, int, IntRange, int, float]] = {
    "Getting Ready": (8, 8, IntRange(5, 7), 2, 3.00),
    "The Neighborhood": (10, 10, IntRange(7, 9), 2, 3.00),
    "The Dark End of the Street": (12, 12, IntRange(9, 11), 2, 2.75),
    "The Haunted Half Hour": (14, 14, IntRange(11, 13), 1, 2.50),
    "The Long Way Home": (16, 16, IntRange(13, 15), 1, 2.50),
    "Finale": (18, 18, IntRange(16, 18), 1, 2.25),
}

# (loops range, deadEnds range) as printed in PRD 17.4. The shipped profile drifted
# from these two columns; a 50-maze run against a probe profile carrying the PRD's
# numbers verbatim accepted every maze (worst case 14 attempts, slowest 1.5 s), so
# the profile was corrected to the PRD rather than the reverse.
_HALLOWEEN_PRD_LOOPS_DEADENDS: dict[str, tuple[IntRange, IntRange]] = {
    "Getting Ready": (IntRange(3, 4), IntRange(4, 5)),
    "The Neighborhood": (IntRange(4, 5), IntRange(5, 6)),
    "The Dark End of the Street": (IntRange(5, 6), IntRange(6, 7)),
    "The Haunted Half Hour": (IntRange(6, 8), IntRange(7, 8)),
    "The Long Way Home": (IntRange(8, 10), IntRange(8, 9)),
    "Finale": (IntRange(10, 12), IntRange(9, 10)),
}


def _band_by_act_name(profile: Profile, act_name: str):
    band_raw = next(b for b in profile.raw["bands"] if b.get("actName") == act_name)
    return profile.band_for(band_raw["mazeIndexMin"])


def test_halloween_6_10_grid_candy_margin_wall_match_prd_17_4_table(repo_root: Path) -> None:
    profile = load_profile(repo_root / "profiles", "halloween_6_10")
    for act_name, (rows, cols, candies, margin, wall) in _HALLOWEEN_PRD_GRID_CANDY_MARGIN_WALL.items():
        band = _band_by_act_name(profile, act_name)
        assert band.rows == rows, act_name
        assert band.cols == cols, act_name
        assert band.candies == candies, act_name
        assert band.min_margin == margin, act_name
        assert band.wall_width_pt == wall, act_name


def test_halloween_6_10_loops_and_deadends_match_prd_17_4_table(repo_root: Path) -> None:
    profile = load_profile(repo_root / "profiles", "halloween_6_10")
    mismatches = []
    for act_name, (loop_range, dead_end_range) in _HALLOWEEN_PRD_LOOPS_DEADENDS.items():
        band = _band_by_act_name(profile, act_name)
        if band.loops != loop_range:
            mismatches.append(f"{act_name}: loops {band.loops} != PRD {loop_range}")
        if band.dead_ends != dead_end_range:
            mismatches.append(f"{act_name}: deadEnds {band.dead_ends} != PRD {dead_end_range}")
    assert mismatches == []


def test_halloween_6_10_loop_budget_stays_within_3_to_12(repo_root: Path) -> None:
    profile = load_profile(repo_root / "profiles", "halloween_6_10")
    for band_raw in profile.raw["bands"]:
        band = profile.band_for(band_raw["mazeIndexMin"])
        assert 3 <= band.loops.min, band.act_name
        assert band.loops.max <= 12, band.act_name


# ---------------------------------------------------------------------------
# Satisfiability: a profile is a promise the generator has to be able to keep
# ---------------------------------------------------------------------------


def _stub_catalog():
    """A catalog of fictional assets: satisfiability is a property of the
    numbers, and loading real SVGs would only slow it down."""
    from pathlib import Path

    from maze_book.assets.catalog import AssetCatalog, AssetFile

    def asset(name: str) -> AssetFile:
        return AssetFile(asset_id=name, path=Path("/stub") / name)

    return AssetCatalog(
        start=asset("start.svg"),
        finish=asset("finish.svg"),
        dead_ends=[asset(f"dead_{i}.svg") for i in range(4)],
        collectibles=[asset(f"candy_{i}.svg") for i in range(6)],
        page_vectors={},
        dead_end_policy="random-from-folder",
        collectible_policy="random-from-folder",
    )


def _generate_one(repo_root: Path, profile: Profile, maze_index: int):
    import dataclasses

    from maze_book.generation import pipeline
    from maze_book.model.book_config import load_book_config

    config = load_book_config(
        repo_root / "tests" / "fixtures" / "books" / "tiny-child-book", require_assets=False
    )
    band = profile.band_for(maze_index)
    config = dataclasses.replace(
        config,
        book=dataclasses.replace(
            config.book,
            maze_count=max(b["mazeIndexMax"] for b in profile.raw["bands"]),
            profile_id=profile.profile_id,
        ),
    )
    return pipeline.generate_maze(
        config=config, profile=profile, catalog=_stub_catalog(), maze_index=maze_index
    ), band


@pytest.mark.slow
def test_every_band_of_every_shipped_profile_can_actually_produce_a_maze(
    repo_root: Path,
) -> None:
    """A profile is a set of promises the generator has to be able to keep.

    This is the check that was missing when ``child_6_7`` shipped with
    ``deadEndDepth.min = 2`` (braiding leaves depth-1 stubs, so ~85% of shapes
    were rejected for a property the generator cannot produce on request) and
    ``temptingFraction = 0.9`` (with best totals under 10 candies, "tempting"
    rounds up to the best score itself, which C1's uniqueness forbids). Every
    band's numbers validated against the schema and every unit test passed; the
    profile still could not make a single maze.
    """
    failures: list[str] = []
    for path in _profile_paths(repo_root):
        profile = Profile.load(path)
        for band_raw in profile.raw["bands"]:
            index = band_raw["mazeIndexMin"]
            try:
                result, band = _generate_one(repo_root, profile, index)
            except Exception as exc:  # noqa: BLE001 - the message is the report
                failures.append(
                    f"{profile.profile_id}/{band_raw['actName']} (maze {index}): "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            if not (band.loops.min <= result.analysis.loop_count <= band.loops.max):
                failures.append(
                    f"{profile.profile_id}/{band.act_name}: accepted k="
                    f"{result.analysis.loop_count} outside {band.loops}"
                )
    assert failures == []


@pytest.mark.slow
def test_child_6_7_produces_its_whole_book(repo_root: Path) -> None:
    """The second shipped profile, end to end. Books are the unit that matters:
    a profile that satisfies band 1 and stalls at band 3 ships nothing."""
    profile = load_profile(repo_root / "profiles", "child_6_7")
    failures = []
    for index in range(1, 31):
        try:
            _generate_one(repo_root, profile, index)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"maze {index}: {exc}")
    assert failures == []


def test_no_band_sets_a_tempting_fraction_its_candy_count_cannot_reach(
    repo_root: Path,
) -> None:
    """C5 asks for a route scoring at least ``temptingFraction * best``, and C1
    requires the best to be unique. So a tempting route needs a score in
    ``[ceil(fraction * best), best)`` -- and when the fraction is high and the
    candy count low, that interval is empty and no placement can ever satisfy
    both. This is arithmetic, so it is checked without generating anything."""
    import math

    problems = []
    for path in _profile_paths(repo_root):
        profile = Profile.load(path)
        for band_raw in profile.raw["bands"]:
            band = profile.band_for(band_raw["mazeIndexMin"])
            if band.min_tempting_routes < 1:
                continue
            best = band.candies.max  # the most generous case for the profile
            lowest_tempting = math.ceil(band.tempting_fraction * best)
            if lowest_tempting >= best:
                problems.append(
                    f"{profile.profile_id}/{band.act_name}: temptingFraction "
                    f"{band.tempting_fraction:g} x best {best} needs a decoy scoring "
                    f">= {lowest_tempting}, but the best is {best} and must be unique"
                )
    assert problems == []


def test_no_band_demands_a_dead_end_depth_the_generator_cannot_create(
    repo_root: Path,
) -> None:
    """Braiding creates a dead end by opening an edge at a degree-1 cell, which
    leaves a depth-1 stub. A band whose minimum depth exceeds 1 is asking the
    rebalancer for something it has no move to produce."""
    problems = []
    for path in _profile_paths(repo_root):
        profile = Profile.load(path)
        for band_raw in profile.raw["bands"]:
            band = profile.band_for(band_raw["mazeIndexMin"])
            if band.dead_end_depth is not None and band.dead_end_depth.min > 1:
                problems.append(
                    f"{profile.profile_id}/{band.act_name}: deadEndDepth.min is "
                    f"{band.dead_end_depth.min}"
                )
    assert problems == []
