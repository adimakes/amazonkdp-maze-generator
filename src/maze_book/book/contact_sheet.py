"""The contact sheet: every maze and its answer on one image (PRD 17.14, 11).

11: "The build should render and inspect representative pages". The contact sheet
is the cheap version of that for the mazes themselves -- one PNG a human can scan
in a few seconds to spot the failure modes no constraint catches: a maze whose
solution hugs one edge, a run of five mazes that look alike, a finale that reads
easier than the act before it. Those are judgements about the *book*, and no
per-maze assertion makes them.

Each cell shows the walls, the best route, and the candies on it, at the same
size regardless of grid, so difficulty progression is visible as density rather
than as scale.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..model.analysis import MazeAnalysis
from ..model.maze_data import MazeData
from ..rendering.geometry import MazeGeometry, route_polyline, wall_segments

THUMBNAIL_PX = 190
PADDING_PX = 10
LABEL_PX = 16
COLUMNS = 10
WALL_PX = 2
ROUTE_PX = 3
CANDY_PX = 3


@dataclass(frozen=True, slots=True)
class ContactSheetSpec:
    columns: int = COLUMNS
    thumbnail: int = THUMBNAIL_PX
    padding: int = PADDING_PX
    label: int = LABEL_PX
    draw_routes: bool = True


def render_contact_sheet(
    entries: Sequence[tuple[MazeData, MazeAnalysis]],
    path: Path,
    *,
    spec: ContactSheetSpec | None = None,
) -> Path:
    from PIL import Image, ImageDraw

    spec = spec or ContactSheetSpec()
    if not entries:
        raise ValueError("contact sheet needs at least one maze")

    columns = min(spec.columns, len(entries))
    rows = -(-len(entries) // columns)
    cell_w = spec.thumbnail + 2 * spec.padding
    cell_h = spec.thumbnail + 2 * spec.padding + spec.label

    image = Image.new("L", (columns * cell_w, rows * cell_h), color=255)
    draw = ImageDraw.Draw(image)

    for position, (maze, analysis) in enumerate(entries):
        row, column = divmod(position, columns)
        ox = column * cell_w + spec.padding
        oy = row * cell_h + spec.padding
        box = (float(ox), float(oy), float(ox + spec.thumbnail), float(oy + spec.thumbnail))
        geometry = MazeGeometry.fitted(maze.rows, maze.cols, box=box)

        for x0, y0, x1, y1 in wall_segments(maze, geometry):
            draw.line((x0, y0, x1, y1), fill=0, width=WALL_PX)

        if spec.draw_routes and analysis.best_route:
            candies = maze.collectible_cells()
            for cell in analysis.best_route:
                if cell in candies:
                    cx, cy = geometry.cell_centre(cell)
                    draw.ellipse(
                        (cx - CANDY_PX, cy - CANDY_PX, cx + CANDY_PX, cy + CANDY_PX), fill=110
                    )
            points = route_polyline(list(analysis.best_route), geometry)
            if len(points) >= 2:
                draw.line([(x, y) for x, y in points], fill=90, width=ROUTE_PX, joint="curve")

        draw.text(
            (ox, oy + spec.thumbnail + 3),
            f"{maze.maze_index}  {maze.rows}x{maze.cols}  best {analysis.best_candy_total}",
            fill=0,
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True)
    return path
