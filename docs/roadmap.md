# Roadmap

The backend exists to do four things well:

1. **Portfolio:** see how my holdings are doing, decide when to buy more, and find losses worth harvesting for taxes.
2. **Undervalued stocks:** find excellent, cheap companies across the whole market, including ones I've never heard of (e.g. ZIM).
3. **Big players:** follow what institutions, famous investors, insiders and members of Congress are buying and selling.
4. **Trading indicators:** the macro and market data needed to prototype trading strategies: oil, inflation, rates, industrial activity, market internals, currency and bonds/gold.

Status as of 2026-10-03: the platform runs on an Oracle Cloud VM with daily syncs and nightly verified backups. Data held: a security master (31k securities, typed, with FIGIs), 2 years of market-wide daily bars (Massive) and deep history for a watchlist (Tiingo), 2 years of splits and dividends, SEC fundamentals for 10 companies, and 5 FRED series.

## Phase A: portfolio (goal 1): core done

Done: accounts, the tax-lot engine, harvesting with wash-sale checks, position context and replacements, Fidelity positions import (2026-10-03). Waiting on exports: Fidelity transaction history (holding periods, recent purchases) and Vanguard 401(k).


Brokers: Fidelity (taxable and other accounts) and Vanguard (401(k)).

- Accounts (broker, type, taxable or not), transactions, and position snapshots from broker exports.
- Tax-lot engine: lots from transaction history (FIFO), realized gains by tax year (short- vs long-term), unrealized gains per lot.
- Tax-loss harvesting: candidates in taxable accounts, wash-sale checks across all accounts (including IRAs and reinvested dividends), replacement suggestions that keep exposure.
- Position context for "buy more?": return vs SPY, drawdown from high, 52-week range, 200-day average.
- Importers: generic CSV first, then Fidelity and Vanguard parsers written against real exports.

## Phase B: trading indicators (goal 4): core done

Done: the 53-series macro pack with ALFRED revision history, the point-in-time time-series engine and indicators, market breadth, delisted securities (2026-10-03). Still to do: EIA inventories, CFTC positioning, the FRED release calendar.


- Macro pack: ~40 FRED series across oil, inflation (CPI, PCE, breakevens), rates and curve, industrial activity, dollar, term premium, credit spreads, VIX, Fed liquidity. Plus EIA inventories and CFTC positioning.
- **Point-in-time data:** release dates and ALFRED vintages, so backtests only see values that were published at the time.
- Market internals computed from our market-wide bars: breadth (% above 50/200-day averages, new highs/lows, advance/decline), SPY vs equal weight, sector rotation, ETF proxies (CPER, GLD, TLT, UUP, USO…).
- Technical indicators engine and a unified, as-of-aligned time-series endpoint for strategy research.
- Delisted securities (Massive reference with `active=false`, replayed from raw) to remove survivorship bias.

## Phase C: undervalued stocks (goal 2)

- Full-universe fundamentals from SEC's nightly `companyfacts.zip`.
- Standard statements mapped from both US GAAP and IFRS (foreign filers like ZIM file 20-F under IFRS), plus trailing-twelve-month figures.
- Valuation (EV/EBIT, P/FCF, P/B, yields), quality (ROIC, leverage, Piotroski, Altman) and cyclicality-aware metrics; market cap from SEC shares × prices.
- Screener endpoint.
- Infrastructure: move to PostgreSQL on the VM; back up raw nightly and the database weekly.

## Phase D: big players (goal 3)

- 13F holdings for a chosen list of managers (Berkshire, Scion, Pershing Square…), with quarter-over-quarter changes.
- Form 4 insider transactions and cluster-buying signals; 13D/13G stakes.
- Congressional trades (STOCK Act reports from the House Clerk and Senate eFD), if the official files parse reliably.
