"""PDF preflight (PRD 17.15, 18.8)."""

from .checks import (
    CheckResult,
    PreflightReport,
    missing_tools,
)
from .runner import run_preflight

__all__ = ["CheckResult", "PreflightReport", "missing_tools", "run_preflight"]
