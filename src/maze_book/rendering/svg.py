"""SVG rendering of one maze (PRD 9.2, 17.11).

SVG is the inspection and reusable-vector output, not the canonical maze data.
It draws exactly what the PDF draws, from the same ``rendering.geometry``
segments, so a proof read on screen is the page that prints.

Assets are *inlined as geometry*, not referenced. Each one is parsed through
``assets.svg_subset`` -- the same parser the validator measured it with -- and
re-emitted as cubics under a placement transform. That costs a little verbosity
and buys two things: the file is self-contained (no ``<use>``, no external href
for a viewer to fail to resolve), and nothing can appear in the output that the
validator did not already approve, because anything outside the subset raises
during the parse rather than being copied through.

Walls and routes are stroked here. 18.5's no-stroke rule governs *input assets*,
where stroke width would not scale with the icon; a wall's width is a deliberate
per-band typographic choice (``wallWidthPt``) that must stay constant as the grid
grows, which is precisely what a stroke expresses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from ..assets.catalog import AssetCatalog
from ..assets.svg_subset import Cubic, ParsedSvg, Subpath, parse_svg
from ..errors import RenderingError
from ..model.geometry import Cell
from ..model.maze_data import MazeData, PlacedAsset
from .geometry import (
    MazeGeometry,
    Point,
    Segment,
    asset_footprint,
    route_polyline,
    wall_segments,
)

BLACK = "#000000"


def _n(value: float) -> str:
    """Three decimals is 0.001 pt at page scale -- far below press tolerance,
    and enough to keep the file byte-stable across machines."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


# --------------------------------------------------------------------------- #
# Emitting parsed geometry
# --------------------------------------------------------------------------- #


def subpath_to_d(subpath: Subpath, *, scale: float = 1.0, offset: Point = (0.0, 0.0)) -> str:
    """One parsed subpath as an SVG ``d`` string, in cubics only."""
    def place(point: Point) -> str:
        return f"{_n(point[0] * scale + offset[0])} {_n(point[1] * scale + offset[1])}"

    parts = [f"M{place(subpath.start)}"]
    for segment in subpath.segments:
        parts.append(f"C{place(segment.p1)} {place(segment.p2)} {place(segment.p3)}")
    if subpath.closed:
        parts.append("Z")
    return " ".join(parts)


def document_to_paths(
    document: ParsedSvg, *, scale: float, offset: Point, indent: str = "  "
) -> list[str]:
    """Every filled subpath of a parsed asset, placed and re-emitted.

    Subpaths sharing a fill rule are emitted as one element so an ``evenodd``
    hole stays attached to the body it is a hole in.
    """
    groups: dict[str, list[str]] = {}
    for item in document.paths:
        groups.setdefault(item.fill_rule, []).append(
            subpath_to_d(item.subpath, scale=scale, offset=offset)
        )
    out: list[str] = []
    for rule, datas in groups.items():
        attr = "" if rule == "nonzero" else f' fill-rule="{rule}"'
        out.append(f'{indent}<path fill="{BLACK}"{attr} d="{" ".join(datas)}"/>')
    return out


class AssetGeometryCache:
    """Parse each asset file once per render run.

    A 50-maze book places on the order of a thousand assets drawn from a folder
    of six; re-parsing per placement is a thousand XML parses to learn six
    shapes.
    """

    def __init__(self) -> None:
        self._parsed: dict[Path, ParsedSvg] = {}

    def get(self, path: Path) -> ParsedSvg:
        resolved = Path(path).resolve()
        document = self._parsed.get(resolved)
        if document is None:
            document = parse_svg(resolved)
            if not document.paths:
                raise RenderingError(f"asset has no drawable geometry: {resolved.name}")
            self._parsed[resolved] = document
        return document


# --------------------------------------------------------------------------- #
# Options
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MazeRenderOptions:
    """Per-band drawing weights and sizes, in the geometry's own units.

    Widths arrive already converted from the profile's points, because the
    geometry may be normalized, in points, or in thumbnail units, and only the
    caller knows which.
    """

    wall_width: float
    route_width: float = 0.0
    scales: dict[str, float] = field(default_factory=dict)
    draw_assets: bool = True
    route: tuple[Cell, ...] | None = None
    candy_dot_radius: float = 0.0
    margin: float = 0.0


# --------------------------------------------------------------------------- #
# Body fragments -- reused by the standalone SVG and by the PDF page
# --------------------------------------------------------------------------- #


def walls_element(segments: Sequence[Segment], width: float) -> str:
    """Every wall as one ``<path>``.

    A single element with many ``M``/``L`` pairs rather than one per wall: the
    stroke settings are identical for all of them, and repeating the attribute
    set per segment is the bulk of a naive maze SVG's bytes.
    """
    data = " ".join(f"M{_n(x0)} {_n(y0)}L{_n(x1)} {_n(y1)}" for x0, y0, x1, y1 in segments)
    return (
        f'  <path fill="none" stroke="{BLACK}" stroke-width="{_n(width)}" '
        f'stroke-linecap="square" stroke-linejoin="miter" d="{data}"/>'
    )


