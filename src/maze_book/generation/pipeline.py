"""The thirteen-step deterministic maze pipeline (PRD 17.7).

One attempt runs the whole chain -- carve, reshape, enumerate, place candy,
validate -- and either produces an accepted ``MazeData`` or explains, in one
sentence, which step said no. Nothing here relaxes a rule to get a result: PRD
17.8 is explicit that a candidate which cannot satisfy the profile is
regenerated from a new attempt seed and "MUST NOT be exported with relaxed
constraints".

The attempt number is part of every derived seed, so attempt 7 of maze 23 is a
genuinely different draw rather than a retry of the same one, and re-running the
book reproduces attempt 7 exactly. That is what makes the log below worth
keeping: "maze 23 accepted on attempt 7" is a reproducible statement.

Two deliberate structure choices:

*   Endpoint pairs are enumerated once per grid size, before any carving, and a
    grid whose regions admit no legal pair raises ``ConfigError`` rather than
    burning ``maxAttemptsPerMaze`` attempts on an impossibility. The eligible set
    depends only on the regions and the grid, never on the carve, so discovering
    it late would be a waste with no upside.
*   Every rejection is recorded as an ``AttemptRecord`` naming the *stage*, so a
    book that fails to generate answers "which step is the bottleneck?" from its
    own log instead of from a debugging session.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator

from ..assets.catalog import AssetCatalog
from ..errors import ConfigError, GenerationError
from ..model import seeds
from ..model.analysis import MazeAnalysis
from ..model.book_config import BookConfig
from ..model.geometry import Cell, Edge, resolve_region, sort_edges
from ..model.maze_data import (
    MazeData,
    PlacedAsset,
    ROLE_COLLECTIBLE,
    ROLE_DEAD_END,
    ROLE_FINISH,
    ROLE_START,
)
from ..model.profile import Band, Profile
from ..simulation import candy as candy_module
from ..simulation import constraints, graph
from ..simulation.routes import RouteSet, enumerate_routes, sorted_adjacency
from . import braid
from .adapter_mazelib import base_generator
from .base import BaseMazeGenerator

# Stage names used in attempt logs. They match the numbered steps of PRD 17.7.
STAGE_CARVE = "carve"
STAGE_SHAPE = "shape"
STAGE_GRAPH = "graph-analysis"
STAGE_ROUTES = "route-enumeration"
STAGE_CANDY = "candy-placement"
STAGE_VALIDATE = "semantic-validation"


# --------------------------------------------------------------------------- #
# Attempt bookkeeping
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """Why one attempt was rejected."""

    maze_index: int
    attempt: int
    stage: str
    reason: str
    elapsed_seconds: float = 0.0

    def __str__(self) -> str:
        return (
            f"maze {self.maze_index} attempt {self.attempt} rejected at "
            f"{self.stage}: {self.reason}"
        )


@dataclass
class MazeResult:
    """An accepted maze plus the evidence of how it was reached."""

    maze: MazeData
    analysis: MazeAnalysis
    band: Band
    attempts: int
    elapsed_seconds: float
    rejections: list[AttemptRecord] = field(default_factory=list)
    shape: braid.ShapeReport | None = None
    route_set: RouteSet | None = None

    def summary(self) -> str:
        return (
            f"maze {self.maze.maze_index:>2} {self.maze.rows}x{self.maze.cols} "
            f"accepted on attempt {self.attempts} in {self.elapsed_seconds:.2f}s: "
            f"k={self.analysis.loop_count} "
            f"dead-ends={len(self.analysis.dead_end_cells)} "
            f"routes={self.analysis.simple_path_count} "
            f"candy={self.analysis.best_candy_total}"
            f"(2nd {self.analysis.second_best_candy_total}) "
            f"best={self.analysis.best_route_length} "
            f"shortest={self.analysis.shortest_route_length}"
        )


ProgressHook = Callable[[AttemptRecord], None]


# --------------------------------------------------------------------------- #
# Step 4: endpoints
# --------------------------------------------------------------------------- #

def min_endpoint_distance(rows: int, cols: int) -> int:
    """The minimum-distance rule of step 4, made concrete.

    A start and finish two cells apart would make the puzzle a formality however
    good the topology is, so the rule is half the grid's diameter, rounded up:
    ``ceil(((rows - 1) + (cols - 1)) / 2)``. Manhattan distance is the right
    measure here because it is a property of the *grid*, computable before
    carving -- graph distance would depend on the very walls the attempt is about
    to draw, so a graph-distance rule could not be checked once per grid size.
    """
    return -(-((rows - 1) + (cols - 1)) // 2)


def eligible_endpoint_pairs(
    rows: int,
    cols: int,
    start_region: dict[str, int],
    finish_region: dict[str, int],
) -> list[tuple[Cell, Cell]]:
    """Every (start, finish) pair the regions allow, in sorted order.

    Raises ``ConfigError`` when the set is empty: that is a property of the
    config and the grid alone, so no number of attempts could fix it.
    """
    starts = resolve_region(start_region, rows, cols)
    finishes = resolve_region(finish_region, rows, cols)
    floor = min_endpoint_distance(rows, cols)
    pairs = sorted(
        (s, f)
        for s in starts
        for f in finishes
        if s != f and abs(s[0] - f[0]) + abs(s[1] - f[1]) >= floor
    )
    if not pairs:
        raise ConfigError(
            f"generation.startRegion and generation.finishRegion admit no pair of "
            f"cells at least {floor} steps apart on a {rows}x{cols} grid",
            details={
                "startCells": len(starts),
                "finishCells": len(finishes),
                "minDistance": floor,
            },
        )
    return pairs


# --------------------------------------------------------------------------- #
# Steps 11-12: assets
# --------------------------------------------------------------------------- #

def place_assets(
    *,
    maze: MazeData,
    catalog: AssetCatalog,
    band: Band,
    adjacency: graph.Adjacency,
    candy_cells: list[Cell],
    rng: random.Random,
) -> list[PlacedAsset]:
    """Steps 11 and 12: one asset per meaningful cell, deterministically chosen.

    Order matters for reproducibility, not for drawing: variants are drawn from
    ``rng`` in a fixed order (dead ends by cell, then candies by cell), so the
    same maze always gets the same faces. ``MazeData.normalize`` sorts the result
    for serialization afterwards.
    """
    assets = [
        PlacedAsset(
            asset_id=catalog.start.asset_id,
            role=ROLE_START,
            cell=maze.start,
            scale=band.start_scale,
        ),
        PlacedAsset(
            asset_id=catalog.finish.asset_id,
            role=ROLE_FINISH,
            cell=maze.finish,
            scale=band.finish_scale,
        ),
    ]

    # Step 11: every eligible dead end gets exactly one marker (17.8).
    dead_ends = graph.dead_end_cells(adjacency, exclude=(maze.start, maze.finish))
    for ordinal, cell in enumerate(dead_ends):
        chosen = catalog.choose_dead_end(rng, ordinal)
        assets.append(
            PlacedAsset(
                asset_id=chosen.asset_id,
                role=ROLE_DEAD_END,
                cell=cell,
                scale=band.dead_end_scale,
            )
        )

    for ordinal, cell in enumerate(sorted(candy_cells)):
        chosen = catalog.choose_collectible(rng, ordinal)
        assets.append(
            PlacedAsset(
                asset_id=chosen.asset_id,
                role=ROLE_COLLECTIBLE,
                cell=cell,
                value=1,  # 17.8: every collectible is worth 1 in v1.
                scale=band.collectible_scale,
            )
        )
    return assets


# --------------------------------------------------------------------------- #
# One attempt
# --------------------------------------------------------------------------- #

@dataclass
class _Attempt:
    """Outcome of a single attempt: either accepted, or a stage plus a reason."""

    maze: MazeData | None = None
    analysis: MazeAnalysis | None = None
    shape: braid.ShapeReport | None = None
    route_set: RouteSet | None = None
    stage: str = ""
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.maze is not None and self.analysis is not None


def _run_attempt(
    *,
    config: BookConfig,
    band: Band,
    catalog: AssetCatalog,
    generator: BaseMazeGenerator,
    maze_index: int,
    attempt: int,
    endpoint_pairs: list[tuple[Cell, Cell]],
) -> _Attempt:
    book_seed = config.book.seed
    book_id = config.book.id
    rows, cols = band.rows, band.cols

    def derived(purpose: str) -> random.Random:
        return seeds.rng(book_seed, book_id, maze_index, attempt, purpose)

    # ---- 1-3: seeds, carve, canonical graph ------------------------------- #
    topology_seed = seeds.derive_seed(
        book_seed, book_id, maze_index, attempt, seeds.PURPOSE_TOPOLOGY
    )
    try:
        tree_edges = sort_edges(generator.carve(rows, cols, topology_seed))
    except GenerationError as exc:
        return _Attempt(stage=STAGE_CARVE, reason=exc.message)

    # ---- 4: endpoints ----------------------------------------------------- #
    endpoint_rng = derived(seeds.PURPOSE_ENDPOINTS)
    start, finish = endpoint_pairs[endpoint_rng.randrange(len(endpoint_pairs))]

    # ---- 5: reshape to the band ------------------------------------------- #
    report = braid.shape(
        rows=rows,
        cols=cols,
        tree_edges=tree_edges,
        start=start,
        finish=finish,
        band=band,
        rng=derived(seeds.PURPOSE_TOPOLOGY),
    )
    if not report.ok:
        return _Attempt(shape=report, stage=STAGE_SHAPE, reason=report.detail)

    maze = MazeData(
        maze_id=f"{book_id}-{maze_index:03d}",
        book_id=book_id,
        maze_index=maze_index,
        seed=topology_seed,
        rows=rows,
        cols=cols,
        start=start,
        finish=finish,
        open_edges=sort_edges(report.edges),
        profile_id=config.book.profile_id,
        attempt=attempt,
        generator_id=generator.generator_id,
        generator_version=generator.generator_version,
    )
    adjacency = sorted_adjacency(maze)

    # ---- 6: independent graph analysis ------------------------------------ #
    problem = _graph_problem(maze, adjacency, band)
    if problem:
        return _Attempt(shape=report, stage=STAGE_GRAPH, reason=problem)

    # ---- 7-8: exact enumeration, or reject -------------------------------- #
    if not config.generation.exact_route_enumeration:
        raise ConfigError(
            "generation.exactRouteEnumeration must be true: candy scoring is only "
            "honest over a complete route set (PRD 17.7 step 8)"
        )
    route_set = enumerate_routes(
        maze,
        max_routes=band.max_routes,
        max_search_nodes=band.max_search_nodes,
        time_budget_seconds=band.time_budget_seconds,
        adjacency=adjacency,
    )
    if not route_set.exact:
        return _Attempt(
            shape=report,
            route_set=route_set,
            stage=STAGE_ROUTES,
            reason=(
                f"enumeration stopped on {route_set.abort_reason} after "
                f"{route_set.count} routes and {route_set.nodes_visited} nodes"
            ),
        )
    if not band.simple_routes.contains(route_set.count):
        return _Attempt(
            shape=report,
            route_set=route_set,
            stage=STAGE_ROUTES,
            reason=(
                f"{route_set.count} simple routes, need "
                f"{band.simple_routes.min}-{band.simple_routes.max}"
            ),
        )

    # ---- 9-10: candy ------------------------------------------------------ #
    candy_result = candy_module.place_candy(
        route_set=route_set,
        band=band,
        rng=derived(seeds.PURPOSE_CANDY),
        rows=rows,
        cols=cols,
        adjacency=adjacency,
        time_budget_seconds=band.time_budget_seconds,
    )
    if not candy_result.ok:
        return _Attempt(
            shape=report,
            route_set=route_set,
            stage=STAGE_CANDY,
            reason=candy_result.reason(),
        )
    placement = candy_result.placement
    assert placement is not None  # narrowed by .ok

    # ---- 11-12: assets ---------------------------------------------------- #
    maze.assets = place_assets(
        maze=maze,
        catalog=catalog,
        band=band,
        adjacency=adjacency,
        candy_cells=sorted(placement.cells),
        rng=derived(seeds.PURPOSE_ASSET_VARIANT),
    )
    maze.normalize()

    # ---- 13: semantic validation over the finished artifact --------------- #
    analysis = constraints.evaluate(maze, route_set, band, adjacency=adjacency)
    if not analysis.passed:
        return _Attempt(
            shape=report,
            route_set=route_set,
            stage=STAGE_VALIDATE,
            reason=constraints.summarize_failures(analysis),
        )
    return _Attempt(
        maze=maze, analysis=analysis, shape=report, route_set=route_set
    )


def _graph_problem(maze: MazeData, adjacency: graph.Adjacency, band: Band) -> str:
    """Step 6, measured fresh rather than trusted from the reshaper.

    ``braid.shape`` checks the band before returning, so this is a second
    opinion. It is cheap next to enumeration and it is the check that would catch
    a reshaper bug before a bad maze reached a child's book, which is exactly the
    kind of duplication worth paying for.
    """
    components = graph.connected_components(adjacency)
    if len(components) != 1:
        return f"{len(components)} connected components, expected 1"
    expected_cells = maze.rows * maze.cols - len(maze.blocked_cells)
    if len(adjacency) != expected_cells:
        return f"{len(adjacency)} traversable cells, expected {expected_cells}"
    if maze.finish not in graph.reachable_from(adjacency, maze.start):
        return "the finish is not reachable from the start"

    loops = graph.cyclomatic_number(adjacency)
    if not band.loops.contains(loops):
        return f"loop count k={loops}, need {band.loops.min}-{band.loops.max}"

    dead_ends = graph.dead_end_cells(adjacency, exclude=(maze.start, maze.finish))
    if not band.dead_ends.contains(len(dead_ends)):
        return (
            f"{len(dead_ends)} dead ends, need "
            f"{band.dead_ends.min}-{band.dead_ends.max}"
        )
    if band.dead_end_depth is not None:
        depths = [graph.dead_end_depth(adjacency, cell) for cell in dead_ends]
        outside = sorted(d for d in depths if not band.dead_end_depth.contains(d))
        if outside:
            return (
                f"dead-end depths {outside[:6]} outside "
                f"{band.dead_end_depth.min}-{band.dead_end_depth.max}"
            )
    return ""


# --------------------------------------------------------------------------- #
# The attempt loop
# --------------------------------------------------------------------------- #

def generate_maze(
    *,
    config: BookConfig,
    profile: Profile,
    catalog: AssetCatalog,
    maze_index: int,
    generator: BaseMazeGenerator | None = None,
    max_attempts: int | None = None,
    on_rejection: ProgressHook | None = None,
) -> MazeResult:
    """Generate one accepted maze, or raise ``GenerationError`` with the log.

    The error carries every rejection reason. A book that cannot be generated is
    a profile-tuning problem, and the tuning question -- "is it the loop band or
    the candy margin?" -- is answered by the distribution of stages in that log.
    """
    band = profile.band_for(maze_index)
    if generator is None:
        generator = base_generator(config.generation.base_generator)
    attempts_allowed = max_attempts or config.generation.max_attempts_per_maze
    endpoint_pairs = eligible_endpoint_pairs(
        band.rows, band.cols, config.generation.start_region, config.generation.finish_region
    )

    rejections: list[AttemptRecord] = []
    started = time.perf_counter()
    for attempt in range(1, attempts_allowed + 1):
        attempt_started = time.perf_counter()
        outcome = _run_attempt(
            config=config,
            band=band,
            catalog=catalog,
            generator=generator,
            maze_index=maze_index,
            attempt=attempt,
            endpoint_pairs=endpoint_pairs,
        )
        if outcome.accepted:
            assert outcome.maze is not None and outcome.analysis is not None
            return MazeResult(
                maze=outcome.maze,
                analysis=outcome.analysis,
                band=band,
                attempts=attempt,
                elapsed_seconds=time.perf_counter() - started,
                rejections=rejections,
                shape=outcome.shape,
                route_set=outcome.route_set,
            )
        record = AttemptRecord(
            maze_index=maze_index,
            attempt=attempt,
            stage=outcome.stage,
            reason=outcome.reason,
            elapsed_seconds=time.perf_counter() - attempt_started,
        )
        rejections.append(record)
        if on_rejection is not None:
            on_rejection(record)

    raise GenerationError(
        f"maze {maze_index} ({band.rows}x{band.cols}, act '{band.act_name}') failed "
        f"all {attempts_allowed} attempts",
        details={
            "stageCounts": stage_counts(rejections),
            "lastReasons": [str(record) for record in rejections[-5:]],
        },
    )


def stage_counts(records: list[AttemptRecord]) -> dict[str, int]:
    """How many attempts each stage rejected -- the profile-tuning summary."""
    counts: dict[str, int] = {}
    for record in records:
        counts[record.stage] = counts.get(record.stage, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def generate_book_mazes(
    *,
    config: BookConfig,
    profile: Profile,
    catalog: AssetCatalog,
    indices: list[int] | None = None,
    generator: BaseMazeGenerator | None = None,
    on_rejection: ProgressHook | None = None,
) -> Iterator[MazeResult]:
    """Yield accepted mazes in index order.

    A generator function rather than a list: the caller can write each maze's
    JSON as it arrives, so a 50-maze run that fails at maze 48 still leaves 47
    usable artifacts and a log that says exactly where it stopped.
    """
    if generator is None:
        generator = base_generator(config.generation.base_generator)
    wanted = indices if indices is not None else list(range(1, config.book.maze_count + 1))
    for maze_index in wanted:
        yield generate_maze(
            config=config,
            profile=profile,
            catalog=catalog,
            maze_index=maze_index,
            generator=generator,
            on_rejection=on_rejection,
        )
