#!/usr/bin/env python3
"""Turn supplied artwork into bitonal PNG assets, keeping the drawing.

The vector route (``tools/vectorize.py``) thresholds, applies morphology and
fits curves, and each of those steps throws away detail the illustrator drew.
At icon size the result validated cleanly and looked nothing like the source: a
ghost with no eyes, a pumpkin bucket with no face, a spider with no legs.

This does the one step that is unavoidable -- deciding which pixels are ink --
and then stops. Otsu picks the threshold from the image's own histogram, the
result is cropped to the ink, padded to a centred square, and written as a 1-bit
PNG. What comes out is the drawing, at whatever resolution the source had.

Run with:  uv run python tools/import_rasters.py books/<book-id>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

from maze_book.assets.raster import check_raster, load_raster  # noqa: E402
from vectorize import despeckle, otsu_threshold  # noqa: E402

#: Padding around the artwork, as a fraction of the final square. Matches the
#: subset's 6-of-100 with room to spare, so vector and raster assets in one book
#: sit the same way in their cells.
PADDING_FRACTION = 0.07

#: What each asset is stored at, as a multiple of press resolution. Twice 300
#: is enough for a RIP to land a clean edge and little enough that it is not
#: throwing most of the file away: an icon kept at a flat 1400 px is 2950 dpi at
#: its printed size, and every one of those pixels gets resampled back out
#: again, with the averaging that produces showing up as grey along every edge.
STORAGE_DPI = 600.0

#: Ceiling, for a book whose layout asks for something very large.
MAX_SIDE = 2000


def to_bitonal(path: Path, *, padding: float = PADDING_FRACTION, side_px: int = MAX_SIDE):
    """Threshold, crop to the ink, pad to a centred square, return a 1-bit image."""
    with Image.open(path) as source:
        grey = source.convert("L")
        grey.load()

    pixels = np.asarray(grey)
    ink = despeckle(pixels < otsu_threshold(pixels))
    rows = np.flatnonzero(ink.any(axis=1))
    cols = np.flatnonzero(ink.any(axis=0))
    if not rows.size or not cols.size:
        raise ValueError(f"{path.name}: no ink found after thresholding")

    cropped = grey.crop((int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1))

    # Square first, then pad, so the artwork keeps its proportions and lands in
    # the middle of the square the renderer will scale into.
    artwork = max(cropped.size)
    side = int(round(artwork / (1.0 - 2.0 * padding)))
    square = Image.new("L", (side, side), 255)
    square.paste(cropped, ((side - cropped.width) // 2, (side - cropped.height) // 2))

    if side != side_px:
        square = square.resize((side_px, side_px), Image.LANCZOS)

    # Threshold again after any resample: LANCZOS reintroduces grey along every
    # edge, and grey is what a monochrome press turns into a halftone.
    return square.point(lambda value: 0 if value < 160 else 255).convert("1")


def import_assets(manifest_path: Path, package: Path, *, only: str | None = None):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_dir = manifest_path.parent
    sizes = json.loads((package / "book.json").read_text(encoding="utf-8"))
    printed = _printed_inches(sizes)
    ok = bad = 0

    for entry in manifest["assets"]:
        target = (package / "assets" / entry["target"]).with_suffix(".png")
        if only and only not in entry["target"]:
            continue
        role = _role_of(entry["target"])
        side_px = min(
            MAX_SIDE, max(300, int(round(printed.get(role, 1.0) * STORAGE_DPI)))
        )
        try:
            image = to_bitonal(source_dir / entry["source"], side_px=side_px)
        except Exception as exc:  # noqa: BLE001 - the message is the report
            print(f"!! {entry['target']}: {exc}")
            bad += 1
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, optimize=True)

        asset = load_raster(target)
        problems = check_raster(asset, printed_inches=printed.get(role))
        mark = "ok " if not problems else "!! "
        print(
            f"{mark}{target.relative_to(package / 'assets')!s:<40} "
            f"{asset.side}px  ink {asset.coverage_fraction:.0%}  "
            f"{asset.effective_dpi(printed.get(role, 1.0)):.0f} dpi at "
            f"{printed.get(role, 0):.2f} in"
        )
        for problem in problems:
            print(f"      {problem}")
        ok, bad = (ok + 1, bad) if not problems else (ok, bad + 1)

    return ok, bad


ROLE_BY_FOLDER = {
    "maze-vectors/collectibles": "collectible",
    "maze-vectors/dead-end": "dead-end",
    "beginning-vectors": "start",
    "ending-vectors": "finish",
    "page-vectors": "page-vector",
}


def _role_of(target: str) -> str:
    return ROLE_BY_FOLDER[str(Path(target).parent).replace("\\", "/")]


def _printed_inches(book: dict) -> dict[str, float]:
    """The largest printed size each role reaches, in inches.

    Largest, not smallest: resolution has to hold at the biggest placement, and
    the same file is used at every band.
    """
    from maze_book.model.profile import load_profile
    from maze_book.rendering.story_page import VECTOR_SIZE_IN

    profile = load_profile(REPO / "profiles", book["book"]["profileId"])
    square = float(book["layout"]["mazeSquareIn"])
    bands = profile.bands
    largest_cell = max(square / max(b.rows, b.cols) for b in bands)
    marker = max(b.outside_marker_fraction * square for b in bands)
    return {
        "collectible": largest_cell * max(b.collectible_scale for b in bands),
        "dead-end": largest_cell * max(b.dead_end_scale for b in bands),
        "start": max(marker, 2.4),   # also the title page figure
        "finish": max(marker, 2.0),  # also the end page figure
        "page-vector": VECTOR_SIZE_IN,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("package", type=Path)
    parser.add_argument("--manifest", type=Path, default=REPO / "artifacts" / "asset-manifest.json")
    parser.add_argument("--only")
    args = parser.parse_args(argv)
    ok, bad = import_assets(args.manifest, args.package, only=args.only)
    print(f"\n{ok} asset(s) imported and valid, {bad} failed")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
