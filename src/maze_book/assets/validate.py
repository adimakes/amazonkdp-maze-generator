"""Asset validation (PRD 17.9, 18.5).

The rules exist because of one number: on an 18x18 finale maze a collectible at
scale 0.5 prints about 5.5 mm across. At that size a 3-unit line in a 100-unit
viewBox is a 0.17 mm hairline that the printer's toner cannot hold, and a 3-unit
gap between two limbs closes into a blob. So the validator does not merely parse
the file -- it *measures the artwork*, and the measurement is done the way the
press sees it: rasterized, then morphologically opened with a disc the size of
the minimum feature.

17.9 states that "the exact minimum feature size is profile data", so every
threshold lives in ``AssetProfile``. The defaults are the hardened Halloween
numbers of 18.5.

Structural rules (subset membership, no live strokes, black-only fill, no text,
no raster, no entities) are enforced by ``svg_subset.parse_svg``, which is the
same code the PDF converter uses. That sharing is deliberate and required by
18.5: a file that validates is by construction convertible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Sequence

from ..errors import AssetError
from . import svg_subset
from .svg_subset import ParsedSvg, Subpath

#: Rule codes, reported verbatim so a failure can be looked up in the PRD.
CODE_PARSE = "A1_subset"
CODE_VIEWBOX = "A2_viewBox"
CODE_PADDING = "A3_padding"
CODE_CENTERED = "A4_centered"
CODE_SUBPATHS = "A5_subpathBudget"
CODE_COVERAGE = "A6_coverage"
CODE_THIN_FEATURE = "A7_minimumFeature"
CODE_THIN_GAP = "A8_minimumGap"
CODE_EMPTY = "A9_notEmpty"

ALL_CODES = (
    CODE_PARSE, CODE_VIEWBOX, CODE_PADDING, CODE_CENTERED, CODE_SUBPATHS,
    CODE_COVERAGE, CODE_THIN_FEATURE, CODE_THIN_GAP, CODE_EMPTY,
)


@dataclass(frozen=True)
class AssetProfile:
    """Thresholds for one asset profile. 17.9: "profile data", not constants.

    The defaults are 18.5's hardened icon rules. ``min_feature_units`` is the
    load-bearing one: 7 units of a 100-unit viewBox is 0.39 mm at the 5.5 mm
    finale size, which is about the thinnest line a print-on-demand press holds
    on cream stock.
    """

    view_box: tuple[float, float, float, float] = (0.0, 0.0, 100.0, 100.0)
    min_padding_units: float = 6.0
    min_feature_units: float = 7.0
    max_subpaths: int = 12
    max_coverage_fraction: float = 0.85
    #: How far the artwork's centre may sit from the viewBox centre. Composition
    #: is "centred" (18.5) but hand-drawn icons are never centred to the pixel,
    #: and an icon whose ink is lopsided still centres correctly in its cell.
    max_centre_offset_units: float = 12.0
    #: Rasterization density for the geometric checks, in samples per unit.
    samples_per_unit: float = 2.0
    #: A lost region smaller than this many square units is corner rounding from
    #: the opening itself, not a thin feature. A sharp right-angle corner loses
    #: ``r^2 (1 - pi/4)`` = 2.6 sq units at r=3.5; a 6.5-unit-wide limb only
    #: 12 units long loses 78. The gap between those two is wide, and this
    #: threshold sits in it.
    lost_area_tolerance_units: float = 49.0

    #: The thinnest line a print-on-demand press holds on cream stock. Every
    #: other number in this class is a ratio; this one is the millimetre the
    #: ratios are anchored to.
    PRESS_LIMIT_MM: ClassVar[float] = 0.39

    @classmethod
    def for_print_size(
        cls,
        millimetres: float,
        *,
        max_subpaths: int | None = None,
        **overrides: object,
    ) -> "AssetProfile":
        """The profile for artwork that prints at ``millimetres`` on the page.

        7 units is not a property of SVG, it is 0.39 mm at the 5.5 mm size a
        finale collectible prints at. Holding a 0.6 in page decoration to the
        same 7 units asks it to be six times coarser than the press needs, which
        is how a perfectly printable drawing gets rejected; holding a marker
        drawn outside the grid to it is how a walking boy becomes a blob. So the
        rule is stated once, in millimetres, and the units follow from the size.

        The subpath budget is raised in step, because artwork with room for
        finer strokes has room for more of them. 18.5's 12 is what the formula
        gives at the finale size, so an icon-sized asset is judged exactly as
        before.
        """
        millimetres = max(millimetres, 0.5)
        units = cls.PRESS_LIMIT_MM / millimetres * 100.0
        if max_subpaths is None:
            # 12 subpaths at 5.5 mm, growing with the area the artwork has.
            max_subpaths = max(12, int(round(12.0 * (millimetres / 5.5) ** 2)))
        # A lost region is excused up to the area of a short limb at this width.
        tolerance = max(4.0, units * units)
        return cls(
            min_feature_units=units,
            max_subpaths=max_subpaths,
            lost_area_tolerance_units=tolerance,
            **overrides,  # type: ignore[arg-type]
        )


MM_PER_IN = 25.4


def profiles_for_roles(
    *,
    bands: "Sequence[object]",
    maze_square_in: float,
    page_vector_in: float,
) -> dict[str, AssetProfile]:
    """The profile each asset role is judged by, from how big it actually prints.

    Every number here is read off the book's own layout rather than assumed: the
    smallest cell in the profile decides how small a collectible ever gets, the
    marker fraction decides how big an endpoint marker is, and the story page
    fixes the decoration. Artwork is then held to the rule its printed size
    earns, which is the difference between rejecting a 0.6 in haunted house for
    a hairline it does not have and catching a real one on a 5 mm sweet.
    """
    square_mm = maze_square_in * MM_PER_IN
    cells_mm = [square_mm / max(b.rows, b.cols) for b in bands]  # type: ignore[attr-defined]
    smallest_cell = min(cells_mm)
    marker_mm = max(
        (b.outside_marker_fraction * square_mm for b in bands),  # type: ignore[attr-defined]
        default=0.0,
    )
    if marker_mm <= 0.0:
        marker_mm = smallest_cell * min(
            min(b.start_scale, b.finish_scale) for b in bands  # type: ignore[attr-defined]
        )

    return {
        "collectible": AssetProfile.for_print_size(
            smallest_cell * min(b.collectible_scale for b in bands)  # type: ignore[attr-defined]
        ),
        "dead-end": AssetProfile.for_print_size(
            smallest_cell * min(b.dead_end_scale for b in bands)  # type: ignore[attr-defined]
        ),
        "start": AssetProfile.for_print_size(marker_mm),
        "finish": AssetProfile.for_print_size(marker_mm),
        "page-vector": AssetProfile.for_print_size(page_vector_in * MM_PER_IN),
    }


@dataclass
class AssetReport:
    """The verdict on one file, with measurements attached.

    Measurements are reported whether or not the file passed, because "which
    rule" is only half of a useful answer -- "and the measured value was 4.2
    units" is what tells an illustrator what to change.
    """

    path: Path
    passed: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    constraints: dict[str, bool] = field(default_factory=dict)
    subpath_count: int = 0
    bbox: tuple[float, float, float, float] | None = None
    padding: tuple[float, float, float, float] | None = None
    coverage_fraction: float = 0.0
    #: Diameter of the largest circle that fits inside the artwork. For a
    #: uniformly thin shape this *is* its thickness; for a mixed one it says how
    #: substantial the heaviest part is.
    widest_feature_units: float | None = None

    def summary(self) -> str:
        state = "ok" if self.passed else f"FAIL ({len(self.errors)})"
        parts = [f"{self.path.name}: {state}"]
        if self.bbox is not None:
            parts.append(f"bbox {tuple(round(v, 1) for v in self.bbox)}")
        if self.padding is not None:
            parts.append(f"pad {min(self.padding):.1f}")
        parts.append(f"{self.subpath_count} subpaths")
        parts.append(f"ink {self.coverage_fraction:.0%}")
        if self.widest_feature_units is not None:
            parts.append(f"widest {self.widest_feature_units:.1f}")
        return " | ".join(parts)

    def raise_for_status(self) -> None:
        if not self.passed:
            raise AssetError(
                f"{self.path.name} failed asset validation ({len(self.errors)} rule(s))",
                details=self.errors,
            )


# --------------------------------------------------------------------------- #
# Rasterization
# --------------------------------------------------------------------------- #

class Mask:
    """A binary raster of the artwork, in samples of ``1 / scale`` units.

    Rasterizing is the only honest way to answer "is anything thinner than 7
    units?" for arbitrary Bezier artwork: the question is about the *filled
    region*, not about the path that bounds it, and two paths that never touch
    can still leave a 2-unit gap between them.
    """

    __slots__ = ("width", "height", "scale", "bits")

    def __init__(self, width: int, height: int, scale: float) -> None:
        self.width = width
        self.height = height
        self.scale = scale
        self.bits = bytearray(width * height)

    def __getitem__(self, index: tuple[int, int]) -> int:
        x, y = index
        return self.bits[y * self.width + x]

    def count(self) -> int:
        return sum(self.bits)

    def inverted(self) -> "Mask":
        other = Mask(self.width, self.height, self.scale)
        other.bits = bytearray(1 - value for value in self.bits)
        return other


def rasterize(document: ParsedSvg, *, samples_per_unit: float) -> Mask:
    """Scan-convert the filled paths into a mask.

    Each subpath is flattened to a polygon and filled by the scanline rule its
    ``fill-rule`` names. Subpaths are accumulated per fill-rule group so that a
    hole -- an inner subpath wound against its container under ``nonzero`` --
    still reads as a hole, which is the only way this subset can put a light
    shape inside a dark one.
    """
    _, _, vb_width, vb_height = document.view_box
    origin_x, origin_y = document.view_box[0], document.view_box[1]
    width = max(1, int(math.ceil(vb_width * samples_per_unit)))
    height = max(1, int(math.ceil(vb_height * samples_per_unit)))
    mask = Mask(width, height, samples_per_unit)

    # Flatten finely enough that curvature is below one sample.
    steps = max(8, int(math.ceil(samples_per_unit * 4)))

    groups: dict[str, list[list[tuple[float, float]]]] = {"nonzero": [], "evenodd": []}
    for item in document.paths:
        polygon = [
            ((x - origin_x) * samples_per_unit, (y - origin_y) * samples_per_unit)
            for x, y in item.subpath.polyline(steps)
        ]
        if len(polygon) >= 3:
            groups[item.fill_rule].append(polygon)

    for rule, polygons in groups.items():
        if polygons:
            _fill(mask, polygons, rule)
    return mask


def _fill(mask: Mask, polygons: list[list[tuple[float, float]]], rule: str) -> None:
    """Scanline fill of a polygon set, sampling at pixel centres."""
    edges: list[tuple[float, float, float, float, int]] = []
    for polygon in polygons:
        points = polygon if polygon[0] == polygon[-1] else polygon + [polygon[0]]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if y0 == y1:
                continue
            direction = 1 if y1 > y0 else -1
            edges.append((x0, y0, x1, y1, direction))
    if not edges:
        return

    for row in range(mask.height):
        sample_y = row + 0.5
        crossings: list[tuple[float, int]] = []
        for x0, y0, x1, y1, direction in edges:
            low, high = (y0, y1) if y0 < y1 else (y1, y0)
            if not (low <= sample_y < high):
                continue
            t = (sample_y - y0) / (y1 - y0)
            crossings.append((x0 + t * (x1 - x0), direction))
        if not crossings:
            continue
        crossings.sort()

        base = row * mask.width
        winding = 0
        for index, (x_start, direction) in enumerate(crossings):
            winding += direction if rule == "nonzero" else 1
            inside = winding != 0 if rule == "nonzero" else winding % 2 == 1
            if not inside or index + 1 >= len(crossings):
                continue
            x_end = crossings[index + 1][0]
            first = max(0, int(math.ceil(x_start - 0.5)))
            last = min(mask.width - 1, int(math.floor(x_end - 0.5)))
            for column in range(first, last + 1):
                mask.bits[base + column] = 1


# --------------------------------------------------------------------------- #
# Distance transform and morphology
# --------------------------------------------------------------------------- #

_INF = float("inf")


def _edt_1d(values: list[float]) -> list[float]:
    """Felzenszwalb & Huttenlocher's exact 1D squared distance transform.

    Linear time via the lower envelope of the parabolas ``(x - i)^2 + f(i)``.
    The naive alternative -- dilating with an explicit disc -- is 150 offsets per
    pixel and turns a 40k-pixel icon into seconds of pure-Python work.
    """
    n = len(values)
    if n == 0:
        return []
    result = [0.0] * n
    v = [0] * n
    z = [0.0] * (n + 1)
    k = 0
    v[0] = 0
    z[0] = -_INF
    z[1] = _INF
    for q in range(1, n):
        if values[q] == _INF:
            continue
        while True:
            if values[v[k]] == _INF:
                k -= 1
                if k < 0:
                    break
                continue
            s = ((values[q] + q * q) - (values[v[k]] + v[k] * v[k])) / (2.0 * q - 2.0 * v[k])
            if k > 0 and s <= z[k]:
                k -= 1
                continue
            break
        if k < 0:
            k = 0
            v[0] = q
            z[0] = -_INF
            z[1] = _INF
        else:
            k += 1
            v[k] = q
            z[k] = s
            z[k + 1] = _INF
    if values[v[0]] == _INF:
        return [_INF] * n
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        result[q] = (q - v[k]) ** 2 + values[v[k]]
    return result


def distance_transform(mask: Mask, *, foreground: int = 1) -> list[float]:
    """Euclidean distance, in units, from each sample to the nearest ``1 - fg``.

    Returns a flat row-major list. Samples that are not foreground get 0.0.
    """
    width, height = mask.width, mask.height
    seed = [0.0 if mask.bits[i] != foreground else _INF for i in range(width * height)]

    # Columns, then rows: the composition of two 1D transforms is the exact 2D
    # Euclidean transform (F&H section 2).
    for column in range(width):
        values = [seed[row * width + column] for row in range(height)]
        transformed = _edt_1d(values)
        for row in range(height):
            seed[row * width + column] = transformed[row]
    for row in range(height):
        base = row * width
        transformed = _edt_1d(seed[base:base + width])
        for column in range(width):
            seed[base + column] = transformed[column]

    scale = mask.scale
    return [math.sqrt(value) / scale if value != _INF else _INF for value in seed]


def _opened_losses(
    mask: Mask, radius_units: float
) -> tuple[list[int], float, bytearray]:
    """Samples removed by opening ``mask`` with a disc of ``radius_units``.

    Opening is erosion then dilation. Erosion keeps the samples at least
    ``radius`` from the background; dilation re-grows everything within
    ``radius`` of what survived. Whatever does not come back was thinner than
    ``2 * radius`` -- which is exactly the minimum-feature question.

    Returns the lost samples, the largest distance-to-background found anywhere
    in the foreground (half the *widest* part of the artwork), and the opened set
    itself -- the last of which is what lets a caller distinguish a fringe shaved
    off a thick shape from a region that vanished outright.
    """
    inner = distance_transform(mask, foreground=1)
    widest = max((d for d in inner if d != _INF), default=0.0)

    eroded = Mask(mask.width, mask.height, mask.scale)
    eroded.bits = bytearray(1 if d >= radius_units else 0 for d in inner)
    if not any(eroded.bits):
        # Nothing survived erosion: the whole shape is thinner than the disc.
        return [i for i, v in enumerate(mask.bits) if v], widest, bytearray(len(mask.bits))

    outer = distance_transform(eroded.inverted(), foreground=1)
    lost = [
        index
        for index, value in enumerate(mask.bits)
        if value and not (outer[index] != _INF and outer[index] <= radius_units)
        and not eroded.bits[index]
    ]
    opened = bytearray(
        1 if value and index not in set(lost) else 0
        for index, value in enumerate(mask.bits)
    )
    return lost, widest, opened


def _components(indices: list[int], width: int) -> list[list[int]]:
    """4-connected components of a sparse sample set."""
    remaining = set(indices)
    groups: list[list[int]] = []
    while remaining:
        seed = remaining.pop()
        group = [seed]
        stack = [seed]
        while stack:
            index = stack.pop()
            x, y = index % width, index // width
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if nx < 0 or nx >= width:
                    continue
                neighbor = ny * width + nx
                if neighbor in remaining:
                    remaining.discard(neighbor)
                    group.append(neighbor)
                    stack.append(neighbor)
        groups.append(group)
    return groups


# --------------------------------------------------------------------------- #
# The validator
# --------------------------------------------------------------------------- #

def validate_svg(
    path: Path | str,
    *,
    profile: AssetProfile | None = None,
    text: str | None = None,
    geometry: bool = True,
) -> AssetReport:
    """Validate one asset. Never raises for a *rule* failure -- see the report.

    ``geometry=False`` skips the rasterized checks. It exists for the CLI's fast
    path over hundreds of files, not as a way to ship unmeasured artwork: the
    build always runs with geometry on.
    """
    profile = profile or AssetProfile()
    report = AssetReport(path=Path(path))

    try:
        document = svg_subset.parse_svg(path, text=text)
    except AssetError as exc:
        report.passed = False
        report.constraints[CODE_PARSE] = False
        detail = f" ({exc.details})" if exc.details else ""
        report.errors.append(f"{CODE_PARSE}: {exc.message}{detail}")
        return report
    report.constraints[CODE_PARSE] = True

    _check_viewbox(document, profile, report)
    _check_composition(document, profile, report)
    if geometry and report.subpath_count:
        _check_geometry(document, profile, report)

    report.passed = not report.errors
    return report


def _fail(report: AssetReport, code: str, message: str) -> None:
    report.constraints[code] = False
    report.errors.append(f"{code}: {message}")


def _ok(report: AssetReport, code: str) -> None:
    report.constraints[code] = True


def _check_viewbox(document: ParsedSvg, profile: AssetProfile, report: AssetReport) -> None:
    expected = profile.view_box
    actual = document.view_box
    if any(abs(a - b) > 1e-6 for a, b in zip(actual, expected)):
        _fail(
            report,
            CODE_VIEWBOX,
            f"viewBox is {' '.join(f'{v:g}' for v in actual)}, profile requires "
            f"{' '.join(f'{v:g}' for v in expected)} -- scale is computed from the "
            f"viewBox, so a different one silently resizes every placement",
        )
    else:
        _ok(report, CODE_VIEWBOX)


def _check_composition(document: ParsedSvg, profile: AssetProfile, report: AssetReport) -> None:
    report.subpath_count = len(document.paths)

    if report.subpath_count == 0:
        _fail(report, CODE_EMPTY, "no filled artwork: the asset would print blank")
        for code in (CODE_PADDING, CODE_CENTERED, CODE_SUBPATHS, CODE_COVERAGE):
            report.constraints.setdefault(code, False)
        return
    _ok(report, CODE_EMPTY)

    if report.subpath_count > profile.max_subpaths:
        _fail(
            report,
            CODE_SUBPATHS,
            f"{report.subpath_count} subpaths, budget {profile.max_subpaths} -- detail "
            f"beyond this is invisible at 5.5 mm and only slows the PDF",
        )
    else:
        _ok(report, CODE_SUBPATHS)

    box = document.bbox()
    assert box is not None
    report.bbox = box
    min_x, min_y, max_x, max_y = box
    vb_x, vb_y, vb_w, vb_h = document.view_box
    padding = (
        min_x - vb_x,
        vb_y + vb_h - max_y,
        vb_x + vb_w - max_x,
        min_y - vb_y,
    )  # left, bottom, right, top
    report.padding = padding

    names = ("left", "bottom", "right", "top")
    short = [
        f"{name} {value:.1f}"
        for name, value in zip(names, padding)
        if value < profile.min_padding_units - 1e-6
    ]
    if short:
        _fail(
            report,
            CODE_PADDING,
            f"padding below {profile.min_padding_units:g} units ({', '.join(short)}) -- "
            f"artwork this close to the viewBox edge collides with the cell wall "
            f"once scaled into a maze cell",
        )
    else:
        _ok(report, CODE_PADDING)

    centre_x, centre_y = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
    offset = math.hypot(centre_x - (vb_x + vb_w / 2.0), centre_y - (vb_y + vb_h / 2.0))
    if offset > profile.max_centre_offset_units:
        _fail(
            report,
            CODE_CENTERED,
            f"artwork centre is {offset:.1f} units from the viewBox centre, ceiling "
            f"{profile.max_centre_offset_units:g} -- placement centres the viewBox, so "
            f"an off-centre drawing lands off-centre in the cell",
        )
    else:
        _ok(report, CODE_CENTERED)


def _check_geometry(document: ParsedSvg, profile: AssetProfile, report: AssetReport) -> None:
    mask = rasterize(document, samples_per_unit=profile.samples_per_unit)
    ink = mask.count()
    sample_area = 1.0 / (profile.samples_per_unit ** 2)
    total = mask.width * mask.height
    report.coverage_fraction = ink / total if total else 0.0

    if ink == 0:
        _fail(
            report,
            CODE_COVERAGE,
            "the paths enclose no area: artwork made only of zero-width shapes "
            "prints blank (18.5 requires filled paths, not outlines)",
        )
        report.constraints.setdefault(CODE_THIN_FEATURE, False)
        report.constraints.setdefault(CODE_THIN_GAP, False)
        return

    if report.coverage_fraction > profile.max_coverage_fraction:
        _fail(
            report,
            CODE_COVERAGE,
            f"artwork covers {report.coverage_fraction:.0%} of the viewBox, ceiling "
            f"{profile.max_coverage_fraction:.0%} -- this is what a background "
            f"rectangle or an inverted drawing looks like, and it would black out "
            f"the cell it sits in",
        )
    else:
        _ok(report, CODE_COVERAGE)

    radius = profile.min_feature_units / 2.0

    lost, widest, opened = _opened_losses(mask, radius)
    report.widest_feature_units = 2.0 * widest
    offenders = _offending(
        lost, opened, mask,
        sample_area=sample_area,
        tolerance=profile.lost_area_tolerance_units,
    )
    if offenders:
        _fail(
            report,
            CODE_THIN_FEATURE,
            f"{len(offenders)} region(s) thinner than {profile.min_feature_units:g} units "
            f"{_locations(offenders, mask, sample_area)} -- at the 5.5 mm finale size "
            f"a feature this thin is a hairline the press drops",
        )
    else:
        _ok(report, CODE_THIN_FEATURE)

    # Gaps and holes: the same test on the negative, restricted to the artwork's
    # own bounding box. Outside that box is the padding ring, which is white by
    # design and not a gap; inside it, every white region is either a hole or a
    # channel between two limbs, and both of those fill in with toner. Filtering
    # by "is this white connected to the border?" instead would excuse a 3-unit
    # slot between two blocks just because the slot is open at one end -- and
    # that slot is precisely the blob this rule exists to prevent.
    gaps, _, gaps_opened = _opened_losses(mask.inverted(), radius)
    gap_offenders = [
        group
        for group in _offending(
            _inside_bbox(gaps, document, mask), gaps_opened, mask,
            sample_area=sample_area,
            tolerance=profile.lost_area_tolerance_units,
        )
    ]
    if gap_offenders:
        _fail(
            report,
            CODE_THIN_GAP,
            f"{len(gap_offenders)} gap(s) or hole(s) narrower than "
            f"{profile.min_feature_units:g} units "
            f"{_locations(gap_offenders, mask, sample_area)} -- a gap this narrow fills "
            f"in with toner and the two shapes merge into one blob",
        )
    else:
        _ok(report, CODE_THIN_GAP)


def _offending(
    lost: list[int],
    opened: bytearray,
    mask: Mask,
    *,
    sample_area: float,
    tolerance: float,
) -> list[list[int]]:
    """The lost components that represent a real thin feature.

    Two different failures hide in the loss set, and one flat area threshold
    cannot catch both:

    * A *fringe* still touching the opened set is material shaved off something
      thicker. Every sharp convex corner produces one -- a right angle loses
      ``r^2 (1 - pi/4)`` = 2.6 sq units at r=3.5, an acute wedge rather more --
      so a fringe only counts once it is big enough to be a limb rather than a
      rounded corner.
    * A component touching *no* survivor vanished completely, which means every
      part of it was thinner than the disc. Area is irrelevant there: a 4-unit
      hole is only 16 sq units, far under any useful fringe tolerance, and it is
      exactly the hole that closes up on press.
    """
    offenders: list[list[int]] = []
    lost_set = set(lost)
    for group in _components(lost, mask.width):
        touches_survivor = False
        for index in group:
            x, y = index % mask.width, index // mask.width
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if nx < 0 or nx >= mask.width or ny < 0 or ny >= mask.height:
                    continue
                neighbor = ny * mask.width + nx
                if opened[neighbor] and neighbor not in lost_set:
                    touches_survivor = True
                    break
            if touches_survivor:
                break
        if not touches_survivor or len(group) * sample_area > tolerance:
            offenders.append(group)
    return offenders


def _inside_bbox(indices: list[int], document: ParsedSvg, mask: Mask) -> list[int]:
    """Keep only the samples lying within the artwork's bounding box."""
    box = document.bbox()
    if box is None:
        return []
    origin_x, origin_y = document.view_box[0], document.view_box[1]
    scale = mask.scale
    left = (box[0] - origin_x) * scale
    bottom = (box[1] - origin_y) * scale
    right = (box[2] - origin_x) * scale
    top = (box[3] - origin_y) * scale
    kept: list[int] = []
    for index in indices:
        x = index % mask.width + 0.5
        y = index // mask.width + 0.5
        if left <= x <= right and bottom <= y <= top:
            kept.append(index)
    return kept


