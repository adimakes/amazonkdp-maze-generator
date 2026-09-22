"""Tests for ``maze_book.book.artifacts`` (PRD 17.12, 17.14).

17.12 lists five things that must be unchanged for a cached maze to be reusable.
Each gets a test that changes exactly that one thing and asserts the cache
notices -- because a cache that serves an artifact nobody can reproduce is worse
than no cache at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maze_book.assets.catalog import load_catalog
from maze_book.book.artifacts import (
    GENERATOR_VERSION,
    BookFingerprint,
    MazeStore,
    OutputPaths,
    assets_digest,
    resolve_only,
    write_normalized_book,
)
from maze_book.errors import ConfigError, GenerationError
from maze_book.model.book_config import load_book_config
from maze_book.model.profile import load_profile


@pytest.fixture
def tiny_store(tiny_book_dir: Path, repo_root: Path, tmp_path: Path) -> MazeStore:
    config = load_book_config(tiny_book_dir)
    profile = load_profile(repo_root / "profiles", config.book.profile_id)
    return MazeStore(
        config=config, profile=profile, catalog=load_catalog(config),
        paths=OutputPaths(tmp_path, config.book.id),
    )


# ---------------------------------------------------------------------------
# Output paths (17.14)
# ---------------------------------------------------------------------------


def test_output_paths_match_the_17_14_contract(tmp_path: Path) -> None:
    """17.14 splits what a build writes by who the file is for."""
    paths = OutputPaths(tmp_path, "my-book")
    base = tmp_path / "my-book"

    # `output` is what gets uploaded, and only that.
    assert paths.interior == base / "output" / "book-interior.pdf"
    assert paths.cover == base / "output" / "book-cover.pdf"

    # `build` is the working set: regenerable, and safe to delete.
    assert paths.maze_json(1) == base / "build" / "mazes" / "001.json"
    assert paths.analysis_json(42) == base / "build" / "mazes" / "042.analysis.json"
    assert paths.maze_svg(7) == base / "build" / "mazes" / "007.svg"
    assert paths.page_pdf(6) == base / "build" / "pages" / "page-006.pdf"
    assert paths.normalized_book == base / "build" / "normalized-book.json"
    assert paths.page_plan == base / "build" / "page-plan.json"
    assert paths.contact_sheet == base / "build" / "contact-sheet.png"
    assert paths.preflight == base / "build" / "preflight.json"
    # The editable interior is for proofreading, not for uploading.
    assert paths.editable == base / "build" / "book-interior-editable.pdf"


def test_a_package_folder_is_its_own_output_root(tmp_path: Path) -> None:
    """Duplicating the folder duplicates the book: inputs, outputs and working
    files all travel with it."""
    paths = OutputPaths(tmp_path / "books" / "my-book")
    assert paths.interior == tmp_path / "books" / "my-book" / "output" / "book-interior.pdf"
    assert paths.page_plan == tmp_path / "books" / "my-book" / "build" / "page-plan.json"


def test_only_uploads_land_in_output(tmp_path: Path) -> None:
    """The folder's whole job is that you can open it and know what to do."""
    paths = OutputPaths(tmp_path / "my-book")
    uploads = {paths.interior, paths.cover}
    others = {
        paths.editable, paths.page_plan, paths.preflight, paths.contact_sheet,
        paths.normalized_book, paths.interior_text, paths.maze_json(1),
        paths.page_pdf(1),
    }
    assert all(p.parent == paths.output for p in uploads)
    assert not any(paths.output in p.parents for p in others)


def test_maze_indexes_are_zero_padded_so_they_sort(tmp_path: Path) -> None:
    paths = OutputPaths(tmp_path, "b")
    names = [paths.maze_json(i).name for i in (1, 2, 10, 50)]
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# --only
# ---------------------------------------------------------------------------


