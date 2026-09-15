"""The SVG subset, parsed once for both the validator and the PDF converter.

PRD 18.5 requires that "the validator's subset definition MUST be the single
source shared with the internal SVG->PDF converter", so that a file which
validates is *by construction* convertible. This module is that single source: it
parses an asset into flattened cubic subpaths in viewBox coordinates and knows
nothing about either rules or PDF. ``validate.py`` adds the rules;
``rendering/svg_to_pdf.py`` draws the result.

Splitting it the other way -- a validator that reads XML and a converter that
reads XML separately -- is how an asset passes QA and then renders as an empty
box, because the two readers disagreed about one attribute.

Everything is reduced to cubic Beziers and straight lines. Arcs become cubics
here (SVG endpoint parameterization -> centre parameterization -> at most four
quarter-arc cubics), so nothing downstream needs to know that ``A`` exists.

The subset is deliberately narrow:

*   elements: ``svg``, ``g``, ``path``, plus ``title``/``desc``, which carry no
    geometry and are skipped;
*   geometry: filled paths only. No ``stroke`` anywhere, because stroke width
    does not scale with the icon: a stroked drawing that looks right at 0.6 in
    becomes a blob at the 5.5 mm of an 18x18 finale maze;
*   paint: pure black or none. Grey, gradients and opacity all print as
    unpredictable halftone on cream stock.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import AssetError

SVG_NS = "http://www.w3.org/2000/svg"

#: Elements the subset admits. Anything else is a rejection, including the
#: ``<text>``/``<image>``/``<filter>``/``<script>`` families PRD 17.9 names.
ALLOWED_ELEMENTS = frozenset({"svg", "g", "path", "title", "desc", "metadata"})

#: Elements that exist but carry no geometry: skipped whole, children included.
IGNORED_ELEMENTS = frozenset({"title", "desc", "metadata"})

#: Attributes admitted on ``path`` and ``g``. ``id`` and ``class`` are harmless
#: labels; ``transform`` is admitted because real illustration tools emit it
#: constantly and rejecting it would make the validator hostile to actual
#: artwork -- the affine is resolved here instead.
ALLOWED_GEOMETRY_ATTRS = frozenset({"d", "fill", "fill-rule", "transform", "id", "class", "style"})

#: Paint values the subset admits, lower-cased.
BLACK_VALUES = frozenset({"#000", "#000000", "black", "rgb(0,0,0)", "currentcolor"})
NONE_VALUES = frozenset({"none", "transparent"})

#: Attributes that are always a rejection wherever they appear.
FORBIDDEN_ATTR_PREFIXES = ("stroke",)
FORBIDDEN_ATTRS = frozenset({"opacity", "fill-opacity", "filter", "mask", "clip-path", "href"})

Point = tuple[float, float]


# --------------------------------------------------------------------------- #
# Affine transforms
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class Matrix:
    """A 2D affine, in SVG's ``matrix(a b c d e f)`` order."""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    def apply(self, point: Point) -> Point:
        x, y = point
        return (self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f)

    def then(self, outer: "Matrix") -> "Matrix":
        """``outer`` applied after ``self`` -- i.e. the product ``outer * self``."""
        return Matrix(
            a=outer.a * self.a + outer.c * self.b,
            b=outer.b * self.a + outer.d * self.b,
            c=outer.a * self.c + outer.c * self.d,
            d=outer.b * self.c + outer.d * self.d,
            e=outer.a * self.e + outer.c * self.f + outer.e,
            f=outer.b * self.e + outer.d * self.f + outer.f,
        )


_TRANSFORM_RE = re.compile(r"([a-zA-Z]+)\s*\(([^)]*)\)")
_NUMBER_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def _numbers(text: str) -> list[float]:
    return [float(match.group()) for match in _NUMBER_RE.finditer(text)]


