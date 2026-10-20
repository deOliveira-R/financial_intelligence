import { useEffect, useMemo, useRef, useState } from "react";
import {
  useApi,
  type Bar,
  type Context,
  type Dossier,
  type Position,
  type SecurityHit,
} from "../api";
import { TimeChart, type SeriesSpec } from "../components/TimeChart";
import { Badge, Card, Empty, Icon, Query, Seg } from "../components/ui";
import { big, bigMoney, date, isoDaysAgo, money, num, pct, tone } from "../format";
import { navigate } from "../router";

// --- search ---------------------------------------------------------------------------

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function TickerSearch({ autoFocus = false }: { autoFocus?: boolean }) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const q = useDebounced(text.trim(), 180);
  const hits = useApi<SecurityHit[]>(q ? "/securities" : null, { q, active: true, limit: 40 });
  const box = useRef<HTMLDivElement>(null);

  // Exact ticker first, then common stock and ETFs, then the rest.
  const results = useMemo(() => {
    const rank = (h: SecurityHit) =>
      (h.ticker?.toUpperCase() === q.toUpperCase() ? 0 : 10) +
      (h.ticker?.toUpperCase().startsWith(q.toUpperCase()) ? 0 : 5) +
      (h.security_type === "CS" || h.security_type === "ETF" || h.security_type === "ADRC" ? 0 : 3);
    return [...(hits.data ?? [])].filter((h) => h.ticker).sort((a, b) => rank(a) - rank(b)).slice(0, 8);
  }, [hits.data, q]);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const go = (ticker: string) => {
    setText("");
    setOpen(false);
    navigate(`/company/${ticker}`);
  };

  return (
    <div className="search" ref={box}>
      <span className="search-icon">
        <Icon name="search" />
      </span>
      <input
        className="input"
        placeholder="Search a ticker or company"
        value={text}
        autoFocus={autoFocus}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
          setCursor(0);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setCursor((c) => Math.min(c + 1, results.length - 1));
          else if (e.key === "ArrowUp") setCursor((c) => Math.max(c - 1, 0));
          else if (e.key === "Enter") {
            const hit = results[cursor];
            if (hit?.ticker) go(hit.ticker);
            else if (text.trim()) go(text.trim().toUpperCase());
          } else if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && q && results.length > 0 && (
        <div className="search-results">
          {results.map((h, i) => (
            <div
              key={h.id}
              className={`search-result ${i === cursor ? "on" : ""}`}
              onMouseEnter={() => setCursor(i)}
              onMouseDown={() => go(h.ticker!)}
            >
              <span className="t">{h.ticker}</span>
              <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{h.name}</span>
              <span className="small muted">{h.security_type}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// --- price ------------------------------------------------------------------------------

const RANGES = [
  { value: "90", label: "3M" },
  { value: "365", label: "1Y" },
  { value: "1095", label: "3Y" },
  { value: "1825", label: "5Y" },
  { value: "7300", label: "20Y" },
] as const;

function PriceCard({ ticker }: { ticker: string }) {
  const [range, setRange] = useState<(typeof RANGES)[number]["value"]>("365");
  const [style, setStyle] = useState<"line" | "candles">("line");
  const start = isoDaysAgo(Number(range));
  // Tiingo's history is longer and adjusted; Massive covers every US listing for 2 years.
  const tiingo = useApi<Bar[]>(`/prices/${ticker}/daily`, { start, source: "tiingo" });
  const fallback = tiingo.data && tiingo.data.length === 0;
  const massive = useApi<Bar[]>(fallback ? `/prices/${ticker}/daily` : null, { start, source: "massive" });
  const q = fallback ? massive : tiingo;
  const series = useMemo<SeriesSpec[]>(() => {
    const bars = (q.data ?? []).filter((b) => b.adj_close != null);
    if (style === "candles") {
      return [
        {
          kind: "candles",
          data: bars
            .filter((b) => b.adj_open != null && b.adj_high != null && b.adj_low != null)
            .map((b) => ({ time: b.date, open: b.adj_open!, high: b.adj_high!, low: b.adj_low!, close: b.adj_close! })),
        },
        { kind: "volume", data: bars.map((b) => ({ time: b.date, value: b.volume ?? 0 })) },
      ];
    }
    const first = bars[0]?.adj_close ?? 0;
    const last = bars[bars.length - 1]?.adj_close ?? 0;
    const color = getComputedStyle(document.documentElement).getPropertyValue(last >= first ? "--up" : "--down").trim();
    return [
      { kind: "area", color, data: bars.map((b) => ({ time: b.date, value: b.adj_close! })) },
      { kind: "volume", data: bars.map((b) => ({ time: b.date, value: b.volume ?? 0 })) },
    ];
  }, [q.data, style]);
  const bars = q.data ?? [];
  const change = bars.length > 1 ? bars[bars.length - 1].adj_close! / bars[0].adj_close! - 1 : null;
  return (
    <Card
      title="Price"
      sub={
        <>
          {change != null && <span className={tone(change)}>{pct(change, 1, true)}</span>}
          {fallback && " · Massive (2 years)"}
          {" · adjusted for splits and dividends"}
        </>
      }
      actions={
        <>
          <Seg value={style} options={[{ value: "line", label: "Line" }, { value: "candles", label: "Candles" }]} onChange={setStyle} />
          <Seg value={range} options={RANGES as unknown as { value: typeof range; label: string }[]} onChange={setRange} />
        </>
      }
    >
      <Query q={q} rows={8} empty={(d) => d.length === 0} emptyText="No price history stored for this ticker">
        {() => <TimeChart series={series} height={340} />}
      </Query>
    </Card>
  );
}

// --- metrics ----------------------------------------------------------------------------

type Fmt = "x" | "pct" | "money" | "num";
const METRIC_GROUPS: { title: string; items: [string, string, Fmt][] }[] = [
  {
    title: "Valuation",
    items: [
      ["pe", "P/E", "x"],
      ["ev_ebit", "EV/EBIT", "x"],
      ["ev_ebitda", "EV/EBITDA", "x"],
      ["ev_sales", "EV/Sales", "x"],
      ["p_fcf", "P/FCF", "x"],
      ["p_b", "P/B", "x"],
      ["earnings_yield", "Earnings yield", "pct"],
      ["fcf_yield", "FCF yield", "pct"],
      ["dividend_yield", "Dividend yield", "pct"],
      ["shareholder_yield", "Shareholder yield", "pct"],
    ],
  },
  {
    title: "Size (trailing 12 months)",
    items: [
      ["market_cap", "Market cap", "money"],
      ["enterprise_value", "Enterprise value", "money"],
      ["revenue_ttm", "Revenue", "money"],
      ["ebit_ttm", "EBIT", "money"],
      ["net_income_ttm", "Net income", "money"],
      ["fcf_ttm", "Free cash flow", "money"],
    ],
  },
  {
    title: "Quality",
    items: [
      ["gross_margin", "Gross margin", "pct"],
      ["operating_margin", "Operating margin", "pct"],
      ["net_margin", "Net margin", "pct"],
      ["roe", "ROE", "pct"],
      ["roic", "ROIC", "pct"],
      ["piotroski_f", "Piotroski F", "num"],
      ["altman_z", "Altman Z", "num"],
    ],
  },
  {
    title: "Balance sheet and growth",
    items: [
      ["debt_to_equity", "Debt/Equity", "x"],
      ["net_debt_to_ebitda", "Net debt/EBITDA", "x"],
      ["current_ratio", "Current ratio", "x"],
      ["interest_coverage", "Interest coverage", "x"],
      ["revenue_growth", "Revenue growth", "pct"],
      ["earnings_growth", "Earnings growth", "pct"],
      ["ebit_growth", "EBIT growth", "pct"],
      ["operating_margin_vs_5y", "Op. margin vs 5y", "pct"],
    ],
  },
];

function fmtMetric(v: unknown, f: Fmt, currency = "USD"): string {
  if (typeof v !== "number") return "—";
  if (f === "x") return `${num(v, 1)}×`;
  if (f === "pct") return pct(v, 1);
  if (f === "money") return currency === "USD" ? bigMoney(v) : `${big(v)} ${currency}`;
  return num(v, 1);
}

function MetricsCard({ metrics }: { metrics: Dossier["metrics"] }) {
  if (!metrics || "error" in metrics) {
    return (
      <Card title="Metrics">
        <Empty>No point-in-time metrics (not a primary listing with SEC financials)</Empty>
      </Card>
    );
  }
  const currency = (metrics.currency as string) ?? "USD";
  return (
    <Card title="Metrics" sub={`as of ${date(metrics.as_of as string)} · financials to ${date(metrics.period_end as string)}`} flush>
      {METRIC_GROUPS.map((g) => ({ ...g, items: g.items.filter(([k]) => typeof metrics[k] === "number") }))
        .filter((g) => g.items.length > 0)
        .map((g) => (
        <div key={g.title}>
          <div className="small muted" style={{ padding: "10px 16px 6px", fontWeight: 550 }}>
            {g.title}
          </div>
          <div className="kv">
            {g.items.map(([k, label, f]) => (
              <div key={k}>
                <div className="k">{label}</div>
                <div className="v">{fmtMetric(metrics[k], f, currency)}</div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </Card>
  );
}

// --- statements -------------------------------------------------------------------------

const LINES: Record<string, [string, string][]> = {
  income: [
    ["revenue", "Revenue"],
    ["cost_of_revenue", "Cost of revenue"],
    ["gross_profit", "Gross profit"],
    ["operating_income", "Operating income"],
    ["interest_expense", "Interest expense"],
    ["pretax_income", "Pretax income"],
    ["income_tax", "Income tax"],
    ["net_income", "Net income"],
    ["eps_diluted", "EPS (diluted)"],
    ["shares_diluted", "Diluted shares"],
  ],
  balance: [
    ["cash", "Cash"],
    ["short_term_investments", "Short-term investments"],
    ["current_assets", "Current assets"],
    ["total_assets", "Total assets"],
    ["current_liabilities", "Current liabilities"],
    ["short_term_debt", "Short-term debt"],
    ["long_term_debt", "Long-term debt"],
    ["lease_liabilities", "Lease liabilities"],
    ["total_liabilities", "Total liabilities"],
    ["equity", "Equity"],
    ["retained_earnings", "Retained earnings"],
    ["shares_outstanding", "Shares outstanding"],
  ],
  cash_flow: [
    ["operating_cash_flow", "Operating cash flow"],
    ["capex", "Capital expenditure"],
    ["depreciation", "Depreciation"],
    ["dividends_paid", "Dividends paid"],
    ["buybacks", "Buybacks"],
  ],
};

type StatementResp = { periods: Record<string, unknown>[] };

function StatementsCard({ ticker }: { ticker: string }) {
  const [statement, setStatement] = useState<"income" | "balance" | "cash_flow">("income");
  const [period, setPeriod] = useState<"annual" | "quarter">("annual");
  const q = useApi<StatementResp>(`/fundamentals/${ticker}/statements`, { statement, period, limit: 8 });
  return (
    <Card
      title="Financial statements"
      actions={
        <>
          <Seg
            value={statement}
            options={[
              { value: "income", label: "Income" },
              { value: "balance", label: "Balance" },
              { value: "cash_flow", label: "Cash flow" },
            ]}
            onChange={setStatement}
          />
          <Seg value={period} options={[{ value: "annual", label: "Annual" }, { value: "quarter", label: "Quarterly" }]} onChange={setPeriod} />
        </>
      }
      flush
    >
      <Query q={q} empty={(d) => d.periods.length === 0} emptyText="No statements stored">
        {(d) => {
          const periods = [...d.periods].reverse();
          const lines = LINES[statement].filter(([k]) => periods.some((p) => p[k] != null));
          return (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th></th>
                    {periods.map((p) => (
                      <th key={String(p.period_end)} className="r">
                        {period === "annual" ? `FY${p.fiscal_year ?? ""}` : `${p.fiscal_period ?? ""} ${p.fiscal_year ?? ""}`}
                        <div style={{ fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>{date(String(p.period_end))}</div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {lines.map(([k, label]) => (
                    <tr key={k}>
                      <td style={{ whiteSpace: "nowrap" }}>{label}</td>
                      {periods.map((p) => {
                        const v = p[k] as number | undefined;
                        const concept = (p.concepts as Record<string, string>)[k];
                        return (
                          <td key={String(p.period_end)} className="r num" title={concept}>
                            {v == null ? <span className="muted">—</span> : k.startsWith("eps") ? num(v, 2) : big(v)}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }}
      </Query>
    </Card>
  );
}

// --- players ----------------------------------------------------------------------------

type Insider = {
  trans_date: string;
  filing_date: string | null;
  owner_name: string | null;
  owner_title: string | null;
  relationship: string | null;
  trans_code: string | null;
  acquired_disposed: string | null;
  shares: number | null;
  price: number | null;
  plan_10b5_1: boolean;
};

const CODES: Record<string, string> = {
  P: "Buy",
  S: "Sale",
  A: "Award",
  M: "Option exercise",
  F: "Tax withholding",
  G: "Gift",
  C: "Conversion",
  X: "Option exercise",
};

function InsidersCard({ ticker }: { ticker: string }) {
  const [only, setOnly] = useState<"market" | "all">("market");
  const q = useApi<Insider[]>(`/insiders/${ticker}`, { limit: 200 });
  const rows = (q.data ?? []).filter((r) => only === "all" || r.trans_code === "P" || r.trans_code === "S");
  return (
    <Card
      title="Insider transactions"
      actions={<Seg value={only} options={[{ value: "market", label: "Buys & sales" }, { value: "all", label: "All" }]} onChange={setOnly} />}
      flush
    >
      <Query q={q} empty={() => rows.length === 0} emptyText="No insider transactions">
        {() => (
          <div className="table-wrap" style={{ maxHeight: 380, overflowY: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Insider</th>
                  <th>Type</th>
                  <th className="r">Shares</th>
                  <th className="r">Value</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 60).map((r, i) => {
                  const buy = r.trans_code === "P";
                  const sale = r.trans_code === "S";
                  return (
                    <tr key={i}>
                      <td className="num muted">{date(r.trans_date)}</td>
                      <td>
                        <div style={{ fontWeight: 520 }}>{r.owner_name}</div>
                        <div className="small muted">{r.owner_title || r.relationship}</div>
                      </td>
                      <td>
                        <Badge tone={buy ? "ok" : sale ? "bad" : ""}>{CODES[r.trans_code ?? ""] ?? r.trans_code}</Badge>
                        {r.plan_10b5_1 && <span className="small muted"> 10b5-1</span>}
                      </td>
                      <td className="r num">{big(r.shares)}</td>
                      <td className="r num">{r.shares && r.price ? money(r.shares * r.price) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Query>
    </Card>
  );
}

type Holders = { period: string; holders: { cik: number; name: string; shares: number; value: number; filed: string }[] };

function HoldersCard({ ticker }: { ticker: string }) {
  const q = useApi<Holders>(`/holdings/security/${ticker}`, { limit: 15 });
  return (
    <Card title="Largest institutional holders" sub={q.data ? `13F, quarter ending ${date(q.data.period)}` : "13F"} flush>
      <Query q={q} empty={(d) => d.holders.length === 0} emptyText="No 13F holdings mapped to this security">
        {(d) => {
          const total = d.holders.reduce((s, h) => s + (h.value ?? 0), 0);
          return (
            <div className="table-wrap" style={{ maxHeight: 380, overflowY: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th>Manager</th>
                    <th className="r">Shares</th>
                    <th className="r">Value</th>
                    <th style={{ width: 80 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {d.holders.map((h) => (
                    <tr key={h.cik}>
                      <td style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{h.name}</td>
                      <td className="r num">{big(h.shares)}</td>
                      <td className="r num">{bigMoney(h.value)}</td>
                      <td>
                        <div className="bar-track">
                          <div className="bar-fill" style={{ left: 0, width: `${(h.value / (d.holders[0].value || 1)) * 100}%`, background: "var(--accent)" }} />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="small muted" style={{ padding: "8px 16px" }}>
                Top {d.holders.length}: {bigMoney(total)}
              </div>
            </div>
          );
        }}
      </Query>
    </Card>
  );
}

const KIND_TONE: Record<string, string> = {
  insider: "info",
  congress: "warn",
  filing_event: "",
  report: "",
  short_interest: "",
  "13f": "info",
};

function TimelineCard({ events }: { events: Dossier["timeline"] }) {
  return (
    <Card title="Timeline" sub="last 90 days, by public date" flush>
      {events.length === 0 ? (
        <Empty>Nothing public in the last 90 days</Empty>
      ) : (
        <ul className="timeline" style={{ maxHeight: 420, overflowY: "auto" }}>
          {events.map((e) => (
            <li key={e.ref}>
              <span className="num small muted">{date(e.date)}</span>
              <span>
                <Badge tone={KIND_TONE[e.kind]}>{e.kind.replace("_", " ")}</Badge>
              </span>
              <span>{e.title}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// --- page -------------------------------------------------------------------------------

function Header({ ticker, dossier }: { ticker: string; dossier?: Dossier }) {
  const security = useApi<SecurityHit & { ticker_history: unknown[] }>(`/securities/${ticker}`);
  const ctx = useApi<Context>(`/portfolio/context/${ticker}`);
  const c = ctx.data;
  const name = dossier?.identity.name ?? security.data?.name ?? ticker;
  const r1y = c?.returns["1y"];
  return (
    <div className="page-head" style={{ alignItems: "flex-start" }}>
      <div>
        <div className="row" style={{ gap: 10 }}>
          <h1>{ticker}</h1>
          {security.data?.security_type && <Badge>{security.data.security_type}</Badge>}
          {security.data?.exchange && <span className="small muted">{security.data.exchange}</span>}
        </div>
        <p style={{ fontSize: 15, color: "var(--text-2)" }}>{name}</p>
        {dossier?.identity.sic_description && (
          <p className="small">
            {dossier.identity.sic_description}
            {dossier.themes.length > 0 && ` · ${dossier.themes.map((t) => t.theme.replace("theme:", "").replace(/_/g, " ")).join(", ")}`}
          </p>
        )}
      </div>
      {c && (
        <div style={{ textAlign: "right" }}>
          <div className="kpi-value" style={{ fontSize: 26 }}>
            {money(c.price, 2)}
          </div>
          <div className="small">
            <span className={tone(r1y)}>{pct(r1y, 1, true)}</span>
            <span className="muted"> 1Y vs SPY </span>
            <span className={tone(c.spy_returns["1y"])}>{pct(c.spy_returns["1y"], 1, true)}</span>
          </div>
          <div className="small muted">
            {c.drawdown_from_high > -0.005 ? "at its 52-week high" : `${pct(c.drawdown_from_high, 0)} from 52w high`} · {pct(c.vs_200d_avg, 0, true)} vs 200-day · {date(c.as_of)}
          </div>
        </div>
      )}
    </div>
  );
}

function YourCompanies() {
  const positions = useApi<Position[]>("/portfolio/positions");
  const distress = useApi<{ tickers: string[]; name: string }[]>("/distress");
  const held = new Set((positions.data ?? []).map((p) => p.symbol));
  const watched = (distress.data ?? []).map((d) => d.tickers[0]).filter((t) => t && !held.has(t));
  const groups: [string, string[]][] = [
    ["Holdings", [...held].filter((s) => s !== "SPAXX").sort()],
    ["Watchlist", [...new Set(watched)].sort()],
  ];
  return (
    <div className="grid cols-2" style={{ marginTop: 20 }}>
      {groups.map(([title, tickers]) => (
        <Card key={title} title={title}>
          {tickers.length === 0 ? (
            <Empty>None</Empty>
          ) : (
            <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
              {tickers.map((t) => (
                <a key={t} href={`#/company/${t}`} className="btn">
                  {t}
                </a>
              ))}
            </div>
          )}
        </Card>
      ))}
    </div>
  );
}

export default function Company({ ticker }: { ticker?: string }) {
  const dossier = useApi<Dossier>(ticker ? `/dossier/company/${ticker}` : null, undefined, { retry: false });
  if (!ticker) {
    return (
      <>
        <div className="page-head">
          <div>
            <h1>Company</h1>
            <p>Price, financials, metrics, insiders, institutions and what became public, for any ticker.</p>
          </div>
        </div>
        <TickerSearch autoFocus />
        <YourCompanies />
      </>
    );
  }
  const d = dossier.data;
  const hasFilings = !dossier.error;
  return (
    <>
      <div className="row" style={{ marginBottom: 16, justifyContent: "space-between", flexWrap: "wrap" }}>
        <a href="#/company" className="btn ghost" style={{ paddingLeft: 6 }}>
          <Icon name="back" /> Companies
        </a>
        <TickerSearch />
      </div>
      <Header ticker={ticker} dossier={d} />
      <div className="stack">
        <PriceCard ticker={ticker} />
        {dossier.isLoading ? null : hasFilings ? (
          <>
            <div className="grid cols-2">
              <MetricsCard metrics={d?.metrics ?? null} />
              <div className="stack">
                {d?.short_interest && !("error" in d.short_interest) && (
                  <Card title="Short interest" sub={`settled ${date(d.short_interest.settlement_date)}`}>
                    <div className="row" style={{ gap: 28 }}>
                      <div>
                        <div className="kpi-label">Shares short</div>
                        <div className="kpi-value">{big(d.short_interest.short_position)}</div>
                      </div>
                      <div>
                        <div className="kpi-label">Days to cover</div>
                        <div className="kpi-value">{num(d.short_interest.days_to_cover, 1)}</div>
                      </div>
                      <div>
                        <div className="kpi-label">Change</div>
                        <div className={`kpi-value ${tone(-d.short_interest.change)}`}>{pct(d.short_interest.change, 0, true)}</div>
                      </div>
                    </div>
                  </Card>
                )}
                <TimelineCard events={Array.isArray(d?.timeline) ? d.timeline : []} />
              </div>
            </div>
            <StatementsCard ticker={ticker} />
            <div className="grid cols-2">
              <InsidersCard ticker={ticker} />
              <HoldersCard ticker={ticker} />
            </div>
          </>
        ) : (
          <div className="grid cols-2">
            <HoldersCard ticker={ticker} />
            <Card title="Filings">
              <Empty>No SEC filings for {ticker} (a fund, or a non-SEC issuer)</Empty>
            </Card>
          </div>
        )}
      </div>
    </>
  );
}
