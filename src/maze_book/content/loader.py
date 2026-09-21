"""Scene text, wrapped and measured before anything is drawn (PRD 17.3, 18.6).

All story text lives in ``book.json`` under ``content.scenes`` (17.3), so there
is nothing to load from disk. What there *is* to do is turn a paragraph into the
exact lines a story page will print, and to decide -- once, up front -- whether
it fits.

18.6 fixes the story page at "body 16 pt centred, at most two lines". That is a
design constraint, not a hint: the page is deliberately mostly empty, and a third
line is what turns a spread that reads as designed into one that reads as thin.
So a scene whose text will not fit is a configuration error reported by scene
number, before any page is rendered. The alternatives are all worse -- silently
shrinking the type makes one spread typographically different from the other
forty-nine, and silently truncating loses the author's words.

Measuring uses the real font metrics of the face that will actually print, so
"fits" here means fits in the PDF, not in a character count that happens to be
close.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Sequence

from ..errors import ConfigError
from ..model.book_config import BookConfig, Scene
from ..rendering.svg_to_pdf import BODY_FONT, TITLE_FONT

#: 18.6's story-page type, in points. Overridable by a caller that has a
#: config knob for it; the defaults are the contract.
DEFAULT_NUMBER_SIZE = 28.0
DEFAULT_TITLE_SIZE = 34.0
DEFAULT_BODY_SIZE = 16.0
DEFAULT_BODY_LEADING_RATIO = 1.5
DEFAULT_MAX_BODY_LINES = 2


@dataclass(frozen=True, slots=True)
class SceneText:
    """One story page's text, already broken into the lines that will print."""

    number: int
    title: str
    body_lines: tuple[str, ...]
    page_vector: str | None = None

    @property
    def line_count(self) -> int:
        return len(self.body_lines)


def _measure(text: str, font: str, size: float) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return stringWidth(text, font, size)


def wrap_to_width(
    text: str, *, font: str, size: float, width: float
) -> list[str]:
    """Greedy wrap on whitespace, measured in the printing face.

    Greedy rather than optimal-fit: at two lines there is no meaningful raggedness
    to optimize, and a greedy break is the one a reader would predict.

    A single word too long for the line is kept whole and returned on its own
    line rather than hyphenated. The caller detects the overflow by measuring;
    breaking a word silently would hide a real problem inside a plausible page.
    """
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if _measure(candidate, font, size) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def normalize_text(text: str) -> str:
    """Collapse whitespace and normalize to NFC.

    NFC because the same accented character can be spelled two ways in JSON, and
    two spellings measure and render identically but hash differently -- which
    would break the byte-stability the cache depends on.
    """
    return " ".join(unicodedata.normalize("NFC", text).split())


def prepare_scene(
    scene: Scene,
    *,
    width: float,
    body_font: str = BODY_FONT,
    body_size: float = DEFAULT_BODY_SIZE,
    title_font: str = TITLE_FONT,
    title_size: float = DEFAULT_TITLE_SIZE,
    max_body_lines: int = DEFAULT_MAX_BODY_LINES,
) -> tuple[SceneText, list[str]]:
    """Wrap one scene. Returns the prepared text and any fit problems."""
    problems: list[str] = []

    title = normalize_text(scene.title).upper()
    if not title:
        problems.append(f"scene {scene.number}: title is empty")
    title_width = _measure(title, title_font, title_size)
    if title_width > width:
        problems.append(
            f"scene {scene.number}: title {title!r} is {title_width:.0f} pt wide at "
            f"{title_size:g} pt, {width:.0f} pt available -- 18.6 centres it on one line"
        )

    body = normalize_text(scene.text)
    if not body:
        problems.append(f"scene {scene.number}: text is empty")
    lines = wrap_to_width(body, font=body_font, size=body_size, width=width)

    if len(lines) > max_body_lines:
        problems.append(
            f"scene {scene.number}: text wraps to {len(lines)} lines at {body_size:g} pt, "
            f"limit {max_body_lines} (18.6) -- shorten the text by roughly "
            f"{_estimate_excess(lines, max_body_lines)} characters"
        )
    for line in lines:
        measured = _measure(line, body_font, body_size)
        if measured > width:
            problems.append(
                f"scene {scene.number}: the word {line.split()[0]!r} alone is "
                f"{measured:.0f} pt wide, wider than the {width:.0f} pt line"
            )

    return (
        SceneText(
            number=scene.number,
            title=title,
            body_lines=tuple(lines),
            page_vector=scene.page_vector,
        ),
        problems,
    )


def _estimate_excess(lines: Sequence[str], limit: int) -> int:
    overflow = " ".join(lines[limit:])
    return len(overflow) + 1 if overflow else 0


def prepare_scenes(
    config: BookConfig,
    *,
    width: float,
    body_font: str = BODY_FONT,
    body_size: float = DEFAULT_BODY_SIZE,
    title_font: str = TITLE_FONT,
    title_size: float = DEFAULT_TITLE_SIZE,
    max_body_lines: int = DEFAULT_MAX_BODY_LINES,
) -> list[SceneText]:
    """Prepare every scene, reporting *all* fit problems in one raise.

    One raise listing every scene, because fixing story text is an editing pass:
    being told about scene 3, fixing it, and then being told about scene 17 turns
    one pass into as many passes as there are long scenes.
    """
    prepared: list[SceneText] = []
    problems: list[str] = []
    for scene in config.scenes:
        text, scene_problems = prepare_scene(
            scene,
            width=width,
            body_font=body_font, body_size=body_size,
            title_font=title_font, title_size=title_size,
            max_body_lines=max_body_lines,
        )
        prepared.append(text)
        problems.extend(scene_problems)

    if problems:
        raise ConfigError(
            f"{len(problems)} story-text fit problem(s) in {config.book.id}",
            details=problems,
        )
    return prepared


def page_vector_path(config: BookConfig, scene_text: SceneText):
    """Resolve a scene's decorative vector, or ``None`` when it has none.

    17.2: "The repository MUST support a book with no page-vectors folder."
    """
    if scene_text.page_vector is None:
        return None
    directory = config.page_vectors_dir
    if directory is None:
        raise ConfigError(
            f"scene {scene_text.number} sets pageVector but assets.pageVectorsDir is null"
        )
    return directory / scene_text.page_vector
