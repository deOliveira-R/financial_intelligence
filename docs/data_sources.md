# Free Data Sources for Financial Intelligence

Research date: 2026-09-30. Free tiers change often, so check each limit again when we build its adapter.

## Goal

Build a backend that matches what Alpha Vantage (and similar providers such as FMP, Finnhub and Polygon/Massive) offer, using only free sources. No single free provider covers everything at a useful volume. The plan is to combine:

1. **Primary public sources** (government, exchanges, regulators). These are free, have high limits and are authoritative. They make up the core of the system.
2. **Free tiers of commercial APIs.** Each has a small quota, so we use them for what they do best and to fill gaps.
3. **Our own computation**, for anything derived from raw data: technical indicators, ratios, returns and screens. Alpha Vantage charges API calls for these, but we can compute them locally at no cost.

---

## 1. Coverage map: Alpha Vantage category → our source

| Alpha Vantage category | Primary free source | Fallback / complement |
|---|---|---|
| Daily OHLCV (stocks/ETFs) | Tiingo EOD, Stooq | yfinance, Massive (Polygon) free, Alpaca |
| Adjusted prices, splits, dividends | Tiingo (adjusted), Massive free | yfinance, SEC filings |
| Intraday bars | Alpaca (IEX feed, history back to 2016) | yfinance (short windows), Twelve Data |
| Real-time quote | Finnhub `/quote`, Alpaca IEX WebSocket | Twelve Data |
| Fundamentals (IS/BS/CF) | **SEC EDGAR XBRL API** (US) | FMP free, Tiingo (5y), yfinance |
| Company overview / profile | SEC submissions + our own computed metrics | Finnhub `profile2`, FMP |
| Earnings (EPS history, calendar) | SEC XBRL (actuals), Finnhub `stock/earnings` + calendar | Nasdaq.com (unofficial), yfinance |
| Insider transactions | **SEC Form 4** (EDGAR) | Finnhub (may need a paid plan) |
| Institutional holdings | **SEC 13F** (EDGAR) | — |
| Listings / delisting status | SEC `company_tickers.json`, Nasdaq Trader symbol files | Alpha Vantage `LISTING_STATUS` (1 call) |
| ETF holdings | SEC N-PORT filings | Issuer websites (CSV) |
| Options chains | yfinance, CBOE delayed quotes (unofficial JSON) | Alpaca indicative feed |
| Forex | ECB reference rates / Frankfurter, FRED | Twelve Data, Alpha Vantage |
| Crypto | Exchange public APIs (Coinbase, Kraken, Binance/Binance.US) | CoinGecko Demo |
| Commodities | FRED, EIA API | World Bank Pink Sheet |
| Economic indicators | **FRED**, BLS, BEA, US Treasury | IMF, OECD, World Bank, ECB |
| Technical indicators | **Computed locally** (pandas / TA-Lib / pandas-ta) | — |
| News & sentiment | Finnhub `company-news`, GDELT, SEC 8-K RSS | Marketaux free, press-release RSS; sentiment computed with our own LLM/NLP |
| Short interest / short volume | FINRA daily short-sale volume files, SEC fails-to-deliver | Exchange short-interest reports (twice a month) |
| Positioning (futures) | CFTC Commitments of Traders | — |
| Symbol / identifier mapping | OpenFIGI, SEC CIK ↔ ticker map | — |

---

## 2. Primary public sources (the core)

### SEC EDGAR — fundamentals, filings, ownership
- **Endpoints:** `data.sec.gov/submissions/CIK##########.json` (filing history and metadata), `/api/xbrl/companyfacts/CIK…json` (every XBRL fact a company has filed), `/api/xbrl/companyconcept/…` (one concept), `/api/xbrl/frames/{tag}/{unit}/{period}.json` (one concept across all companies for a period, useful for screens). Full-text search at `efts.sec.gov`. Bulk ZIPs (`companyfacts.zip`, `submissions.zip`) are refreshed nightly.
- **Covers:** income statement, balance sheet and cash flow (10-K/10-Q), shares outstanding, Form 4 insider trades, 13F holdings, 8-K events, N-PORT fund holdings, and the ticker↔CIK map (`sec.gov/files/company_tickers.json`).
- **Limits:** free, no API key. Every request must send a `User-Agent` header with a name and email. Maximum 10 requests per second per IP.
- **Notes:** this is the best free source for fundamentals, and it's where the paid vendors get their data. Normalising XBRL takes work: companies use different tags, some use custom extensions, and fiscal periods don't line up. The `edgartools` Python library (also available as a skill in this environment) handles a lot of this.

