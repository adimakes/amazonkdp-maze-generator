"""The story page: verso, even, and deliberately mostly empty (PRD 18.6).

18.6: "scene number 28 pt centred, 3.2 in from the top; title 34 pt all-caps
centred display face; body 16 pt centred, at most two lines, 1.5 line spacing;
one decorative page vector at 0.6 in centred below the body; optional corner
ornament in the outside top corner, story pages only. The page is deliberately
mostly empty -- white space is what makes a two-color interior read as designed
rather than thin."

The emptiness is the specification, not an omission, so nothing here grows to
fill the space. Text arrives already wrapped and already measured by
``content.loader``, which is where a scene that does not fit is rejected; by the
time drawing happens, every line is known to fit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..content.loader import (
    DEFAULT_BODY_LEADING_RATIO,
    DEFAULT_BODY_SIZE,
    DEFAULT_NUMBER_SIZE,
    DEFAULT_TITLE_SIZE,
    SceneText,
)
from .page import PT_PER_IN, Box, PageMetrics
from .svg import AssetGeometryCache
from .svg_to_pdf import BODY_FONT, PdfFrame, TITLE_FONT, place_document

NUMBER_TOP_IN = 3.2
TITLE_GAP_PT = 42.0

#: The act's name, above the scene number. The profile has named its acts since
#: it was written and none of the names reached a printed page, which left a
#: parent no way to see that the book ramps and a child no landmark between
#: maze 1 and maze 50.
ACT_SIZE = 10.5
ACT_GAP_PT = 16.0


def _tracked(text: str) -> str:
    """Letter-spacing, done with spaces because the subset has no tracking."""
    return " ".join(text)
BODY_GAP_PT = 34.0
VECTOR_GAP_PT = 40.0
VECTOR_SIZE_IN = 1.15
ORNAMENT_SIZE_IN = 0.55
#: The ornament sits *inside* the live area like everything else. A negative
#: inset put it 6 pt above the top safe margin the book declares: legal for the
#: press, which only cares about the 0.25 in trim band, but a page that breaks
#: its own contract, and in a spread the ornament visibly rode higher than the
#: page beside it.
ORNAMENT_INSET_PT = 0.0


@dataclass(frozen=True, slots=True)
class StoryPageLayout:
    side: str
    centre_x: float
    number_baseline: float
    title_baseline: float
    body_baselines: tuple[float, ...]
    vector_box: Box | None
    ornament_box: Box | None
    act_baseline: float | None = None


def plan_story_page(
    scene: SceneText,
    *,
    metrics: PageMetrics,
    side: str,
    has_vector: bool,
    has_ornament: bool = False,
    act_name: str | None = None,
    number_size: float = DEFAULT_NUMBER_SIZE,
    title_size: float = DEFAULT_TITLE_SIZE,
    body_size: float = DEFAULT_BODY_SIZE,
    body_leading_ratio: float = DEFAULT_BODY_LEADING_RATIO,
) -> StoryPageLayout:
    live_x0, live_y0, live_x1, live_y1 = metrics.live_box(side)
    centre_x = (live_x0 + live_x1) / 2.0

    number_baseline = NUMBER_TOP_IN * PT_PER_IN + number_size
    act_baseline = number_baseline - number_size - ACT_GAP_PT if act_name else None
    title_baseline = number_baseline + TITLE_GAP_PT + title_size

    leading = body_size * body_leading_ratio
    first_body = title_baseline + BODY_GAP_PT + body_size
    body_baselines = tuple(first_body + index * leading for index in range(len(scene.body_lines)))

    vector_box: Box | None = None
    if has_vector:
        size = VECTOR_SIZE_IN * PT_PER_IN
        top = (body_baselines[-1] if body_baselines else title_baseline) + VECTOR_GAP_PT
        vector_box = (centre_x - size / 2.0, top, centre_x + size / 2.0, top + size)

    ornament_box: Box | None = None
    if has_ornament:
        size = ORNAMENT_SIZE_IN * PT_PER_IN
        x = live_x0 if side == "left" else live_x1 - size
        ornament_box = (x, live_y0 - ORNAMENT_INSET_PT, x + size, live_y0 - ORNAMENT_INSET_PT + size)

    return StoryPageLayout(
        side=side,
        centre_x=centre_x,
        number_baseline=number_baseline,
        title_baseline=title_baseline,
        body_baselines=body_baselines,
        vector_box=vector_box,
        ornament_box=ornament_box,
        act_baseline=act_baseline,
    )


def draw_story_page(
    frame: PdfFrame,
    scene: SceneText,
    layout: StoryPageLayout,
    *,
    cache: AssetGeometryCache,
    vector_path: Path | None = None,
    ornament_path: Path | None = None,
    act_name: str | None = None,
    number_font: str = BODY_FONT,
    title_font: str = TITLE_FONT,
    body_font: str = BODY_FONT,
    number_size: float = DEFAULT_NUMBER_SIZE,
    title_size: float = DEFAULT_TITLE_SIZE,
    body_size: float = DEFAULT_BODY_SIZE,
) -> None:
    canvas = frame.canvas
    canvas.saveState()
    canvas.setFillGray(0.0)

    if layout.act_baseline is not None and act_name:
        # Pure black, letter-spaced. A grey would be the obvious way to make a
        # running head recede and it is the one thing this book cannot do:
        # 18.8 allows no tint between 0 and 1, because a tint halftones on a
        # monochrome press. Size and tracking do the same job in solid ink.
        canvas.setFont(number_font, ACT_SIZE)
        canvas.drawCentredString(
            layout.centre_x, frame.y(layout.act_baseline), _tracked(act_name.upper())
        )

    canvas.setFont(number_font, number_size)
    canvas.drawCentredString(layout.centre_x, frame.y(layout.number_baseline), str(scene.number))

    canvas.setFont(title_font, title_size)
    canvas.drawCentredString(layout.centre_x, frame.y(layout.title_baseline), scene.title)

    canvas.setFont(body_font, body_size)
    for line, baseline in zip(scene.body_lines, layout.body_baselines):
        canvas.drawCentredString(layout.centre_x, frame.y(baseline), line)

    canvas.restoreState()

    if layout.vector_box is not None and vector_path is not None:
        place_document(frame, cache.get(vector_path), layout.vector_box)
    if layout.ornament_box is not None and ornament_path is not None:
        place_document(frame, cache.get(ornament_path), layout.ornament_box)
