"""Tests for ``maze_book.content.loader`` (PRD 17.3, 18.6).

18.6 caps story body text at two lines, which makes "does it fit?" a question
with a real answer rather than a matter of taste. These tests pin both halves of
that: the wrap itself, and the refusal -- a scene that overflows is a
configuration error naming the scene, because the alternatives (shrink the type,
truncate the words, let it run to three lines) each break something the author
or the designer decided on purpose.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maze_book.content.loader import (
    DEFAULT_BODY_SIZE,
    SceneText,
    normalize_text,
    page_vector_path,
    prepare_scene,
    prepare_scenes,
    wrap_to_width,
)
from maze_book.errors import ConfigError
from maze_book.model.book_config import Scene, load_book_config


@pytest.fixture(scope="module", autouse=True)
def fonts(repo_root: Path):
    from maze_book.rendering.svg_to_pdf import register_fonts

    register_fonts(repo_root / "fonts")


def scene(number: int = 1, title: str = "The Attic Hunt", text: str = "Short line.", **kw) -> Scene:
    return Scene(number=number, title=title, text=text, **kw)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_whitespace_is_collapsed() -> None:
    assert normalize_text("  a\n\n b \t c  ") == "a b c"


def test_text_is_normalized_to_nfc() -> None:
    """The same accented character has two spellings in JSON; both render
    identically but hash differently, which would break the byte-stability the
    artifact cache depends on."""
    decomposed = "café"
    composed = "café"
    assert normalize_text(decomposed) == normalize_text(composed) == composed


# ---------------------------------------------------------------------------
# Wrapping
# ---------------------------------------------------------------------------


def test_short_text_stays_on_one_line() -> None:
    assert wrap_to_width("Two words", font="Vera", size=16.0, width=500.0) == ["Two words"]


def test_wrapping_breaks_on_whitespace_only() -> None:
    lines = wrap_to_width(
        "The ghost suit is inside out and the zipper will not move at all today.",
        font="Vera", size=16.0, width=200.0,
    )
    assert len(lines) > 1
    assert " ".join(lines).split() == (
        "The ghost suit is inside out and the zipper will not move at all today.".split()
    )


def test_no_line_exceeds_the_width() -> None:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    text = "Jim paints a white swirl on each cheek and steps past the toys and books."
    for line in wrap_to_width(text, font="Vera", size=16.0, width=180.0):
        if " " in line:
            assert stringWidth(line, "Vera", 16.0) <= 180.0


def test_an_unbreakable_word_is_kept_whole_rather_than_hyphenated() -> None:
    """Breaking a word silently would hide a real problem inside a plausible
    page; the caller detects the overflow by measuring instead."""
    lines = wrap_to_width(
        "Supercalifragilistic", font="Vera", size=16.0, width=20.0
    )
    assert lines == ["Supercalifragilistic"]


def test_empty_text_wraps_to_no_lines() -> None:
    assert wrap_to_width("   ", font="Vera", size=16.0, width=100.0) == []


# ---------------------------------------------------------------------------
# prepare_scene
# ---------------------------------------------------------------------------


def test_the_title_is_upper_cased_for_the_display_face() -> None:
    prepared, problems = prepare_scene(scene(title="The Attic Hunt"), width=500.0)
    assert prepared.title == "THE ATTIC HUNT"
    assert problems == []


def test_a_fitting_scene_reports_no_problems() -> None:
    prepared, problems = prepare_scene(
        scene(text="Jim's costume box is somewhere up in the attic."), width=531.0
    )
    assert problems == []
    assert prepared.line_count == 1


def test_a_three_line_body_is_reported_with_the_scene_number() -> None:
    long_text = " ".join(["candy"] * 60)
    _, problems = prepare_scene(scene(number=17, text=long_text), width=531.0)
    assert any("scene 17" in p and "wraps to" in p for p in problems)
    assert any("limit 2 (18.6)" in p for p in problems)


def test_the_overflow_message_says_roughly_how_much_to_cut() -> None:
    long_text = " ".join(["candy"] * 60)
    _, problems = prepare_scene(scene(text=long_text), width=531.0)
    message = next(p for p in problems if "wraps to" in p)
    assert "shorten the text by roughly" in message


def test_an_over_wide_title_is_reported() -> None:
    _, problems = prepare_scene(scene(number=4, title="A" * 60), width=200.0)
    assert any("scene 4" in p and "title" in p for p in problems)


def test_an_empty_title_or_body_is_reported() -> None:
    _, problems = prepare_scene(scene(title="  ", text="  "), width=500.0)
    assert any("title is empty" in p for p in problems)
    assert any("text is empty" in p for p in problems)


def test_a_word_wider_than_the_line_is_reported_explicitly() -> None:
    _, problems = prepare_scene(scene(number=9, text="Supercalifragilistic"), width=20.0)
    assert any("alone is" in p and "wider than" in p for p in problems)


def test_raising_the_line_limit_accepts_longer_text() -> None:
    """The two-line cap is 18.6's default, not a hard-coded truth: a different
    book may set its own. Asserted on the wrap problem specifically, since a
    narrow width also overflows the 34 pt title."""
    text = " ".join(["candy"] * 20)
    _, two = prepare_scene(scene(text=text), width=200.0, max_body_lines=2)
    _, many = prepare_scene(scene(text=text), width=200.0, max_body_lines=9)
    assert any("wraps to" in p for p in two)
    assert not any("wraps to" in p for p in many)


def test_the_page_vector_passes_through_untouched() -> None:
    prepared, _ = prepare_scene(scene(page_vector="jim_in_sheet.png"), width=500.0)
    assert prepared.page_vector == "jim_in_sheet.png"


# ---------------------------------------------------------------------------
# prepare_scenes over a real book
# ---------------------------------------------------------------------------


def test_every_shipped_halloween_scene_fits_two_lines(repo_root: Path) -> None:
    """The regression that matters: edit a scene too long and the build stops
    here, naming it, rather than producing a page with a third line on it."""
    from maze_book.rendering.page import PageMetrics

    config = load_book_config(repo_root / "books" / "jims-halloween-maze-adventure")
    metrics = PageMetrics.from_print_spec(config.print)
    scenes = prepare_scenes(config, width=metrics.live_width("left"))
    assert len(scenes) == config.book.maze_count
    assert all(text.line_count <= 2 for text in scenes)
    assert [text.number for text in scenes] == list(range(1, 51))


def test_all_fit_problems_are_reported_in_one_raise(tiny_book_dir: Path, mutated_book) -> None:
    """Fixing story text is an editing pass. Being told about scene 1, fixing it,
    and then being told about scene 3 turns one pass into three."""
    def mutator(obj: dict) -> None:
        for entry in obj["content"]["scenes"]:
            entry["text"] = " ".join(["candy"] * 80)

    path = mutated_book(mutator)
    config = load_book_config(path)
    with pytest.raises(ConfigError, match=r"story-text fit problem") as excinfo:
        prepare_scenes(config, width=300.0)
    assert len(excinfo.value.details) >= 3
    assert any("scene 1" in d for d in excinfo.value.details)
    assert any("scene 3" in d for d in excinfo.value.details)


# ---------------------------------------------------------------------------
# Page vectors
# ---------------------------------------------------------------------------


def test_page_vector_path_resolves_under_the_configured_folder(repo_root: Path) -> None:
    config = load_book_config(repo_root / "books" / "jims-halloween-maze-adventure")
    text = SceneText(number=1, title="T", body_lines=(), page_vector="jim_in_sheet.png")
    resolved = page_vector_path(config, text)
    assert resolved is not None and resolved.is_file()
    assert resolved.parent == config.page_vectors_dir


def test_a_scene_without_a_vector_resolves_to_none(repo_root: Path) -> None:
    config = load_book_config(repo_root / "books" / "jims-halloween-maze-adventure")
    assert page_vector_path(config, SceneText(1, "T", (), None)) is None


def test_a_vector_without_a_configured_folder_is_rejected(tiny_book_dir: Path) -> None:
    """17.2: a book may ship no page-vectors folder, but then no scene may name
    one."""
    config = load_book_config(tiny_book_dir)
    assert config.page_vectors_dir is None
    with pytest.raises(ConfigError, match=r"pageVectorsDir is null"):
        page_vector_path(config, SceneText(1, "T", (), "moon.svg"))
