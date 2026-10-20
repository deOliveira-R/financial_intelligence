import { useMemo, useState } from "react";
import { useApi, type Timeseries } from "../api";
import { points, TimeChart, type SeriesSpec } from "../components/TimeChart";
import { Badge, Card, Meter, Query, Seg, Sparkline } from "../components/ui";
import { big, date, isoDaysAgo, money, num, pct, signed, tone } from "../format";

type Kpi = { spec: string; label: string; unit: "price" | "pct" | "share"; invert?: boolean };

// Levels in percent (yields, spreads) change in points; prices change in percent.
const KPIS: Kpi[] = [
  { spec: "px:SPY", label: "S&P 500 (SPY)", unit: "price" },
  { spec: "fred:DGS10", label: "10-year yield", unit: "pct" },
  { spec: "fred:T10Y2Y", label: "10y − 2y curve", unit: "pct" },
  { spec: "fred:VIXCLS", label: "VIX", unit: "price", invert: true },
  { spec: "fred:BAMLH0A0HYM2", label: "High-yield spread", unit: "pct", invert: true },
  { spec: "fred:DCOILWTICO", label: "WTI crude", unit: "price" },
  { spec: "px:GLD", label: "Gold (GLD)", unit: "price" },
  { spec: "breadth:pct_above_200d", label: "Stocks above 200-day", unit: "share" },
];

const CHARTS = {
  spx: {
    label: "S&P 500",
    series: [
      { spec: "px:SPY", title: "SPY", kind: "area" },
      { spec: "px:SPY|sma:200", title: "200-day", kind: "line", dashed: true, color: "--text-3" },
    ],
  },
  rates: {
    label: "Rates",
    series: [
      { spec: "fred:DGS10", title: "10y", kind: "line" },
      { spec: "fred:DGS2", title: "2y", kind: "line", color: "--warn" },
      { spec: "fred:DGS3MO", title: "3m", kind: "line", color: "--text-3" },
    ],
  },
  credit: {
    label: "Credit & vol",
    series: [
      { spec: "fred:BAMLH0A0HYM2", title: "HY spread", kind: "line", color: "--down" },
      { spec: "fred:VIXCLS", title: "VIX", kind: "line", color: "--text-3", scale: "left" },
    ],
  },
  breadth: {
    label: "Breadth",
    series: [{ spec: "breadth:pct_above_200d", title: "% above 200-day", kind: "area", scale100: true }],
  },
} as const;
type ChartKey = keyof typeof CHARTS;

const RANGES = [
  { value: "180", label: "6M" },
  { value: "365", label: "1Y" },
  { value: "1095", label: "3Y" },
  { value: "1825", label: "5Y" },
  { value: "3650", label: "10Y" },
] as const;

