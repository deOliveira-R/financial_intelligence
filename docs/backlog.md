# Backlog

Things deliberately deferred, to pick up when they become useful. The phases themselves are
in [roadmap.md](roadmap.md).

## Data

- **EIA key:** the shared DEMO_KEY is limited per IP (fine on the server, exhausted behind
  VPNs). A free personal key (`FI_EIA_API_KEY`) removes the problem.
- **EIA expectations:** inventory *surprises* (vs analyst consensus) are what move prices;
  consensus isn't free, but changes vs the 5-year seasonal average are a usable proxy.
- **More COT markets:** the curated list in `cot.py` covers 26; add any market by its
  CFTC code. Options-combined reports exist too (futures-only for now).
- **Release-day signals:** the calendar is loaded (`fin-intel calendar`); next is studying
  returns and volatility around CPI, payrolls and FOMC days, and flagging positions into them.
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
- **COT extremes:** managed money / leveraged funds at a 3-year index above 90 or below
  10 (`cot:copper:managed_money:index`), against forward returns of the matching ETF
  (CPER, USO, GLD, TLT, IWM). Commercials' index as a contrarian confirmation.
- **Trend and momentum:** SMA/EMA crossovers and RSI on sector ETFs, with breadth confirmation.

Infrastructure these need: a backtest module (signals, position sizing, costs, walk-forward
splits, performance stats), plus storing strategy runs for comparison.

## Fundamentals and screening

- **Multi-class share counts:** companies reporting EPS and shares per class (Berkshire,
  Greif) get no market cap. Per-class counts are dimensioned XBRL facts, which SEC's
  company facts omit; the filings' instance documents have them.
- **Bank metrics:** price to tangible book, net interest margin, efficiency ratio (banks
  report no operating income, so EV/EBIT and ROIC don't apply).
- **Historical point-in-time metrics:** backfill company_metrics for past dates from
  statement filing dates and historical prices, for screening backtests.
- **Sector/industry:** SIC codes (SEC submissions) for peer-relative valuation.
- **PostgreSQL:** SQLite holds the full fundamentals load (~20-25 GB, read-mostly, one
  writer); move when concurrency or size demands it.

## Big players (phase D)

- **Paper congressional reports:** about 12% of House PTRs (DocID 8/9) and some Senate
  reports are scanned images; they're indexed but have no transactions. OCR would recover
  them.
- **Congress history:** PTRs go back to 2012; the default load is 2 years. Amendments
  arrive as new reports, so an amended trade can appear twice.
- **Congress signals:** purchases by several members, committee membership vs sector
  (e.g. Armed Services and defense stocks), excess returns after disclosure date.
- **13D/13G stakes:** activists crossing 5% (EDGAR full-text search or daily index).
- **Famous-investor watchlist:** named 13F filers (Berkshire, Pershing Square, Scion,
  Baupost, Appaloosa...) with alerts on new positions.
- **Insider and 13F signal backtests:** cluster buys and "new position by N top managers"
  vs forward returns.

## Portfolio

- Fidelity transaction-history parser (holding periods, recent purchases, realized gains).
- Vanguard 401(k) import (export or statement).
- Specific-lot identification from Fidelity's lot-level cost basis, when exported.

## Operations

- fail2ban on the server (SSH must stay public for the tunnel).
- macOS launch agent keeping the SSH tunnel up.
- Pin the exact Python patch (`.python-version` 3.14.x) so local and server match.
- Restart the editor's Pyright after the `pyrightconfig.json` change, if warnings persist.
