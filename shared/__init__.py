"""Shared infrastructure for the geo_toolkit MVPs.

Modules in this package are intentionally framework-light: they expose plain
functions (with thin wrappers around external SDKs) that the three MVP apps
can compose freely.
"""

from pathlib import Path

__version__ = "0.1.0"

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
"""Absolute path to the project root (the directory containing pyproject.toml)."""
