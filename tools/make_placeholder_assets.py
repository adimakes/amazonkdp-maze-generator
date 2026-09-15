"""Generate the placeholder Halloween SVG set (PRD 17.9 / 18.5 subset).

These are placeholders in *subject* only. Every one of them obeys the rules the
shipping artwork will have to obey, because the point of a placeholder is to
exercise the pipeline honestly rather than to defer its hardest constraint:

*   ``viewBox="0 0 100 100"`` and no ``width``/``height``, so the renderer scales
    by cell size rather than by whatever the illustrator's canvas happened to be;
*   **filled paths only.** No ``stroke`` anywhere, and therefore no ``<circle>``,
    ``<rect>``, ``<ellipse>`` or ``<line>`` either -- ``assets/svg_subset.py``
    admits ``svg``/``g``/``path`` and nothing else. A stroke does not scale with
    the icon: 6 units of stroke on a 0.6 in start marker is a confident line, and
    the same 6 units on a 5.5 mm finale collectible is a blob;
*   no external references, scripts, entities, filters, raster images or text;
*   no background rectangle, so the maze walls stay visible behind the art;
*   pure ``#000000``. Where a shape needs a hole -- a ghost's eye, a carved
    cross -- it is a counter-subpath under ``fill-rule="evenodd"``, never white
    paint, which would print as a white block on cream stock.

Two measurements drive every number below, and both are widths rather than
radii: ``min_feature_units = 7`` means no limb may be thinner than 7 units *and*
no gap between two limbs may be narrower than 7 units (``validate.py`` runs the
same morphological opening over the artwork and over its negative). At the 5.5 mm
finale size, 7 units is 0.39 mm -- about the thinnest mark a print-on-demand
press holds. So the art here is deliberately chunky: solid silhouettes, bars of
10 to 20 units, and holes no smaller than 12 units across.

The subpath budget is 12 *for the whole document*, not per element.

Run with:  uv run python tools/make_placeholder_assets.py [package-dir] [--check]
"""

from __future__ import annotations

import math
import sys
from pathlib import Path as FsPath

Point = tuple[float, float]

TAU = 2.0 * math.pi


# --------------------------------------------------------------------------- #
# Path building
#
# One representation -- a start point plus line/cubic segments -- with explicit
# winding control. Winding is load-bearing: the validator's rasterizer fills
# every ``nonzero`` subpath in a single scanline pass, so two overlapping shapes
# wound against each other would cancel and punch a hole through their overlap.
# Every solid shape is therefore normalized to the same orientation, and the
# only intentional holes live in ``evenodd`` elements where winding is moot.
# --------------------------------------------------------------------------- #


class Shape:
    """A closed subpath: a start point and a run of line/cubic segments."""

    __slots__ = ("start", "segs")

    def __init__(self, start: Point) -> None:
        self.start: Point = start
        self.segs: list[tuple] = []  # ("L", end) | ("C", c1, c2, end)

    # -- construction -------------------------------------------------------
    def line_to(self, point: Point) -> "Shape":
        self.segs.append(("L", point))
        return self

    def curve_to(self, c1: Point, c2: Point, point: Point) -> "Shape":
        self.segs.append(("C", c1, c2, point))
        return self

    def lines_to(self, points: list[Point]) -> "Shape":
        for point in points:
            self.line_to(point)
        return self

    # -- geometry -----------------------------------------------------------
    def _endpoints(self) -> list[Point]:
        return [self.start] + [seg[-1] for seg in self.segs]

    def polyline(self, steps: int = 8) -> list[Point]:
        points = [self.start]
        current = self.start
        for seg in self.segs:
            if seg[0] == "L":
                points.append(seg[1])
                current = seg[1]
            else:
                _, c1, c2, end = seg
                for index in range(1, steps + 1):
                    t = index / steps
                    u = 1.0 - t
                    points.append(
                        (
                            u * u * u * current[0] + 3 * u * u * t * c1[0]
                            + 3 * u * t * t * c2[0] + t * t * t * end[0],
                            u * u * u * current[1] + 3 * u * u * t * c1[1]
                            + 3 * u * t * t * c2[1] + t * t * t * end[1],
                        )
                    )
                current = end
        return points

    def signed_area(self) -> float:
        points = self.polyline()
        total = 0.0
        for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]):
            total += x0 * y1 - x1 * y0
        return total / 2.0

    def reversed_shape(self) -> "Shape":
        ends = self._endpoints()
        out = Shape(ends[-1])
        for index in range(len(self.segs) - 1, -1, -1):
            seg = self.segs[index]
            target = ends[index]
            if seg[0] == "L":
                out.line_to(target)
            else:
                out.curve_to(seg[2], seg[1], target)
        return out

    def oriented(self, positive: bool = True) -> "Shape":
        """Normalize winding so overlapping solids never cancel under nonzero."""
        if (self.signed_area() >= 0.0) == positive:
            return self
        return self.reversed_shape()

    # -- emission -----------------------------------------------------------
    def d(self) -> str:
        parts = [f"M{_n(self.start[0])} {_n(self.start[1])}"]
        for seg in self.segs:
            if seg[0] == "L":
                parts.append(f"L{_n(seg[1][0])} {_n(seg[1][1])}")
            else:
                _, c1, c2, end = seg
                parts.append(
                    f"C{_n(c1[0])} {_n(c1[1])} {_n(c2[0])} {_n(c2[1])} "
                    f"{_n(end[0])} {_n(end[1])}"
                )
        parts.append("Z")
        return " ".join(parts)


