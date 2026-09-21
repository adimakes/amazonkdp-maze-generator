"""Trace raster line art into the SVG subset the asset validator accepts.

The book package contract is SVG-only (PRD 17.9) and the subset is narrow (18.5):
filled paths, no strokes, at most 12 subpaths, and no feature or gap thinner than
7 units of a 100-unit viewBox. Supplied artwork is none of those things -- it
arrives as 2048px JPEGs of outlined icons -- so this converts, and the conversion
has to be opinionated rather than faithful.

Two modes, because two print sizes:

``solid``
    For anything that lands inside a maze cell: collectibles, dead-end markers,
    the start and finish. A finale collectible prints at about 5.5 mm, where an
    outlined icon's 4-unit stroke is 0.22 mm and the outline closes up into a
    grey smudge. So the silhouette is filled and only the interior holes wide
    enough to survive the press are kept -- a jack-o'-lantern keeps its face, a
    ghost keeps its eyes, and the hairline between two outline strokes goes away
    because at that size it was never going to print.

``outline``
    For story-page decoration, which prints at 0.6 in and upward. There the
    original line work survives, so it is kept as drawn.

The 12-subpath budget is spent on the largest contours by area; what falls off
the end is speckle and JPEG ringing, which is what the despeckle pass is for.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

MODE_SOLID = "solid"
MODE_OUTLINE = "outline"


# --------------------------------------------------------------------------- #
# Raster preparation
# --------------------------------------------------------------------------- #


def otsu_threshold(gray: np.ndarray) -> int:
    """Otsu's between-class variance threshold.

    Chosen over a fixed 128 because the supplied art is JPEG: compression puts a
    halo around every black stroke, and where the true threshold sits depends on
    how much white the particular image has.
    """
    histogram = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = histogram.sum()
    weight_bg = np.cumsum(histogram)
    weight_fg = total - weight_bg
    mean_bg = np.cumsum(histogram * np.arange(256)) / np.maximum(weight_bg, 1)
    grand = (histogram * np.arange(256)).sum()
    mean_fg = (grand - np.cumsum(histogram * np.arange(256))) / np.maximum(weight_fg, 1)
    between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
    return int(np.argmax(between[:-1]))


def load_mask(path: Path, *, max_side: int = 520, threshold: int | None = None) -> np.ndarray:
    """Load an image as a boolean ink mask, ink True.

    Downsampled first: tracing follows pixel boundaries, so a 2048px source
    yields a contour with tens of thousands of vertices that simplification then
    has to throw away. Resampling with a box filter also averages out JPEG
    ringing before the threshold ever sees it.
    """
    image = Image.open(path).convert("L")
    scale = max_side / max(image.size)
    if scale < 1.0:
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.LANCZOS,
        )
    gray = np.asarray(image, dtype=np.uint8)
    cut = otsu_threshold(gray) if threshold is None else threshold
    return gray <= cut


def _labels(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """4-connected component labels of the True cells. 0 means unlabelled."""
    height, width = mask.shape
    labels = np.zeros((height, width), dtype=np.int32)
    current = 0
    stack: list[tuple[int, int]] = []
    for start_y in range(height):
        for start_x in range(width):
            if not mask[start_y, start_x] or labels[start_y, start_x]:
                continue
            current += 1
            labels[start_y, start_x] = current
            stack.append((start_y, start_x))
            while stack:
                y, x = stack.pop()
                for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                    if 0 <= ny < height and 0 <= nx < width:
                        if mask[ny, nx] and not labels[ny, nx]:
                            labels[ny, nx] = current
                            stack.append((ny, nx))
    return labels, current


def despeckle(mask: np.ndarray, *, min_fraction: float = 0.002) -> np.ndarray:
    """Drop ink islands too small to be art.

    JPEG ringing around a black stroke survives thresholding as a scatter of
    one- and two-pixel specks. Left in, they eat the subpath budget that the
    actual drawing needs.
    """
    labels, count = _labels(mask)
    if count == 0:
        return mask
    sizes = np.bincount(labels.ravel())
    floor = max(4, int(mask.size * min_fraction))
    keep = {index for index in range(1, count + 1) if sizes[index] >= floor}
    return np.isin(labels, list(keep)) if keep else mask


def _shift(mask: np.ndarray, dy: int, dx: int, *, fill: bool) -> np.ndarray:
    """Translate ``mask`` by (dy, dx), with ``fill`` arriving from outside.

    ``np.roll`` would wrap the bottom row onto the top one, which welds an icon's
    feet to its hat. The padded shift is what makes the morphology below honest
    about the image border.
    """
    if dy == 0 and dx == 0:
        return mask
    out = np.full(mask.shape, fill, dtype=bool)
    height, width = mask.shape
    src_y = slice(max(0, -dy), height - max(0, dy))
    dst_y = slice(max(0, dy), height - max(0, -dy))
    src_x = slice(max(0, -dx), width - max(0, dx))
    dst_x = slice(max(0, dx), width - max(0, -dx))
    if src_y.start < src_y.stop and src_x.start < src_x.stop:
        out[dst_y, dst_x] = mask[src_y, src_x]
    return out


@lru_cache(maxsize=None)
def _disc_offsets(radius: int) -> tuple[tuple[int, int], ...]:
    """Every offset inside a disc of ``radius``, as the structuring element."""
    return tuple(
        (dy, dx)
        for dy in range(-radius, radius + 1)
        for dx in range(-radius, radius + 1)
        if dy * dy + dx * dx <= radius * radius
    )


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Grow ink by a disc of ``radius``."""
    if radius <= 0:
        return mask
    out = np.zeros_like(mask)
    for dy, dx in _disc_offsets(radius):
        out |= _shift(mask, dy, dx, fill=False)
    return out


