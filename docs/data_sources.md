# Data sources

Every source the backend uses, the free ones we know of but don't use (yet), the ones we
tried and rejected, and paid options that could replace many free feeds with one. Free
tiers and prices change often: check them again before relying on a number here.
Last reviewed 2026-10-04.

**How we choose:** official and primary sources first (regulators, exchanges, central
banks): they're free, authoritative, point-in-time and usually fine to redistribute.
Commercial free tiers fill gaps. Anything we can compute (indicators, ratios, adjusted
prices) we compute. No unofficial or scraped endpoints without saying so, and no getting
around bot protection. The proof of concept stays on free data; if it makes money, a paid
feed replaces the patchwork (and makes a commercial product possible).

## In use

### Fundamentals and filings

| Source | What we take | Access and limits | Code | Licensing |
|---|---|---|---|---|
| SEC EDGAR: company facts (nightly bulk `companyfacts.zip`, per-company JSON) | XBRL financials of every SEC filer (10-K, 10-Q, 20-F, 40-F) | Free, no key; `User-Agent` with contact email required (`FI_SEC_USER_AGENT`); 10 req/s | `providers/sec.py`, `sync-fundamentals-bulk` | Public |
| SEC EDGAR: filing archives | XBRL instances of filings the company facts API lacks (e.g. IFRS 2025 taxonomy 20-Fs: TSMC, Toyota, Sony) | Same | `xbrl.py`, `fill-xbrl-gaps` | Public |
| SEC EDGAR: submissions | SIC code, filer category, country of incorporation, recent filings | Same | `sync-sic` | Public |
| SEC: Insider Transactions Data Sets + daily Form 4 XML | Forms 3/4/5 transactions | Same | `insiders.py`, `sync-insiders` | Public |
| SEC: Form 13F Data Sets | Institutional holdings, quarterly | Same | `thirteenf.py`, `sync-13f` | Public |
| EDINET (Japan FSA) API v2 | Annual, quarterly and half-year reports' XBRL for ~4,000 listed companies (J-GAAP, IFRS) | Free key (`FI_EDINET_API_KEY`); we keep only the XBRL instance of each report package (~100 KB gzipped) | `providers/edinet.py`, `edinet.py`, `sync-edinet` | Public |
| OpenDART (Korea FSS) | Financial statements (all accounts, quarterly) and share counts for ~4,000 listed companies | Free key (`FI_OPENDART_API_KEY`); ~20,000 requests/day, so backfills run over days | `providers/dart.py`, `dart.py`, `sync-dart` | Public |
| TWSE / TPEx open data (Taiwan) | Company profiles; latest quarter's income statement and balance sheet for ~1,950 companies | Free, no key; latest quarter only | `providers/twse.py`, `taiwan.py`, `sync-twse` | Public |
| filings.xbrl.org (ESEF) | European listed companies' annual reports as xBRL-JSON, fiscal years 2023+ | Free, no key | `providers/esef.py`, `esef.py`, `sync-esef` | Repository terms (check before redistributing) |

### Prices and market data

| Source | What we take | Access and limits | Code | Licensing |
|---|---|---|---|---|
| Massive (formerly Polygon.io), free plan | Grouped daily bars for every US stock (OTC included), splits, dividends, reference tickers (types, FIGIs), delistings, ADR share counts | Free key (`FI_MASSIVE_API_KEY`); 5 calls/min; 2 years of history | `providers/massive.py`, `sync-market-daily`, `sync-reference` | Personal use |
| Tiingo, free plan | Deep adjusted daily history per symbol (watchlist, SPY) | Free key (`FI_TIINGO_API_KEY`); 500 symbols/month, 1,000 req/day | `providers/tiingo.py`, `sync-prices` | Personal use |
| TWSE / TPEx daily quotes | Daily prices of every Taiwanese stock since 2024-10 | Free; one request per exchange and day, about 1 per 3 s | `sync-tw-prices` | Public website data |

### Macro, energy, positioning, calendar

