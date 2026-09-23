"""Language editions: one book, printed in several languages.

An edition is a separate package that shares its puzzles with the original: the
same ``book.seedId`` gives the same fifty mazes and the same answer key, and the
same assets and layout give the same pages. What differs is the words -- the
scenes, the front matter, and the page furniture in ``content.labels``.

These tests hold the three pieces that make that work (shared seeds, per-scene
finish markers, localized labels) and the promise that sibling editions have
not drifted apart, because a fix made to one edition and forgotten in the
others is invisible until someone compares two printed copies.
"""

from __future__ import annotations

import dataclasses
import filecmp
import importlib.util
import json
import re
from pathlib import Path

import pytest

from maze_book.assets.catalog import AssetCatalog, AssetFile, load_catalog
from maze_book.errors import ConfigError
from maze_book.generation import pipeline
from maze_book.model.book_config import load_book_config
from maze_book.model.labels import (
    DEFAULT_LABELS,
    find_best_possible,
    labels_from_json,
)
from maze_book.model.profile import load_profile
from maze_book.rendering.page import PT_PER_IN

ENGLISH = "jims-halloween-maze-adventure"

GERMAN = {
    "bestPossible": {
        "one": "Mehr geht nicht: {n} Süßigkeit",
        "other": "Mehr geht nicht: {n} Süßigkeiten",
    },
    "mazeNumber": "Labyrinth {n}",
    "total": "Gesamt",
    "actNames": {"Finale": "Das große Finale"},
}


# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #


def test_the_default_labels_print_what_the_english_book_always_printed() -> None:
    assert DEFAULT_LABELS.best_possible(1) == "Best possible: 1 candy"
    assert DEFAULT_LABELS.best_possible(6) == "Best possible: 6 candies"
    assert DEFAULT_LABELS.maze_label(18) == "Maze 18"
    assert (DEFAULT_LABELS.start, DEFAULT_LABELS.finish) == ("START", "FINISH")
    assert DEFAULT_LABELS.act_name("Finale") == "Finale"


def test_a_book_overrides_only_the_labels_it_names() -> None:
    labels = labels_from_json(GERMAN)
    assert labels.best_possible(1) == "Mehr geht nicht: 1 Süßigkeit"
    assert labels.maze_label(4) == "Labyrinth 4"
    assert labels.act_name("Finale") == "Das große Finale"
    assert labels.act_name("Getting Ready") == "Getting Ready"
    assert labels.start == "START"


def test_a_template_without_its_number_is_refused() -> None:
    with pytest.raises(ConfigError) as caught:
        labels_from_json({"mazeNumber": "Labyrinth"})
    assert "mazeNumber" in " ".join(caught.value.details)


def test_a_score_template_without_its_number_is_refused() -> None:
    with pytest.raises(ConfigError) as caught:
        labels_from_json({"bestPossible": {"one": "Eins", "other": "{n} Stück"}})
    assert "bestPossible.one" in " ".join(caught.value.details)


def test_preflight_reads_the_score_back_in_the_books_own_words() -> None:
    """pdftotext may break the line anywhere, and the singular must be found."""
    labels = labels_from_json(GERMAN)
    text = "3. Mehr geht nicht: 7 Süßigkeiten   4. Mehr geht\nnicht: 1 Süßigkeit"
    assert find_best_possible(labels, text) == [7, 1]
    # The English wording is not the German book's score line.
    assert find_best_possible(labels, "Best possible: 5 candies") == []
    assert find_best_possible(DEFAULT_LABELS, "Best possible: 12") == [12]


# --------------------------------------------------------------------------- #
# Shared seeds and per-scene finishes
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def english(repo_root: Path):
    config = load_book_config(repo_root / "books" / ENGLISH)
    return config, load_profile(repo_root / "profiles", config.book.profile_id)


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


def test_seed_id_defaults_to_the_book_id(mutated_book) -> None:
    config = load_book_config(mutated_book(lambda obj: obj["book"].pop("seedId", None)))
    assert config.book.seed_id == config.book.id