def parse_transform(text: str) -> Matrix:
    """Parse an SVG ``transform`` list into one matrix.

    Supports the whole affine vocabulary (``matrix``, ``translate``, ``scale``,
    ``rotate``, ``skewX``, ``skewY``) because a partial implementation would
    silently misplace artwork rather than fail loudly.
    """
    result = Matrix()
    for match in _TRANSFORM_RE.finditer(text):
        name = match.group(1)
        values = _numbers(match.group(2))
        if name == "matrix" and len(values) == 6:
            step = Matrix(*values)
        elif name == "translate" and values:
            step = Matrix(e=values[0], f=values[1] if len(values) > 1 else 0.0)
        elif name == "scale" and values:
            sx = values[0]
            sy = values[1] if len(values) > 1 else sx
            step = Matrix(a=sx, d=sy)
        elif name == "rotate" and values:
            angle = math.radians(values[0])
            cos, sin = math.cos(angle), math.sin(angle)
            rotation = Matrix(a=cos, b=sin, c=-sin, d=cos)
            if len(values) >= 3:
                cx, cy = values[1], values[2]
                step = (
                    Matrix(e=-cx, f=-cy)
                    .then(rotation)
                    .then(Matrix(e=cx, f=cy))
                )
            else:
                step = rotation
        elif name == "skewX" and values:
            step = Matrix(c=math.tan(math.radians(values[0])))
        elif name == "skewY" and values:
            step = Matrix(b=math.tan(math.radians(values[0])))
        else:
            raise AssetError(f"unsupported transform {match.group(0)!r}")
        result = step.then(result)  # SVG lists apply left-to-right
    return result


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class Cubic:
    """One cubic Bezier. Straight lines are cubics with collinear controls.

    Keeping a single segment type means every consumer -- bounding box, polyline
    flattening, PDF emission -- has exactly one case to handle.
    """

    p0: Point
    p1: Point
    p2: Point
    p3: Point

    @staticmethod
    def line(start: Point, end: Point) -> "Cubic":
        third = (
            start[0] + (end[0] - start[0]) / 3.0,
            start[1] + (end[1] - start[1]) / 3.0,
        )
        two_thirds = (
            start[0] + 2.0 * (end[0] - start[0]) / 3.0,
            start[1] + 2.0 * (end[1] - start[1]) / 3.0,
        )
        return Cubic(start, third, two_thirds, end)

    def at(self, t: float) -> Point:
        u = 1.0 - t
        w0, w1, w2, w3 = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
        return (
            w0 * self.p0[0] + w1 * self.p1[0] + w2 * self.p2[0] + w3 * self.p3[0],
            w0 * self.p0[1] + w1 * self.p1[1] + w2 * self.p2[1] + w3 * self.p3[1],
        )

    def transform(self, matrix: Matrix) -> "Cubic":
        return Cubic(
            matrix.apply(self.p0),
            matrix.apply(self.p1),
            matrix.apply(self.p2),
            matrix.apply(self.p3),
        )

    def polyline(self, steps: int) -> list[Point]:
        """Sample the curve, excluding ``p0`` so segments concatenate cleanly."""
        return [self.at(i / steps) for i in range(1, steps + 1)]


@dataclass
class Subpath:
    """A run of cubics between ``M`` commands, plus whether ``Z`` closed it."""

    segments: list[Cubic] = field(default_factory=list)
    closed: bool = False
    start: Point = (0.0, 0.0)

    @property
    def end(self) -> Point:
        return self.segments[-1].p3 if self.segments else self.start

    def polyline(self, steps_per_segment: int = 16) -> list[Point]:
        points = [self.start]
        for segment in self.segments:
            points.extend(segment.polyline(steps_per_segment))
        return points

    def bbox(self, steps_per_segment: int = 16) -> tuple[float, float, float, float]:
        points = self.polyline(steps_per_segment)
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return (min(xs), min(ys), max(xs), max(ys))

    def signed_area(self, steps_per_segment: int = 16) -> float:
        """Shoelace area; the sign gives the winding direction.

        Winding is how a hole is drawn under ``nonzero`` fill: an inner subpath
        wound against its container is a hole, and a hole is the only way this
        subset can put a light shape inside a dark one -- white paint would print
        as a white block on cream stock.
        """
        points = self.polyline(steps_per_segment)
        total = 0.0
        for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]):
            total += x0 * y1 - x1 * y0
        return total / 2.0