def route_element(points: Sequence[Point], width: float) -> str:
    data = " ".join(
        ("M" if index == 0 else "L") + f"{_n(x)} {_n(y)}"
        for index, (x, y) in enumerate(points)
    )
    return (
        f'  <path fill="none" stroke="{BLACK}" stroke-width="{_n(width)}" '
        f'stroke-linecap="round" stroke-linejoin="round" d="{data}"/>'
    )


def dots_element(centres: Iterable[Point], radius: float) -> str:
    """Filled dots as cubic circles -- used for candies on a solution thumbnail,
    where a full icon at 2.1 in across nine mazes is an illegible smudge."""
    k = radius * 0.5522847498307936
    parts = []
    for x, y in centres:
        parts.append(
            f"M{_n(x - radius)} {_n(y)}"
            f"C{_n(x - radius)} {_n(y - k)} {_n(x - k)} {_n(y - radius)} {_n(x)} {_n(y - radius)}"
            f"C{_n(x + k)} {_n(y - radius)} {_n(x + radius)} {_n(y - k)} {_n(x + radius)} {_n(y)}"
            f"C{_n(x + radius)} {_n(y + k)} {_n(x + k)} {_n(y + radius)} {_n(x)} {_n(y + radius)}"
            f"C{_n(x - k)} {_n(y + radius)} {_n(x - radius)} {_n(y + k)} {_n(x - radius)} {_n(y)}Z"
        )
    return f'  <path fill="{BLACK}" d="{" ".join(parts)}"/>'


def asset_elements(
    maze: MazeData,
    geometry: MazeGeometry,
    *,
    catalog: AssetCatalog,
    cache: AssetGeometryCache,
    scales: dict[str, float],
) -> list[str]:
    """Every placed asset, inlined as geometry in placement order.

    Placement order is ``MazeData.assets`` order, which is normalized and sorted
    upstream, so the output is byte-stable for a fixed maze.
    """
    out: list[str] = []
    for asset in maze.assets:
        document = cache.get(catalog.path_for(asset.asset_id))
        footprint = asset_footprint(asset, geometry, scale=scales.get(asset.role, asset.scale))
        vb_x, vb_y, vb_w, vb_h = document.view_box
        scale = footprint.width / vb_w if vb_w else 0.0
        offset = (footprint.rect[0] - vb_x * scale, footprint.rect[1] - vb_y * scale)
        if vb_h and abs(vb_h - vb_w) > 1e-9:
            # The subset fixes a square viewBox; a non-square one would scale the
            # two axes differently and silently distort every placement.
            raise RenderingError(
                f"asset {asset.asset_id!r} has a non-square viewBox {vb_w}x{vb_h}"
            )
        out.extend(document_to_paths(document, scale=scale, offset=offset))
    return out


def maze_body(
    maze: MazeData,
    geometry: MazeGeometry,
    options: MazeRenderOptions,
    *,
    catalog: AssetCatalog | None = None,
    cache: AssetGeometryCache | None = None,
) -> list[str]:
    """The drawing itself, without a document wrapper.

    Order is deliberate: walls first, then assets over them, then the solution
    route last so it stays visible where it crosses a candy.
    """
    body = [walls_element(wall_segments(maze, geometry), options.wall_width)]

    if options.draw_assets and catalog is not None:
        body.extend(
            asset_elements(
                maze, geometry,
                catalog=catalog, cache=cache or AssetGeometryCache(), scales=options.scales,
            )
        )

    if options.route:
        if options.candy_dot_radius > 0.0:
            candies = maze.collectible_cells()
            on_route = [cell for cell in options.route if cell in candies]
            if on_route:
                body.append(
                    dots_element(
                        (geometry.cell_centre(cell) for cell in on_route),
                        options.candy_dot_radius,
                    )
                )
        body.append(route_element(route_polyline(options.route, geometry), options.route_width))

    return body


# --------------------------------------------------------------------------- #
# Standalone document
# --------------------------------------------------------------------------- #


def render_maze_svg(
    maze: MazeData,
    *,
    catalog: AssetCatalog | None = None,
    options: MazeRenderOptions,
    cell_units: float = 100.0,
    cache: AssetGeometryCache | None = None,
) -> str:
    """A standalone SVG for one maze, in its own coordinate space.

    The viewBox is the maze plus ``options.margin``, and there is no ``width`` or
    ``height``: the file scales to whatever it is placed in, which is the same
    contract 18.5 imposes on the assets it contains.
    """
    margin = options.margin
    geometry = MazeGeometry(
        rows=maze.rows, cols=maze.cols, origin=(margin, margin), cell=cell_units
    )
    width = geometry.width + 2 * margin
    height = geometry.height + 2 * margin
    body = maze_body(maze, geometry, options, catalog=catalog, cache=cache)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {_n(width)} {_n(height)}">\n'
        f"  <title>{maze.book_id} maze {maze.maze_index}</title>\n"
        + "\n".join(body)
        + "\n</svg>\n"
    )