| Source | What we take | Access and limits | Code | Licensing |
|---|---|---|---|---|
| FRED / ALFRED (St. Louis Fed) | 53-series macro pack + exchange rates for 21 currencies, with full revision history; release dates | Free key (`FI_FRED_API_KEY`); ~120 req/min | `providers/fred.py`, `macro.py`, `fx.py`, `releases.py` | Public (some series have source restrictions) |
| Federal Reserve Board website | FOMC meeting calendar | Free | `providers/fed.py` | Public |
| EIA API v2 | 13 weekly petroleum and natural gas series | Free key (`FI_EIA_API_KEY`; the shared DEMO_KEY is rate-limited per IP) | `providers/eia.py`, `energy.py`, `sync-eia` | Public |
| CFTC Public Reporting (Socrata) | Commitments of Traders: legacy, disaggregated, financial futures, 26 markets since 2006 | Free, no key | `providers/cftc.py`, `cot.py`, `sync-cot` | Public |

### Ownership, politics, identifiers

| Source | What we take | Access and limits | Code | Licensing |
|---|---|---|---|---|
| House Clerk financial disclosures | Yearly filing index; periodic transaction report PDFs (electronic filings parsed; paper scans skipped) | Free | `providers/congress.py`, `congress.py` | Public |
| Senate eFD | Periodic transaction reports (HTML) after accepting the site's terms | Free | same | Public |
| OpenFIGI | CUSIP → security (13F), ticker/ISIN → share-class FIGI (cross-listings) | Free; key (`FI_OPENFIGI_API_KEY`) raises limits ~100x | `providers/openfigi.py`, `crosslist.py` | Free to use |
| GLEIF | ISIN ↔ LEI mapping (daily bulk file, ~32 MB) for European issuers | Free; the per-LEI API throttles hard, so we use the bulk file | `providers/gleif.py` | Open data (CC0) |

## Known, not used (yet)

| Source | Would give us | Why not yet |
|---|---|---|
| BLS, BEA, US Treasury FiscalData | CPI and payroll detail, GDP by industry, auctions, daily yield curve | FRED carries the headline series; add when a strategy needs the detail |
| ECB Data Portal / Frankfurter, IMF, OECD, World Bank | Euro-area and cross-country macro, reference FX | FRED FX suffices for conversion; add for non-US macro signals |
| FINRA short-sale volume, SEC fails-to-deliver, exchange short interest | Short positioning | Speculation track; not started |
| SEC N-PORT | ETF and fund holdings | Not needed yet |
| Nasdaq Trader symbol directory | Daily list of US-listed symbols | SEC + Massive reference cover it |
| Alpaca (free IEX feed) | Intraday bars since 2016, real-time IEX stream | No intraday use case yet |
| Finnhub, Twelve Data, FMP and Alpha Vantage free tiers | Quotes, profiles, estimates, international prices | Quotas too small to build on; spot checks only |
| CoinGecko, exchange APIs (Coinbase, Kraken) | Crypto OHLCV and metadata | Crypto not in scope yet |
| congress.gov API (free key), Senate LDA lobbying API, USAspending, unitedstates/congress-legislators | Bills and policy areas, lobbying by industry, federal contracts, committee rosters | Planned to corroborate congressional signals (see backlog) |
| GDELT, SEC 8-K feeds, company RSS | News and events | News/sentiment not started |
| MOPS (Taiwan) | Historical quarterly statements | Backfill planned: the open data has only the latest quarter |
| J-Quants (JPX), free tier | Japanese prices and fundamentals, 12-week delay | Delay makes it useless for current valuations; fine for backtests |
| Depositary banks' DR directories (BNY, JPMorgan, Citi) | ADR ratios | Would value ~370 linked ADRs that lack an inferred ratio |
| HKEX, CNINFO | Hong Kong and mainland China filings | Mostly PDFs; hard |
| SEDAR+ (Canada), Companies House (UK) | Canadian and UK filings not in XBRL elsewhere | Canadian 40-F filers often lack XBRL; not started |

## Tried and rejected, or blocked

