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

#: The only extension a v1 book package may use (PRD 17.9).
SVG_SUFFIX = ".svg"

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
        (p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == SVG_SUFFIX),
        key=lambda p: p.name,
    )
    if require_nonempty and not files:
        raise AssetError(f"{field_name} contains no {SVG_SUFFIX} files: {directory}")
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
        if path.suffix.lower() != SVG_SUFFIX:
            raise AssetError(f"{name} must be an {SVG_SUFFIX} file: {path.name}")

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
    )