def erode(mask: np.ndarray, radius: int, *, border: bool = False) -> np.ndarray:
    """Shrink ink by a disc of ``radius``.

    ``border`` says what lies outside the image. ``False`` treats the frame as
    background, so ink running off the edge is eaten there -- right for ink.
    ``True`` treats it as more of the same, which is what the *background* needs
    when we measure how narrow a channel is: the page does not end at the
    crop.
    """
    if radius <= 0:
        return mask
    out = mask.copy()
    for dy, dx in _disc_offsets(radius):
        out &= _shift(mask, dy, dx, fill=border)
    return out


def opening(mask: np.ndarray, radius: int, *, border: bool = False) -> np.ndarray:
    """Erode then dilate: everything a disc of ``radius`` can actually reach.

    What the opening drops is exactly what is thinner than ``2 * radius`` -- the
    same question the asset validator asks, asked here where it can still be
    fixed.
    """
    if radius <= 0:
        return mask
    return dilate(erode(mask, radius, border=border), radius)


def thin_regions(mask: np.ndarray, *, min_width_px: float, border: bool = False) -> np.ndarray:
    """The part of ``mask`` a disc of the minimum width cannot cover."""
    radius = max(1, int(round(min_width_px / 2.0)))
    return mask & ~opening(mask, radius, border=border)


