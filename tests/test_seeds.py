"""Tests for ``maze_book.model.seeds`` derived-seed determinism (PRD 17.12).

The module's entire contract is that ``derive_seed`` is a pure function of its
five inputs -- reproducible across processes (unlike ``hash()``, which CPython
salts per-process) -- and sensitive to each input independently, so that
regenerating one maze/attempt/purpose can never disturb another stream.
"""

from __future__ import annotations

import subprocess
import sys

from maze_book.model.seeds import (
    PURPOSE_ASSET_VARIANT,
    PURPOSE_CANDY,
    PURPOSE_ENDPOINTS,
    PURPOSE_PAGE_DECORATION,
    PURPOSE_TOPOLOGY,
    derive_seed,
    rng,
)

_MASK = (1 << 64) - 1


# --------------------------------------------------------------------------- #
# In-process determinism
# --------------------------------------------------------------------------- #

def test_derive_seed_is_deterministic_in_process():
    args = (42, "tiny-child-book", 3, 0, PURPOSE_TOPOLOGY)
    assert derive_seed(*args) == derive_seed(*args)


def test_derive_seed_returns_a_64_bit_nonnegative_integer():
    seed = derive_seed(42, "tiny-child-book", 3, 0, PURPOSE_TOPOLOGY)
    assert isinstance(seed, int)
    assert 0 <= seed <= _MASK


def test_rng_is_deterministic_and_produces_a_random_instance():
    args = (42, "tiny-child-book", 3, 0, PURPOSE_CANDY)
    r1 = rng(*args)
    r2 = rng(*args)
    assert [r1.random() for _ in range(20)] == [r2.random() for _ in range(20)]


# --------------------------------------------------------------------------- #
# Cross-process determinism -- the entire reason hash() is forbidden.
# --------------------------------------------------------------------------- #

def _derive_seed_in_subprocess(book_seed, book_id, maze_index, attempt, purpose) -> int:
    code = (
        "from maze_book.model.seeds import derive_seed; "
        f"print(derive_seed({book_seed!r}, {book_id!r}, {maze_index!r}, {attempt!r}, {purpose!r}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout.strip())


def test_derive_seed_is_deterministic_across_processes():
    args = (42, "tiny-child-book", 3, 0, PURPOSE_TOPOLOGY)
    in_process = derive_seed(*args)
    cross_process_1 = _derive_seed_in_subprocess(*args)
    cross_process_2 = _derive_seed_in_subprocess(*args)
    assert in_process == cross_process_1 == cross_process_2


def test_derive_seed_cross_process_matches_for_several_streams():
    # Exercise a handful of distinct streams, not just one, to guard against a
    # coincidental single-value match.
    cases = [
        (1, "book-a", 1, 0, PURPOSE_TOPOLOGY),
        (1, "book-a", 2, 0, PURPOSE_TOPOLOGY),
        (99999, "book-b", 50, 3, PURPOSE_CANDY),
        (0, "x", 0, 0, PURPOSE_ENDPOINTS),
    ]
    for args in cases:
        assert derive_seed(*args) == _derive_seed_in_subprocess(*args)


# --------------------------------------------------------------------------- #
# Sensitivity to each input independently
# --------------------------------------------------------------------------- #

_BASE = (42, "tiny-child-book", 3, 0, PURPOSE_TOPOLOGY)


def test_derive_seed_sensitive_to_book_seed():
    base = derive_seed(*_BASE)
    changed = derive_seed(43, "tiny-child-book", 3, 0, PURPOSE_TOPOLOGY)
    assert base != changed


def test_derive_seed_sensitive_to_book_id():
    base = derive_seed(*_BASE)
    changed = derive_seed(42, "other-book", 3, 0, PURPOSE_TOPOLOGY)
    assert base != changed


def test_derive_seed_sensitive_to_maze_index():
    base = derive_seed(*_BASE)
    changed = derive_seed(42, "tiny-child-book", 4, 0, PURPOSE_TOPOLOGY)
    assert base != changed


def test_derive_seed_sensitive_to_attempt():
    base = derive_seed(*_BASE)
    changed = derive_seed(42, "tiny-child-book", 3, 1, PURPOSE_TOPOLOGY)
    assert base != changed


def test_derive_seed_sensitive_to_purpose():
    base = derive_seed(*_BASE)
    changed = derive_seed(42, "tiny-child-book", 3, 0, PURPOSE_CANDY)
    assert base != changed


def test_regenerating_one_maze_does_not_disturb_another():
    # This is the concrete promise from the module docstring: maze_index 7's
    # stream must differ from maze_index 8's, for every purpose.
    for purpose in (PURPOSE_TOPOLOGY, PURPOSE_ENDPOINTS, PURPOSE_CANDY):
        seed_7 = derive_seed(42, "book", 7, 0, purpose)
        seed_8 = derive_seed(42, "book", 8, 0, purpose)
        assert seed_7 != seed_8


# --------------------------------------------------------------------------- #
# Independence of different-purpose streams for the same maze
# --------------------------------------------------------------------------- #

def test_different_purpose_streams_are_independent_for_the_same_maze():
    purposes = [
        PURPOSE_TOPOLOGY,
        PURPOSE_ENDPOINTS,
        PURPOSE_CANDY,
        PURPOSE_ASSET_VARIANT,
        PURPOSE_PAGE_DECORATION,
    ]
    draws_by_purpose = {}
    for purpose in purposes:
        r = rng(42, "tiny-child-book", 3, 0, purpose)
        draws_by_purpose[purpose] = [r.random() for _ in range(20)]

    # No two distinct purposes should produce the same draw sequence.
    seen = list(draws_by_purpose.items())
    for i in range(len(seen)):
        for j in range(i + 1, len(seen)):
            (purpose_i, draws_i), (purpose_j, draws_j) = seen[i], seen[j]
            assert draws_i != draws_j, (
                f"purposes {purpose_i!r} and {purpose_j!r} produced identical draws"
            )
