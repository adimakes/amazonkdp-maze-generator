"""Tests for the mazelib adapter and the generation pipeline (PRD 17.7, 17.12, 17.16).

Two contracts are under test.

The **library boundary**: only ``generation/adapter_mazelib.py`` may import
mazelib, and it converts the library's grid into the canonical open-edge graph.
Swapping the dependency must touch exactly one file, so the conversion is tested
as a conversion -- spanning tree in, spanning tree out, symmetric and canonical.

**Determinism**: every stochastic step draws from a stream derived from
``(book.seed, book.id, mazeIndex, attempt, purpose)``. The property that matters
is not merely "same input, same output" but *independence*: regenerating maze 7
must not disturb maze 8, or a one-maze fix silently reprints the book.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from maze_book.assets.catalog import AssetCatalog, AssetFile
from maze_book.errors import GenerationError
from maze_book.generation import pipeline
from maze_book.generation.adapter_mazelib import (
    GENERATOR_ID,
    BASE_GENERATORS,
    base_generator,
)
from maze_book.generation.base import validate_spanning_tree
from maze_book.model.book_config import load_book_config
from maze_book.model.geometry import canonical_edge
from maze_book.model.profile import load_profile
from maze_book.model.seeds import derive_seed
from maze_book.simulation import graph as G


@pytest.fixture(scope="module")
def stub_catalog() -> AssetCatalog:
    def asset(name: str) -> AssetFile:
        return AssetFile(asset_id=name, path=Path("/stub") / name)

    return AssetCatalog(
        start=asset("start.svg"), finish=asset("finish.svg"),
        dead_ends=[asset(f"dead_{i}.svg") for i in range(4)],
        collectibles=[asset(f"candy_{i}.svg") for i in range(6)],
        page_vectors={},
        dead_end_policy="random-from-folder", collectible_policy="random-from-folder",
    )


@pytest.fixture(scope="module")
def halloween(repo_root: Path):
    config = load_book_config(repo_root / "books" / "jims-halloween-maze-adventure")
    profile = load_profile(repo_root / "profiles", config.book.profile_id)
    return config, profile


def adjacency_of(rows: int, cols: int, edges) -> dict:
    adj: dict = {(r, c): set() for r in range(rows) for c in range(cols)}
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    return adj


# ---------------------------------------------------------------------------
# The library boundary
# ---------------------------------------------------------------------------


def test_only_the_adapter_imports_mazelib(repo_root: Path) -> None:
    """17.7: swapping or removing the dependency must touch exactly one file.

    Checked by parsing import statements rather than grepping for the string:
    every other module names the *adapter* (``from ..generation.adapter_mazelib
    import base_generator``), which contains "mazelib" as a substring and is
    exactly the indirection this rule exists to enforce.
    """
    import ast

    offenders = []
    for path in sorted((repo_root / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == "mazelib" or name.startswith("mazelib.") for name in names):
                if path.name != "adapter_mazelib.py":
                    offenders.append(str(path.relative_to(repo_root)))
    assert offenders == []


def test_the_adapter_really_does_import_mazelib(repo_root: Path) -> None:
    """The companion assertion: a boundary test that would also pass if the
    dependency had been removed entirely is not testing the boundary."""
    source = (
        repo_root / "src" / "maze_book" / "generation" / "adapter_mazelib.py"
    ).read_text(encoding="utf-8")
    assert "from mazelib import" in source


def test_the_registry_maps_the_configured_id_to_a_generator() -> None:
    assert GENERATOR_ID in BASE_GENERATORS
    generator = base_generator(GENERATOR_ID)
    assert generator.generator_id == GENERATOR_ID
    assert generator.generator_version.startswith("mazelib-")


def test_an_unknown_generator_id_lists_what_is_available() -> None:
    with pytest.raises(GenerationError, match=r"unknown baseGenerator") as excinfo:
        base_generator("recursive-backtracker")
    assert GENERATOR_ID in excinfo.value.details["available"]


@pytest.mark.parametrize("size", [(4, 4), (6, 8), (10, 10)])
def test_carving_produces_a_spanning_tree_of_the_whole_grid(size) -> None:
    rows, cols = size
    edges = base_generator(GENERATOR_ID).carve(rows, cols, seed=11)
    adj = adjacency_of(rows, cols, edges)
    assert len(edges) == rows * cols - 1
    assert G.cyclomatic_number(adj) == 0
    assert len(G.reachable_from(adj, (0, 0))) == rows * cols


def test_carved_edges_are_canonical_and_sorted() -> None:
    """Canonical form is what makes MazeData JSON byte-comparable."""
    edges = base_generator(GENERATOR_ID).carve(6, 6, seed=3)
    assert edges == sorted(edges)
    for a, b in edges:
        assert canonical_edge(a, b) == (a, b)


def test_carving_is_deterministic_for_a_seed() -> None:
    first = base_generator(GENERATOR_ID).carve(8, 8, seed=42)
    second = base_generator(GENERATOR_ID).carve(8, 8, seed=42)
    assert first == second


def test_different_seeds_carve_different_trees() -> None:
    assert base_generator(GENERATOR_ID).carve(8, 8, 1) != base_generator(
        GENERATOR_ID
    ).carve(8, 8, 2)


def test_a_grid_smaller_than_two_by_two_is_rejected() -> None:
    with pytest.raises(GenerationError, match=r"at least 2x2"):
        base_generator(GENERATOR_ID).carve(1, 5, seed=1)


def test_validate_spanning_tree_accepts_a_tree_and_rejects_a_cycle() -> None:
    edges = base_generator(GENERATOR_ID).carve(5, 5, seed=1)
    validate_spanning_tree(5, 5, edges)  # must not raise

    with_loop = list(edges)
    for candidate in (
        canonical_edge((0, 0), (0, 1)), canonical_edge((0, 0), (1, 0)),
        canonical_edge((1, 1), (1, 2)), canonical_edge((2, 2), (2, 3)),
    ):
        if candidate not in with_loop:
            with_loop.append(candidate)
            break
    with pytest.raises(GenerationError):
        validate_spanning_tree(5, 5, sorted(with_loop))


# ---------------------------------------------------------------------------
# Derived seeds
# ---------------------------------------------------------------------------


def test_seed_streams_separate_by_every_component() -> None:
    base = dict(book_seed=42001, book_id="b", maze_index=1, attempt=1, purpose="topology")
    reference = derive_seed(**base)
    for field, value in (
        ("book_seed", 42002), ("book_id", "other"), ("maze_index", 2),
        ("attempt", 2), ("purpose", "candy"),
    ):
        assert derive_seed(**{**base, field: value}) != reference


def test_derived_seeds_are_stable_across_processes() -> None:
    """blake2b, never hash(): Python's hash is salted per process, so a book
    seeded with it would be unreproducible between runs."""
    assert derive_seed(42001, "jims-halloween-maze-adventure", 7, 1, "topology") == (
        derive_seed(42001, "jims-halloween-maze-adventure", 7, 1, "topology")
    )


# ---------------------------------------------------------------------------
# generate_maze
# ---------------------------------------------------------------------------


def test_an_accepted_maze_carries_its_evidence(halloween, stub_catalog) -> None:
    config, profile = halloween
    result = pipeline.generate_maze(
        config=config, profile=profile, catalog=stub_catalog, maze_index=1
    )
    assert result.analysis.passed is True
    assert result.analysis.exact is True
    assert result.attempts >= 1
    assert result.maze.maze_index == 1
    assert result.maze.generator_id == GENERATOR_ID
    assert "maze  1" in result.summary()


def test_an_accepted_maze_matches_its_band(halloween, stub_catalog) -> None:
    config, profile = halloween
    for index in (1, 11, 21):
        result = pipeline.generate_maze(
            config=config, profile=profile, catalog=stub_catalog, maze_index=index
        )
        band = profile.band_for(index)
        assert (result.maze.rows, result.maze.cols) == (band.rows, band.cols)
        assert band.loops.min <= result.analysis.loop_count <= band.loops.max
        assert band.candies.min <= len(result.maze.collectible_cells()) <= band.candies.max


def test_every_dead_end_gets_a_marker_and_no_candy(halloween, stub_catalog) -> None:
    """8.3: place a dead-end vector in each accepted dead-end cell, and do not
    place collectibles in dead-end cells."""
    config, profile = halloween
    result = pipeline.generate_maze(
        config=config, profile=profile, catalog=stub_catalog, maze_index=11
    )
    maze = result.maze
    marked = {a.cell for a in maze.assets if a.role == "dead-end"}
    candies = set(maze.collectible_cells())
    assert marked == set(result.analysis.dead_end_cells)
    assert marked & candies == set()
    assert maze.start not in candies and maze.finish not in candies


def test_the_endpoints_get_exactly_one_asset_each(halloween, stub_catalog) -> None:
    config, profile = halloween
    maze = pipeline.generate_maze(
        config=config, profile=profile, catalog=stub_catalog, maze_index=1
    ).maze
    starts = [a for a in maze.assets if a.role == "start"]
    finishes = [a for a in maze.assets if a.role == "finish"]
    assert len(starts) == len(finishes) == 1
    assert starts[0].cell == maze.start and finishes[0].cell == maze.finish


def test_generation_is_reproducible(halloween, stub_catalog) -> None:
    config, profile = halloween
    first = pipeline.generate_maze(
        config=config, profile=profile, catalog=stub_catalog, maze_index=5
    )
    second = pipeline.generate_maze(
        config=config, profile=profile, catalog=stub_catalog, maze_index=5
    )
    assert first.maze.to_json_obj() == second.maze.to_json_obj()
    assert first.analysis.to_json_obj() == second.analysis.to_json_obj()


def test_regenerating_one_maze_leaves_the_others_untouched(
    halloween, stub_catalog
) -> None:
    """17.12: the point of deriving a stream per maze index. If one maze's
    generation consumed a shared RNG, fixing maze 7 would silently reprint the
    rest of the book."""
    config, profile = halloween
    baseline = {
        index: pipeline.generate_maze(
            config=config, profile=profile, catalog=stub_catalog, maze_index=index
        ).maze.to_json_obj()
        for index in (6, 7, 8)
    }

    pipeline.generate_maze(
        config=config, profile=profile, catalog=stub_catalog, maze_index=7
    )
    pipeline.generate_maze(
        config=config, profile=profile, catalog=stub_catalog, maze_index=7
    )

    for index in (6, 8):
        again = pipeline.generate_maze(
            config=config, profile=profile, catalog=stub_catalog, maze_index=index
        ).maze.to_json_obj()
        assert again == baseline[index], index


def test_a_different_book_seed_changes_the_maze(halloween, stub_catalog) -> None:
    config, profile = halloween
    reseeded = dataclasses.replace(
        config, book=dataclasses.replace(config.book, seed=config.book.seed + 1)
    )
    first = pipeline.generate_maze(
        config=config, profile=profile, catalog=stub_catalog, maze_index=3
    ).maze
    second = pipeline.generate_maze(
        config=reseeded, profile=profile, catalog=stub_catalog, maze_index=3
    ).maze
    assert first.open_edges != second.open_edges


def test_an_out_of_range_index_is_rejected(halloween, stub_catalog) -> None:
    config, profile = halloween
    with pytest.raises(Exception):
        pipeline.generate_maze(
            config=config, profile=profile, catalog=stub_catalog, maze_index=999
        )


def test_rejections_are_reported_by_stage(halloween, stub_catalog) -> None:
    """A generation failure must report the per-constraint histogram, never just
    "it didn't work"."""
    config, profile = halloween
    stages: list[str] = []
    pipeline.generate_maze(
        config=config, profile=profile, catalog=stub_catalog, maze_index=45,
        on_rejection=lambda record: stages.append(record.stage),
    )
    assert all(
        stage in {"shape", "route-enumeration", "candy-placement", "analysis"}
        for stage in stages
    )


def test_an_unsatisfiable_band_fails_with_the_histogram(halloween, stub_catalog) -> None:
    config, profile = halloween
    impossible = dataclasses.replace(
        config,
        generation=dataclasses.replace(config.generation, max_attempts_per_maze=3),
    )
    import json
    import tempfile

    raw = json.loads(json.dumps(profile.raw))
    raw["bands"][0]["loops"] = {"min": 400, "max": 500}
    path = Path(tempfile.mkdtemp()) / "broken.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    from maze_book.model.profile import Profile

    with pytest.raises(GenerationError, match=r"failed all 3 attempts") as excinfo:
        pipeline.generate_maze(
            config=impossible, profile=Profile.load(path),
            catalog=stub_catalog, maze_index=1,
        )
    assert "stageCounts" in excinfo.value.details


# ---------------------------------------------------------------------------
# generate_book_mazes
# ---------------------------------------------------------------------------


def test_the_book_generator_yields_in_index_order(halloween, stub_catalog) -> None:
    config, profile = halloween
    produced = [
        result.maze.maze_index
        for result in pipeline.generate_book_mazes(
            config=config, profile=profile, catalog=stub_catalog, indices=[3, 1, 2]
        )
    ]
    assert produced == [3, 1, 2]


def test_the_book_generator_streams_rather_than_batching(halloween, stub_catalog) -> None:
    """A 50-maze run that fails at maze 48 still leaves 47 usable artifacts."""
    config, profile = halloween
    stream = pipeline.generate_book_mazes(
        config=config, profile=profile, catalog=stub_catalog, indices=[1, 2]
    )
    first = next(stream)
    assert first.maze.maze_index == 1
