"""Byte-stable JSON serialization (PRD 17.5).

Determinism means *byte* determinism: identical inputs must produce identical
files, so a cache hit and a fresh build cannot silently differ. That requires a
fixed separator set, a trailing newline, no ASCII escaping (UTF-8 throughout),
and insertion-ordered keys -- the dataclass ``to_json_obj`` methods are written
in contract order, so keys are never sorted here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def dumps(obj: Any) -> str:
    """Serialize to canonical text: 2-space indent, no ASCII escapes, newline."""
    return json.dumps(obj, indent=2, ensure_ascii=False, separators=(",", ": ")) + "\n"


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(obj), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def hash_obj(obj: Any) -> str:
    """Content hash of a JSON-serializable object, for cache keys.

    Keys *are* sorted here: two configs that differ only in key order describe
    the same book and must hit the same cache entry.
    """
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
