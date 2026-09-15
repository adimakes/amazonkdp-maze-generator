"""Page planning: a pure step that runs before any rendering (PRD 17.10, 18.7).

The planner decides what every physical page *is*, writes it out as
``page-plan.json``, and never draws anything. Keeping it pure is what makes
parity auditable: the plan can be checked, diffed and asserted against without
producing a 112-page PDF first.

Physical parity is normative and is the reason this module exists at all:

* even page numbers are ``left`` (verso), odd are ``right`` (recto);
* a story/maze pair is valid only when the two are adjacent and on the
  configured opposite sides;
* when the next page cannot start a pair on the required side, a blank is
  inserted if ``insertBlankPagesForParity`` allows it, and otherwise the planner
  **fails** -- 17.10 forbids silently flipping the pair.

18.7 adds the rule that makes this fragile in practice: front matter must have an
odd page count so scene 1's story page lands on an even page, and any front- or
back-matter change must be made **two pages at a time**, because a one-page
change flips every spread in the book. That is asserted here against the real
page count of the supplied PDF, never against a number retyped into config.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ..errors import ConfigError
from ..model.book_config import LayoutSpec
from ..rendering.page import SIDE_LEFT, SIDE_RIGHT, side_for_page

KIND_FRONT_MATTER = "front-matter"
KIND_BLANK = "blank"
KIND_STORY = "story"
KIND_MAZE = "maze"
KIND_END = "end"
KIND_SOLUTIONS = "solutions"


@dataclass(frozen=True, slots=True)
class PageRecord:
    page_number: int
    side: str
    kind: str
    scene_number: int | None = None
    maze_index: int | None = None
    facing_pair_id: str | None = None
    solution_indices: tuple[int, ...] = ()

    def to_json_obj(self) -> dict[str, Any]:
        obj: dict[str, Any] = {
            "pageNumber": self.page_number,
            "side": self.side,
            "kind": self.kind,
        }
        if self.scene_number is not None:
            obj["sceneNumber"] = self.scene_number
        if self.maze_index is not None:
            obj["mazeIndex"] = self.maze_index
        if self.facing_pair_id is not None:
            obj["facingPairId"] = self.facing_pair_id
        if self.solution_indices:
            obj["solutionIndices"] = list(self.solution_indices)
        return obj


@dataclass(slots=True)
class PagePlan:
    pages: list[PageRecord] = field(default_factory=list)
    front_matter_pages: int = 0

    @property
    def total_pages(self) -> int:
        return len(self.pages)

    def page(self, number: int) -> PageRecord:
        return self.pages[number - 1]

    def of_kind(self, kind: str) -> list[PageRecord]:
        return [page for page in self.pages if page.kind == kind]

    def to_json_obj(self) -> dict[str, Any]:
        return {
            "totalPages": self.total_pages,
            "frontMatterPages": self.front_matter_pages,
            "pages": [page.to_json_obj() for page in self.pages],
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_json_obj(), indent=2, sort_keys=False, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


class _Builder:
    def __init__(self, *, allow_blanks: bool) -> None:
        self.pages: list[PageRecord] = []
        self.allow_blanks = allow_blanks

    @property
    def next_number(self) -> int:
        return len(self.pages) + 1

    @property
    def next_side(self) -> str:
        return side_for_page(self.next_number)

    def add(self, kind: str, **kwargs: Any) -> PageRecord:
        record = PageRecord(
            page_number=self.next_number, side=self.next_side, kind=kind, **kwargs
        )
        self.pages.append(record)
        return record

    def align_to(self, side: str, *, reason: str) -> None:
        if self.next_side == side:
            return
        if not self.allow_blanks:
            raise ConfigError(
                f"{reason}: page {self.next_number} is a {self.next_side} page but must be "
                f"{side}, and layout.insertBlankPagesForParity is false",
                details=[
                    "17.10 forbids silently flipping a pair; either enable blank insertion "
                    "or change the front matter by two pages (18.7)",
                ],
            )
        self.add(KIND_BLANK)


def plan_pages(
    *,
    front_matter_pages: int,
    maze_count: int,
    layout: LayoutSpec,
) -> PagePlan:
    """Build the page plan. Raises ``ConfigError`` rather than bending parity.

    Everything configurable is read off ``layout``. An earlier signature also took
    ``solutions_per_page`` separately, which let a caller pass a value that
    disagreed with the very ``LayoutSpec`` it handed in -- two sources for one
    setting, and the planner silently believing the wrong one.
    """
    if maze_count < 1:
        raise ConfigError(f"book.mazeCount must be at least 1, got {maze_count}")
    if front_matter_pages < 0:
        raise ConfigError(f"front matter page count must not be negative, got {front_matter_pages}")
    solutions_per_page = layout.solutions_per_page
    if solutions_per_page < 1:
        raise ConfigError(
            f"layout.solutionsPerPage must be at least 1, got {solutions_per_page}"
        )
    if layout.story_page_side == layout.maze_page_side:
        raise ConfigError(
            f"layout.storyPageSide and layout.mazePageSide are both "
            f"{layout.story_page_side!r}; a facing pair needs opposite sides"
        )

    builder = _Builder(allow_blanks=layout.insert_blank_pages_for_parity)

    for _ in range(front_matter_pages):
        builder.add(KIND_FRONT_MATTER)

    builder.align_to(layout.story_page_side, reason="the first story page")

    for scene_number in range(1, maze_count + 1):
        pair_id = f"scene-{scene_number:03d}"
        builder.align_to(layout.story_page_side, reason=f"story page for scene {scene_number}")
        builder.add(KIND_STORY, scene_number=scene_number, facing_pair_id=pair_id)
        story = builder.pages[-1]
        builder.add(
            KIND_MAZE, scene_number=scene_number, maze_index=scene_number, facing_pair_id=pair_id
        )
        maze = builder.pages[-1]
        if maze.page_number != story.page_number + 1 or maze.side != layout.maze_page_side:
            raise ConfigError(
                f"scene {scene_number}: story page {story.page_number} ({story.side}) and "
                f"maze page {maze.page_number} ({maze.side}) do not form a valid facing pair"
            )

    if layout.insert_end_page:
        builder.add(KIND_END)

    builder.align_to(layout.solutions_start_side, reason="the first solutions page")

    remaining = maze_count
    index = 1
    while remaining > 0:
        take = min(solutions_per_page, remaining)
        builder.add(
            KIND_SOLUTIONS, solution_indices=tuple(range(index, index + take))
        )
        index += take
        remaining -= take

    # 18.8: the finished book must have an even page count. A printer folds
    # sheets, not pages, so an odd count is padded by the press -- with a blank
    # nobody chose the position of.
    if builder.next_number % 2 == 0:
        if not builder.allow_blanks:
            raise ConfigError(
                f"the book would have {len(builder.pages)} pages, which is odd, and "
                f"layout.insertBlankPagesForParity is false"
            )
        builder.add(KIND_BLANK)

    plan = PagePlan(pages=builder.pages, front_matter_pages=front_matter_pages)
    _check_plan(plan, layout=layout, maze_count=maze_count)
    return plan


def _check_plan(plan: PagePlan, *, layout: LayoutSpec, maze_count: int) -> None:
    problems: list[str] = []

    if plan.total_pages % 2 != 0:
        problems.append(f"total page count {plan.total_pages} is odd")

    for page in plan.pages:
        if page.side != side_for_page(page.page_number):
            problems.append(
                f"page {page.page_number} is recorded as {page.side} but parity makes it "
                f"{side_for_page(page.page_number)}"
            )

    stories = plan.of_kind(KIND_STORY)
    mazes = plan.of_kind(KIND_MAZE)
    if len(stories) != maze_count or len(mazes) != maze_count:
        problems.append(
            f"expected {maze_count} story and {maze_count} maze pages, found "
            f"{len(stories)} and {len(mazes)}"
        )
    for story, maze in zip(stories, mazes):
        if maze.page_number != story.page_number + 1:
            problems.append(
                f"{story.facing_pair_id}: maze page {maze.page_number} does not follow "
                f"story page {story.page_number}"
            )
        if story.side != layout.story_page_side or maze.side != layout.maze_page_side:
            problems.append(
                f"{story.facing_pair_id}: sides are {story.side}/{maze.side}, expected "
                f"{layout.story_page_side}/{layout.maze_page_side}"
            )

    covered = sorted(
        index for page in plan.of_kind(KIND_SOLUTIONS) for index in page.solution_indices
    )
    if covered != list(range(1, maze_count + 1)):
        problems.append(
            f"solution pages cover {len(covered)} maze(s); expected 1..{maze_count}"
        )

    expected = layout.expected_page_count
    if expected is not None and expected != plan.total_pages:
        problems.append(
            f"layout.expectedPageCount is {expected} but the plan needs {plan.total_pages}; "
            f"17.10 requires the assembler to fail rather than reconcile silently"
        )

    if problems:
        raise ConfigError(
            f"page plan failed validation ({len(problems)} problem(s))", details=problems
        )


def front_matter_page_count(path: Path | None) -> int:
    """Read the *actual* page count of a supplied front-matter PDF.

    17.10: "the assembler MUST read its actual page count with a PDF parser. The
    user MUST NOT have to duplicate that count in book.json." A retyped count is
    a number that silently goes stale the first time the front matter is edited,
    and the symptom is every spread in the book flipping.
    """
    if path is None:
        return 0
    from pypdf import PdfReader

    if not Path(path).is_file():
        raise ConfigError(f"layout.frontMatterPdf does not exist: {path}")
    reader = PdfReader(str(path))
    count = len(reader.pages)
    if count == 0:
        raise ConfigError(f"front matter PDF has no pages: {path}")
    return count