def inner_radius(mask: np.ndarray, *, cap: int = 64) -> int:
    """Radius of the largest disc that fits inside ``mask``, by bisection.

    Bisecting on erosion replaces an exact distance transform that cost more
    than the rest of the tracer put together. The answer only ever feeds a
    pixel-count decision, so integer precision is the precision that matters.
    """
    if not mask.any():
        return 0
    high = min(cap, min(mask.shape) // 2)
    if high <= 0:
        return 0
    if erode(mask, high).any():
        return high
    low = 0
    while high - low > 1:
        mid = (low + high) // 2
        if erode(mask, mid).any():
            low = mid
        else:
            high = mid
    return low


def harden(mask: np.ndarray, *, min_width_px: float, max_steps: int = 10) -> np.ndarray:
    """Widen every part of ``mask`` that is thinner than ``min_width_px``.

    One dilation sized from the widest part of the drawing fixes nothing: a
    spider with a fat body and hairline legs measures as thick and is left alone.
    So the thin set is re-measured each round and grown by a single pixel, which
    also keeps the fix local -- the body stays the size it was drawn.
    """
    radius = max(1, int(round(min_width_px / 2.0)))
    out = mask
    for _ in range(max_steps):
        thin = out & ~opening(out, radius)
        if not thin.any():
            break
        out = out | dilate(thin, 1)
    return out


def close_thin_gaps(mask: np.ndarray, *, min_width_px: float) -> np.ndarray:
    """Weld shut any background channel narrower than ``min_width_px``.

    A gap too narrow to print is not a gap, it is two strokes that will merge on
    paper. Closing it in the file means the file shows what the page will.
    Measured with ``border=True`` so the crop edge is not mistaken for a wall.
    """
    radius = max(1, int(round(min_width_px / 2.0)))
    background = ~mask
    return mask | (background & ~opening(background, radius, border=True))


def fill_thin_holes(mask: np.ndarray, *, min_width_px: float) -> np.ndarray:
    """Keep only the holes wide enough to survive the press.

    One opening decides it for every hole at once: a hole keeps the pixels a
    disc of the minimum width can reach, so a hole with none of them left is a
    hole that would have closed up in ink anyway.
    """
    radius = max(1, int(round(min_width_px / 2.0)))
    background = ~mask
    labels, count = _labels(background)
    if count == 0:
        return mask

    border = set(labels[0, :]) | set(labels[-1, :]) | set(labels[:, 0]) | set(labels[:, -1])
    border.discard(0)

    survivors = opening(background, radius, border=True)
    kept = set(np.unique(labels[survivors])) - {0}

    doomed = [index for index in range(1, count + 1) if index not in border and index not in kept]
    if not doomed:
        return mask
    return mask | np.isin(labels, doomed)


def _fill_interior_background(mask: np.ndarray) -> np.ndarray:
    """Fill every background region that does not reach the image border."""
    labels, count = _labels(~mask)
    if count == 0:
        return mask
    border = set(labels[0, :]) | set(labels[-1, :]) | set(labels[:, 0]) | set(labels[:, -1])
    border.discard(0)
    outside = np.isin(labels, list(border)) if border else np.zeros_like(mask)
    return ~outside


def solidify(mask: np.ndarray, *, min_width_px: float, keep_knockouts: bool = True) -> np.ndarray:
    """Turn line art into a filled silhouette that keeps what it was drawing.

    Printed at 5.5 mm a drawn contour is 0.22 mm and closes up, so the
    silhouette has to carry the icon. The trap is that filling it swallows the
    drawing: a ghost becomes a blob, a gravestone loses its cross, a web becomes
    a triangle. What the eye reads at that size is the *knockout* -- the white
    inside the black -- so the shape is rebuilt from both directions:

    * the body is filled solid, which is what survives the press;
    * every white region enclosed by it is kept as a hole, widened first if it
      is too narrow to stay open;
    * black shapes floating inside the body (how outlined art draws a face) are
      cut out as holes too, which is the same thing one step removed.

    A hole that is still too narrow after widening is filled, because a hole the
    press closes is not a hole -- but that is now the exception rather than what
    happens to every detail in the file.

    ``keep_knockouts=False`` fills the body flat instead. Whether an icon reads
    better hollow or solid at 5.5 mm is a judgement about that one drawing --
    candy corn wants the solid cone, a ghost wants its outline -- so it is
    recorded per asset in the manifest rather than guessed from a threshold
    here.
    """
    labels, count = _labels(mask)
    if count == 0:
        return mask
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    body = labels == int(np.argmax(sizes))

    silhouette = _fill_interior_background(body)
    holes = np.zeros_like(mask)

    # White regions enclosed by the body: the eyes, the cross, the web's cells.
    knockouts = silhouette & ~mask
    if keep_knockouts and knockouts.any():
        holes |= harden(knockouts, min_width_px=min_width_px)

    # Black islands floating inside the body, which outlined art uses for the
    # same job. They read as holes once the body around them is filled.
    details = mask & ~body & silhouette
    if details.any():
        holes |= harden(details, min_width_px=min_width_px)

    # A hole may only eat the inside. Widening one near the rim would otherwise
    # bite a notch out of the silhouette and the icon would lose its outline.
    holes &= erode(silhouette, max(1, int(round(min_width_px / 2.0))))

    return fill_thin_holes(silhouette & ~holes, min_width_px=min_width_px)


# --------------------------------------------------------------------------- #
# Contour tracing
# --------------------------------------------------------------------------- #


def trace_contours(mask: np.ndarray) -> list[list[tuple[float, float]]]:
    """Every closed boundary loop between ink and background.

    Walks the unit edges of the pixel lattice rather than the pixel centres, so
    a contour is exact: it is the actual outline of the filled region, not a
    polyline threaded through it. Orientation is consistent, and nesting is left
    for ``fill-rule="evenodd"`` to resolve -- which is why no outer/hole
    bookkeeping appears anywhere in this file.
    """
    padded = np.pad(mask, 1, constant_values=False)
    height, width = padded.shape

    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}
    ys, xs = np.nonzero(padded)
    for y, x in zip(ys.tolist(), xs.tolist()):
        if not padded[y - 1, x]:
            edges.setdefault((x + 1, y), []).append((x, y))
        if not padded[y + 1, x]:
            edges.setdefault((x, y + 1), []).append((x + 1, y + 1))
        if not padded[y, x - 1]:
            edges.setdefault((x, y), []).append((x, y + 1))
        if not padded[y, x + 1]:
            edges.setdefault((x + 1, y + 1), []).append((x + 1, y))

    contours: list[list[tuple[float, float]]] = []
    while edges:
        start = next(iter(edges))
        loop = [start]
        current = start
        while True:
            outgoing = edges.get(current)
            if not outgoing:
                break
            nxt = outgoing.pop()
            if not outgoing:
                del edges[current]
            loop.append(nxt)
            current = nxt
            if current == start:
                break
        if len(loop) >= 4:
            contours.append([(float(x - 1), float(y - 1)) for x, y in loop])
    return contours