function css(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function KpiCard({ kpi, dates, values }: { kpi: Kpi; dates: string[]; values: (number | null)[] }) {
  const known = values.map((v, i) => [v, i] as const).filter(([v]) => v != null) as [number, number][];
  if (!known.length) {
    return (
      <div className="card kpi">
        <span className="kpi-label">{kpi.label}</span>
        <span className="kpi-value muted">—</span>
      </div>
    );
  }
  const [last, lastIdx] = known[known.length - 1];
  const monthAgo = known.filter(([, i]) => i <= lastIdx - 21).pop()?.[0] ?? known[0][0];
  const change = kpi.unit === "price" ? last / monthAgo - 1 : last - monthAgo;
  const good = kpi.invert ? -change : change;
  const shown =
    kpi.unit === "price" ? (last > 1000 ? num(last, 0) : num(last, 2)) : kpi.unit === "pct" ? `${num(last, 2)}%` : pct(last, 0);
  const delta =
    kpi.unit === "price" ? pct(change, 1, true) : kpi.unit === "pct" ? `${signed(change * 100, 0)} bp` : `${signed(change * 100, 1)} pts`;
  return (
    <div className="card kpi">
      <span className="kpi-label">{kpi.label}</span>
      <span className="kpi-value">{shown}</span>
      <div className="kpi-foot">
        <span>
          <span className={tone(good)}>{delta}</span> <span className="muted">1M</span>
          <div className="muted" style={{ fontSize: 11 }}>
            {date(dates[lastIdx])}
          </div>
        </span>
        <Sparkline values={values.slice(-260)} color={good >= 0 ? "var(--up)" : "var(--down)"} />
      </div>
    </div>
  );
}

function MainChart() {
  const [key, setKey] = useState<ChartKey>("spx");
  const [range, setRange] = useState<(typeof RANGES)[number]["value"]>("1095");
  const chart = CHARTS[key];
  const ts = useApi<Timeseries>("/timeseries", {
    s: chart.series.map((s) => s.spec),
    start: isoDaysAgo(Number(range)),
  });
  const series = useMemo<SeriesSpec[]>(() => {
    if (!ts.data) return [];
    return chart.series.map((s) => {
      const data = points(ts.data.dates, ts.data.series[s.spec] ?? [], "scale100" in s ? 100 : 1);
      return {
        kind: s.kind,
        data,
        title: s.title,
        dashed: "dashed" in s ? s.dashed : false,
        color: "color" in s ? css(s.color) : undefined,
        scale: "scale" in s ? s.scale : undefined,
      } as SeriesSpec;
    });
  }, [ts.data, chart]);
  return (
    <Card
      title={chart.label}
      actions={
        <>
          <Seg
            value={key}
            options={Object.entries(CHARTS).map(([value, c]) => ({ value: value as ChartKey, label: c.label }))}
            onChange={setKey}
          />
          <Seg value={range} options={RANGES as unknown as { value: typeof range; label: string }[]} onChange={setRange} />
        </>
      }
    >
      <Query q={ts} rows={8}>
        {() => <TimeChart series={series} height={340} percent={key === "breadth"} />}
      </Query>
    </Card>
  );
}

type Release = { date: string; release: string; series: string[] };
type Cot = {
  market: string;
  description: string;
  report_date: string;
  net: number;
  change: number;
  net_pct_oi: number;
  index: number | null;
};
type Cluster = { issuer_cik: number; symbol: string | null; insiders: number; purchases: number; value: number; last: string };
type Popular = { ticker: string; members: number; purchases: number; sales: number; amount_min: number };
type Distress = {
  name: string;
  tickers: string[];
  scored: boolean;
  score?: number;
  cutoff?: number;
  flagged?: boolean;
  signals?: string[];
  reason?: string;
};

function Calendar() {
  const q = useApi<Release[]>("/calendar", { days: 14 });
  return (
    <Card title="Upcoming releases" sub="next 14 days" flush>
      <Query q={q} empty={(d) => !d.length} emptyText="No scheduled releases">
        {(rows) => {
          const byRelease = new Map<string, Release>();
          for (const r of rows) {
            const k = `${r.date}|${r.release}`;
            if (!byRelease.has(k)) byRelease.set(k, r);
          }
          return (
            <div className="table-wrap" style={{ maxHeight: 340, overflowY: "auto" }}>
              <table>
                <tbody>
                  {[...byRelease.values()].map((r) => (
                    <tr key={`${r.date}|${r.release}`}>
                      <td className="num muted" style={{ width: 90 }}>
                        {new Date(`${r.date}T00:00:00`).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}
                      </td>
                      <td>{r.release}</td>
                      <td className="r small muted mono">{r.series.length > 3 ? `${r.series.length} series` : r.series.join(" ")}</td>
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

function Positioning() {
  const q = useApi<Cot[]>("/cot");
  return (
    <Card title="Speculative positioning" sub="CFTC, 3-year COT index" flush>
      <Query q={q} empty={(d) => !d.length}>
        {(rows) => (
          <div className="table-wrap" style={{ maxHeight: 340, overflowY: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>Market</th>
                  <th className="r">Net % OI</th>
                  <th style={{ width: 120 }}>Index</th>
                  <th className="r"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const extreme = r.index != null && (r.index >= 90 || r.index <= 10);
                  return (
                    <tr key={r.market} title={`${r.description} · report ${r.report_date}`}>
                      <td>
                        <span style={{ fontWeight: 550 }}>{r.description.split(" (")[0]}</span>
                      </td>
                      <td className={`r num ${tone(r.net_pct_oi)}`}>{num(r.net_pct_oi, 1)}%</td>
                      <td>
                        <Meter value={r.index} tone={extreme ? "var(--warn)" : "var(--accent)"} />
                      </td>
                      <td className="r num small">{r.index == null ? "—" : num(r.index, 0)}</td>
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

function InsiderClusters() {
  const q = useApi<Cluster[]>("/insiders/clusters", { days: 60, min_insiders: 3 });
  return (
    <Card title="Insider cluster buys" sub="3+ insiders, open market, 60 days" flush>
      <Query q={q} empty={(d) => !d.length} emptyText="No clusters in the window">
        {(rows) => (
          <div className="table-wrap" style={{ maxHeight: 340, overflowY: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>Company</th>
                  <th className="r">Insiders</th>
                  <th className="r">Value</th>
                  <th className="r">Last</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 25).map((r, i) => (
                  <tr key={i}>
                    <td>
                      {r.symbol && r.symbol !== "NONE" ? (
                        <a href={`#/company/${r.symbol.split(",")[0].trim()}`}>{r.symbol}</a>
                      ) : (
                        <span className="muted">CIK {r.issuer_cik}</span>
                      )}
                    </td>
                    <td className="r num">{r.insiders}</td>
                    <td className="r num">{money(r.value)}</td>
                    <td className="r num muted">{date(r.last)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Query>
    </Card>
  );
}

function CongressPopular() {
  const q = useApi<Popular[]>("/congress/popular", { days: 90, limit: 25 });
  return (
    <Card title="Most traded in Congress" sub="distinct members, 90 days" flush>
      <Query q={q} empty={(d) => !d.length}>
        {(rows) => (
          <div className="table-wrap" style={{ maxHeight: 340, overflowY: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th className="r">Members</th>
                  <th className="r">Buys / sells</th>
                  <th className="r">At least</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.ticker}>
                    <td>
                      <a href={`#/company/${r.ticker}`}>{r.ticker}</a>
                    </td>
                    <td className="r num">{r.members}</td>
                    <td className="r num">
                      <span className="up">{r.purchases}</span> <span className="muted">/</span>{" "}
                      <span className="down">{r.sales}</span>
                    </td>
                    <td className="r num muted">{big(r.amount_min)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Query>
    </Card>
  );
}

function DistressWatch() {
  const q = useApi<Distress[]>("/distress");
  return (
    <Card title="Distress watch" sub="watchlist and holdings, H001 model" flush>
      <Query q={q} empty={(d) => !d.length} emptyText="Nothing scored yet">
        {(rows) => {
          const scored = rows.filter((r) => r.scored).sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
          const outside = rows.filter((r) => !r.scored);
          return (
            <div className="table-wrap" style={{ maxHeight: 340, overflowY: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th>Company</th>
                    <th style={{ width: 110 }}>Score vs flag</th>
                    <th>Signals</th>
                  </tr>
                </thead>
                <tbody>
                  {scored.map((r) => {
                    const cutoff = r.cutoff ?? 1;
                    const score = r.score ?? 0;
                    return (
                      <tr key={r.name}>
                        <td>
                          <a href={`#/company/${r.tickers[0]}`} style={{ fontWeight: 550 }}>
                            {r.tickers[0]}
                          </a>{" "}
                          {r.flagged && <Badge tone="bad">flagged</Badge>}
                          <div className="small muted" style={{ maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {r.name}
                          </div>
                        </td>
                        <td>
                          <Meter
                            value={score}
                            max={cutoff}
                            tone={r.flagged ? "var(--down)" : score > cutoff * 0.6 ? "var(--warn)" : "var(--up)"}
                          />
                        </td>
                        <td className="small muted" style={{ maxWidth: 140, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={(r.signals ?? []).join(", ")}>
                          {(r.signals ?? []).map((s) => s.replace(/_[\d.]+$/, "").replace(/_/g, " ")).join(", ") || "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {outside.length > 0 && (
                <div className="small muted" style={{ padding: "8px 16px" }} title={outside.map((r) => `${r.tickers[0]}: ${r.reason}`).join("\n")}>
                  Not scored: {outside.map((r) => r.tickers[0]).join(", ")} (no recent annual report or float)
                </div>
              )}
            </div>
          );
        }}
      </Query>
    </Card>
  );
}

export default function Markets() {
  const ts = useApi<Timeseries>("/timeseries", { s: KPIS.map((k) => k.spec), start: isoDaysAgo(400) });
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Markets</h1>
          <p>Prices, rates, credit and positioning, with what the big players did recently.</p>
        </div>
      </div>
      <div className="kpis">
        {KPIS.map((k) =>
          ts.data ? (
            <KpiCard key={k.spec} kpi={k} dates={ts.data.dates} values={ts.data.series[k.spec] ?? []} />
          ) : (
            <div key={k.spec} className="card kpi">
              <span className="kpi-label">{k.label}</span>
              <div className="skeleton" style={{ height: 26, width: "60%", margin: "4px 0" }} />
              <div className="skeleton" style={{ height: 14, width: "80%" }} />
            </div>
          ),
        )}
      </div>
      <div className="stack">
        <MainChart />
        <div className="grid cols-3">
          <Positioning />
          <DistressWatch />
          <Calendar />
        </div>
        <div className="grid cols-2">
          <InsiderClusters />
          <CongressPopular />
        </div>
      </div>
    </>
  );
}
