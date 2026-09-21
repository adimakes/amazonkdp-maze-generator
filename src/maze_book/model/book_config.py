"""Loading and validating ``books/<book-id>/book.json`` (PRD 17.3).

This module is the only place that turns untrusted on-disk configuration into
typed objects. Everything downstream may assume a ``BookConfig`` is valid: paths
resolve inside the package, scene numbers are exactly ``1..mazeCount``, and page
sides are opposite. The three defences that matter are schema validation
(shape), cross-field checks (coherence), and path containment (safety).

Fields this module reads with a bare subscript are listed in the schema's
``required`` arrays, so a missing one is reported as a ``ConfigError`` by schema
validation before the read happens. Two of them are required *and* nullable --
``book.subtitle`` and ``assets.pageVectorsDir`` -- because ``null`` states that
the book has no subtitle and no page vectors, which is a decision, whereas an
absent key is only an omission. ``book.contentOrigin`` and all three of its keys
are required for the same reason in a stronger form: it is the KDP AI-disclosure
record, and a defaulted authorship claim on a published book is worse than a
failed build.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import ConfigError
from .json_io import read_json

SCHEMA_VERSION = "1.0"


# --------------------------------------------------------------------------- #
# Path safety
# --------------------------------------------------------------------------- #

def _reject_unsafe_spelling(rel: str, *, field_name: str) -> None:
    if rel == "":
        raise ConfigError(f"{field_name} must not be empty")
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        raise ConfigError(f"{field_name} must be a relative path, got {rel!r}")
    if "\\" in rel:
        raise ConfigError(f"{field_name} must use POSIX separators, got {rel!r}")
    parts = rel.split("/")
    if any(part == ".." for part in parts):
        raise ConfigError(f"{field_name} must not contain '..', got {rel!r}")
    if parts[-1] == "":
        raise ConfigError(f"{field_name} must not end with '/', got {rel!r}")


def safe_join(root: Path, rel: str, *, field_name: str) -> Path:
    """Resolve ``rel`` under ``root``, rejecting escapes and escaping symlinks.

    Both the spelling and the *resolved* location are checked: ``..`` never
    appears in a valid config, and a symlink whose target leaves the package is
    rejected even though its spelling looks innocent.
    """
    _reject_unsafe_spelling(rel, field_name=field_name)
    root_resolved = root.resolve()
    candidate = (root_resolved / rel).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ConfigError(
            f"{field_name} resolves outside the book package",
            details={"value": rel, "resolved": str(candidate), "package": str(root_resolved)},
        )
    return candidate


# --------------------------------------------------------------------------- #
# Typed sections
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ContentOrigin:
    text: str
    images: str
    translations: str


@dataclass(frozen=True)
class BookMeta:
    id: str
    title: str
    subtitle: str | None
    locale: str
    seed: int
    maze_count: int
    profile_id: str
    content_origin: ContentOrigin


@dataclass(frozen=True)
class SafeMargins:
    inside: float
    outside: float
    top: float
    bottom: float


@dataclass(frozen=True)
class PrintSpec:
    trim_width_in: float
    trim_height_in: float
    interior_bleed: bool
    color_mode: str
    paper: str
    safe_margins_in: SafeMargins
    gutter_in: float


@dataclass(frozen=True)
class GenerationSpec:
    #: Keep both endpoints on an outer row or column, so each can carry an
    #: opening in the border and a marker drawn outside it.
    endpoints_on_border: bool
    start_region: dict[str, int]
    finish_region: dict[str, int]
    max_attempts_per_maze: int
    base_generator: str
    exact_route_enumeration: bool


@dataclass(frozen=True)
class LayoutSpec:
    front_matter_pdf: str | None
    story_page_side: str
    maze_page_side: str
    maze_square_in: float
    tally_position: str
    show_maze_number: bool
    show_best_possible_score: bool
    solutions_per_page: int
    solutions_start_side: str
    insert_end_page: bool
    insert_blank_pages_for_parity: bool
    expected_page_count: int | None = None
    #: How many page-vector decorations to scatter on each maze page. 0 is off.
    maze_page_decorations: int = 0


@dataclass(frozen=True)
class AssetsSpec:
    beginning_vectors_dir: str
    ending_vectors_dir: str
    dead_end_vectors_dir: str
    collectible_vectors_dir: str
    page_vectors_dir: str | None
    start_asset: str
    finish_asset: str
    dead_end_asset_policy: str
    collectible_asset_policy: str


@dataclass(frozen=True)
class Scene:
    number: int
    title: str
    text: str
    page_vector: str | None = None


@dataclass(frozen=True)
class EndPage:
    """Wording for the optional end page (``layout.insertEndPage``).

    It lives in ``book.json`` rather than in ``src/`` because it is book content:
    "Now go and count your candy" is right for a Halloween candy book and wrong
    for anything else, and the engine is theme-agnostic by contract (17.2). A
    null ``content.endPage`` selects the neutral built-in wording.
    """

    title: str
    text: str = ""


def _end_page(raw: Any) -> "EndPage | None":
    if raw is None:
        return None
    return EndPage(title=raw["title"], text=raw.get("text", ""))


@dataclass(frozen=True)
class OutputsSpec:
    write_maze_json: bool
    write_analysis_json: bool
    write_maze_svg: bool
    write_standalone_maze_pdf: bool
    write_book_pdf: bool
    write_contact_sheet: bool
    write_preflight_report: bool
    write_editable_proof_pdf: bool


@dataclass
class BookConfig:
    package_dir: Path
    config_path: Path
    book: BookMeta
    print: PrintSpec
    generation: GenerationSpec
    layout: LayoutSpec
    assets: AssetsSpec
    scenes: list[Scene]
    end_page: EndPage | None
    outputs: OutputsSpec
    raw: dict[str, Any] = field(default_factory=dict)

    # ---- resolved paths -------------------------------------------------- #

    def dir_path(self, rel: str, *, field_name: str) -> Path:
        return safe_join(self.package_dir, rel, field_name=field_name)

    @property
    def beginning_vectors_dir(self) -> Path:
        return self.dir_path(self.assets.beginning_vectors_dir, field_name="assets.beginningVectorsDir")

    @property
    def ending_vectors_dir(self) -> Path:
        return self.dir_path(self.assets.ending_vectors_dir, field_name="assets.endingVectorsDir")

    @property
    def dead_end_vectors_dir(self) -> Path:
        return self.dir_path(self.assets.dead_end_vectors_dir, field_name="assets.deadEndVectorsDir")

    @property
    def collectible_vectors_dir(self) -> Path:
        return self.dir_path(self.assets.collectible_vectors_dir, field_name="assets.collectibleVectorsDir")

    @property
    def page_vectors_dir(self) -> Path | None:
        if self.assets.page_vectors_dir is None:
            return None
        return self.dir_path(self.assets.page_vectors_dir, field_name="assets.pageVectorsDir")

    @property
    def start_asset_path(self) -> Path:
        return safe_join(
            self.beginning_vectors_dir, self.assets.start_asset, field_name="assets.startAsset"
        )

    @property
    def finish_asset_path(self) -> Path:
        return safe_join(
            self.ending_vectors_dir, self.assets.finish_asset, field_name="assets.finishAsset"
        )

    @property
    def front_matter_path(self) -> Path | None:
        if self.layout.front_matter_pdf is None:
            return None
        return safe_join(
            self.package_dir, self.layout.front_matter_pdf, field_name="layout.frontMatterPdf"
        )

    def scene_for(self, maze_index: int) -> Scene:
        return self.scenes[maze_index - 1]

    def page_vector_path(self, scene: Scene) -> Path | None:
        if scene.page_vector is None:
            return None
        directory = self.page_vectors_dir
        if directory is None:
            raise ConfigError(
                f"scene {scene.number} sets pageVector but assets.pageVectorsDir is null"
            )
        return safe_join(
            directory, scene.page_vector, field_name=f"content.scenes[{scene.number}].pageVector"
        )


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

_OPPOSITE = {"left": "right", "right": "left"}


def _validate_schema(obj: dict[str, Any], schema_path: Path) -> None:
    import jsonschema

    schema = read_json(schema_path)
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path))
    if errors:
        details = [
            f"{'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}"
            for error in errors[:20]
        ]
        raise ConfigError(
            f"book.json failed schema validation ({len(errors)} error(s))", details=details
        )


def _check_cross_fields(obj: dict[str, Any], package_dir: Path) -> None:
    problems: list[str] = []
    book = obj["book"]
    layout = obj["layout"]
    scenes = obj["content"]["scenes"]

    if book["id"] != package_dir.name:
        problems.append(
            f"book.id '{book['id']}' does not match folder name '{package_dir.name}'"
        )

    maze_count = book["mazeCount"]
    if len(scenes) != maze_count:
        problems.append(
            f"content.scenes has {len(scenes)} entries but book.mazeCount is {maze_count}"
        )

    numbers = [scene["number"] for scene in scenes]
    expected = list(range(1, len(scenes) + 1))
    if numbers != expected:
        duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
        if duplicates:
            problems.append(f"scene numbers are not unique: duplicated {duplicates}")
        else:
            problems.append(
                "scene numbers must be consecutive 1..mazeCount in order; "
                f"first mismatch at position {next(i for i, (a, b) in enumerate(zip(numbers, expected)) if a != b) + 1}"
            )

    if _OPPOSITE.get(layout["storyPageSide"]) != layout["mazePageSide"]:
        problems.append(
            f"layout.storyPageSide '{layout['storyPageSide']}' and mazePageSide "
            f"'{layout['mazePageSide']}' must be opposite sides"
        )

    if obj["print"]["interiorBleed"] is not False:
        problems.append("print.interiorBleed must be false for the v1 profile")

    # A maze square plus its margins has to fit the trim.
    printing = obj["print"]
    margins = printing["safeMarginsIn"]
    usable_w = printing["trimWidthIn"] - margins["inside"] - margins["outside"]
    usable_h = printing["trimHeightIn"] - margins["top"] - margins["bottom"]
    square = layout["mazeSquareIn"]
    if square > usable_w + 1e-9 or square > usable_h + 1e-9:
        problems.append(
            f"layout.mazeSquareIn {square} does not fit the usable area "
            f"{usable_w:.3f} x {usable_h:.3f} in"
        )

    if problems:
        raise ConfigError("book.json failed cross-field validation", details=problems)


def _check_paths(config: BookConfig, *, require_assets: bool) -> None:
    problems: list[str] = []
    dirs = {
        "assets.beginningVectorsDir": config.beginning_vectors_dir,
        "assets.endingVectorsDir": config.ending_vectors_dir,
        "assets.deadEndVectorsDir": config.dead_end_vectors_dir,
        "assets.collectibleVectorsDir": config.collectible_vectors_dir,
    }
    page_dir = config.page_vectors_dir
    if page_dir is not None:
        dirs["assets.pageVectorsDir"] = page_dir

    if require_assets:
        for name, path in dirs.items():
            if not path.is_dir():
                problems.append(f"{name} does not exist: {path}")
        for name, path in (
            ("assets.startAsset", config.start_asset_path),
            ("assets.finishAsset", config.finish_asset_path),
        ):
            if not path.is_file():
                problems.append(f"{name} does not exist: {path}")

        front = config.front_matter_path
        if front is not None and not front.is_file():
            problems.append(f"layout.frontMatterPdf does not exist: {front}")

        missing_vectors: list[str] = []
        for scene in config.scenes:
            path = config.page_vector_path(scene)
            if path is not None and not path.is_file():
                missing_vectors.append(f"scene {scene.number}: {path.name}")
        if missing_vectors:
            problems.append(
                f"{len(missing_vectors)} scene pageVector file(s) missing: "
                + ", ".join(missing_vectors[:5])
            )

    if problems:
        raise ConfigError("book.json references paths that do not exist", details=problems)


def load_book_config(
    path: Path,
    *,
    schema_path: Path | None = None,
    require_assets: bool = True,
) -> BookConfig:
    """Load ``book.json`` (or its containing directory) into a ``BookConfig``."""
    path = Path(path)
    if path.is_dir():
        path = path / "book.json"
    if not path.is_file():
        raise ConfigError(f"book config not found: {path}")

    package_dir = path.parent
    obj = read_json(path)

    if not isinstance(obj, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    if obj.get("schemaVersion") != SCHEMA_VERSION:
        raise ConfigError(
            f"unsupported schemaVersion {obj.get('schemaVersion')!r}; expected {SCHEMA_VERSION!r}"
        )

    if schema_path is None:
        schema_path = _default_schema_path()
    if schema_path.is_file():
        _validate_schema(obj, schema_path)

    _check_cross_fields(obj, package_dir)

    book = obj["book"]
    origin = book["contentOrigin"]
    printing = obj["print"]
    margins = printing["safeMarginsIn"]
    generation = obj["generation"]
    layout = obj["layout"]
    assets = obj["assets"]
    outputs = obj["outputs"]

    config = BookConfig(
        package_dir=package_dir,
        config_path=path,
        book=BookMeta(
            id=book["id"],
            title=book["title"],
            subtitle=book["subtitle"],
            locale=book["locale"],
            seed=int(book["seed"]),
            maze_count=int(book["mazeCount"]),
            profile_id=book["profileId"],
            content_origin=ContentOrigin(
                text=origin["text"],
                images=origin["images"],
                translations=origin["translations"],
            ),
        ),
        print=PrintSpec(
            trim_width_in=float(printing["trimWidthIn"]),
            trim_height_in=float(printing["trimHeightIn"]),
            interior_bleed=bool(printing["interiorBleed"]),
            color_mode=printing["colorMode"],
            paper=printing["paper"],
            safe_margins_in=SafeMargins(
                inside=float(margins["inside"]),
                outside=float(margins["outside"]),
                top=float(margins["top"]),
                bottom=float(margins["bottom"]),
            ),
            gutter_in=float(printing["gutterIn"]),
        ),
        generation=GenerationSpec(
            endpoints_on_border=bool(generation.get("endpointsOnBorder", True)),
            start_region=dict(generation["startRegion"]),
            finish_region=dict(generation["finishRegion"]),
            max_attempts_per_maze=int(generation["maxAttemptsPerMaze"]),
            base_generator=generation["baseGenerator"],
            exact_route_enumeration=bool(generation["exactRouteEnumeration"]),
        ),
        layout=LayoutSpec(
            front_matter_pdf=layout["frontMatterPdf"],
            story_page_side=layout["storyPageSide"],
            maze_page_side=layout["mazePageSide"],
            maze_square_in=float(layout["mazeSquareIn"]),
            tally_position=layout["tallyPosition"],
            show_maze_number=bool(layout["showMazeNumber"]),
            show_best_possible_score=bool(layout["showBestPossibleScore"]),
            solutions_per_page=int(layout["solutionsPerPage"]),
            solutions_start_side=layout["solutionsStartSide"],
            insert_end_page=bool(layout["insertEndPage"]),
            insert_blank_pages_for_parity=bool(layout["insertBlankPagesForParity"]),
            expected_page_count=(
                int(layout["expectedPageCount"]) if layout.get("expectedPageCount") is not None else None
            ),
            maze_page_decorations=int(layout.get("mazePageDecorations", 0)),
        ),
        assets=AssetsSpec(
            beginning_vectors_dir=assets["beginningVectorsDir"],
            ending_vectors_dir=assets["endingVectorsDir"],
            dead_end_vectors_dir=assets["deadEndVectorsDir"],
            collectible_vectors_dir=assets["collectibleVectorsDir"],
            page_vectors_dir=assets["pageVectorsDir"],
            start_asset=assets["startAsset"],
            finish_asset=assets["finishAsset"],
            dead_end_asset_policy=assets["deadEndAssetPolicy"],
            collectible_asset_policy=assets["collectibleAssetPolicy"],
        ),
        scenes=[
            Scene(
                number=int(scene["number"]),
                title=scene["title"],
                text=scene["text"],
                page_vector=scene.get("pageVector"),
            )
            for scene in obj["content"]["scenes"]
        ],
        end_page=_end_page(obj["content"]["endPage"]),
        outputs=OutputsSpec(
            write_maze_json=bool(outputs["writeMazeJson"]),
            write_analysis_json=bool(outputs["writeAnalysisJson"]),
            write_maze_svg=bool(outputs["writeMazeSvg"]),
            write_standalone_maze_pdf=bool(outputs["writeStandaloneMazePdf"]),
            write_book_pdf=bool(outputs["writeBookPdf"]),
            write_contact_sheet=bool(outputs["writeContactSheet"]),
            write_preflight_report=bool(outputs["writePreflightReport"]),
            write_editable_proof_pdf=bool(outputs["writeEditableProofPdf"]),
        ),
        raw=obj,
    )

    # Touching every resolved path here surfaces containment failures at load
    # time rather than deep inside rendering.
    _ = (
        config.beginning_vectors_dir,
        config.ending_vectors_dir,
        config.dead_end_vectors_dir,
        config.collectible_vectors_dir,
        config.page_vectors_dir,
        config.start_asset_path,
        config.finish_asset_path,
        config.front_matter_path,
    )
    for scene in config.scenes:
        config.page_vector_path(scene)

    _check_paths(config, require_assets=require_assets)
    return config


def _default_schema_path() -> Path:
    """Repository ``schemas/book.schema.json`` when running from a checkout."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "schemas" / "book.schema.json"
        if candidate.is_file():
            return candidate
    return here.parent / "book.schema.json"


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "schemas").is_dir() and (parent / "profiles").is_dir():
            return parent
    return here.parents[3]
