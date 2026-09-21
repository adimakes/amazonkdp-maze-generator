"""Discovering the SVG files a book package offers, and picking variants (PRD 17.8-17.9).

Two rules from the PRD shape this module. "Asset variant selection is
deterministic and uses sorted filenames" means discovery must not depend on
directory iteration order, which is filesystem-dependent -- so every listing is
sorted by name. And a book must be reproducible, so a variant choice is a
function of a derived RNG plus an ordinal, never of wall-clock or hash order.

Discovery is deliberately separate from validation (``assets/validate.py``): the
generation stage needs filenames to record in ``MazeData`` and would be slowed to
no purpose by parsing every SVG, while rendering needs the parsed geometry and
must reject anything outside the subset. Both read the same sorted listing.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from ..errors import AssetError
from ..model.book_config import BookConfig
from .raster import RASTER_SUFFIXES

ROLE_START = "start"
ROLE_FINISH = "finish"
ROLE_COLLECTIBLE = "collectible"
ROLE_DEAD_END = "dead-end"
ROLE_PAGE_VECTOR = "page-vector"

#: The only extension a v1 book package may use (PRD 17.9).
SVG_SUFFIX = ".svg"

#: A book may ship vector assets, bitonal raster ones, or both. Artwork that
#: arrives as a picture loses the drawing when it is traced into the subset,
#: so the contract is the folder and the composition, not the file format.
ASSET_SUFFIXES = (SVG_SUFFIX, *RASTER_SUFFIXES)

POLICY_RANDOM = "random-from-folder"
POLICY_FIRST = "first-in-folder"
POLICY_CYCLE = "cycle-folder"
POLICIES = (POLICY_RANDOM, POLICY_FIRST, POLICY_CYCLE)


@dataclass(frozen=True, slots=True)
class AssetFile:
    """One SVG on disk, identified downstream by its filename alone.

    ``asset_id`` is what lands in ``MazeData.assets`` and therefore in the
    committed artifact, so it is the bare filename: a path would bake the
    checkout location into the book.
    """

    asset_id: str
    path: Path

    @property
    def stem(self) -> str:
        return self.path.stem


def discover(directory: Path, *, field_name: str, require_nonempty: bool = True) -> list[AssetFile]:
    """Every ``*.svg`` directly inside ``directory``, sorted by filename."""
    if not directory.is_dir():
        raise AssetError(f"{field_name} is not a directory: {directory}")
    files = sorted(
        (p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in ASSET_SUFFIXES),
        key=lambda p: p.name,
    )
    if require_nonempty and not files:
        raise AssetError(
            f"{field_name} contains no {' or '.join(ASSET_SUFFIXES)} files: {directory}"
        )
    return [AssetFile(asset_id=p.name, path=p) for p in files]


def _pick(
    choices: list[AssetFile], policy: str, rng: random.Random, ordinal: int, *, field_name: str
) -> AssetFile:
    if not choices:
        raise AssetError(f"{field_name} has no variants to choose from")
    if policy == POLICY_FIRST:
        return choices[0]
    if policy == POLICY_CYCLE:
        return choices[ordinal % len(choices)]
    if policy == POLICY_RANDOM:
        # ``randrange`` over a sorted list: the same derived rng and the same
        # folder give the same sequence of variants on every machine.
        return choices[rng.randrange(len(choices))]
    raise AssetError(f"unknown asset policy {policy!r}, expected one of {POLICIES}")


@dataclass
class AssetCatalog:
    """The asset vocabulary of one book package."""

    start: AssetFile
    finish: AssetFile
    dead_ends: list[AssetFile]
    collectibles: list[AssetFile]
    page_vectors: dict[str, AssetFile]
    dead_end_policy: str
    collectible_policy: str
    maze_decorations: tuple[str, ...] = ()

    def choose_dead_end(self, rng: random.Random, ordinal: int) -> AssetFile:
        return _pick(
            self.dead_ends, self.dead_end_policy, rng, ordinal,
            field_name="assets.deadEndVectorsDir",
        )

    def choose_collectible(self, rng: random.Random, ordinal: int) -> AssetFile:
        return _pick(
            self.collectibles, self.collectible_policy, rng, ordinal,
            field_name="assets.collectibleVectorsDir",
        )

    def all_files(self) -> list[AssetFile]:
        """Every distinct asset, for the validator to sweep."""
        seen: dict[str, AssetFile] = {}
        for asset in (
            [self.start, self.finish]
            + self.dead_ends
            + self.collectibles
            + sorted(self.page_vectors.values(), key=lambda a: a.asset_id)
        ):
            seen.setdefault(str(asset.path), asset)
        return sorted(seen.values(), key=lambda a: str(a.path))

    def decorations(self) -> list["AssetFile"]:
        """Page vectors allowed to decorate a maze page, in a stable order."""
        allowed = set(self.maze_decorations)
        chosen = [
            asset for asset in self.page_vectors.values()
            if not allowed or asset.asset_id in allowed
        ]
        return sorted(chosen, key=lambda asset: asset.asset_id)

    def roles(self) -> dict[str, str]:
        """Which role each asset file plays, keyed by path.

        The role decides how big the artwork prints, and how big it prints
        decides which rules it has to keep: a 0.6 in page decoration and a 5 mm
        collectible are not the same job, and judging them by the same minimum
        feature size rejects perfectly printable artwork for the decoration
        while letting a hairline through on the icon.
        """
        assignment: dict[str, str] = {}
        for asset in self.collectibles:
            assignment[str(asset.path)] = ROLE_COLLECTIBLE
        for asset in self.dead_ends:
            assignment[str(asset.path)] = ROLE_DEAD_END
        for asset in self.page_vectors.values():
            assignment[str(asset.path)] = ROLE_PAGE_VECTOR
        # Start and finish last: a file used both as a marker and as something
        # else is held to the marker's rules, which are the stricter pairing of
        # "printed large" and "must be recognised instantly".
        assignment[str(self.start.path)] = ROLE_START
        assignment[str(self.finish.path)] = ROLE_FINISH
        return assignment

    def path_for(self, asset_id: str) -> Path:
        """Resolve an ``asset_id`` recorded in ``MazeData`` back to a file."""
        for asset in self.all_files():
            if asset.asset_id == asset_id:
                return asset.path
        raise AssetError(f"asset id {asset_id!r} is not in this book's catalog")


def load_catalog(config: BookConfig) -> AssetCatalog:
    """Build the catalog for a validated ``BookConfig``.

    ``start``/``finish`` come from explicit config fields rather than folder
    order, because those two assets are the identity of the book -- Jim and his
    candy bucket -- and must not change when someone drops a new SVG into the
    folder.
    """
    for policy, field_name in (
        (config.assets.dead_end_asset_policy, "assets.deadEndAssetPolicy"),
        (config.assets.collectible_asset_policy, "assets.collectibleAssetPolicy"),
    ):
        if policy not in POLICIES:
            raise AssetError(f"{field_name} is {policy!r}, expected one of {POLICIES}")

    start_path = config.start_asset_path
    finish_path = config.finish_asset_path
    for name, path in (("assets.startAsset", start_path), ("assets.finishAsset", finish_path)):
        if not path.is_file():
            raise AssetError(f"{name} does not exist: {path}")
        if path.suffix.lower() not in ASSET_SUFFIXES:
            raise AssetError(
                f"{name} must be one of {', '.join(ASSET_SUFFIXES)}: {path.name}"
            )

    page_dir = config.page_vectors_dir
    page_vectors: dict[str, AssetFile] = {}
    if page_dir is not None:
        for asset in discover(
            page_dir, field_name="assets.pageVectorsDir", require_nonempty=False
        ):
            page_vectors[asset.asset_id] = asset

    return AssetCatalog(
        start=AssetFile(asset_id=start_path.name, path=start_path),
        finish=AssetFile(asset_id=finish_path.name, path=finish_path),
        dead_ends=discover(config.dead_end_vectors_dir, field_name="assets.deadEndVectorsDir"),
        collectibles=discover(
            config.collectible_vectors_dir, field_name="assets.collectibleVectorsDir"
        ),
        page_vectors=page_vectors,
        dead_end_policy=config.assets.dead_end_asset_policy,
        collectible_policy=config.assets.collectible_asset_policy,
        maze_decorations=config.assets.maze_decorations,
    )
