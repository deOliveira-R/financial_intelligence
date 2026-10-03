# Backlog

Things deliberately deferred, to pick up when they become useful. The phases themselves are
in [roadmap.md](roadmap.md).

## Data

- **EIA weekly petroleum status:** crude and gasoline inventories, refinery utilization,
  production. EIA API v2, free key. Inventory surprises move oil and energy stocks.
- **CFTC Commitments of Traders:** weekly futures positioning for crude, gold, copper,
  Treasuries and S&P 500 (`publicreporting.cftc.gov`, Socrata API, free).
- **FRED release calendar:** upcoming CPI, payrolls and GDP release dates for event
  awareness (`fred/releases/dates`); FOMC meeting dates from the Fed.
- **Longer price history:** rolling Tiingo backfill of the S&P 500 plus major ETFs within
  its 500 symbols/month (about two months); Stooq for long index and FX history.
- **Intraday and real-time:** Alpaca's free IEX feed (minute bars since 2016, live stream).
- **Market structure:** FINRA short volume, SEC fails-to-deliver.
- **ETF holdings:** SEC N-PORT filings.
- **Options:** yfinance or delayed CBOE chains, with Greeks computed ourselves.
- **Sector and industry classification:** SIC codes from SEC submissions, for peer
  comparisons and sector breadth.

## Trading strategy prototypes (phase B data)

Each needs a walk-forward backtest on point-in-time data with transaction costs.

- **Breadth divergence:** SPY near highs while % above 200-day falls (e.g. Oct 2026:
  index at highs, 39% of stocks above their 200-day). Does it precede drawdowns?
- **Copper/gold ratio vs yields:** `px:CPER/px:GLD` as a growth signal against `fred:DGS10`.
- **Curve regimes:** `fred:T10Y2Y` and `fred:T10Y3M` inversions and re-steepening vs equity returns.
- **Credit stress:** high-yield spread z-score as a risk-off filter (FRED keeps ~3 years).
- **Inflation surprises:** breakevens (`fred:T5YIE`) and CPI releases vs sector rotation
  (energy, materials vs long-duration growth).
- **Liquidity:** Fed balance sheet minus reverse repo minus TGA vs SPY.
- **Oil shocks:** `fred:DCOILWTICO|ret:21` vs airlines, energy and broad market (with EIA
  inventories once available).
- **Trend and momentum:** SMA/EMA crossovers and RSI on sector ETFs, with breadth confirmation.

Infrastructure these need: a backtest module (signals, position sizing, costs, walk-forward
splits, performance stats), plus storing strategy runs for comparison.

## Portfolio

- Fidelity transaction-history parser (holding periods, recent purchases, realized gains).
- Vanguard 401(k) import (export or statement).
- Specific-lot identification from Fidelity's lot-level cost basis, when exported.

## Operations

- fail2ban on the server (SSH must stay public for the tunnel).
- macOS launch agent keeping the SSH tunnel up.
- Pin the exact Python patch (`.python-version` 3.14.x) so local and server match.
- Restart the editor's Pyright after the `pyrightconfig.json` change, if warnings persist.