def test_an_edition_with_the_same_seed_id_prints_the_same_maze(english, stub_catalog) -> None:
    """The id names the package; the seed id names the puzzles."""
    config, profile = english

    def maze_for(book) -> tuple:
        result = pipeline.generate_maze(
            config=dataclasses.replace(config, book=book), profile=profile,
            catalog=stub_catalog, maze_index=3,
        )
        return result.maze.open_edges, result.maze.start, result.maze.finish

    original = maze_for(config.book)
    edition = dataclasses.replace(config.book, id=f"{ENGLISH}_german")
    assert maze_for(edition) == original
    other = dataclasses.replace(config.book, id="other", seed_id="other")
    assert maze_for(other) != original


def test_each_scene_finishes_at_its_own_object(english) -> None:
    config, _ = english
    catalog = load_catalog(config)
    assert catalog.finish_for(1).asset_id == "costume_trunk.png"
    assert catalog.finish_for(6).asset_id == "candy_bucket.png"
    roles = catalog.roles()
    for number in range(1, config.book.maze_count + 1):
        assert roles[str(catalog.finish_for(number).path)] == "finish"


def test_a_missing_finish_vector_is_reported_at_load(mutated_book) -> None:
    def point_at_nothing(obj: dict) -> None:
        obj["content"]["scenes"][0]["finishVector"] = "no_such_thing.svg"

    with pytest.raises(ConfigError) as caught:
        load_book_config(mutated_book(point_at_nothing))
    assert "finishVector" in " ".join(caught.value.details or [])


def test_an_edition_id_may_carry_an_underscore_suffix(repo_root: Path) -> None:
    schema = json.loads((repo_root / "schemas" / "book.schema.json").read_text("utf-8"))
    pattern = re.compile(schema["properties"]["book"]["properties"]["id"]["pattern"])
    assert pattern.match(f"{ENGLISH}_spanish")
    assert pattern.match(ENGLISH)
    for bad in ("Jims-book", "book__es", "book_", "_book"):
        assert not pattern.match(bad), bad


# --------------------------------------------------------------------------- #
# Sibling editions stay in step
# --------------------------------------------------------------------------- #

#: Everything that is not words. Two editions that differ here are two books.
SHARED_SECTIONS = ("print", "generation", "layout", "assets", "outputs")
SHARED_BOOK_KEYS = ("seed", "seedId", "mazeCount", "profileId", "author", "printContentOrigin")
SHARED_SCENE_KEYS = ("number", "pageVector", "finishVector")


def _editions(repo_root: Path) -> list[list[Path]]:
    groups: dict[str, list[Path]] = {}
    for path in sorted((repo_root / "books").glob("*/input/book.json")):
        book = json.loads(path.read_text("utf-8"))["book"]
        groups.setdefault(book.get("seedId") or book["id"], []).append(path)
    return [paths for paths in groups.values() if len(paths) > 1]


def test_there_are_editions_to_compare(repo_root: Path) -> None:
    assert _editions(repo_root), "expected the Halloween book's language editions"


def test_editions_share_everything_but_their_words(repo_root: Path) -> None:
    for paths in _editions(repo_root):
        first, *rest = [json.loads(p.read_text("utf-8")) for p in paths]
        for path, other in zip(paths[1:], rest):
            name = path.parent.parent.name
            for section in SHARED_SECTIONS:
                assert other[section] == first[section], f"{name}: {section} differs"
            for key in SHARED_BOOK_KEYS:
                assert other["book"].get(key) == first["book"].get(key), f"{name}: book.{key}"
            for mine, theirs in zip(first["content"]["scenes"], other["content"]["scenes"]):
                for key in SHARED_SCENE_KEYS:
                    assert mine.get(key) == theirs.get(key), (
                        f"{name}: scene {mine['number']} {key} differs"
                    )


