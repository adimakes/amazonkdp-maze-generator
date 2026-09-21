"""Turn supplied artwork into a book package's SVG assets.

Reads ``artifacts/asset-manifest.json`` and traces every entry through
``tools/vectorize.py`` into ``books/<book-id>/assets/``. Each file is validated
before it is written, with the *same* profile the build will judge it by, so a
failed trace reports rather than leaving an invalid asset in the package. The
run reports per file rather than aborting on the first failure, because fixing
artwork is a pass over all of it.

The rules an asset has to keep come from how big it prints, and that is read off
the book's own profile and layout rather than assumed here -- a collectible in
the 18x18 finale, an endpoint marker drawn outside the grid, and a 0.6 in story
decoration are three different sizes and therefore three different minimum
feature widths. 17.9: "the exact minimum feature size is profile data".

Run with:  uv run python tools/import_artifacts.py books/<book-id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vectorize import MODE_OUTLINE, MODE_SOLID, vectorize  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from maze_book.assets.validate import (  # noqa: E402
    AssetProfile,
    profiles_for_roles,
    validate_svg,
)
from maze_book.model.book_config import load_book_config  # noqa: E402
from maze_book.model.profile import load_profile  # noqa: E402
from maze_book.rendering.story_page import VECTOR_SIZE_IN  # noqa: E402

#: Which role a target folder puts an asset in. The folder names are the public,
#: case-sensitive contract of 17.2, so this map is the contract read backwards.
ROLE_BY_FOLDER = {
    "maze-vectors/collectibles": "collectible",
    "maze-vectors/dead-end": "dead-end",
    "beginning-vectors": "start",
    "ending-vectors": "finish",
    "page-vectors": "page-vector",
}


def role_of(target: str) -> str:
    folder = str(Path(target).parent).replace("\\", "/")
    try:
        return ROLE_BY_FOLDER[folder]
    except KeyError:
        raise SystemExit(f"{target}: {folder!r} is not one of {sorted(ROLE_BY_FOLDER)}")


def book_profiles(package: Path) -> dict[str, AssetProfile]:
    """The per-role profiles this book will actually validate against."""
    config = load_book_config(package / "book.json")
    profile = load_profile(REPO / "profiles", config.book.profile_id)
    return profiles_for_roles(
        bands=profile.bands,
        maze_square_in=config.layout.maze_square_in,
        page_vector_in=VECTOR_SIZE_IN,
    )


def trace_settings(profile: AssetProfile, mode: str) -> dict[str, object]:
    """Trace to the rule that will judge the result, with room to spare.

    Padding is the one number not taken from the profile: it is a composition
    choice, and a marker or decoration drawn edge to edge reads better with less
    of it than an icon that has to sit inside a cell.
    """
    return dict(
        max_subpaths=profile.max_subpaths,
        min_feature_units=profile.min_feature_units,
        padding=8.0 if mode == MODE_SOLID else 6.0,
        tolerance_units=0.35 if mode == MODE_SOLID else 0.25,
    )


def import_assets(
    manifest_path: Path, package: Path, *, only: str | None = None
) -> tuple[int, int]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_dir = manifest_path.parent
    by_role = book_profiles(package)
    ok = bad = 0

    for entry in manifest["assets"]:
        target = package / "assets" / entry["target"]
        if only and only not in entry["target"]:
            continue
        mode = entry.get("mode", MODE_SOLID)
        profile = by_role[role_of(entry["target"])]
        settings = trace_settings(profile, mode)
        settings.update(entry.get("options", {}))

        try:
            result = vectorize(
                source_dir / entry["source"],
                mode=mode,
                title=target.stem.replace("_", " "),
                **settings,
            )
        except Exception as exc:  # noqa: BLE001 - the message is the report
            print(f"!! {entry['target']}: tracing failed: {exc}")
            bad += 1
            continue

        # Judged before it is written. A failing trace that still lands in the
        # package turns one bad asset into a book that will not validate, and the
        # cause is then a directory listing away from the message that named it.
        report = validate_svg(target, profile=profile, text=result.svg)
        mark = "ok " if report.passed else "!! "
        print(
            f"{mark}{entry['target']:<46} {mode:<7} "
            f"min {profile.min_feature_units:.1f}u | {report.summary()}"
        )
        if not report.passed:
            bad += 1
            for error in report.errors:
                print(f"      {error}")
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result.svg, encoding="utf-8")
        ok += 1

    return ok, bad


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("package", type=Path, help="books/<book-id>")
    parser.add_argument("--manifest", type=Path, default=REPO / "artifacts" / "asset-manifest.json")
    parser.add_argument("--only", help="substring filter on the target path")
    args = parser.parse_args(argv)

    ok, bad = import_assets(args.manifest, args.package, only=args.only)
    print(f"\n{ok} asset(s) imported and valid, {bad} failed")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