def _n(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #


def disc(cx: float, cy: float, r: float) -> Shape:
    """A full circle as four quarter-arc cubics (W3C SVG 1.1 F.6)."""
    shape = Shape((cx + r, cy))
    _append_arc(shape, cx, cy, r, 0.0, TAU)
    return shape


def _append_arc(shape: Shape, cx: float, cy: float, r: float, a0: float, a1: float) -> None:
    delta = a1 - a0
    count = max(1, int(math.ceil(abs(delta) / (math.pi / 2.0) - 1e-9)))
    step = delta / count
    k = (4.0 / 3.0) * math.tan(step / 4.0)
    for index in range(count):
        t1 = a0 + index * step
        t2 = t1 + step
        p1 = (cx + r * math.cos(t1), cy + r * math.sin(t1))
        p2 = (cx + r * math.cos(t2), cy + r * math.sin(t2))
        d1 = (-r * math.sin(t1), r * math.cos(t1))
        d2 = (-r * math.sin(t2), r * math.cos(t2))
        shape.curve_to(
            (p1[0] + k * d1[0], p1[1] + k * d1[1]),
            (p2[0] - k * d2[0], p2[1] - k * d2[1]),
            p2,
        )


def poly(points: list[Point]) -> Shape:
    shape = Shape(points[0])
    shape.lines_to(list(points[1:]))
    return shape


def rect(x0: float, y0: float, x1: float, y1: float) -> Shape:
    return poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def arc_points(
    cx: float, cy: float, r: float, a0_deg: float, a1_deg: float, step_deg: float = 4.0
) -> list[Point]:
    a0, a1 = math.radians(a0_deg), math.radians(a1_deg)
    count = max(2, int(math.ceil(abs(a1 - a0) / math.radians(step_deg))))
    return [
        (cx + r * math.cos(a0 + (a1 - a0) * i / count), cy + r * math.sin(a0 + (a1 - a0) * i / count))
        for i in range(count + 1)
    ]


def bezier_points(p0: Point, p1: Point, p2: Point, p3: Point, count: int = 24) -> list[Point]:
    out: list[Point] = []
    for index in range(count + 1):
        t = index / count
        u = 1.0 - t
        out.append(
            (
                u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
            )
        )
    return out


def quad_points(p0: Point, p1: Point, p2: Point, count: int = 20) -> list[Point]:
    out: list[Point] = []
    for index in range(count + 1):
        t = index / count
        u = 1.0 - t
        out.append(
            (u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
             u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1])
        )
    return out


