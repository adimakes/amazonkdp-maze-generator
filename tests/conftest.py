"""Shared fixtures for the model-layer test suite.

``repo_root`` anchors every schema/profile lookup back to the checkout root so
tests do not depend on the current working directory pytest happens to be
invoked from. ``tiny_book_dir`` points at the hand-authored fixture package
under ``tests/fixtures/books/tiny-child-book/``, which has the same
input/output/build shape as a real package. ``mutated_book`` copies that
package into a throwaway ``tmp_path`` and lets a test apply an arbitrary
mutation to the parsed ``book.json`` dict before it is written back — this is
the workhorse behind the negative book_config tests, each of which wants a
package that is identical to the known-good fixture except for one deliberate
defect.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "schemas").is_dir() and (parent / "profiles").is_dir():
            return parent
    raise RuntimeError("could not locate repository root from tests/conftest.py")


@pytest.fixture(scope="session")
def tiny_book_dir(repo_root: Path) -> Path:
    path = repo_root / "tests" / "fixtures" / "books" / "tiny-child-book"
    assert (path / "input" / "book.json").is_file(), (
        f"fixture book.json missing at {path}/input"
    )
    return path


@pytest.fixture
def mutated_book(tiny_book_dir: Path, tmp_path: Path) -> Callable[[Callable[[dict], None]], Path]:
    """Return a factory that builds a mutated copy of the tiny fixture package.

    ``factory(mutator)`` copies ``tiny_book_dir`` into ``tmp_path``, parses
    ``book.json``, calls ``mutator(obj)`` (which should mutate ``obj`` in
    place — the return value, if any, is ignored), writes the result back,
    and returns the path to the mutated package directory. Each call gets a
    fresh copy of the fixture, so tests may call the factory more than once
    with different mutators without interfering with each other.
    """

    counter = {"n": 0}

    def factory(mutator: Callable[[dict], None]) -> Path:
        counter["n"] += 1
        dest = tmp_path / f"mutated-{counter['n']}" / "tiny-child-book"
        shutil.copytree(tiny_book_dir, dest)
        config_path = dest / "input" / "book.json"
        obj = json.loads(config_path.read_text(encoding="utf-8"))
        mutator(obj)
        config_path.write_text(json.dumps(obj, indent=2), encoding="utf-8")
        return dest

    return factory