def test_only_accepts_a_range_a_single_index_and_open_ends() -> None:
    assert resolve_only("2:4", 50) == [2, 3, 4]
    assert resolve_only("7", 50) == [7]
    assert resolve_only(":3", 50) == [1, 2, 3]
    assert resolve_only("48:", 50) == [48, 49, 50]
    assert resolve_only(None, 3) == [1, 2, 3]


@pytest.mark.parametrize("spec", ["0:3", "1:99", "5:2", "abc", "1:x"])
def test_an_out_of_range_or_malformed_only_is_rejected(spec: str) -> None:
    with pytest.raises(ConfigError, match=r"--only"):
        resolve_only(spec, 50)


# ---------------------------------------------------------------------------
# The cache key (17.12)
# ---------------------------------------------------------------------------


def test_the_key_carries_all_five_17_12_inputs(tiny_store: MazeStore) -> None:
    key = tiny_store.key_for(1).to_json_obj()
    assert set(key) == {
        "bookHash", "profileHash", "generatorVersion", "assetsHash", "mazeIndex", "seed"
    }
    assert key["generatorVersion"] == GENERATOR_VERSION


def test_the_key_is_stable_across_store_instances(
    tiny_book_dir: Path, repo_root: Path, tmp_path: Path
) -> None:
    config = load_book_config(tiny_book_dir)
    profile = load_profile(repo_root / "profiles", config.book.profile_id)
    catalog = load_catalog(config)
    first = MazeStore(
        config=config, profile=profile, catalog=catalog,
        paths=OutputPaths(tmp_path, config.book.id),
    ).key_for(2)
    second = MazeStore(
        config=config, profile=profile, catalog=catalog,
        paths=OutputPaths(tmp_path, config.book.id),
    ).key_for(2)
    assert first == second


def test_each_maze_index_gets_its_own_seed(tiny_store: MazeStore) -> None:
    """Regenerating maze 7 must not reshuffle maze 8."""
    seeds = {tiny_store.seed_for(i) for i in range(1, 4)}
    assert len(seeds) == 3


def test_the_assets_digest_changes_when_an_asset_is_redrawn(
    mutated_book, repo_root: Path
) -> None:
    """A maze's JSON records asset *filenames*. Redraw ghost.svg and every maze
    placing it changes on the page while its JSON stays byte-identical -- hashing
    the file contents is what makes that visible."""
    path = mutated_book(lambda obj: None)
    config = load_book_config(path)
    before = assets_digest(load_catalog(config))

    svg = path / "input" / "assets" / "maze-vectors" / "collectibles" / "candy_01.svg"
    svg.write_text(
        svg.read_text(encoding="utf-8").replace("</svg>", "<path d=\"M10 10 L90 90 Z\"/></svg>"),
        encoding="utf-8",
    )
    assert assets_digest(load_catalog(config)) != before


def test_a_key_does_not_match_a_different_generator_version(tiny_store: MazeStore) -> None:
    key = tiny_store.key_for(1)
    stale = dict(key.to_json_obj(), generatorVersion="0.0.1")
    assert key.matches(key.to_json_obj())
    assert not key.matches(stale)


@pytest.mark.parametrize(
    "field", ["bookHash", "profileHash", "assetsHash", "mazeIndex", "seed"]
)
def test_changing_any_single_key_field_invalidates_the_entry(
    tiny_store: MazeStore, field: str
) -> None:
    key = tiny_store.key_for(1)
    obj = key.to_json_obj()
    obj[field] = "changed" if isinstance(obj[field], str) else obj[field] + 1
    assert not key.matches(obj)


def test_a_missing_or_malformed_cache_key_is_not_a_match(tiny_store: MazeStore) -> None:
    key = tiny_store.key_for(1)
    assert not key.matches(None)
    assert not key.matches({})
    assert not key.matches("not-a-dict")


# ---------------------------------------------------------------------------
# Reading cached artifacts
# ---------------------------------------------------------------------------


def test_nothing_on_disk_means_no_cache_hit(tiny_store: MazeStore) -> None:
    assert tiny_store.cached(1) is None


