"""The `fin-intel` command line. Commands live in modules by area; importing them here
registers them on the shared app."""

from fin_intel.cli import (  # noqa: F401  (registers commands)
    analytics,
    filings,
    macro,
    market,
    ops,
    players,
    portfolio,
    research,
)
from fin_intel.cli.app import app

__all__ = ["app"]