### FRED (St. Louis Fed) — macro and rates
- More than 800k series: GDP, CPI, unemployment, Fed funds rate, the Treasury yield curve, credit spreads, FX, commodity prices, and more. **ALFRED** keeps point-in-time vintages, so backtests don't use revised data that wasn't known at the time.
- Free API key. About 120 requests per minute.
- Replaces Alpha Vantage's `REAL_GDP`, `CPI`, `FEDERAL_FUNDS_RATE`, `TREASURY_YIELD`, `UNEMPLOYMENT`, `INFLATION`, `RETAIL_SALES` and `DURABLES`, plus its commodity endpoints.

### BLS, BEA, US Treasury, EIA
- **BLS API v2:** CPI detail, employment, wages, PPI. Free registration. v2 allows about 500 queries per day (v1 without registration is much lower). Up to 50 series per request.
- **BEA API:** NIPA tables, GDP by industry, personal income. Free key. Limit is roughly 100 requests per minute.
- **Treasury FiscalData API** (`api.fiscaldata.treasury.gov`): debt, auctions, average interest rates. No key. Daily par yield curve CSVs are on treasury.gov.
- **EIA API v2:** oil, natural gas and electricity prices and inventories. Free key.

### International macro / FX
- **ECB Data Portal** (SDMX): euro reference FX rates, euro-area rates and money aggregates. No key. **Frankfurter** (`api.frankfurter.app`) wraps the ECB FX rates as simple JSON.
- **IMF, OECD and World Bank** (SDMX/REST): cross-country macro data. No key.
- Other central banks have similar APIs if we expand coverage (e.g. BCB SGS for Brazil, BoE, BoJ).

### Market-structure data
- **FINRA:** daily short-sale volume files (free CSV). Equity short interest twice a month.
- **SEC fails-to-deliver:** twice-monthly files.
- **CFTC Commitments of Traders:** weekly, via the Socrata API on `publicreporting.cftc.gov`.
- **Nasdaq Trader symbol directory** (`nasdaqlisted.txt`, `otherlisted.txt`): the daily universe of US-listed symbols.
- **OpenFIGI:** maps identifiers (ticker, ISIN, CUSIP, FIGI). Free. A key raises the rate limit.

---

## 3. Free tiers of commercial APIs (fill gaps, within quota)

| Provider | Free limit | Best for | Caveats |
|---|---|---|---|
| **Tiingo** | 1,000 req/day, 50 req/hr, 500 unique symbols/mo | Clean adjusted EOD history (30+ years), IEX intraday, news | The symbol cap limits how much of the universe we can backfill |
| **Alpaca Market Data** (Basic) | 200 req/min | Intraday and minute bars since 2016 (US stocks), IEX real-time WebSocket | Free feed is IEX only (~2.5% of volume). The last 15 minutes of history are not available. Needs a free (paper) account |
| **Massive** (formerly Polygon.io) | 5 req/min, EOD, 2 years of history | Grouped daily bars (whole market in one call), splits, dividends, ticker reference data | Low call rate. The old `api.polygon.io` host still works |
| **Finnhub** | 60 req/min | Real-time US quotes, company profile, peers, earnings surprises, analyst recommendations, basic metrics, company news, WebSocket (50 symbols) | Candles (OHLCV) and many fundamentals endpoints are premium |
| **Twelve Data** | 8 req/min, 800/day | Multi-asset (FX, crypto, ETFs, international) time series | Free stock data is delayed |
| **Financial Modeling Prep** | 250 req/day | Pre-normalised statements, profiles | US only, 5 years of prices / 5 quarters of statements on the free tier, personal use only |
| **Alpha Vantage** | 25 req/day, 5/min | Occasional gap-filling (e.g. `LISTING_STATUS`, news sentiment) | Too little quota to rely on |
| **EODHD** | 20 req/day | International EOD spot checks | Very small quota |
| **CoinGecko** (Demo) | 100 req/min, 10k/month | Crypto metadata, market cap, rankings | Monthly cap |
| **Stooq** | Free CSV, but needs an API key since 2026 (obtained via CAPTCHA) | Long daily history for global indices, FX, bonds and stocks | No official SLA. URL format changed in 2026 |
| **yfinance** (Yahoo, unofficial) | No official limit. IP throttling at heavy use (~2k/hr) | Wide global coverage, options chains, quick fallback | Unofficial and against Yahoo's terms for redistribution. Breaks without warning. Never make it the only source for anything |

**Crypto exchanges** (Coinbase Exchange, Kraken, Binance / Binance.US): public market-data REST and WebSocket endpoints need no key and have generous limits. They give real OHLCV and order-book data, which is better than aggregator data.

**Options:** there's no good free source. Practical choices are yfinance chains, CBOE's delayed-quote JSON (unofficial) and Alpaca's indicative feed. Greeks and IV should be computed locally (Black-Scholes) from the chain and our own rates (FRED).