def arc_to_cubics(
    start: Point,
    rx: float,
    ry: float,
    rotation_deg: float,
    large_arc: bool,
    sweep: bool,
    end: Point,
) -> list[Cubic]:
    """SVG elliptical arc -> at most four cubics (W3C F.6 implementation notes).

    Out-of-range radii are corrected exactly as the specification requires
    (degenerate radius becomes a line, too-small radii are scaled up) rather than
    rejected, because those are the shapes real exporters produce and the
    specified correction is what every browser does.
    """
    x1, y1 = start
    x2, y2 = end
    if math.isclose(x1, x2) and math.isclose(y1, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0.0 or ry == 0.0:
        return [Cubic.line(start, end)]

    phi = math.radians(rotation_deg % 360.0)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)

    dx2, dy2 = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cos_phi * dx2 + sin_phi * dy2
    y1p = -sin_phi * dx2 + cos_phi * dy2

    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale

    numerator = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    denominator = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    factor = math.sqrt(max(0.0, numerator / denominator)) if denominator else 0.0
    if large_arc == sweep:
        factor = -factor
    cxp = factor * rx * y1p / ry
    cyp = -factor * ry * x1p / rx
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    def angle_of(x: float, y: float) -> float:
        return math.atan2((y - cyp) / ry, (x - cxp) / rx)

    theta1 = angle_of(x1p, y1p)
    theta2 = angle_of(-x1p, -y1p)
    delta = theta2 - theta1
    if sweep and delta < 0:
        delta += 2.0 * math.pi
    elif not sweep and delta > 0:
        delta -= 2.0 * math.pi

    count = max(1, int(math.ceil(abs(delta) / (math.pi / 2.0))))
    step = delta / count
    alpha = (4.0 / 3.0) * math.tan(step / 4.0)

    cubics: list[Cubic] = []
    current = start
    for index in range(count):
        t1 = theta1 + index * step
        t2 = t1 + step

        def point(theta: float) -> Point:
            ex = rx * math.cos(theta)
            ey = ry * math.sin(theta)
            return (cos_phi * ex - sin_phi * ey + cx, sin_phi * ex + cos_phi * ey + cy)

        def derivative(theta: float) -> Point:
            ex = -rx * math.sin(theta)
            ey = ry * math.cos(theta)
            return (cos_phi * ex - sin_phi * ey, sin_phi * ex + cos_phi * ey)

        p3 = point(t2)
        d1 = derivative(t1)
        d2 = derivative(t2)
        p1 = (current[0] + alpha * d1[0], current[1] + alpha * d1[1])
        p2 = (p3[0] - alpha * d2[0], p3[1] - alpha * d2[1])
        cubics.append(Cubic(current, p1, p2, p3))
        current = p3
    return cubics


# --------------------------------------------------------------------------- #
# Path data
# --------------------------------------------------------------------------- #

_COMMAND_RE = re.compile(r"([MmZzLlHhVvCcSsQqTtAa])|([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)")


def _tokenize(d: str) -> list[str | float]:
    tokens: list[str | float] = []
    position = 0
    for match in _COMMAND_RE.finditer(d):
        if match.start() > position and d[position:match.start()].strip(", \t\r\n"):
            raise AssetError(f"unexpected characters in path data: {d[position:match.start()]!r}")
        tokens.append(match.group(1) if match.group(1) else float(match.group(2)))
        position = match.end()
    return tokens


