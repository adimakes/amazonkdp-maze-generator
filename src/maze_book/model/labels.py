"""The words the engine prints on its own behalf, in the book's language.

Scene text has always been book content. These are the rest: the marker words,
the tally prompt, the "Best possible" line and the folio -- furniture that
appears on every page, which is exactly why an English word left among them is
the first thing a Spanish or German buyer sees. The defaults are the wording the
English book shipped with, so a package that sets nothing prints what it always
printed.

The "Best possible" line is also what preflight reads back out of the finished
PDF (18.8), so its templates are the one label with a contract: each carries
``{n}`` exactly once, and :func:`find_best_possible` turns the pair back into
the numbers a reader sees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from ..errors import ConfigError

NUMBER_SLOT = "{n}"


@dataclass(frozen=True)
class Labels:
    start: str = "START"
    finish: str = "FINISH"
    tally_prompt: str = "Color in one box for every candy you collect"
    total: str = "Total"
    best_possible_one: str = "Best possible: {n} candy"
    best_possible_other: str = "Best possible: {n} candies"
    maze_number: str = "Maze {n}"
    #: The profile's band names are data about difficulty and stay in the
    #: profile; a translated book renames them here, keyed by the profile's own
    #: ``actName``, so three language editions can share one profile.
    act_names: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def best_possible(self, total: int) -> str:
        template = self.best_possible_one if total == 1 else self.best_possible_other
        return template.replace(NUMBER_SLOT, str(total))

    def maze_label(self, index: int) -> str:
        return self.maze_number.replace(NUMBER_SLOT, str(index))

    def act_name(self, profile_name: str) -> str:
        return self.act_names.get(profile_name, profile_name)


DEFAULT_LABELS = Labels()

#: book.json key -> Labels attribute, for the flat string fields.
_FIELDS = {
    "start": "start",
    "finish": "finish",
    "tallyPrompt": "tally_prompt",
    "total": "total",
    "mazeNumber": "maze_number",
}


def labels_from_json(raw: Mapping[str, Any] | None) -> Labels:
    """Build ``Labels`` from ``content.labels``; absent keys keep the default.

    The schema checks shape. What it cannot say is that a template without its
    number slot prints a score with no score in it, so that is checked here.
    """
    if not raw:
        return DEFAULT_LABELS
    values: dict[str, Any] = {
        attr: raw[key] for key, attr in _FIELDS.items() if key in raw
    }
    best = raw.get("bestPossible")
    if best is not None:
        values["best_possible_one"] = best["one"]
        values["best_possible_other"] = best["other"]
    if raw.get("actNames"):
        values["act_names"] = MappingProxyType(dict(raw["actNames"]))
    labels = Labels(**values)

    problems = [
        f"content.labels.{name} must contain {NUMBER_SLOT} exactly once: {template!r}"
        for name, template in (
            ("bestPossible.one", labels.best_possible_one),
            ("bestPossible.other", labels.best_possible_other),
            ("mazeNumber", labels.maze_number),
        )
        if template.count(NUMBER_SLOT) != 1
    ]
    if problems:
        raise ConfigError("content.labels is not usable", details=problems)
    return labels


def find_best_possible(labels: Labels, text: str) -> list[int]:
    """Every score printed in ``text`` with this book's "Best possible" wording.

    The number is anchored by the words in front of it, as the English check
    always was, and by the words after it only when nothing comes first -- a
    template like "{n} at most" has no other anchor. Whitespace is matched
    loosely because pdftotext is free to turn one space into several, or into
    a line break.
    """
    forms: list[str] = []
    for template in (labels.best_possible_other, labels.best_possible_one):
        before, after = template.split(NUMBER_SLOT)
        form = (
            _loose(before.strip()) + r"\s*(\d+)"
            if before.strip()
            else r"(\d+)\s*" + _loose(after.strip())
        )
        if form not in forms:
            forms.append(form)
    pattern = re.compile("|".join(forms))
    return [
        int(next(group for group in match.groups() if group is not None))
        for match in pattern.finditer(text)
    ]


def _loose(text: str) -> str:
    return r"\s+".join(re.escape(word) for word in text.split(" "))