def test_editions_ship_byte_identical_assets(repo_root: Path) -> None:
    for paths in _editions(repo_root):
        reference = paths[0].parent / "assets"
        for path in paths[1:]:
            comparison = filecmp.dircmp(reference, path.parent / "assets")
            problems = _dircmp_problems(comparison)
            assert not problems, f"{path.parent.parent.name}: {problems[:5]}"


def _dircmp_problems(comparison: filecmp.dircmp) -> list[str]:
    _, mismatch, errors = filecmp.cmpfiles(
        comparison.left, comparison.right, comparison.common_files, shallow=False
    )
    problems = [*comparison.left_only, *comparison.right_only, *mismatch, *errors]
    for sub in comparison.subdirs.values():
        problems.extend(_dircmp_problems(sub))
    return problems


# --------------------------------------------------------------------------- #
# Longer words, same page
# --------------------------------------------------------------------------- #


def test_the_folio_leaves_room_for_a_descender() -> None:
    """ "Labyrinth" has a y; "Maze" has none, which is how a baseline on the
    live edge went unnoticed until the German edition."""
    from tests.test_rendering_pages import metrics, small_maze

    from maze_book.rendering.maze_page import MAZE_NUMBER_SIZE, plan_maze_page

    m = metrics()
    layout = plan_maze_page(
        small_maze(), metrics=m, side="right", candy_count=5, maze_square_in=6.75
    )
    live_bottom = m.live_box("right")[3]
    assert layout.number_origin[1] + MAZE_NUMBER_SIZE * 0.25 <= live_bottom + 1e-6


def test_a_longer_total_word_moves_the_box_rather_than_the_ticks(repo_root: Path) -> None:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    from tests.test_rendering_pages import metrics, small_maze

    from maze_book.rendering.maze_page import TALLY_VALUE_SIZE, plan_maze_page
    from maze_book.rendering.svg_to_pdf import BODY_FONT, register_fonts

    register_fonts(repo_root / "fonts")
    for word in ("Total", "Gesamt", "Insgesamt"):
        layout = plan_maze_page(
            small_maze(), metrics=metrics(), side="right", candy_count=9,
            maze_square_in=7.3, total_label=word,
        )
        last_tick_right = max(box[2] for box in layout.tick_boxes)
        word_left = layout.total_label_origin[0] - stringWidth(word, BODY_FONT, TALLY_VALUE_SIZE)
        assert word_left > last_tick_right, word


def test_an_accented_capital_is_measured_above_the_cap_height(repo_root: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "make_front_matter", repo_root / "tools" / "make_front_matter.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    writer = tool.PageWriter
    assert writer.ascent_ratio("HOW TO PLAY") == writer.ASCENT_RATIO
    assert writer.ascent_ratio("CÓMO SE JUEGA") > writer.ASCENT_RATIO
    assert writer.ascent_ratio("ÜBERRASCHUNG") > writer.ASCENT_RATIO
    assert writer.ascent_ratio("cómo") == writer.ASCENT_RATIO
    assert 7.3 * PT_PER_IN > 0  # keep the import honest for readers of this file


def test_validate_reports_an_end_page_that_runs_to_three_lines(
    mutated_book, repo_root: Path
) -> None:
    """The German closing line did, and the first to notice was the assembler."""
    from maze_book.cli import _check_end_page_fits
    from maze_book.rendering.page import PageMetrics
    from maze_book.rendering.svg_to_pdf import register_fonts

    register_fonts(repo_root / "fonts")

    def long_goodbye(obj: dict) -> None:
        obj["layout"]["insertEndPage"] = True
        obj["content"]["endPage"] = {
            "title": "Ende",
            "text": "Was für ein Abend! Jimmy zählt und zählt, und sein erstes "
            "Halloween wird er so schnell nicht vergessen.",
        }

    config = load_book_config(mutated_book(long_goodbye), require_assets=False)
    with pytest.raises(ConfigError) as caught:
        _check_end_page_fits(config, PageMetrics.from_print_spec(config.print))
    assert "3 lines" in " ".join(caught.value.details)