def simplify(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Douglas-Peucker, applied to a closed ring."""
    if len(points) < 4 or tolerance <= 0:
        return points

    def walk(chunk: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(chunk) < 3:
            return chunk
        (x0, y0), (x1, y1) = chunk[0], chunk[-1]
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        worst, index = -1.0, 0
        for i in range(1, len(chunk) - 1):
            px, py = chunk[i]
            if length == 0:
                distance = math.hypot(px - x0, py - y0)
            else:
                distance = abs(dy * px - dx * py + x1 * y0 - y1 * x0) / length
            if distance > worst:
                worst, index = distance, i
        if worst <= tolerance:
            return [chunk[0], chunk[-1]]
        return walk(chunk[: index + 1])[:-1] + walk(chunk[index:])

    ring = points if points[0] == points[-1] else points + [points[0]]
    simplified = walk(ring)
    return simplified[:-1] if len(simplified) > 3 else points


def ring_area(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]):
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


# --------------------------------------------------------------------------- #
# SVG emission
# --------------------------------------------------------------------------- #


def _n(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


@dataclass(frozen=True, slots=True)
class TraceResult:
    svg: str
    subpaths: int
    coverage: float
    min_feature_units: float
    thin_px: int = 0


def to_svg(
    contours: list[list[tuple[float, float]]],
    *,
    source_shape: tuple[int, int],
    padding: float,
    title: str,
) -> str:
    xs = [x for ring in contours for x, _ in ring]
    ys = [y for ring in contours for _, y in ring]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span = max(max_x - min_x, max_y - min_y) or 1.0

    usable = 100.0 - 2.0 * padding
    scale = usable / span
    offset_x = padding + (usable - (max_x - min_x) * scale) / 2.0
    offset_y = padding + (usable - (max_y - min_y) * scale) / 2.0

    parts: list[str] = []
    for ring in contours:
        placed = [
            ((x - min_x) * scale + offset_x, (y - min_y) * scale + offset_y) for x, y in ring
        ]
        data = f"M{_n(placed[0][0])} {_n(placed[0][1])}"
        data += "".join(f"L{_n(x)} {_n(y)}" for x, y in placed[1:])
        parts.append(data + "Z")

    body = " ".join(parts)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">\n'
        f"  <title>{title}</title>\n"
        f'  <path fill="#000000" fill-rule="evenodd" d="{body}"/>\n'
        "</svg>\n"
    )


def vectorize(
    path: Path,
    *,
    mode: str = MODE_SOLID,
    max_subpaths: int = 12,
    min_feature_units: float = 7.0,
    keep_knockouts: bool = True,
    padding: float = 8.0,
    tolerance_units: float = 0.35,
    title: str | None = None,
    trim_border: int = 2,
) -> TraceResult:
    mask = load_mask(path)
    if trim_border:
        mask[:trim_border, :] = mask[-trim_border:, :] = False
        mask[:, :trim_border] = mask[:, -trim_border:] = False
    mask = despeckle(mask)
    if not mask.any():
        raise ValueError(f"{path.name}: no ink found after thresholding")

    # Work out how many source pixels one viewBox unit will be worth, so the
    # 7-unit rule can be enforced in the raster where it is cheap to fix.
    ys, xs = np.nonzero(mask)
    span_px = max(xs.max() - xs.min(), ys.max() - ys.min()) or 1
    px_per_unit = span_px / (100.0 - 2.0 * padding)

    # Fix the raster to a *wider* minimum than the rule asks for. Simplification
    # is allowed to move a vertex by ``tolerance``, so a corrected gap can close
    # back to ``min - 2 * tolerance`` before it is ever written, and the pixel
    # lattice itself is worth another unit. Aiming exactly at the limit is how
    # the first run produced three files that were right in the mask and thin in
    # the file.
    target_units = min_feature_units + 2.0 * tolerance_units + 1.5
    min_width_px = target_units * px_per_unit

    if mode == MODE_SOLID:
        mask = solidify(mask, min_width_px=min_width_px, keep_knockouts=keep_knockouts)
    # Both modes finish the same way, because both end up on paper: widen what is
    # too thin to print, then weld shut what is too narrow to stay open. Solid
    # mode needs it after ``solidify`` as well -- swapping a detail for a hole
    # can leave a 2 px isthmus between the hole and the silhouette's edge.
    mask = harden(mask, min_width_px=min_width_px)
    mask = close_thin_gaps(mask, min_width_px=min_width_px)
    mask = fill_thin_holes(mask, min_width_px=min_width_px)

    contours = trace_contours(mask)
    if not contours:
        raise ValueError(f"{path.name}: tracing produced no contours")

    tolerance = tolerance_units * px_per_unit
    contours = [simplify(ring, tolerance) for ring in contours]
    contours = [ring for ring in contours if len(ring) >= 3 and ring_area(ring) > 4.0]
    contours.sort(key=ring_area, reverse=True)
    contours = contours[:max_subpaths]

    svg = to_svg(
        contours,
        source_shape=mask.shape,
        padding=padding,
        title=title or path.stem.replace("_", " "),
    )
    coverage = float(mask.sum()) / mask.size
    thin = thin_regions(mask, min_width_px=min_width_px)
    return TraceResult(
        svg=svg,
        subpaths=len(contours),
        coverage=coverage,
        min_feature_units=2.0 * inner_radius(mask) / px_per_unit,
        thin_px=int(thin.sum()),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--mode", choices=(MODE_SOLID, MODE_OUTLINE), default=MODE_SOLID)
    parser.add_argument("--max-subpaths", type=int, default=12)
    parser.add_argument("--min-feature", type=float, default=7.0)
    parser.add_argument("--padding", type=float, default=8.0)
    parser.add_argument("--tolerance", type=float, default=0.35)
    parser.add_argument("--flat", action="store_true", help="fill the body, dropping knockouts")
    parser.add_argument("--title")
    args = parser.parse_args(argv)

    result = vectorize(
        args.source,
        mode=args.mode,
        max_subpaths=args.max_subpaths,
        min_feature_units=args.min_feature,
        padding=args.padding,
        tolerance_units=args.tolerance,
        keep_knockouts=not args.flat,
        title=args.title,
    )
    args.target.parent.mkdir(parents=True, exist_ok=True)
    args.target.write_text(result.svg, encoding="utf-8")
    print(
        f"{args.target.name}: {result.subpaths} subpaths, ink {result.coverage:.0%}, "
        f"widest feature {result.min_feature_units:.1f} units"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