def parse_path(d: str) -> list[Subpath]:
    """Parse a ``d`` attribute into subpaths of cubics.

    Every command of SVG 1.1 path syntax is supported, absolute and relative.
    Anything unparseable raises rather than degrading, since a silently dropped
    command means artwork that is missing a limb in print.
    """
    tokens = _tokenize(d)
    subpaths: list[Subpath] = []
    current: Subpath | None = None
    position: Point = (0.0, 0.0)
    subpath_start: Point = (0.0, 0.0)
    last_control: Point | None = None
    previous_command = ""
    index = 0

    def need(count: int, command: str) -> list[float]:
        nonlocal index
        values = tokens[index:index + count]
        if len(values) != count or any(isinstance(v, str) for v in values):
            raise AssetError(f"path command {command!r} needs {count} numbers")
        index += count
        return [float(v) for v in values]

    def emit(segment: Cubic) -> None:
        nonlocal current
        if current is None:
            raise AssetError("path data must begin with a moveto command")
        current.segments.append(segment)

    while index < len(tokens):
        token = tokens[index]
        if isinstance(token, str):
            command = token
            index += 1
        else:
            # An implicit repeat: "M 0 0 10 10" repeats as lineto, per SVG 1.1.
            command = {"M": "L", "m": "l"}.get(previous_command, previous_command)
            if not command:
                raise AssetError("path data must begin with a moveto command")

        relative = command.islower()
        upper = command.upper()

        if upper == "M":
            x, y = need(2, command)
            position = (position[0] + x, position[1] + y) if relative else (x, y)
            current = Subpath(start=position)
            subpaths.append(current)
            subpath_start = position
            last_control = None
        elif upper == "Z":
            if current is not None:
                if current.segments and current.end != subpath_start:
                    emit(Cubic.line(current.end, subpath_start))
                current.closed = True
            position = subpath_start
            last_control = None
        elif upper == "L":
            x, y = need(2, command)
            target = (position[0] + x, position[1] + y) if relative else (x, y)
            emit(Cubic.line(position, target))
            position = target
            last_control = None
        elif upper == "H":
            (x,) = need(1, command)
            target = (position[0] + x, position[1]) if relative else (x, position[1])
            emit(Cubic.line(position, target))
            position = target
            last_control = None
        elif upper == "V":
            (y,) = need(1, command)
            target = (position[0], position[1] + y) if relative else (position[0], y)
            emit(Cubic.line(position, target))
            position = target
            last_control = None
        elif upper in ("C", "S"):
            if upper == "C":
                x1, y1, x2, y2, x, y = need(6, command)
                control1 = (position[0] + x1, position[1] + y1) if relative else (x1, y1)
            else:
                x2, y2, x, y = need(4, command)
                if last_control is not None and previous_command.upper() in ("C", "S"):
                    control1 = (2 * position[0] - last_control[0], 2 * position[1] - last_control[1])
                else:
                    control1 = position
            control2 = (position[0] + x2, position[1] + y2) if relative else (x2, y2)
            target = (position[0] + x, position[1] + y) if relative else (x, y)
            emit(Cubic(position, control1, control2, target))
            position = target
            last_control = control2
        elif upper in ("Q", "T"):
            if upper == "Q":
                x1, y1, x, y = need(4, command)
                control = (position[0] + x1, position[1] + y1) if relative else (x1, y1)
            else:
                x, y = need(2, command)
                if last_control is not None and previous_command.upper() in ("Q", "T"):
                    control = (2 * position[0] - last_control[0], 2 * position[1] - last_control[1])
                else:
                    control = position
            target = (position[0] + x, position[1] + y) if relative else (x, y)
            # Quadratic -> cubic: controls at two thirds toward the quadratic one.
            control1 = (
                position[0] + 2.0 * (control[0] - position[0]) / 3.0,
                position[1] + 2.0 * (control[1] - position[1]) / 3.0,
            )
            control2 = (
                target[0] + 2.0 * (control[0] - target[0]) / 3.0,
                target[1] + 2.0 * (control[1] - target[1]) / 3.0,
            )
            emit(Cubic(position, control1, control2, target))
            position = target
            last_control = control
        elif upper == "A":
            rx, ry, rotation, large, sweep, x, y = need(7, command)
            target = (position[0] + x, position[1] + y) if relative else (x, y)
            for cubic in arc_to_cubics(
                position, rx, ry, rotation, bool(large), bool(sweep), target
            ):
                emit(cubic)
            position = target
            last_control = None
        else:
            raise AssetError(f"unsupported path command {command!r}")
        previous_command = command

    return [sub for sub in subpaths if sub.segments]


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class FilledPath:
    """One filled subpath, already in viewBox coordinates."""

    subpath: Subpath
    fill_rule: str = "nonzero"


