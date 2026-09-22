"""Tests for ``maze_book.model.book_config`` (PRD 17.3).

Covers: a positive load of the tiny fixture with resolved paths, loading with
``require_assets=False`` when asset files are absent, the cross-field checks
in ``_check_cross_fields``, schema-level rejections, the explicit
pageVector/pageVectorsDir consistency check, the path-safety contract (unsafe
spelling caught at the schema layer for pattern-constrained fields, at the
Python ``safe_join``/``_reject_unsafe_spelling`` layer for fields with no
schema pattern, and a symlink escaping the package caught by the *resolved*
location check), and a documented KeyError-vs-ConfigError bug pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maze_book.errors import ConfigError
from maze_book.model.book_config import load_book_config


def _details_text(exc: ConfigError) -> str:
    """Flatten a ConfigError's ``details`` (list | dict | None) for substring checks.

    ``MazeBookError.__init__`` calls ``super().__init__(message)`` only, so
    ``str(exc)`` never includes ``details`` -- the wrapping messages used by
    ``_check_cross_fields``/``_validate_schema``/``_check_paths`` are generic
    ("book.json failed cross-field validation", etc.), and the actual reason
    lives only in ``.details``.
    """
    if exc.details is None:
        return ""
    if isinstance(exc.details, (list, tuple)):
        return "\n".join(str(d) for d in exc.details)
    return str(exc.details)


# ---------------------------------------------------------------------------
# Positive load
# ---------------------------------------------------------------------------


def test_load_tiny_fixture_with_required_assets_resolves_paths(tiny_book_dir: Path) -> None:
    config = load_book_config(tiny_book_dir, require_assets=True)

    assert config.book.id == "tiny-child-book"
    assert config.book.title == "Tiny Child Book"
    assert config.book.maze_count == 3
    assert config.book.profile_id == "child_6_7"
    assert config.book.content_origin.text == "human"

    assert config.beginning_vectors_dir == (tiny_book_dir / "input" / "assets" / "beginning-vectors").resolve()
    assert config.ending_vectors_dir == (tiny_book_dir / "input" / "assets" / "ending-vectors").resolve()
    assert config.dead_end_vectors_dir == (tiny_book_dir / "input" / "assets" / "maze-vectors" / "dead-end").resolve()
    assert config.collectible_vectors_dir == (
        tiny_book_dir / "input" / "assets" / "maze-vectors" / "collectibles"
    ).resolve()
    assert config.page_vectors_dir is None

    assert config.start_asset_path == (tiny_book_dir / "input" / "assets" / "beginning-vectors" / "start.svg").resolve()
    assert config.start_asset_path.is_file()
    assert config.finish_asset_path.is_file()
    assert config.front_matter_path is None

    assert len(config.scenes) == 3
    assert config.scene_for(2).number == 2
    assert config.scene_for(2).title == "Scene Two"


def test_require_assets_false_succeeds_despite_missing_asset_file(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        obj["assets"]["startAsset"] = "does_not_exist.svg"

    path = mutated_book(mutator)
    config = load_book_config(path, require_assets=False)
    assert config.start_asset_path.name == "does_not_exist.svg"
    assert not config.start_asset_path.is_file()


# ---------------------------------------------------------------------------
# Cross-field checks (_check_cross_fields)
# ---------------------------------------------------------------------------


def test_book_id_mismatch_with_folder_name_is_rejected(mutated_book) -> None:
    path = mutated_book(lambda obj: obj["book"].update(id="wrong-id"))
    with pytest.raises(ConfigError, match=r"cross-field validation") as excinfo:
        load_book_config(path)
    assert "does not match folder name" in _details_text(excinfo.value)


def test_scene_count_mismatch_with_maze_count_is_rejected(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        obj["content"]["scenes"].pop()  # 2 scenes left, mazeCount stays 3

    path = mutated_book(mutator)
    with pytest.raises(ConfigError, match=r"cross-field validation") as excinfo:
        load_book_config(path)
    assert "content.scenes has 2 entries but book.mazeCount is 3" in _details_text(excinfo.value)


def test_duplicate_scene_numbers_are_rejected(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        obj["content"]["scenes"][1]["number"] = 1  # duplicates scene 1

    path = mutated_book(mutator)
    with pytest.raises(ConfigError, match=r"cross-field validation") as excinfo:
        load_book_config(path)
    assert "scene numbers are not unique" in _details_text(excinfo.value)


def test_non_consecutive_scene_numbers_are_rejected(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        obj["content"]["scenes"][2]["number"] = 5  # was 3; leaves a gap

    path = mutated_book(mutator)
    with pytest.raises(ConfigError, match=r"cross-field validation") as excinfo:
        load_book_config(path)
    assert "scene numbers must be consecutive" in _details_text(excinfo.value)


def test_story_and_maze_page_side_must_be_opposite(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        obj["layout"]["mazePageSide"] = "left"  # storyPageSide is already "left"

    path = mutated_book(mutator)
    with pytest.raises(ConfigError, match=r"cross-field validation") as excinfo:
        load_book_config(path)
    assert "must be opposite sides" in _details_text(excinfo.value)


def test_maze_square_in_exceeding_usable_area_is_rejected(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        obj["layout"]["mazeSquareIn"] = 20.0  # trim is 8.5x11in; this cannot fit

    path = mutated_book(mutator)
    with pytest.raises(ConfigError, match=r"cross-field validation") as excinfo:
        load_book_config(path)
    assert "does not fit the usable area" in _details_text(excinfo.value)


# ---------------------------------------------------------------------------
# Schema-level rejections (_validate_schema / early schemaVersion check)
# ---------------------------------------------------------------------------


def test_interior_bleed_true_is_rejected_by_schema(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        obj["print"]["interiorBleed"] = True  # schema: {"const": false}

    path = mutated_book(mutator)
    with pytest.raises(ConfigError, match=r"schema validation") as excinfo:
        load_book_config(path)
    assert any("interiorBleed" in d for d in excinfo.value.details)


def test_unknown_top_level_field_is_rejected_by_schema(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        obj["bogusField"] = 1  # top-level additionalProperties: false

    path = mutated_book(mutator)
    with pytest.raises(ConfigError, match=r"schema validation") as excinfo:
        load_book_config(path)
    assert any("bogusField" in d for d in excinfo.value.details)


def test_unsupported_schema_version_is_rejected(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        obj["schemaVersion"] = "2.0"

    path = mutated_book(mutator)
    with pytest.raises(ConfigError, match=r"unsupported schemaVersion '2\.0'"):
        load_book_config(path)


def test_missing_start_asset_file_is_rejected_when_assets_required(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        obj["assets"]["startAsset"] = "does_not_exist.svg"

    path = mutated_book(mutator)
    with pytest.raises(ConfigError, match=r"references paths that do not exist") as excinfo:
        load_book_config(path, require_assets=True)
    assert any(d.startswith("assets.startAsset does not exist") for d in excinfo.value.details)


# ---------------------------------------------------------------------------
# Explicit Python-level consistency check (page_vector_path)
# ---------------------------------------------------------------------------


def test_scene_page_vector_without_page_vectors_dir_is_rejected(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        obj["content"]["scenes"][0]["pageVector"] = "foo.svg"  # assets.pageVectorsDir stays null

    path = mutated_book(mutator)
    with pytest.raises(
        ConfigError, match=r"scene 1 sets pageVector but assets\.pageVectorsDir is null"
    ):
        load_book_config(path)


# ---------------------------------------------------------------------------
# Path safety: unsafe spelling
#
# assets.beginningVectorsDir is schema-typed against $defs/relPath, whose
# pattern already excludes a leading "/" and any ".." path segment -- so an
# unsafe spelling there is caught at the JSON-Schema layer. layout.frontMatterPdf
# has no such pattern ({"type": ["string", "null"]}), so an unsafe spelling
# there can only be caught by the Python-level safe_join/_reject_unsafe_spelling
# defense. Both layers are exercised deliberately, on the field where each is
# actually the one doing the rejecting.
# ---------------------------------------------------------------------------


def test_absolute_beginning_vectors_dir_is_rejected_by_schema(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        obj["assets"]["beginningVectorsDir"] = "/etc"

    path = mutated_book(mutator)
    with pytest.raises(ConfigError, match=r"schema validation") as excinfo:
        load_book_config(path)
    assert any("beginningVectorsDir" in d for d in excinfo.value.details)


def test_dotdot_traversal_in_beginning_vectors_dir_is_rejected_by_schema(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        obj["assets"]["beginningVectorsDir"] = "assets/../../../etc"

    path = mutated_book(mutator)
    with pytest.raises(ConfigError, match=r"schema validation") as excinfo:
        load_book_config(path)
    assert any("beginningVectorsDir" in d for d in excinfo.value.details)


def test_absolute_front_matter_pdf_is_rejected_by_safe_join(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        obj["layout"]["frontMatterPdf"] = "/etc/passwd"

    path = mutated_book(mutator)
    with pytest.raises(ConfigError, match=r"layout\.frontMatterPdf must be a relative path"):
        load_book_config(path)


def test_dotdot_traversal_in_front_matter_pdf_is_rejected_by_safe_join(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        obj["layout"]["frontMatterPdf"] = "../../../etc/passwd"

    path = mutated_book(mutator)
    with pytest.raises(ConfigError, match=r"layout\.frontMatterPdf must not contain '\.\.'"):
        load_book_config(path)


def test_beginning_vectors_dir_symlink_escaping_package_is_rejected(mutated_book, tmp_path: Path) -> None:
    def mutator(obj: dict) -> None:
        obj["assets"]["beginningVectorsDir"] = "assets/escape-vectors"

    path = mutated_book(mutator)
    outside = tmp_path / "outside-target"
    outside.mkdir()
    link = path / "input" / "assets" / "escape-vectors"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not permitted in this environment")

    with pytest.raises(ConfigError, match=r"resolves outside the book package"):
        load_book_config(path)


# ---------------------------------------------------------------------------
# Fields the loader reads with a bare subscript must be in the schema's
# "required" arrays, or a fully schema-valid book.json crashes with a raw
# KeyError instead of the module's own ConfigError contract. These four were
# exactly that gap. The fix was to require them rather than to default them:
# book.contentOrigin is the KDP AI-disclosure record, so a quietly defaulted
# authorship claim would be worse than a failed build, and book.subtitle /
# assets.pageVectorsDir are required-but-nullable so that "no subtitle" and
# "no page vectors" stay decisions instead of omissions.
# ---------------------------------------------------------------------------


def test_missing_subtitle_is_rejected(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        del obj["book"]["subtitle"]

    path = mutated_book(mutator)
    with pytest.raises(ConfigError):
        load_book_config(path)


def test_missing_content_origin_is_rejected(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        del obj["book"]["contentOrigin"]

    path = mutated_book(mutator)
    with pytest.raises(ConfigError):
        load_book_config(path)


def test_incomplete_content_origin_is_rejected(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        obj["book"]["contentOrigin"] = {"text": "human"}

    path = mutated_book(mutator)
    with pytest.raises(ConfigError):
        load_book_config(path)


def test_missing_page_vectors_dir_is_rejected(mutated_book) -> None:
    def mutator(obj: dict) -> None:
        del obj["assets"]["pageVectorsDir"]

    path = mutated_book(mutator)
    with pytest.raises(ConfigError):
        load_book_config(path)
