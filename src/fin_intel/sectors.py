"""Sectors from SIC codes (SEC's Standard Industrial Classification), by SIC division.

Coarse, but enough for what screening needs most: leaving out financials and utilities,
whose statements don't fit EV/EBIT and ROIC (banks have no operating income in the usual
sense; utilities' returns are regulated).
"""

# (first code, last code, sector)
DIVISIONS = [
    (100, 999, "agriculture"),
    (1000, 1499, "mining"),  # including oil and gas extraction (1311)
    (1500, 1799, "construction"),
    (2000, 3999, "manufacturing"),
    (4000, 4899, "transportation"),  # including communications
    (4900, 4999, "utilities"),
    (5000, 5199, "wholesale"),
    (5200, 5999, "retail"),
    (6000, 6799, "finance"),  # banks, insurance, real estate, REITs, blank checks
    (7000, 8999, "services"),
    (9100, 9999, "public"),
]
SECTORS = sorted({name for _, _, name in DIVISIONS})


def sector(sic: int | None) -> str | None:
    if sic is None:
        return None
    return next((name for lo, hi, name in DIVISIONS if lo <= sic <= hi), None)


def codes(name: str) -> list[tuple[int, int]]:
    """SIC ranges of a sector."""
    if name not in SECTORS:
        raise ValueError(f"unknown sector {name!r}; one of {', '.join(SECTORS)}")
    return [(lo, hi) for lo, hi, n in DIVISIONS if n == name]
