"""Deterministic derived seed streams (PRD 17.12).

Every stochastic decision draws from an independent stream derived from
``(book_seed, book_id, maze_index, attempt, purpose)``. Two consequences that
the whole reproducibility story rests on:

* Regenerating maze 7 cannot reshuffle maze 8, because the maze index is part of
  the derivation.
* Changing how many random draws the candy placer makes cannot move the
  topology, because ``purpose`` separates the streams.

``hash()`` is never used: CPython salts it per process, so it is not
reproducible across runs.
"""

from __future__ import annotations

import hashlib
import random

#: Distinct purposes keep the streams independent.
PURPOSE_TOPOLOGY = "topology"
PURPOSE_ENDPOINTS = "endpoints"
PURPOSE_CANDY = "candy"
PURPOSE_ASSET_VARIANT = "asset-variant"
PURPOSE_PAGE_DECORATION = "page-decoration"

_MASK = (1 << 64) - 1


def derive_seed(
    book_seed: int,
    book_id: str,
    maze_index: int,
    attempt: int,
    purpose: str,
) -> int:
    """Return a stable 64-bit seed for one (maze, attempt, purpose) stream."""
    payload = "\x1f".join(
        (str(book_seed), book_id, str(maze_index), str(attempt), purpose)
    ).encode("utf-8")
    digest = hashlib.blake2b(payload, digest_size=8).digest()
    return int.from_bytes(digest, "big") & _MASK


def rng(
    book_seed: int,
    book_id: str,
    maze_index: int,
    attempt: int,
    purpose: str,
) -> random.Random:
    """A private ``Random`` instance for one stream.

    Always use this instead of the ``random`` module-level functions, which share
    one global generator and would couple unrelated decisions together.
    """
    return random.Random(derive_seed(book_seed, book_id, maze_index, attempt, purpose))
