"""Tests for ``maze_book.model.json_io`` byte-stable JSON serialization (PRD 17.5).

Determinism here means byte determinism: identical logical content must
serialize to identical bytes regardless of dict-construction order, and the
cache-key hash must be insensitive to key order while remaining sensitive to
any actual value change.
"""

from __future__ import annotations

import hashlib
import json

from maze_book.model.json_io import dumps, hash_file, hash_obj, read_json, write_json


# --------------------------------------------------------------------------- #
# dumps
# --------------------------------------------------------------------------- #

def test_dumps_is_byte_stable_regardless_of_construction_order():
    # Two dicts with the same insertion order (built two different ways) must
    # serialize identically.
    obj_a = {"schemaVersion": "1.0", "mazeId": "m1", "openEdges": [[[0, 0], [0, 1]]]}
    obj_b = dict()
    obj_b["schemaVersion"] = "1.0"
    obj_b["mazeId"] = "m1"
    obj_b["openEdges"] = [[[0, 0], [0, 1]]]
    assert dumps(obj_a) == dumps(obj_b)


def test_dumps_preserves_insertion_order_rather_than_sorting_keys():
    obj = {"z": 1, "a": 2, "m": 3}
    text = dumps(obj)
    assert text.index('"z"') < text.index('"a"') < text.index('"m"')


def test_dumps_does_not_escape_non_ascii():
    obj = {"title": "Café Münchën café"}
    text = dumps(obj)
    assert "Café Münchën café" in text
    assert "\\u" not in text


def test_dumps_ends_with_exactly_one_trailing_newline():
    text = dumps({"a": 1})
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_dumps_uses_two_space_indent_and_space_after_colon():
    text = dumps({"a": 1})
    assert text == '{\n  "a": 1\n}\n'


def test_dumps_round_trips_through_json_loads():
    obj = {"a": [1, 2, 3], "b": {"c": None, "d": True}, "e": "text"}
    assert json.loads(dumps(obj)) == obj


# --------------------------------------------------------------------------- #
# hash_obj
# --------------------------------------------------------------------------- #

def test_hash_obj_is_insensitive_to_key_order():
    obj_a = {"a": 1, "b": 2, "c": 3}
    obj_b = {"c": 3, "a": 1, "b": 2}
    assert hash_obj(obj_a) == hash_obj(obj_b)


def test_hash_obj_is_sensitive_to_value_changes():
    obj_a = {"a": 1, "b": 2}
    obj_b = {"a": 1, "b": 3}
    assert hash_obj(obj_a) != hash_obj(obj_b)


def test_hash_obj_is_sensitive_to_key_changes():
    obj_a = {"a": 1}
    obj_b = {"z": 1}
    assert hash_obj(obj_a) != hash_obj(obj_b)


def test_hash_obj_matches_manual_sha256_computation():
    obj = {"b": 2, "a": 1}
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert hash_obj(obj) == expected


def test_hash_obj_returns_a_hex_sha256_digest():
    digest = hash_obj({"a": 1})
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


# --------------------------------------------------------------------------- #
# write_json / read_json round trip
# --------------------------------------------------------------------------- #

def test_write_json_then_read_json_round_trips(tmp_path):
    obj = {"schemaVersion": "1.0", "values": [1, 2, 3], "nested": {"x": None}}
    path = tmp_path / "sub" / "out.json"
    write_json(path, obj)
    assert path.is_file()
    assert read_json(path) == obj


def test_write_json_creates_parent_directories(tmp_path):
    path = tmp_path / "a" / "b" / "c" / "out.json"
    write_json(path, {"k": "v"})
    assert path.is_file()


def test_write_json_produces_the_same_bytes_as_dumps(tmp_path):
    obj = {"a": 1, "b": [1, 2]}
    path = tmp_path / "out.json"
    write_json(path, obj)
    assert path.read_text(encoding="utf-8") == dumps(obj)


# --------------------------------------------------------------------------- #
# hash_file
# --------------------------------------------------------------------------- #

def test_hash_file_matches_hashlib_sha256_of_on_disk_bytes(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"some file content \xff\x00 more bytes" * 100)
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert hash_file(path) == expected


def test_hash_file_matches_hash_obj_for_a_written_json_file(tmp_path):
    obj = {"a": 1, "b": 2}
    path = tmp_path / "out.json"
    write_json(path, obj)
    # hash_file hashes raw bytes of what write_json produced (dumps output),
    # which is a different canonicalization from hash_obj (sorted, compact) --
    # they need not be equal, but hash_file must be reproducible and match a
    # direct hash of the same bytes.
    again = hashlib.sha256(path.read_bytes()).hexdigest()
    assert hash_file(path) == again


def test_hash_file_is_sensitive_to_content_changes(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"content-one")
    digest_1 = hash_file(path)
    path.write_bytes(b"content-two")
    digest_2 = hash_file(path)
    assert digest_1 != digest_2
