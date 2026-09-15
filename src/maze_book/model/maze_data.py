"""The canonical maze representation (PRD 17.5).

``MazeData`` is the single source of truth for a maze. It stores *open edges* --
passages -- not walls. Walls are derived from open edges exactly once, in
``rendering.geometry``; no other module may infer them, because two inference
sites is how the SVG and the PDF end up disagreeing about the same maze.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .geometry import Cell, Edge, canonical_edge, neighbors, sort_edges

SCHEMA_VERSION = "1.0"

ROLE_START = "start"
ROLE_FINISH = "finish"
ROLE_DEAD_END = "dead-end"
ROLE_COLLECTIBLE = "collectible"


@dataclass(frozen=True)
class PlacedAsset:
    """One icon placed in one cell.

    ``assetId`` is the normalized POSIX path of the SVG relative to the book
    package, so a maze JSON identifies its artwork unambiguously.
    """

    asset_id: str
    role: str
    cell: Cell
    value: int | None = None
    scale: float = 0.6
    rotation_deg: float = 0.0

    def to_json_obj(self) -> dict[str, Any]:
        obj: dict[str, Any] = {
            "assetId": self.asset_id,
            "role": self.role,
            "cell": [self.cell[0], self.cell[1]],
        }
        if self.value is not None:
            obj["value"] = self.value
        obj["scale"] = round(self.scale, 6)
        obj["rotationDeg"] = round(self.rotation_deg, 6)
        return obj

    @staticmethod
    def from_json_obj(obj: dict[str, Any]) -> "PlacedAsset":
        return PlacedAsset(
            asset_id=obj["assetId"],
            role=obj["role"],
            cell=(obj["cell"][0], obj["cell"][1]),
            value=obj.get("value"),
            scale=float(obj.get("scale", 0.6)),
            rotation_deg=float(obj.get("rotationDeg", 0.0)),
        )


@dataclass
class MazeData:
    """A maze as an open-edge grid graph plus endpoints and placed assets."""

    maze_id: str
    book_id: str
    maze_index: int
    seed: int
    rows: int
    cols: int
    start: Cell
    finish: Cell
    open_edges: list[Edge] = field(default_factory=list)
    blocked_cells: list[Cell] = field(default_factory=list)
    assets: list[PlacedAsset] = field(default_factory=list)
    profile_id: str = ""
    attempt: int = 1
    generator_id: str = ""
    generator_version: str = ""

    # -- graph access -----------------------------------------------------

    def edge_set(self) -> set[Edge]:
        return set(self.open_edges)

    def adjacency(self) -> dict[Cell, set[Cell]]:
        """Build the traversable adjacency map from the open edges.

        Blocked cells are excluded, so callers never have to remember to filter
        them out.
        """
        blocked = set(self.blocked_cells)
        adj: dict[Cell, set[Cell]] = {
            (r, c): set()
            for r in range(self.rows)
            for c in range(self.cols)
            if (r, c) not in blocked
        }
        for a, b in self.open_edges:
            if a in adj and b in adj:
                adj[a].add(b)
                adj[b].add(a)
        return adj

    def traversable_cells(self) -> list[Cell]:
        blocked = set(self.blocked_cells)
        return [
            (r, c)
            for r in range(self.rows)
            for c in range(self.cols)
            if (r, c) not in blocked
        ]

    def is_open(self, a: Cell, b: Cell) -> bool:
        return canonical_edge(a, b) in self.edge_set()

    def collectible_cells(self) -> dict[Cell, int]:
        """Map of cell -> candy value for every placed collectible."""
        return {
            asset.cell: (asset.value if asset.value is not None else 1)
            for asset in self.assets
            if asset.role == ROLE_COLLECTIBLE
        }

    def normalize(self) -> None:
        """Put mutable collections into canonical serialization order."""
        self.open_edges = sort_edges(self.open_edges)
        self.blocked_cells = sorted(set(self.blocked_cells))
        # Role order is fixed so two runs cannot differ by asset ordering alone.
        role_rank = {ROLE_START: 0, ROLE_FINISH: 1, ROLE_COLLECTIBLE: 2, ROLE_DEAD_END: 3}
        self.assets = sorted(
            self.assets, key=lambda a: (role_rank.get(a.role, 9), a.cell, a.asset_id)
        )

    # -- serialization ----------------------------------------------------

    def to_json_obj(self) -> dict[str, Any]:
        self.normalize()
        return {
            "schemaVersion": SCHEMA_VERSION,
            "mazeId": self.maze_id,
            "bookId": self.book_id,
            "mazeIndex": self.maze_index,
            "seed": self.seed,
            "grid": {
                "rows": self.rows,
                "cols": self.cols,
                "coordinateSystem": "row-major-top-left",
            },
            "start": [self.start[0], self.start[1]],
            "finish": [self.finish[0], self.finish[1]],
            "openEdges": [
                [[a[0], a[1]], [b[0], b[1]]] for a, b in self.open_edges
            ],
            "blockedCells": [[r, c] for r, c in self.blocked_cells],
            "assets": [asset.to_json_obj() for asset in self.assets],
            "metadata": {
                "profileId": self.profile_id,
                "attempt": self.attempt,
                "generatorId": self.generator_id,
                "generatorVersion": self.generator_version,
            },
        }

    @staticmethod
    def from_json_obj(obj: dict[str, Any]) -> "MazeData":
        meta = obj.get("metadata", {})
        return MazeData(
            maze_id=obj["mazeId"],
            book_id=obj["bookId"],
            maze_index=obj["mazeIndex"],
            seed=obj["seed"],
            rows=obj["grid"]["rows"],
            cols=obj["grid"]["cols"],
            start=(obj["start"][0], obj["start"][1]),
            finish=(obj["finish"][0], obj["finish"][1]),
            open_edges=[
                ((a[0], a[1]), (b[0], b[1])) for a, b in obj["openEdges"]
            ],
            blocked_cells=[(r, c) for r, c in obj.get("blockedCells", [])],
            assets=[PlacedAsset.from_json_obj(a) for a in obj.get("assets", [])],
            profile_id=meta.get("profileId", ""),
            attempt=meta.get("attempt", 1),
            generator_id=meta.get("generatorId", ""),
            generator_version=meta.get("generatorVersion", ""),
        )


def edges_from_adjacency(adj: dict[Cell, Iterable[Cell]]) -> list[Edge]:
    """Convert an adjacency map back to a canonical edge list."""
    return sort_edges(
        canonical_edge(a, b) for a, bs in adj.items() for b in bs
    )


def full_grid_edges(rows: int, cols: int) -> list[Edge]:
    """Every possible passage in an ``rows x cols`` grid (the wall-free graph)."""
    return sort_edges(
        canonical_edge((r, c), n)
        for r in range(rows)
        for c in range(cols)
        for n in neighbors((r, c), rows, cols)
    )
