"""Maze artifacts on disk, and the cache contract that governs reuse (PRD 17.12, 17.14).

17.12 lists exactly what must be unchanged for a cached maze to be reusable: the
normalized ``book.json`` hash, the profile hash, the generator version, the
relevant asset hashes, and the maze index with its derived seed. Those five are
recorded in ``normalized-book.json`` and in each maze's metadata, so a stale
artifact is detected rather than trusted.

Assets are in that list for a reason that is easy to miss: a maze's JSON records
asset *filenames*, not their contents. Redraw ``ghost.svg`` and every maze that
places it changes on the page while its JSON stays byte-identical. Hashing the
asset files is what makes that visible.

``--force`` bypasses the cache. Nothing else does: a cache that silently decides
it knows better is worse than no cache, because the artifact it serves is the one
nobody can reproduce.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from ..assets.catalog import AssetCatalog
from ..errors import ConfigError, GenerationError
from ..generation import pipeline
from ..generation.adapter_mazelib import base_generator
from ..model.analysis import MazeAnalysis
from ..model.book_config import BookConfig
from ..model.json_io import hash_file, hash_obj, read_json, write_json
from ..model.maze_data import MazeData
from ..model.profile import Profile
from ..model.seeds import derive_seed

#: Bumped when a change to generation or simulation would produce a different
#: maze from the same inputs. A cache entry from a different version is stale by
#: definition, whatever its other hashes say.
GENERATOR_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class CacheKey:
    """The 17.12 tuple, in the order the PRD lists it."""

    book_hash: str
    profile_hash: str
    generator_version: str
    assets_hash: str
    maze_index: int
    seed: int

    def to_json_obj(self) -> dict[str, Any]:
        return {
            "bookHash": self.book_hash,
            "profileHash": self.profile_hash,
            "generatorVersion": self.generator_version,
            "assetsHash": self.assets_hash,
            "mazeIndex": self.maze_index,
            "seed": self.seed,
        }

    def matches(self, obj: dict[str, Any] | None) -> bool:
        return isinstance(obj, dict) and obj == self.to_json_obj()


def assets_digest(catalog: AssetCatalog) -> str:
    """One hash over every asset the book can place, by name and content."""
    return hash_obj(
        [[asset.asset_id, hash_file(asset.path)] for asset in catalog.all_files()]
    )


@dataclass(frozen=True, slots=True)
class BookFingerprint:
    book_hash: str
    profile_hash: str
    assets_hash: str
    generator_version: str = GENERATOR_VERSION

    @staticmethod
    def build(config: BookConfig, profile: Profile, catalog: AssetCatalog) -> "BookFingerprint":
        return BookFingerprint(
            book_hash=hash_obj(config.raw),
            profile_hash=hash_obj(profile.raw),
            assets_hash=assets_digest(catalog),
        )

    def key_for(self, maze_index: int, seed: int) -> CacheKey:
        return CacheKey(
            book_hash=self.book_hash,
            profile_hash=self.profile_hash,
            generator_version=self.generator_version,
            assets_hash=self.assets_hash,
            maze_index=maze_index,
            seed=seed,
        )


class OutputPaths:
    """Every path a build writes, split by who the file is for (17.14).

    Three destinations, because three audiences:

    * ``output/`` is what gets uploaded. Two PDFs, nothing else -- if a file in
      there is not going to KDP, it is in the wrong folder, and the point of the
      folder is that you can open it and know what to do.
    * ``build/`` is the working set: the per-maze JSON and SVG, the per-page
      PDFs, the page plan, the preflight report, the contact sheet, and the
      editable interior a proofreader reads. Regenerable, and safe to delete.
    * ``input/`` is never written by a build. It is what you supplied.

    The split exists because these all used to land in one directory, where the
    two files that matter sat among a hundred that do not.
    """

    #: Folder names, so a book package reads the same whoever opens it.
    OUTPUT_DIR = "output"
    BUILD_DIR = "build"

    def __init__(self, root: Path, book_id: str | None = None) -> None:
        """``root`` is the book package folder. ``book_id`` is accepted for the
        older layout, where one shared directory held every book by name."""
        root = Path(root)
        self.book = root / book_id if book_id else root
        self.output = self.book / self.OUTPUT_DIR
        self.build = self.book / self.BUILD_DIR
        #: Kept as the working root, since most writers want ``build``.
        self.base = self.build
        self.mazes = self.build / "mazes"
        self.pages = self.build / "pages"

    def ensure_dirs(self) -> None:
        """Create the folders a build writes into.

        Both, always. A book whose ``output`` folder only appears once the
        interior is written is a book where an empty ``output`` and a failed
        build look the same from the outside.
        """
        self.output.mkdir(parents=True, exist_ok=True)
        self.build.mkdir(parents=True, exist_ok=True)

    def maze_json(self, index: int) -> Path:
        return self.mazes / f"{index:03d}.json"

    def analysis_json(self, index: int) -> Path:
        return self.mazes / f"{index:03d}.analysis.json"

    def maze_svg(self, index: int) -> Path:
        return self.mazes / f"{index:03d}.svg"

    def maze_pdf(self, index: int) -> Path:
        return self.mazes / f"{index:03d}.pdf"

    def page_pdf(self, number: int) -> Path:
        return self.pages / f"page-{number:03d}.pdf"

    # -- the two files that get uploaded ------------------------------------

    @property
    def interior(self) -> Path:
        return self.output / "book-interior.pdf"

    @property
    def cover(self) -> Path:
        return self.output / "book-cover.pdf"

    # -- everything else ----------------------------------------------------

    @property
    def normalized_book(self) -> Path:
        return self.build / "normalized-book.json"

    @property
    def page_plan(self) -> Path:
        return self.build / "page-plan.json"

    @property
    def contact_sheet(self) -> Path:
        return self.build / "contact-sheet.png"

    @property
    def preflight(self) -> Path:
        return self.build / "preflight.json"

    @property
    def editable(self) -> Path:
        """Live text for proofreading. Not an upload, so not in ``output``."""
        return self.build / "book-interior-editable.pdf"

    @property
    def interior_text(self) -> Path:
        return self.build / "book-interior.txt"

    @property
    def validate_page_prefix(self) -> Path:
        """Preflight's page rasterizations (17.15). In their own folder because
        there is one per page, and a hundred and ten of them loose in ``build``
        buries the handful of files anyone actually opens."""
        return self.build / "page-previews" / "page"


def write_normalized_book(
    paths: OutputPaths,
    *,
    config: BookConfig,
    profile: Profile,
    fingerprint: BookFingerprint,
    front_matter_pages: int,
) -> None:
    """The record of exactly what this build was made from."""
    write_json(
        paths.normalized_book,
        {
            "bookId": config.book.id,
            "generatorVersion": fingerprint.generator_version,
            "bookHash": fingerprint.book_hash,
            "profileHash": fingerprint.profile_hash,
            "assetsHash": fingerprint.assets_hash,
            "profileId": profile.profile_id,
            "seed": config.book.seed,
            "mazeCount": config.book.maze_count,
            "frontMatterPages": front_matter_pages,
            "book": config.raw,
        },
    )


@dataclass(slots=True)
class LoadedMaze:
    maze: MazeData
    analysis: MazeAnalysis
    from_cache: bool


class MazeStore:
    """Reads maze artifacts from ``output/``, generating only what is missing."""

    def __init__(
        self,
        *,
        config: BookConfig,
        profile: Profile,
        catalog: AssetCatalog,
        paths: OutputPaths,
        fingerprint: BookFingerprint | None = None,
    ) -> None:
        self.config = config
        self.profile = profile
        self.catalog = catalog
        self.paths = paths
        self.fingerprint = fingerprint or BookFingerprint.build(config, profile, catalog)

    def seed_for(self, maze_index: int) -> int:
        """The cache key's seed component for one maze.

        Deliberately derived at ``attempt=0``, which is never a real attempt.
        The key has to be computable *before* generating -- that is what decides
        whether to generate at all -- but a maze's own recorded seed uses the
        attempt that actually succeeded, which is unknowable in advance and
        varies between runs of identical inputs.

        So this is a stable proxy rather than the maze's seed, and the two differ
        in the written JSON. It still does the one job a key needs: it is a pure
        function of ``(book.seed, book.id, mazeIndex)``, so changing any of them
        invalidates the entry, and changing none of them never does.
        """
        return derive_seed(
            self.config.book.seed, self.config.book.id, maze_index, 0, "topology"
        )

    def key_for(self, maze_index: int) -> CacheKey:
        return self.fingerprint.key_for(maze_index, self.seed_for(maze_index))

    # -- reading ------------------------------------------------------------

    def cached(self, maze_index: int) -> LoadedMaze | None:
        maze_path = self.paths.maze_json(maze_index)
        analysis_path = self.paths.analysis_json(maze_index)
        if not (maze_path.is_file() and analysis_path.is_file()):
            return None
        try:
            maze_obj = read_json(maze_path)
            analysis_obj = read_json(analysis_path)
        except (OSError, json.JSONDecodeError):
            return None
        if not self.key_for(maze_index).matches(maze_obj.get("cacheKey")):
            return None
        try:
            maze = MazeData.from_json_obj(maze_obj)
            analysis = MazeAnalysis.from_json_obj(analysis_obj)
        except Exception:
            return None
        return LoadedMaze(maze=maze, analysis=analysis, from_cache=True)

    def require(self, maze_index: int) -> LoadedMaze:
        """Load a cached maze or fail. Never regenerates.

        17.13: "``maze render`` renders cached maze data and MUST NOT silently
        regenerate it", and ``book assemble`` "requires valid cached maze
        artifacts". Regenerating here would mean the page you proofed and the
        page you print were drawn from different mazes.
        """
        loaded = self.cached(maze_index)
        if loaded is None:
            raise GenerationError(
                f"no valid cached artifact for maze {maze_index}",
                details=[
                    f"expected {self.paths.maze_json(maze_index)}",
                    "run `book generate-mazes` first, or `book build` to do both",
                ],
            )
        return loaded

    # -- writing ------------------------------------------------------------

    def write(self, result: pipeline.MazeResult) -> None:
        index = result.maze.maze_index
        maze_obj = result.maze.to_json_obj()
        maze_obj["cacheKey"] = self.key_for(index).to_json_obj()
        write_json(self.paths.maze_json(index), maze_obj)
        write_json(self.paths.analysis_json(index), result.analysis.to_json_obj())

    def ensure(
        self,
        indices: list[int],
        *,
        force: bool = False,
        on_result: Callable[[int, LoadedMaze], None] | None = None,
        on_rejection: pipeline.ProgressHook | None = None,
    ) -> Iterator[tuple[int, LoadedMaze]]:
        """Yield every requested maze, generating the ones not validly cached."""
        pending: list[int] = []
        for index in indices:
            loaded = None if force else self.cached(index)
            if loaded is None:
                pending.append(index)

        generated: dict[int, LoadedMaze] = {}
        if pending:
            generator = base_generator(self.config.generation.base_generator)
            for result in pipeline.generate_book_mazes(
                config=self.config, profile=self.profile, catalog=self.catalog,
                indices=pending, generator=generator, on_rejection=on_rejection,
            ):
                self.write(result)
                generated[result.maze.maze_index] = LoadedMaze(
                    maze=result.maze, analysis=result.analysis, from_cache=False
                )

        for index in indices:
            loaded = generated.get(index) or self.cached(index)
            if loaded is None:
                raise GenerationError(f"maze {index} was neither generated nor cached")
            if on_result is not None:
                on_result(index, loaded)
            yield index, loaded


def resolve_only(spec: str | None, maze_count: int) -> list[int]:
    """Parse ``--only START:END`` (1-based, inclusive) into maze indices."""
    if spec is None:
        return list(range(1, maze_count + 1))
    text = spec.strip()
    try:
        if ":" in text:
            start_text, _, end_text = text.partition(":")
            start = int(start_text) if start_text else 1
            end = int(end_text) if end_text else maze_count
        else:
            start = end = int(text)
    except ValueError:
        raise ConfigError(f"--only must be N or START:END, got {spec!r}") from None
    if start < 1 or end > maze_count or start > end:
        raise ConfigError(
            f"--only {spec!r} resolves to {start}:{end}, outside 1:{maze_count}"
        )
    return list(range(start, end + 1))
