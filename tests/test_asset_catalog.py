"""Tests for ``maze_book.assets.catalog`` (PRD 17.8-17.9).

Two properties carry the weight here. Discovery must be **sorted**, because
``Path.iterdir`` order is filesystem-dependent and a book that renumbers its
variants when rebuilt on a different machine is not reproducible. And variant
choice must be a pure function of ``(derived rng, ordinal)``, so regenerating
one maze cannot reshuffle another.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from maze_book.assets.catalog import (
    POLICIES,
    AssetCatalog,
    AssetFile,
    discover,
    load_catalog,
)
from maze_book.errors import AssetError
from maze_book.model.book_config import load_book_config


@pytest.fixture
def tiny_catalog(tiny_book_dir: Path) -> AssetCatalog:
    return load_catalog(load_book_config(tiny_book_dir, require_assets=True))


def _files(*names: str) -> list[AssetFile]:
    return [AssetFile(asset_id=n, path=Path("/stub") / n) for n in names]


# ---------------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------------


def test_discover_returns_svgs_sorted_by_filename(tmp_path: Path) -> None:
    for name in ("zeta.svg", "alpha.svg", "middle.svg"):
        (tmp_path / name).touch()
    assert [a.asset_id for a in discover(tmp_path, field_name="f")] == [
        "alpha.svg", "middle.svg", "zeta.svg",
    ]


def test_discover_ignores_non_svg_files_and_subdirectories(tmp_path: Path) -> None:
    (tmp_path / "keep.svg").touch()
    (tmp_path / "notes.txt").touch()
    (tmp_path / "art.png").touch()
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "deep.svg").touch()
    assert [a.asset_id for a in discover(tmp_path, field_name="f")] == ["keep.svg"]


def test_discover_accepts_an_uppercase_extension(tmp_path: Path) -> None:
    (tmp_path / "Bat.SVG").touch()
    assert [a.asset_id for a in discover(tmp_path, field_name="f")] == ["Bat.SVG"]


def test_discover_rejects_a_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(AssetError, match=r"assets\.deadEndVectorsDir is not a directory"):
        discover(tmp_path / "nope", field_name="assets.deadEndVectorsDir")


def test_discover_rejects_an_empty_required_directory(tmp_path: Path) -> None:
    with pytest.raises(AssetError, match=r"contains no \.svg files"):
        discover(tmp_path, field_name="assets.collectibleVectorsDir")


def test_discover_tolerates_an_empty_optional_directory(tmp_path: Path) -> None:
    assert discover(tmp_path, field_name="assets.pageVectorsDir", require_nonempty=False) == []


def test_asset_file_records_the_bare_filename_not_a_path(tmp_path: Path) -> None:
    """``asset_id`` lands in ``MazeData`` and therefore in the committed
    artifact: a path would bake the checkout location into the book."""
    (tmp_path / "ghost.svg").touch()
    asset = discover(tmp_path, field_name="f")[0]
    assert asset.asset_id == "ghost.svg"
    assert asset.stem == "ghost"
    assert asset.path.is_absolute() or not str(asset.path).startswith("..")


# ---------------------------------------------------------------------------
# Variant policies
# ---------------------------------------------------------------------------


def _catalog(policy: str, names: list[str]) -> AssetCatalog:
    return AssetCatalog(
        start=_files("start.svg")[0],
        finish=_files("finish.svg")[0],
        dead_ends=_files(*names),
        collectibles=_files(*names),
        page_vectors={},
        dead_end_policy=policy,
        collectible_policy=policy,
    )


def test_first_in_folder_always_returns_the_sorted_first() -> None:
    catalog = _catalog("first-in-folder", ["a.svg", "b.svg", "c.svg"])
    picks = {catalog.choose_dead_end(random.Random(i), i).asset_id for i in range(10)}
    assert picks == {"a.svg"}


def test_cycle_folder_walks_the_variants_in_order() -> None:
    catalog = _catalog("cycle-folder", ["a.svg", "b.svg", "c.svg"])
    picked = [catalog.choose_dead_end(random.Random(0), i).asset_id for i in range(7)]
    assert picked == ["a.svg", "b.svg", "c.svg", "a.svg", "b.svg", "c.svg", "a.svg"]


def test_random_from_folder_is_reproducible_for_a_given_seed() -> None:
    """Determinism is the whole point: the same derived rng and the same folder
    must give the same variant on every machine."""
    catalog = _catalog("random-from-folder", ["a.svg", "b.svg", "c.svg", "d.svg"])
    first = [catalog.choose_dead_end(random.Random(7), i).asset_id for i in range(20)]
    second = [catalog.choose_dead_end(random.Random(7), i).asset_id for i in range(20)]
    assert first == second


def test_random_from_folder_actually_varies_across_streams() -> None:
    catalog = _catalog("random-from-folder", ["a.svg", "b.svg", "c.svg", "d.svg"])
    picks = {catalog.choose_dead_end(random.Random(seed), 0).asset_id for seed in range(40)}
    assert len(picks) > 1


def test_random_from_folder_ignores_the_ordinal() -> None:
    """Randomness comes from the stream, not the position: passing a different
    ordinal to the same rng must not change the draw."""
    catalog = _catalog("random-from-folder", ["a.svg", "b.svg", "c.svg"])
    assert (
        catalog.choose_dead_end(random.Random(3), 0).asset_id
        == catalog.choose_dead_end(random.Random(3), 99).asset_id
    )


def test_a_single_variant_folder_is_stable_under_every_policy() -> None:
    for policy in POLICIES:
        catalog = _catalog(policy, ["only.svg"])
        assert catalog.choose_collectible(random.Random(1), 5).asset_id == "only.svg"


def test_unknown_policy_is_rejected_at_selection_time() -> None:
    catalog = _catalog("pick-my-favourite", ["a.svg"])
    with pytest.raises(AssetError, match=r"unknown asset policy"):
        catalog.choose_dead_end(random.Random(0), 0)


def test_choosing_from_an_empty_variant_list_is_rejected() -> None:
    catalog = _catalog("first-in-folder", [])
    with pytest.raises(AssetError, match=r"has no variants to choose from"):
        catalog.choose_dead_end(random.Random(0), 0)


# ---------------------------------------------------------------------------
# all_files() and path_for()
# ---------------------------------------------------------------------------


def test_all_files_is_deduplicated_and_sorted_by_path() -> None:
    shared = AssetFile(asset_id="shared.svg", path=Path("/stub/shared.svg"))
    catalog = AssetCatalog(
        start=shared,
        finish=AssetFile(asset_id="finish.svg", path=Path("/stub/finish.svg")),
        dead_ends=[shared],
        collectibles=_files("candy.svg"),
        page_vectors={"deco.svg": AssetFile("deco.svg", Path("/stub/deco.svg"))},
        dead_end_policy="first-in-folder",
        collectible_policy="first-in-folder",
    )
    names = [a.asset_id for a in catalog.all_files()]
    assert names == ["candy.svg", "deco.svg", "finish.svg", "shared.svg"]


def test_path_for_resolves_an_asset_id_recorded_in_maze_data(tiny_catalog: AssetCatalog) -> None:
    assert tiny_catalog.path_for("dead_end_01.svg").name == "dead_end_01.svg"
    assert tiny_catalog.path_for("start.svg").is_file()


def test_path_for_rejects_an_id_outside_the_catalog(tiny_catalog: AssetCatalog) -> None:
    with pytest.raises(AssetError, match=r"is not in this book's catalog"):
        tiny_catalog.path_for("imaginary.svg")


# ---------------------------------------------------------------------------
# load_catalog()
# ---------------------------------------------------------------------------


def test_load_catalog_builds_the_tiny_fixture_vocabulary(tiny_catalog: AssetCatalog) -> None:
    assert tiny_catalog.start.asset_id == "start.svg"
    assert tiny_catalog.finish.asset_id == "finish.svg"
    assert [a.asset_id for a in tiny_catalog.dead_ends] == ["dead_end_01.svg"]
    assert [a.asset_id for a in tiny_catalog.collectibles] == ["candy_01.svg"]
    assert tiny_catalog.page_vectors == {}


def test_load_catalog_takes_start_and_finish_from_config_not_folder_order(
    mutated_book, tmp_path: Path
) -> None:
    """Those two assets are the identity of the book. Dropping a new SVG that
    sorts first into ``beginning-vectors/`` must not silently replace Jim."""
    path = mutated_book(lambda obj: None)
    (path / "assets" / "beginning-vectors" / "aaa_first.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"/>', encoding="utf-8"
    )
    catalog = load_catalog(load_book_config(path, require_assets=True))
    assert catalog.start.asset_id == "start.svg"


def test_load_catalog_rejects_an_unknown_policy(mutated_book) -> None:
    path = mutated_book(lambda obj: obj["assets"].update(deadEndAssetPolicy="whatever"))
    with pytest.raises((AssetError, Exception)):
        load_catalog(load_book_config(path, require_assets=False))


def test_load_catalog_rejects_a_missing_start_asset(mutated_book) -> None:
    path = mutated_book(lambda obj: obj["assets"].update(startAsset="gone.svg"))
    with pytest.raises(AssetError, match=r"assets\.startAsset does not exist"):
        load_catalog(load_book_config(path, require_assets=False))


def test_a_non_svg_start_asset_never_reaches_the_catalog(mutated_book) -> None:
    """``assets.startAsset`` is schema-constrained to ``*.svg``, so the loader
    rejects it first. ``catalog.py``'s own suffix check is therefore a second
    line of defence for a ``BookConfig`` built directly rather than parsed --
    tested on its own below."""
    from maze_book.errors import ConfigError

    path = mutated_book(lambda obj: obj["assets"].update(startAsset="start.png"))
    (path / "assets" / "beginning-vectors" / "start.png").write_text("x", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"schema validation") as excinfo:
        load_book_config(path, require_assets=False)
    assert any("startAsset" in detail for detail in excinfo.value.details)


def test_load_catalog_rejects_a_non_svg_start_asset_on_a_hand_built_config(
    tiny_book_dir: Path, tmp_path: Path
) -> None:
    """The guard ``catalog.py`` owns, reached with a config object that never
    went through schema validation. ``load_catalog`` reads only these five
    members, so a stand-in is enough and keeps the test about the suffix."""
    config = load_book_config(tiny_book_dir, require_assets=True)
    decoy = tmp_path / "start.png"
    decoy.write_text("not an svg", encoding="utf-8")

    class ConfigStandIn:
        start_asset_path = decoy
        finish_asset_path = config.finish_asset_path
        page_vectors_dir = config.page_vectors_dir
        dead_end_vectors_dir = config.dead_end_vectors_dir
        collectible_vectors_dir = config.collectible_vectors_dir
        assets = config.assets

    with pytest.raises(AssetError, match=r"must be an \.svg file"):
        load_catalog(ConfigStandIn())


def test_load_catalog_picks_up_page_vectors_when_the_folder_is_configured(
    mutated_book,
) -> None:
    def mutator(obj: dict) -> None:
        obj["assets"]["pageVectorsDir"] = "assets/page-vectors"

    path = mutated_book(mutator)
    directory = path / "assets" / "page-vectors"
    directory.mkdir(parents=True)
    for name in ("moon.svg", "bat.svg"):
        (directory / name).write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"/>', encoding="utf-8"
        )
    catalog = load_catalog(load_book_config(path, require_assets=True))
    assert sorted(catalog.page_vectors) == ["bat.svg", "moon.svg"]
    assert catalog.page_vectors["moon.svg"].path.is_file()


def test_the_halloween_package_catalog_loads_and_every_file_validates(repo_root: Path) -> None:
    """The integration the pipeline actually depends on: real config, real
    folders, real SVGs, and every discovered file inside the 18.5 subset.

    Each file is judged by the profile its *printed size* earns, exactly as
    ``book validate`` does. Holding a 0.6 in page decoration to the finale
    collectible's minimum feature would fail artwork that prints perfectly.
    """
    from maze_book.assets.validate import (
        profiles_for_roles,
        raise_for_reports,
        validate_svg,
    )
    from maze_book.model.profile import load_profile
    from maze_book.rendering.story_page import VECTOR_SIZE_IN

    config = load_book_config(repo_root / "books" / "jims-halloween-maze-adventure")
    catalog = load_catalog(config)
    assert catalog.start.asset_id.endswith(".svg")
    assert len(catalog.dead_ends) >= 4
    assert len(catalog.collectibles) >= 4
    assert len(catalog.page_vectors) >= 1

    by_role = profiles_for_roles(
        bands=load_profile(repo_root / "profiles", config.book.profile_id).bands,
        maze_square_in=config.layout.maze_square_in,
        page_vector_in=VECTOR_SIZE_IN,
    )
    roles = catalog.roles()
    reports = [
        validate_svg(asset.path, profile=by_role[roles[str(asset.path)]])
        for asset in catalog.all_files()
    ]
    raise_for_reports(reports, label="halloween")
