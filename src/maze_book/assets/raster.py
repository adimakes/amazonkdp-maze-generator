"""Raster assets: bitonal PNGs placed at a known printed size (PRD 17.9, 18.5).

The SVG subset exists because a vector icon scales to any size. It is the right
contract for artwork *drawn* as flat shapes. It is the wrong one for artwork
that arrives as a picture: tracing a drawing into the subset means thresholding,
morphology and curve fitting, and each of those throws away the thing the
illustrator drew. A ghost loses its eyes, a jack-o'-lantern bucket loses its
face, a spider loses its legs -- and the result still validates, because every
rule it breaks is a rule about lines the drawing no longer has.

So a book may ship either. A raster asset is judged on the two questions that
actually decide whether it prints:

* is every pixel pure black or pure white, so the press has no tint to halftone;
* are there enough pixels for the size it prints at.

Everything else -- padding, centring, squareness -- is the same composition rule
the subset already states, measured on pixels instead of on a viewBox.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..errors import AssetError

#: Extensions this module handles. PNG only: JPEG is lossy, and lossy artefacts
#: around a hard black edge are exactly the grey fringe the press turns into a
#: halftone.
RASTER_SUFFIXES = (".png",)

#: What a print-on-demand press resolves. KDP asks 300; line art is cheap to
#: store at more, and 1-bit data compresses far better than the number suggests.
MIN_EFFECTIVE_DPI = 300.0


def is_raster(path: Path | str) -> bool:
    return Path(path).suffix.lower() in RASTER_SUFFIXES


@dataclass(frozen=True, slots=True)
class RasterAsset:
    """One bitonal image, measured once."""

    path: Path
    width: int
    height: int
    #: Ink bounding box in pixels, as (left, top, right, bottom), right/bottom
    #: exclusive.
    ink_box: tuple[int, int, int, int]
    ink_pixels: int

    @property
    def side(self) -> int:
        return max(self.width, self.height)

    @property
    def coverage_fraction(self) -> float:
        return self.ink_pixels / float(self.width * self.height or 1)

    def padding_fraction(self) -> tuple[float, float, float, float]:
        """Empty margin on each side, as a fraction of the image: L, T, R, B."""
        left, top, right, bottom = self.ink_box
        return (
            left / self.width,
            top / self.height,
            (self.width - right) / self.width,
            (self.height - bottom) / self.height,
        )

    def centre_offset_fraction(self) -> tuple[float, float]:
        """How far the ink's centre sits from the image's, per axis."""
        left, top, right, bottom = self.ink_box
        return (
            abs((left + right) / 2.0 - self.width / 2.0) / self.width,
            abs((top + bottom) / 2.0 - self.height / 2.0) / self.height,
        )

    def effective_dpi(self, printed_inches: float) -> float:
        return self.side / printed_inches if printed_inches > 0 else float("inf")


def load_raster(path: Path | str) -> RasterAsset:
    """Measure a raster asset, raising ``AssetError`` if it is not bitonal.

    Bitonality is checked on the decoded pixels rather than on the file's mode,
    because a greyscale PNG whose values happen to be 0 and 255 prints exactly
    like a 1-bit one and there is no reason to reject it.
    """
    import numpy as np
    from PIL import Image

    path = Path(path)
    try:
        with Image.open(path) as image:
            image.load()
            grey = image.convert("L")
    except OSError as error:  # pragma: no cover - unreadable file
        raise AssetError(f"{path.name}: cannot be read as an image ({error})") from error

    pixels = np.asarray(grey)
    values = np.unique(pixels)
    if values.size and not set(values.tolist()) <= {0, 255}:
        strays = [int(v) for v in values if v not in (0, 255)][:4]
        raise AssetError(
            f"{path.name}: has {values.size} grey level(s) including {strays}; "
            f"a raster asset must be pure black and white, because any level "
            f"between them halftones on a monochrome press"
        )

    ink = pixels == 0
    rows = np.flatnonzero(ink.any(axis=1))
    cols = np.flatnonzero(ink.any(axis=0))
    if not rows.size or not cols.size:
        raise AssetError(f"{path.name}: has no ink and would print blank")

    return RasterAsset(
        path=path,
        width=int(pixels.shape[1]),
        height=int(pixels.shape[0]),
        ink_box=(int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1),
        ink_pixels=int(ink.sum()),
    )


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #

#: Composition, as fractions of the image. These mirror the subset's 6-of-100
#: padding and 12-of-100 centring, so a book can mix vector and raster assets
#: without the two being held to different compositions.
MIN_PADDING_FRACTION = 0.04
MAX_CENTRE_OFFSET_FRACTION = 0.12
MAX_COVERAGE_FRACTION = 0.85
#: A square box is scaled by one factor; anything else distorts the drawing.
MAX_ASPECT_DRIFT = 0.02


def check_raster(
    asset: "RasterAsset", *, printed_inches: float | None = None
) -> list[str]:
    """Every rule this asset breaks, as messages. Never raises for a rule.

    ``printed_inches`` is the largest size the asset is placed at. Without it
    the resolution rule is skipped rather than guessed, because "enough pixels"
    is meaningless until you know how big it prints.
    """
    problems: list[str] = []

    if abs(asset.width - asset.height) > MAX_ASPECT_DRIFT * asset.side:
        problems.append(
            f"R1_square: {asset.width}x{asset.height}; a non-square asset is "
            f"scaled by one factor into a square box and comes out distorted"
        )

    padding = min(asset.padding_fraction())
    if padding < MIN_PADDING_FRACTION:
        problems.append(
            f"R2_padding: {padding:.1%} of the image at the tightest side, "
            f"minimum {MIN_PADDING_FRACTION:.0%} -- artwork touching the edge "
            f"runs into the cell's safe inset"
        )

    offset = max(asset.centre_offset_fraction())
    if offset > MAX_CENTRE_OFFSET_FRACTION:
        problems.append(
            f"R3_centered: ink centre is {offset:.1%} off the image centre, "
            f"limit {MAX_CENTRE_OFFSET_FRACTION:.0%} -- the asset is centred in "
            f"its cell, so a lopsided image lands lopsided on the page"
        )

    if asset.coverage_fraction > MAX_COVERAGE_FRACTION:
        problems.append(
            f"R4_coverage: {asset.coverage_fraction:.0%} ink, ceiling "
            f"{MAX_COVERAGE_FRACTION:.0%} -- this is what an inverted image or a "
            f"stray background looks like, and it would black out its cell"
        )

    if printed_inches is not None:
        dpi = asset.effective_dpi(printed_inches)
        if dpi < MIN_EFFECTIVE_DPI:
            problems.append(
                f"R5_resolution: {asset.side} px at {printed_inches:.2f} in is "
                f"{dpi:.0f} dpi, below the {MIN_EFFECTIVE_DPI:.0f} dpi a press "
                f"resolves -- the edges will show their pixels"
            )

    return problems