def bar(centre: list[Point], width: float, cap_steps: int = 10) -> Shape:
    """A round-capped thick line along ``centre`` -- the filled equivalent of a
    stroked polyline, which is exactly what stroke-to-fill conversion produces.

    Offsets use per-vertex averaged normals rather than mitres: a mitre at a
    sharp turn shoots the outer corner off to infinity, and every centreline
    here is either straight or densely sampled, where the two agree to well
    under a sample.
    """
    points = _dedupe(centre)
    if len(points) < 2:
        raise ValueError("bar needs at least two distinct centreline points")
    half = width / 2.0

    normals: list[Point] = []
    seg_normals = []
    for a, b in zip(points, points[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        seg_normals.append((-dy / length, dx / length))
    for index in range(len(points)):
        if index == 0:
            nx, ny = seg_normals[0]
        elif index == len(points) - 1:
            nx, ny = seg_normals[-1]
        else:
            ax, ay = seg_normals[index - 1]
            bx, by = seg_normals[index]
            nx, ny = ax + bx, ay + by
            length = math.hypot(nx, ny)
            nx, ny = (ax, ay) if length < 1e-9 else (nx / length, ny / length)
        normals.append((nx, ny))

    left = [(p[0] + n[0] * half, p[1] + n[1] * half) for p, n in zip(points, normals)]
    right = [(p[0] - n[0] * half, p[1] - n[1] * half) for p, n in zip(points, normals)]

    outline: list[Point] = list(left)
    outline += _cap(points[-1], normals[-1], half, cap_steps)
    outline += list(reversed(right))
    outline += _cap(points[0], (-normals[0][0], -normals[0][1]), half, cap_steps)
    return poly(_dedupe(outline))


def _cap(centre: Point, normal: Point, half: float, steps: int) -> list[Point]:
    """Semicircle from ``centre + normal*half`` sweeping -pi around ``centre``."""
    start = math.atan2(normal[1], normal[0])
    return [
        (
            centre[0] + half * math.cos(start - math.pi * i / steps),
            centre[1] + half * math.sin(start - math.pi * i / steps),
        )
        for i in range(1, steps)
    ]


def _dedupe(points: list[Point]) -> list[Point]:
    out: list[Point] = []
    for point in points:
        if not out or math.hypot(point[0] - out[-1][0], point[1] - out[-1][1]) > 1e-9:
            out.append(point)
    return out


# --------------------------------------------------------------------------- #
# Document assembly
# --------------------------------------------------------------------------- #


def svg(*elements: str) -> str:
    body = "\n".join(f"  {element}" for element in elements)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">\n{body}\n</svg>\n'


def solid(*shapes: Shape) -> str:
    """One filled element per shape, all wound the same way.

    Separate elements rather than one multi-subpath element so that adding a
    shape can never change how an existing one fills.
    """
    return "\n  ".join(f'<path fill="#000000" d="{s.oriented().d()}"/>' for s in shapes)


def pierced(body: Shape, *holes: Shape) -> str:
    """A body with holes, as one ``evenodd`` element.

    ``evenodd`` rather than counter-winding under ``nonzero`` because it states
    the intent in the file: these subpaths are holes, and they stay holes no
    matter which direction a later edit happens to draw them in.
    """
    parts = " ".join(shape.d() for shape in (body, *holes))
    return f'<path fill="#000000" fill-rule="evenodd" d="{parts}"/>'


# --------------------------------------------------------------------------- #
# Collectibles -- read at about 5.5 mm, so: silhouette plus one strong mark
# --------------------------------------------------------------------------- #


def _candy_wrapped() -> str:
    return svg(
        solid(
            disc(50, 50, 21),
            poly([(36, 50), (10, 31), (10, 69)]),
            poly([(64, 50), (90, 31), (90, 69)]),
        )
    )


def _lollipop() -> str:
    return svg(solid(disc(50, 34, 24), bar([(50, 52), (50, 87)], 12)))


def _candy_corn() -> str:
    return svg(
        solid(
            poly([(44, 10), (56, 10), (60, 32), (40, 32)]),
            poly([(38, 41), (62, 41), (66, 61), (34, 61)]),
            poly([(31, 70), (69, 70), (74, 90), (26, 90)]),
        )
    )


def _chocolate_bar() -> str:
    columns = ((8, 30), (39, 61), (70, 92))
    rows = ((22, 48), (57, 83))
    return svg(solid(*[rect(x0, y0, x1, y1) for y0, y1 in rows for x0, x1 in columns]))


def _gummy_worm() -> str:
    spine = bezier_points((20, 66), (36, 26), (64, 78), (80, 36))
    return svg(solid(bar(spine, 14)))


def _candy_cane() -> str:
    hook = arc_points(48, 42, 14, 0.0, -180.0)
    return svg(solid(bar([(62, 42), (62, 86)], 12), bar(hook, 12)))


# --------------------------------------------------------------------------- #
# Dead ends -- "wrong turn" markers: a small warning at the end of a corridor
# --------------------------------------------------------------------------- #


def _web_corner() -> str:
    """Concentric bands rather than radiating rays.

    Rays from a shared origin necessarily pass through a zone where the gap
    between neighbours is one or two units -- a real A8 failure -- however far
    apart they end up. Bands keep a constant 12-unit gap the whole way.
    """
    bands = [bar(arc_points(10, 10, r, 8.0, 82.0), 10) for r in (30, 54, 78)]
    spoke = bar([(28.4, 28.4), (72.2, 72.2)], 10)
    return svg(solid(*bands, spoke))


def _black_cat() -> str:
    tail = quad_points((68, 64), (88, 66), (84, 86))
    return svg(
        solid(
            disc(50, 54, 23),
            poly([(31, 42), (24, 16), (46, 32)]),
            poly([(69, 42), (76, 16), (54, 32)]),
            bar(tail, 11),
        )
    )


def _bat() -> str:
    return svg(
        solid(
            disc(50, 50, 17),
            poly([(38, 42), (8, 30), (7, 62), (36, 58)]),
            poly([(62, 42), (92, 30), (93, 62), (64, 58)]),
            poly([(40, 38), (34, 18), (52, 32)]),
            poly([(60, 38), (66, 18), (48, 32)]),
        )
    )


def _ghost_body(left: float, right: float, shoulder: float, foot: float, feet: list[Point]) -> Shape:
    """Domed top, flat sides, scalloped hem.

    The hem's feet are flat-bottomed on purpose. A pointed foot is a wedge that
    the 7-unit opening shaves tens of square units off; an 8-unit flat sole is
    a right-angle corner, which costs 2.6.
    """
    cx = (left + right) / 2.0
    shape = Shape((left, foot))
    shape.line_to((left, shoulder))
    _append_arc(shape, cx, shoulder, (right - left) / 2.0, math.pi, TAU)
    shape.line_to((right, foot))
    shape.lines_to(feet)
    return shape


def _ghost() -> str:
    body = _ghost_body(
        22, 78, 44, 90,
        [(70, 90), (64, 78), (56, 90), (44, 90), (36, 78), (30, 90)],
    )
    return svg(pierced(body, disc(40, 44, 6), disc(60, 44, 6)))


def _cross(cx: float, top: float, bottom: float, arm_y: float, half_w: float, half_span: float,
           arm_h: float) -> Shape:
    """A carved cross, as a hole. Both bars are 2*half_w / arm_h units wide."""
    return poly([
        (cx - half_w, top), (cx + half_w, top),
        (cx + half_w, arm_y), (cx + half_span, arm_y),
        (cx + half_span, arm_y + arm_h), (cx + half_w, arm_y + arm_h),
        (cx + half_w, bottom), (cx - half_w, bottom),
        (cx - half_w, arm_y + arm_h), (cx - half_span, arm_y + arm_h),
        (cx - half_span, arm_y), (cx - half_w, arm_y),
    ])


def _headstone(left: float, right: float, shoulder: float, foot: float) -> Shape:
    cx = (left + right) / 2.0
    shape = Shape((left, foot))
    shape.line_to((left, shoulder))
    _append_arc(shape, cx, shoulder, (right - left) / 2.0, math.pi, TAU)
    shape.line_to((right, foot))
    return shape


def _gravestone() -> str:
    stone = _headstone(26, 74, 40, 90)
    cross = _cross(50, 32, 70, 44, 4, 14, 8)
    return svg(pierced(stone, cross))


# --------------------------------------------------------------------------- #
# Endpoints -- the identity of the book, named explicitly in book.json
# --------------------------------------------------------------------------- #


def _jim_start() -> str:
    return svg(
        solid(
            disc(50, 26, 15),
            bar([(50, 40), (50, 66)], 20),
            bar([(42, 48), (24, 60)], 11),
            bar([(58, 48), (76, 60)], 11),
            bar([(45, 68), (34, 88)], 11),
            bar([(55, 68), (66, 88)], 11),
        )
    )


def _candy_bucket() -> str:
    handle = arc_points(50, 46, 26, 180.0, 360.0)
    return svg(
        solid(
            poly([(24, 46), (76, 46), (70, 90), (30, 90)]),
            bar([(20, 46), (80, 46)], 12),
            bar(handle, 11),
        )
    )


# --------------------------------------------------------------------------- #
# Page vectors -- story-page decoration, printed larger, same rules
# --------------------------------------------------------------------------- #


def _jim_costume() -> str:
    return svg(
        solid(
            disc(50, 24, 14),
            poly([(50, 36), (78, 44), (72, 88), (28, 88), (22, 44)]),
            bar([(30, 52), (14, 64)], 11),
            bar([(70, 52), (86, 64)], 11),
        )
    )


def _jim_head() -> str:
    return svg(
        pierced(
            disc(50, 50, 32),
            disc(38, 42, 7),
            disc(62, 42, 7),
            bar([(38, 66), (62, 66)], 9),
        )
    )


def _deco_moon() -> str:
    """A cratered full moon, not a crescent.

    A crescent tapers to nothing at both horns, and the horns are precisely the
    feature the 7-unit rule exists to reject; a full disc with 12-unit craters
    reads as "moon" at story-page size and measures clean.
    """
    return svg(pierced(disc(50, 50, 36), disc(36, 38, 6), disc(60, 34, 6), disc(44, 64, 6)))


def _deco_tree() -> str:
    return svg(
        solid(
            bar([(50, 40), (50, 84)], 14),
            bar([(50, 62), (22, 40)], 11),
            bar([(50, 54), (78, 34)], 11),
            bar([(50, 80), (24, 70)], 11),
        )
    )


def _deco_ghost() -> str:
    body = _ghost_body(
        18, 82, 42, 92,
        [(74, 92), (68, 80), (58, 92), (42, 92), (32, 80), (26, 92)],
    )
    return svg(pierced(body, disc(38, 40, 7), disc(62, 40, 7), disc(50, 64, 8)))


def _deco_bat() -> str:
    return svg(
        solid(
            disc(50, 48, 19),
            poly([(38, 40), (8, 26), (7, 62), (36, 56)]),
            poly([(62, 40), (92, 26), (93, 62), (64, 56)]),
            poly([(40, 34), (33, 12), (52, 28)]),
            poly([(60, 34), (67, 12), (48, 28)]),
        )
    )


def _deco_spider() -> str:
    """Legs radiate from the body centre, so every root is buried in the body.

    Rooting them on the rim instead leaves a three-unit slot between each leg
    and the belly it sprouts from.
    """
    legs = [
        bar([(50, 50), (50 + 38 * math.cos(math.radians(a)), 50 + 38 * math.sin(math.radians(a)))], 10)
        for a in (0, 50, 130, 180, 230, 310)
    ]
    return svg(solid(disc(50, 50, 22), *legs))


def _deco_gravestone() -> str:
    stone = _headstone(28, 72, 42, 82)
    cross = _cross(50, 32, 66, 44, 4, 13, 8)
    return svg(pierced(stone, cross), solid(bar([(14, 86), (86, 86)], 12)))


def _deco_web_corner() -> str:
    bands = [bar(arc_points(10, 10, r, 8.0, 82.0), 10) for r in (26, 48, 70)]
    spokes = [
        bar([(37.2, 22.7), (78.9, 42.1)], 10),
        bar([(22.7, 37.2), (42.1, 78.9)], 10),
    ]
    return svg(solid(*bands, *spokes))


# --------------------------------------------------------------------------- #
# The set
# --------------------------------------------------------------------------- #

COLLECTIBLES = {
    "candy_wrapped.svg": _candy_wrapped,
    "lollipop.svg": _lollipop,
    "candy_corn.svg": _candy_corn,
    "chocolate_bar.svg": _chocolate_bar,
    "gummy_worm.svg": _gummy_worm,
    "candy_cane.svg": _candy_cane,
}

DEAD_ENDS = {
    "web_corner.svg": _web_corner,
    "black_cat.svg": _black_cat,
    "bat.svg": _bat,
    "ghost.svg": _ghost,
    "gravestone.svg": _gravestone,
}

START = {"jim_start.svg": _jim_start}

FINISH = {"candy_bucket.svg": _candy_bucket}

PAGE_VECTORS = {
    "jim_costume.svg": _jim_costume,
    "jim_head.svg": _jim_head,
    "deco_moon.svg": _deco_moon,
    "deco_tree.svg": _deco_tree,
    "deco_ghost.svg": _deco_ghost,
    "deco_bat.svg": _deco_bat,
    "deco_spider.svg": _deco_spider,
    "deco_gravestone.svg": _deco_gravestone,
    "deco_web_corner.svg": _deco_web_corner,
}

TARGETS = {
    "assets/beginning-vectors": START,
    "assets/ending-vectors": FINISH,
    "assets/maze-vectors/dead-end": DEAD_ENDS,
    "assets/maze-vectors/collectibles": COLLECTIBLES,
    "assets/page-vectors": PAGE_VECTORS,
}


def main(package_dir: FsPath, *, check: bool = False) -> int:
    written: list[FsPath] = []
    for rel, files in TARGETS.items():
        directory = package_dir / rel
        directory.mkdir(parents=True, exist_ok=True)
        for name, builder in files.items():
            path = directory / name
            path.write_text(builder(), encoding="utf-8")
            written.append(path)
        print(f"{rel}: {len(files)} file(s)")
    print(f"wrote {len(written)} placeholder SVGs to {package_dir}")

    if not check:
        return 0

    from maze_book.assets.validate import validate_svg

    failures = 0
    for path in written:
        report = validate_svg(path)
        print(("    " if report.passed else "!!  ") + report.summary())
        if not report.passed:
            failures += 1
            for error in report.errors:
                print(f"      {error}")
    print(f"\n{len(written) - failures}/{len(written)} assets pass 18.5 validation")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    target = FsPath(args[0]) if args else FsPath("books/jims-halloween-maze-adventure")
    raise SystemExit(main(target, check="--check" in sys.argv[1:]))