@dataclass
class ParsedSvg:
    """An asset reduced to what the renderer draws and the validator measures."""

    view_box: tuple[float, float, float, float]
    paths: list[FilledPath] = field(default_factory=list)
    source: Path | None = None

    @property
    def width(self) -> float:
        return self.view_box[2]

    @property
    def height(self) -> float:
        return self.view_box[3]

    def bbox(self) -> tuple[float, float, float, float] | None:
        """Bounding box of all artwork, or ``None`` for an empty document."""
        boxes = [item.subpath.bbox() for item in self.paths]
        if not boxes:
            return None
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _style_attrs(value: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for declaration in value.split(";"):
        if ":" in declaration:
            name, _, val = declaration.partition(":")
            attrs[name.strip().lower()] = val.strip()
    return attrs


def parse_svg(source: Path | str, *, text: str | None = None) -> ParsedSvg:
    """Parse an asset file into ``ParsedSvg``, or raise ``AssetError``.

    This performs the *structural* half of validation -- everything needed to
    produce geometry at all. Composition rules (padding, minimum feature size,
    subpath budget) live in ``validate.py``, which measures what comes back here.
    """
    path = Path(source) if not isinstance(source, str) or text is None else None
    raw = text if text is not None else Path(source).read_text(encoding="utf-8")

    if "<!ENTITY" in raw or "<!DOCTYPE" in raw:
        raise AssetError("SVG must not declare entities or a DOCTYPE (billion-laughs risk)")

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise AssetError(f"SVG is not well-formed XML: {exc}") from exc

    if _local(root.tag) != "svg":
        raise AssetError(f"root element must be <svg>, got <{_local(root.tag)}>")

    view_box_attr = root.get("viewBox")
    if not view_box_attr:
        raise AssetError("SVG must declare a viewBox")
    values = _numbers(view_box_attr)
    if len(values) != 4:
        raise AssetError(f"viewBox must have four numbers, got {view_box_attr!r}")
    if values[2] <= 0 or values[3] <= 0:
        raise AssetError(f"viewBox width and height must be positive, got {view_box_attr!r}")

    document = ParsedSvg(view_box=(values[0], values[1], values[2], values[3]), source=path)
    _walk(root, Matrix(), document, inherited_fill=None)
    return document


def _walk(
    element: ET.Element, matrix: Matrix, document: ParsedSvg, inherited_fill: str | None
) -> None:
    for child in element:
        tag = _local(child.tag)
        if tag in IGNORED_ELEMENTS:
            continue
        if tag not in ALLOWED_ELEMENTS:
            raise AssetError(
                f"<{tag}> is outside the supported SVG subset "
                f"(allowed: {', '.join(sorted(ALLOWED_ELEMENTS - IGNORED_ELEMENTS))})"
            )

        attrs = {name.lower(): value for name, value in child.attrib.items()}
        if "style" in attrs:
            attrs.update(_style_attrs(attrs.pop("style")))

        for name in attrs:
            if name in FORBIDDEN_ATTRS or any(
                name.startswith(prefix) for prefix in FORBIDDEN_ATTR_PREFIXES
            ):
                raise AssetError(
                    f"<{tag}> uses {name!r}: the subset is filled paths only, because "
                    f"stroke width does not scale with the icon (PRD 18.5)"
                )

        local_matrix = matrix
        if "transform" in attrs:
            local_matrix = parse_transform(attrs["transform"]).then(matrix)

        fill = attrs.get("fill", inherited_fill)

        if tag == "g":
            _walk(child, local_matrix, document, inherited_fill=fill)
            continue

        # tag == "path"
        d = child.get("d")
        if not d:
            raise AssetError("<path> must have a d attribute")

        resolved = (fill or "#000000").strip().lower()
        if resolved in NONE_VALUES:
            continue
        if resolved not in BLACK_VALUES:
            raise AssetError(
                f"fill {resolved!r} is not pure black: the interior prints "
                f"black-and-white, and grey halftones on cream stock are unpredictable"
            )

        fill_rule = attrs.get("fill-rule", "nonzero").strip().lower()
        if fill_rule not in ("nonzero", "evenodd"):
            raise AssetError(f"unsupported fill-rule {fill_rule!r}")

        for subpath in parse_path(d):
            if local_matrix != Matrix():
                subpath = Subpath(
                    segments=[segment.transform(local_matrix) for segment in subpath.segments],
                    closed=subpath.closed,
                    start=local_matrix.apply(subpath.start),
                )
            document.paths.append(FilledPath(subpath=subpath, fill_rule=fill_rule))
        _walk(child, local_matrix, document, inherited_fill=fill)