**Dead / avoid:** IEX Cloud (shut down Aug 2024); Quandl / Nasdaq Data Link free datasets (mostly gone or stale, e.g. WIKI prices stopped in 2018); Google Finance API (doesn't exist); Reddit and StockTwits APIs (restricted or closed to new developers).

---

## 4. Things we compute ourselves (no API needed)

Alpha Vantage spends an API call on each of these. We derive them from data we already store:
- **Technical indicators:** SMA, EMA, RSI, MACD, Bollinger Bands, ATR, ADX, Stochastic, OBV, VWAP and the rest of Alpha Vantage's ~50 indicators, using pandas, `TA-Lib` or `pandas-ta` on our own OHLCV.
- **Adjusted prices:** from raw prices plus split and dividend events (cross-checked against Tiingo).
- **Fundamental ratios and overview fields:** P/E, EV/EBITDA, margins, ROE, growth rates, market cap (price × SEC shares outstanding).
- **News sentiment:** score headlines from Finnhub, GDELT and RSS with our own model, instead of paying for a sentiment feed.
- **Options Greeks / IV**, returns, volatility, correlations, screens.

---

## 5. Implications for the backend architecture

- **Provider-adapter layer:** one adapter per source, all behind a common interface (e.g. `get_daily_bars(symbol, start, end)`). Each capability has an ordered list of providers to fall back through.
- **Per-provider rate limiter and quota tracker:** token buckets for per-second/minute limits, plus daily, hourly and monthly counters (Tiingo hourly, CoinGecko monthly, Tiingo's symbol cap).
- **Store first, serve from storage:** the API layer reads from our own database, never from upstream on each request. Backfill once, then update incrementally (nightly EOD job, SEC bulk ZIPs, FRED updates). This is the only way free quotas become enough.
- **Bulk and grouped endpoints first:** SEC `companyfacts.zip` and `frames`, Massive grouped daily (whole market per call), BLS multi-series requests.
- **Canonical identifiers:** use an internal security ID mapped to ticker, CIK, FIGI and exchange, so renames, delistings and ticker reuse don't corrupt history (and to limit survivorship bias).
- **Cross-source validation:** compare closes and splits between sources and flag differences.
- **Licensing:** most free tiers allow personal or internal use only. If we ever expose data to third parties, primary public sources (SEC, FRED, BLS, ECB) are the safe ones. Commercial free tiers and Yahoo generally don't allow redistribution.

## 6. Suggested build order

1. SEC EDGAR (fundamentals, ticker map, insiders, 13F) + FRED. These are free, with high limits and high value.
2. Daily prices: Tiingo + Massive grouped daily + Stooq, with yfinance as a fallback. Corporate actions.
3. Local indicator and ratio engine.
4. Intraday and real-time: Alpaca IEX + Finnhub quotes and WebSocket.
5. News (Finnhub, GDELT, SEC 8-K) + our own sentiment scoring.
6. FX, crypto (exchange APIs), commodities (EIA/FRED), international macro.
7. Options (yfinance/CBOE) + local Greeks; FINRA/CFTC market-structure data.

## Sources

- [Polygon.io is now Massive](https://fisd.net/polygon-io-is-now-massive/) · [Massive request limits](https://polygon.io/knowledge-base/article/what-is-the-request-limit-for-polygons-restful-apis) · [Polygon/Massive pricing 2026](https://qveris.ai/guides/polygon-pricing-optimized/)
- [Alpha Vantage limits (Macroption)](https://www.macroption.com/alpha-vantage-api-limits/)
- [Alpaca: About Market Data API](https://docs.alpaca.markets/us/docs/about-market-data-api) · [Alpaca historical stock data](https://docs.alpaca.markets/us/docs/historical-stock-data-1)
- [SEC developer resources](https://www.sec.gov/about/developer-resources) · [Accessing EDGAR data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
- [FMP FAQs](https://site.financialmodelingprep.com/faqs)
- [CoinGecko changelog](https://docs.coingecko.com/changelog) · [CoinGecko pricing](https://www.coingecko.com/en/api/pricing)
- [Stooq now requires API key (pandas-datareader #1012)](https://github.com/pydata/pandas-datareader/issues/1012)
- [Tiingo rate limits](https://apis.io/rate-limits/tiingo/tiingo-rate-limits/)
- [Finnhub free tier status](https://freeapi.watch/finnhub/) · [Finnhub candles not free (#546)](https://github.com/finnhubio/Finnhub-API/issues/546)
- [EODHD API limits](https://eodhd.com/financial-apis/api-limits) · [Twelve Data pricing](https://twelvedata.com/pricing)
- [yfinance rate-limit discussion](https://github.com/ranaroussi/yfinance/discussions/2431)
- [Economic data APIs guide (FRED/BLS/BEA)](https://www.datasetiq.com/blog/api-access-economic-data-guide)
- [Free stock API comparison 2026 (dev.to)](https://dev.to/nexgendata/best-free-stock-market-apis-and-data-tools-in-2026-a-developers-honest-comparison-1926)