| Source | What happened |
|---|---|
| Stooq | Now behind a JavaScript proof-of-work bot check; not used (circumventing it isn't acceptable) |
| data.go.kr (Korea's public-data portal: FSC stock prices) | Registration needs a Korean phone number |
| KRX Open API | Registration failed (Korean-only site; sign-up didn't complete). Its terms were acceptable: non-commercial, no redistribution, 10,000 requests/day |
| KRX public statistics endpoints | Now require login |
| Naver Finance chart data | Works, but unofficial and against Naver's terms for automated collection; not used |
| yfinance (Yahoo) | Unofficial, against Yahoo's terms for redistribution, breaks without warning; not used |
| IEX Cloud, Quandl free datasets | Shut down or stale |

## Paid consolidators

Prices below are approximate list prices for personal plans (check current pricing and
what "commercial" means for each). The regulators' feeds (SEC, EDINET, DART, ESEF, FRED,
CFTC, EIA) stay regardless: they're free, authoritative, point-in-time and commercial-safe.
A paid feed mainly replaces the price patchwork and the fragile parts.

### Many-to-one candidates

| Vendor | Coverage | Approx. price | Would retire |
|---|---|---|---|
| **EODHD** (All-World / All-In-One) | End-of-day prices for 70+ exchanges (Tokyo, KRX, TWSE, Euronext, LSE, Xetra, HKEX…), splits and dividends, delisted tickers, standardized global fundamentals, earnings/IPO calendars, some macro | ~€20/month (prices) to ~€100/month (all-in-one) | Massive EOD and Tiingo for prices, TWSE price scraping, the US OTC/ADR valuation workaround (`crosslist.py` as a price source), the Korea gap, Stooq. Cheapest way to value every market |
| **Financial Modeling Prep** (Premium / Ultimate) | Global prices and standardized fundamentals, insider trades, 13F, **House and Senate trades**, ETF holdings, earnings estimates and transcripts, calendars | ~$30–150/month | Everything EODHD would, plus our congress PDF/HTML parsers, and a second source for insiders and 13F |
| **Sharadar** (via Nasdaq Data Link) | US-only, backtesting-grade: point-in-time fundamentals since 1998, delisted companies, insiders, 13F, daily prices | Tens of dollars/month for personal use | The short-history problem for US screen and signal backtests; survivorship-free deep history |
| **Massive (Polygon) paid** | Full US history, real-time, flat files, options, indices | ~$30–200/month; business plans much more | Tiingo history limits; 2-year cap; adds options. US only |

### Specialists

| Vendor | Coverage | Approx. price |
|---|---|---|
| Quiver Quantitative | Congress trades, lobbying, government contracts, insiders, alternative data | Low tens of dollars/month |
| Norgate Data | Survivorship-free US (and Australian) prices with delistings and index membership | A few hundred dollars/year |
| J-Quants (JPX) paid plans | Official Japanese prices and fundamentals, current | ~¥1,650–16,500/month |
| TEJ, FnGuide, KRX Data Marketplace | Taiwanese and Korean prices and fundamentals | Varies |
| Databento | Futures, options and equities market data (CME etc.), usage-based | Pay as you go |
| ORATS, CBOE DataShop | Options history, implied volatility | ~$100+/month |
| Estimize, Zacks (via Nasdaq Data Link) | Consensus earnings estimates | Varies |
| Benzinga, RavenPack | News and sentiment | Varies, mostly enterprise |

### Enterprise (for a commercial product)

LSEG Workspace (Refinitiv, I/B/E/S estimates), Bloomberg (Terminal, B-PIPE, Data License),
FactSet, S&P Capital IQ, Morningstar, Xignite, Barchart OnDemand. Typically $20,000+ a
year and up, with explicit redistribution licensing: the route to selling data-derived
products. Consolidators like EODHD, FMP and Polygon also sell commercial and
redistribution licenses at business tiers, well below enterprise prices.

### Suggested path

1. **Proof of concept (now):** free sources as above.
2. **First paid step:** one global consolidator (EODHD or FMP) for prices everywhere,
   keeping the regulators for fundamentals. This values Korea, Japan and Europe fully, and
   lets `crosslist.py` become a cross-check instead of a price source. Add Sharadar if US
   backtests need deeper, survivorship-free history.
3. **Commercial product:** a redistribution license from the chosen consolidator (or an
   enterprise vendor), and an audit of every feature built on personal-use data (Massive,
   Tiingo, ESEF repository terms).