def test_require_refuses_to_regenerate(tiny_store: MazeStore) -> None:
    """17.13: `maze render` and `book assemble` read cached data and must not
    silently regenerate -- otherwise the page proofed and the page printed came
    from different mazes."""
    with pytest.raises(GenerationError, match=r"no valid cached artifact for maze 1") as excinfo:
        tiny_store.require(1)
    assert any("generate-mazes" in d for d in excinfo.value.details)


def test_a_generated_maze_round_trips_through_the_cache(tiny_store: MazeStore) -> None:
    produced = {index: loaded for index, loaded in tiny_store.ensure([1])}
    assert produced[1].from_cache is False

    reread = tiny_store.cached(1)
    assert reread is not None and reread.from_cache is True
    assert reread.maze.to_json_obj() == produced[1].maze.to_json_obj()
    assert reread.analysis.best_candy_total == produced[1].analysis.best_candy_total


def test_a_second_run_reuses_the_artifact(tiny_store: MazeStore) -> None:
    list(tiny_store.ensure([1]))
    again = {index: loaded for index, loaded in tiny_store.ensure([1])}
    assert again[1].from_cache is True


def test_force_bypasses_a_valid_cache_entry(tiny_store: MazeStore) -> None:
    list(tiny_store.ensure([1]))
    forced = {index: loaded for index, loaded in tiny_store.ensure([1], force=True)}
    assert forced[1].from_cache is False


def test_a_stale_key_on_disk_is_ignored(tiny_store: MazeStore) -> None:
    list(tiny_store.ensure([1]))
    path = tiny_store.paths.maze_json(1)
    obj = json.loads(path.read_text(encoding="utf-8"))
    obj["cacheKey"]["profileHash"] = "something-else"
    path.write_text(json.dumps(obj), encoding="utf-8")
    assert tiny_store.cached(1) is None


def test_corrupt_json_is_treated_as_a_miss_not_a_crash(tiny_store: MazeStore) -> None:
    list(tiny_store.ensure([1]))
    tiny_store.paths.maze_json(1).write_text("{not json", encoding="utf-8")
    assert tiny_store.cached(1) is None


def test_a_maze_without_its_analysis_is_a_miss(tiny_store: MazeStore) -> None:
    list(tiny_store.ensure([1]))
    tiny_store.paths.analysis_json(1).unlink()
    assert tiny_store.cached(1) is None


def test_generation_is_reproducible_for_a_fixed_seed(tiny_store: MazeStore) -> None:
    first = {i: m for i, m in tiny_store.ensure([1, 2], force=True)}
    snapshots = {i: m.maze.to_json_obj() for i, m in first.items()}
    second = {i: m for i, m in tiny_store.ensure([1, 2], force=True)}
    assert {i: m.maze.to_json_obj() for i, m in second.items()} == snapshots


# ---------------------------------------------------------------------------
# normalized-book.json
# ---------------------------------------------------------------------------


def test_the_normalized_book_records_what_the_build_was_made_from(
    tiny_book_dir: Path, repo_root: Path, tmp_path: Path
) -> None:
    config = load_book_config(tiny_book_dir)
    profile = load_profile(repo_root / "profiles", config.book.profile_id)
    catalog = load_catalog(config)
    paths = OutputPaths(tmp_path, config.book.id)
    fingerprint = BookFingerprint.build(config, profile, catalog)

    write_normalized_book(
        paths, config=config, profile=profile,
        fingerprint=fingerprint, front_matter_pages=5,
    )
    obj = json.loads(paths.normalized_book.read_text(encoding="utf-8"))
    for field in (
        "bookId", "generatorVersion", "bookHash", "profileHash",
        "assetsHash", "profileId", "seed", "mazeCount", "frontMatterPages",
    ):
        assert field in obj, field
    assert obj["bookHash"] == fingerprint.book_hash
    assert obj["book"] == config.raw
