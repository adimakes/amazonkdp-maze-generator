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
BODY_GAP_PT = 34.0
VECTOR_GAP_PT = 40.0
VECTOR_SIZE_IN = 0.6
ORNAMENT_SIZE_IN = 0.55
ORNAMENT_INSET_PT = 6.0


@dataclass(frozen=True, slots=True)
class StoryPageLayout:
    side: str
    centre_x: float
    number_baseline: float
    title_baseline: float
    body_baselines: tuple[float, ...]
    vector_box: Box | None
    ornament_box: Box | None


def plan_story_page(
    scene: SceneText,
    *,
    metrics: PageMetrics,
    side: str,
    has_vector: bool,
    has_ornament: bool = False,
    number_size: float = DEFAULT_NUMBER_SIZE,
    title_size: float = DEFAULT_TITLE_SIZE,
    body_size: float = DEFAULT_BODY_SIZE,
    body_leading_ratio: float = DEFAULT_BODY_LEADING_RATIO,
) -> StoryPageLayout:
    live_x0, live_y0, live_x1, live_y1 = metrics.live_box(side)
    centre_x = (live_x0 + live_x1) / 2.0

    number_baseline = NUMBER_TOP_IN * PT_PER_IN + number_size
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
    )


def draw_story_page(
    frame: PdfFrame,
    scene: SceneText,
    layout: StoryPageLayout,
    *,
    cache: AssetGeometryCache,
    vector_path: Path | None = None,
    ornament_path: Path | None = None,
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
