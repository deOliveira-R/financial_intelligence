"""Every raw dataset, declared once by the module that loads it.

A dataset is one kind of provider response (`("sec", "companyfacts")`). Its declaration
says how to load a stored response and how the raw layer treats it; `rebuild.py`, raw
retention and backfills read the declarations instead of keeping their own lists.
Modules register their datasets at import; `sources.py` imports them all.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

# loader(session, raw key, payload, fetched_at) -> rows written
Loader = Callable[[Session, str, Any, datetime], int]


@dataclass(frozen=True)
class Dataset:
    provider: str
    name: str
    load: Loader
    # Rebuild targets that replay it (every dataset is also in "all").
    targets: tuple[str, ...] = ()
    # json: parsed JSON; bytes: the raw body; request: {"request": ..., "response": ...}
    # for responses that only mean something with the request recorded as params.
    payload: Literal["json", "bytes", "request"] = "json"
    # Each response is complete: only the latest per key matters (older ones are pruned).
    snapshot: bool = False
    # Creates or renames securities: replayed before everything else in a rebuild.
    reference: bool = False
    # Market-wide rows keyed by symbol, skipped for unknown symbols at load time and
    # replayed when a security is discovered later.
    symbol_keyed: bool = False
    note: str = field(default="", compare=False)

    @property
    def id(self) -> tuple[str, str]:
        return (self.provider, self.name)


DATASETS: dict[tuple[str, str], Dataset] = {}


def _complete() -> None:
    """Import every registering module (once) so lookups see all datasets."""
    from fin_intel import sources  # noqa: F401


def register(*datasets: Dataset) -> None:
    for d in datasets:
        if d.id in DATASETS and DATASETS[d.id] != d:
            raise ValueError(f"dataset {d.id} registered twice")
        DATASETS[d.id] = d


def get(provider: str, name: str) -> Dataset:
    _complete()
    return DATASETS[(provider, name)]


def where(**flags: Any) -> set[tuple[str, str]]:
    """Ids of datasets whose attributes match, e.g. where(snapshot=True)."""
    _complete()
    return {d.id for d in DATASETS.values() if all(getattr(d, k) == v for k, v in flags.items())}


def in_target(target: str) -> set[tuple[str, str]]:
    _complete()
    return {d.id for d in DATASETS.values() if target == "all" or target in d.targets}
