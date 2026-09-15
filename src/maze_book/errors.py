"""Error taxonomy and the process exit-code contract (PRD 17.13).

Exit codes are a public contract. Callers and CI depend on them:

    0  success
    2  invalid input / configuration
    3  maze generation or semantic failure
    4  rendering or assembly failure
    5  PDF or preflight failure
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_GENERATION = 3
EXIT_RENDERING = 4
EXIT_PREFLIGHT = 5


class MazeBookError(Exception):
    """Base class for every error this package raises deliberately."""

    exit_code = EXIT_INVALID_INPUT

    def __init__(self, message: str, *, details: object = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class ConfigError(MazeBookError):
    """Invalid book.json, profile, or CLI argument."""

    exit_code = EXIT_INVALID_INPUT


class AssetError(MazeBookError):
    """Missing asset folder/file, or an SVG that violates the asset subset."""

    exit_code = EXIT_INVALID_INPUT


class GenerationError(MazeBookError):
    """A maze could not be produced that satisfies the profile constraints."""

    exit_code = EXIT_GENERATION


class SemanticError(MazeBookError):
    """A maze exists but violates a puzzle-validity rule (C1-C8, reachability, ...)."""

    exit_code = EXIT_GENERATION


class RenderingError(MazeBookError):
    """SVG/PDF drawing or page assembly failed."""

    exit_code = EXIT_RENDERING


class PreflightError(MazeBookError):
    """A produced PDF failed a print-readiness gate."""

    exit_code = EXIT_PREFLIGHT