def _locations(groups: list[list[int]], mask: Mask, sample_area: float) -> str:
    """Report where the offending regions are, in viewBox units."""
    parts: list[str] = []
    for group in sorted(groups, key=len, reverse=True)[:3]:
        xs = [index % mask.width for index in group]
        ys = [index // mask.width for index in group]
        parts.append(
            f"near ({sum(xs) / len(xs) / mask.scale:.0f}, "
            f"{sum(ys) / len(ys) / mask.scale:.0f}) "
            f"[{len(group) * sample_area:.0f} sq units]"
        )
    return "; ".join(parts)


# --------------------------------------------------------------------------- #
# Directory sweeps
# --------------------------------------------------------------------------- #

def validate_directory(
    directory: Path | str,
    *,
    profile: AssetProfile | None = None,
    geometry: bool = True,
) -> list[AssetReport]:
    directory = Path(directory)
    if not directory.is_dir():
        raise AssetError(f"asset directory not found: {directory}")
    return [
        validate_svg(path, profile=profile, geometry=geometry)
        for path in sorted(directory.glob("*.svg"))
    ]


def raise_for_reports(reports: list[AssetReport], *, label: str) -> None:
    """Raise once, listing every failing file, so one run fixes every problem."""
    failures = [report for report in reports if not report.passed]
    if not failures:
        return
    details = [line for report in failures for line in
               [f"{report.path.name}: {error}" for error in report.errors]]
    raise AssetError(
        f"{len(failures)} of {len(reports)} {label} asset(s) failed validation",
        details=details,
    )
