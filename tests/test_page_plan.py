"""Tests for ``maze_book.book.page_plan`` (PRD 17.10, 18.7).

Parity is the property under test and it is unforgiving: a one-page change in the
front matter flips every spread in the book. So the tests cover not just the
happy 112-page path but each way the planner can be asked to do something it must
refuse -- because 17.10 requires it to *fail* rather than quietly flip a pair,
and a planner that bends under pressure produces a book nobody notices is wrong
until it is printed.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from maze_book.book.page_plan import (
    KIND_BLANK,
    KIND_END,
    KIND_FRONT_MATTER,
    KIND_MAZE,
    KIND_SOLUTIONS,
    KIND_STORY,
    front_matter_page_count,
    plan_pages,
)
from maze_book.errors import ConfigError
from maze_book.model.book_config import LayoutSpec


def layout(**overrides) -> LayoutSpec:
    base = dict(
        front_matter_pdf="front-matter.pdf",
        story_page_side="left",
        maze_page_side="right",
        maze_square_in=6.75,
        tally_position="below",
        show_maze_number=True,
        show_best_possible_score=True,
        solutions_per_page=9,
        solutions_start_side="left",
        insert_end_page=True,
        insert_blank_pages_for_parity=True,
        expected_page_count=None,
    )
    base.update(overrides)
    return LayoutSpec(**base)


# ---------------------------------------------------------------------------
# The shipped shape
# ---------------------------------------------------------------------------


def test_the_halloween_target_is_112_pages_exactly() -> None:
    """17.10 states the target and its composition; this is that arithmetic
    reproduced from inputs rather than copied into config."""
    plan = plan_pages(front_matter_pages=5, maze_count=50, layout=layout())
    assert plan.total_pages == 112
    assert plan.page(6).kind == KIND_STORY and plan.page(6).side == "left"
    assert plan.page(7).kind == KIND_MAZE and plan.page(7).side == "right"
    assert plan.page(105).kind == KIND_MAZE and plan.page(105).maze_index == 50
    assert [p.page_number for p in plan.of_kind(KIND_SOLUTIONS)] == list(range(106, 112))
    assert plan.page(112).kind == KIND_END


def test_the_end_page_is_the_last_page_in_the_book() -> None:
    """Printed before the answer key it tells the reader the book is over six
    pages early, and the last thing they see is a part-filled grid of
    thumbnails."""
    plan = plan_pages(front_matter_pages=5, maze_count=50, layout=layout())
    ends = plan.of_kind(KIND_END)
    assert len(ends) == 1
    assert ends[0].page_number == plan.total_pages
    last_solution = max(p.page_number for p in plan.of_kind(KIND_SOLUTIONS))
    assert ends[0].page_number > last_solution


def test_solution_pages_cover_every_maze_exactly_once() -> None:
    plan = plan_pages(front_matter_pages=5, maze_count=50, layout=layout())
    covered = [i for page in plan.of_kind(KIND_SOLUTIONS) for i in page.solution_indices]
    assert covered == list(range(1, 51))
    assert len(plan.of_kind(KIND_SOLUTIONS)) == 6


def test_facing_pairs_share_an_id_and_are_adjacent() -> None:
    plan = plan_pages(front_matter_pages=5, maze_count=3, layout=layout())
    stories = plan.of_kind(KIND_STORY)
    mazes = plan.of_kind(KIND_MAZE)
    for story, maze in zip(stories, mazes):
        assert story.facing_pair_id == maze.facing_pair_id
        assert maze.page_number == story.page_number + 1
    assert stories[0].facing_pair_id == "scene-001"


def test_every_page_records_the_side_parity_dictates() -> None:
    plan = plan_pages(front_matter_pages=5, maze_count=20, layout=layout())
    for page in plan.pages:
        assert page.side == ("left" if page.page_number % 2 == 0 else "right")


# ---------------------------------------------------------------------------
# 18.7: front matter parity
# ---------------------------------------------------------------------------


def test_odd_front_matter_needs_no_parity_blank() -> None:
    plan = plan_pages(front_matter_pages=5, maze_count=2, layout=layout())
    assert plan.of_kind(KIND_BLANK) == [] or all(
        page.page_number > 6 for page in plan.of_kind(KIND_BLANK)
    )
    assert plan.page(6).kind == KIND_STORY


def test_even_front_matter_forces_a_parity_blank_before_scene_one() -> None:
    """18.7's whole point: with an even front matter, scene 1's story page would
    land on an odd (right) page and every spread in the book would be flipped."""
    plan = plan_pages(front_matter_pages=4, maze_count=2, layout=layout())
    assert plan.page(5).kind == KIND_BLANK
    assert plan.page(6).kind == KIND_STORY and plan.page(6).side == "left"


def test_even_front_matter_without_blank_insertion_fails_loudly() -> None:
    with pytest.raises(ConfigError, match=r"must be left") as excinfo:
        plan_pages(
            front_matter_pages=4, maze_count=2,
            layout=layout(insert_blank_pages_for_parity=False),
        )
    assert "17.10 forbids silently flipping a pair" in "\n".join(excinfo.value.details)


@pytest.mark.parametrize("front", [1, 3, 5, 7, 9])
def test_any_odd_front_matter_puts_scene_one_directly_after_it(front: int) -> None:
    plan = plan_pages(front_matter_pages=front, maze_count=5, layout=layout())
    assert plan.page(front + 1).kind == KIND_STORY
    assert plan.of_kind(KIND_FRONT_MATTER)[-1].page_number == front


# ---------------------------------------------------------------------------
# Even total page count
# ---------------------------------------------------------------------------


def test_the_book_always_ends_on_an_even_page() -> None:
    """18.8 requires an even count. A printer folds sheets, not pages: an odd
    count gets a blank whose position nobody chose."""
    for maze_count in range(1, 15):
        plan = plan_pages(front_matter_pages=5, maze_count=maze_count, layout=layout())
        assert plan.total_pages % 2 == 0, maze_count


def test_a_trailing_blank_is_added_only_when_needed() -> None:
    with_blank = plan_pages(front_matter_pages=5, maze_count=1, layout=layout())
    assert with_blank.pages[-1].kind in (KIND_BLANK, KIND_SOLUTIONS)
    assert with_blank.total_pages % 2 == 0


# ---------------------------------------------------------------------------
# Configuration rejections
# ---------------------------------------------------------------------------


def test_story_and_maze_on_the_same_side_is_rejected() -> None:
    with pytest.raises(ConfigError, match=r"a facing pair needs opposite sides"):
        plan_pages(
            front_matter_pages=5, maze_count=2,
            layout=layout(story_page_side="left", maze_page_side="left"),
        )


def test_a_mismatched_expected_page_count_fails_rather_than_reconciling() -> None:
    with pytest.raises(ConfigError, match=r"page plan failed validation") as excinfo:
        plan_pages(
            front_matter_pages=5, maze_count=50, layout=layout(expected_page_count=110)
        )
    assert any("expectedPageCount is 110" in d for d in excinfo.value.details)


def test_a_matching_expected_page_count_passes() -> None:
    plan = plan_pages(
        front_matter_pages=5, maze_count=50, layout=layout(expected_page_count=112)
    )
    assert plan.total_pages == 112


@pytest.mark.parametrize("maze_count", [0, -1])
def test_a_non_positive_maze_count_is_rejected(maze_count: int) -> None:
    with pytest.raises(ConfigError, match=r"mazeCount must be at least 1"):
        plan_pages(front_matter_pages=5, maze_count=maze_count, layout=layout())


def test_a_negative_front_matter_count_is_rejected() -> None:
    with pytest.raises(ConfigError, match=r"must not be negative"):
        plan_pages(front_matter_pages=-1, maze_count=5, layout=layout())


# ---------------------------------------------------------------------------
# Variations
# ---------------------------------------------------------------------------


def test_no_end_page_when_the_layout_omits_it() -> None:
    plan = plan_pages(
        front_matter_pages=5, maze_count=50, layout=layout(insert_end_page=False)
    )
    assert plan.of_kind(KIND_END) == []
    assert plan.total_pages % 2 == 0


def test_no_front_matter_at_all_still_plans_a_valid_book() -> None:
    """17.2: front matter is optional."""
    plan = plan_pages(front_matter_pages=0, maze_count=4, layout=layout())
    assert plan.of_kind(KIND_FRONT_MATTER) == []
    assert plan.page(1).kind == KIND_BLANK  # page 1 is a right page; story needs left
    assert plan.page(2).kind == KIND_STORY


def test_solutions_per_page_changes_the_page_count() -> None:
    six = plan_pages(front_matter_pages=5, maze_count=50, layout=layout(solutions_per_page=6))
    assert len(six.of_kind(KIND_SOLUTIONS)) == 9


def test_swapping_the_sides_swaps_the_spread() -> None:
    plan = plan_pages(
        front_matter_pages=4, maze_count=3,
        layout=layout(story_page_side="right", maze_page_side="left"),
    )
    stories = plan.of_kind(KIND_STORY)
    assert all(page.side == "right" for page in stories)
    assert all(page.page_number % 2 == 1 for page in stories)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_the_plan_serializes_the_record_shape_17_10_specifies(tmp_path) -> None:
    plan = plan_pages(front_matter_pages=5, maze_count=50, layout=layout())
    path = tmp_path / "page-plan.json"
    plan.write(path)
    obj = json.loads(path.read_text(encoding="utf-8"))

    assert obj["totalPages"] == 112
    assert obj["frontMatterPages"] == 5
    story = next(p for p in obj["pages"] if p["pageNumber"] == 6)
    assert story == {
        "pageNumber": 6,
        "side": "left",
        "kind": "story",
        "sceneNumber": 1,
        "facingPairId": "scene-001",
    }


def test_a_blank_page_record_carries_no_scene_or_pair() -> None:
    plan = plan_pages(front_matter_pages=4, maze_count=2, layout=layout())
    blank = plan.page(5).to_json_obj()
    assert blank == {"pageNumber": 5, "side": "right", "kind": "blank"}


# ---------------------------------------------------------------------------
# Front-matter page counting
# ---------------------------------------------------------------------------


def test_front_matter_page_count_reads_the_real_pdf(repo_root) -> None:
    """17.10: the count comes from the file, not from a number the user retyped
    into book.json and then let go stale."""
    pdf = repo_root / "books" / "jims-halloween-maze-adventure" / "front-matter.pdf"
    assert front_matter_page_count(pdf) == 5


def test_front_matter_page_count_is_zero_when_there_is_none() -> None:
    assert front_matter_page_count(None) == 0


def test_a_missing_front_matter_pdf_is_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError, match=r"frontMatterPdf does not exist"):
        front_matter_page_count(tmp_path / "nope.pdf")
