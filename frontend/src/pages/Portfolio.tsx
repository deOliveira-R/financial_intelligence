import { Fragment, useState, type ReactNode } from "react";
import { useApi, type Harvest, type Position } from "../api";
import { Badge, Card, Empty, Query, Seg } from "../components/ui";
import { date, money, num, pct, tone } from "../format";
import { navigate } from "../router";

type Account = { id: number; name: string; account_type: string; taxable: boolean; number_last4: string | null };
type Replacement = { ticker: string; name: string | null; tracking_error: number; correlation: number; beta: number };
type Realized = {
  symbol: string;
  sold: string;
  acquired: string | null;
  quantity: number;
  proceeds: number;
  cost_basis: number | null;
  gain: number | null;
  term: string | null;
  wash_sale_risk: boolean;
};

function Kpi({ label, value, foot }: { label: string; value: ReactNode; foot?: ReactNode }) {
  return (
    <div className="card kpi">
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      <div className="kpi-foot" style={{ minHeight: 18 }}>
        {foot}
      </div>
    </div>
  );
}

function Replacements({ ticker }: { ticker: string }) {
  const q = useApi<Replacement[]>(`/portfolio/replacements/${ticker}`, { top: 5 });
  return (
    <Query q={q} rows={2} empty={(d) => !d.length} emptyText="No close-tracking ETF found">
      {(rows) => (
        <div className="small" style={{ padding: "4px 0 2px" }}>
          <span className="muted">Keep the exposure with: </span>
          {rows.map((r, i) => (
            <span key={r.ticker}>
              {i > 0 && ", "}
              <a href={`#/company/${r.ticker}`}>{r.ticker}</a>
              <span className="muted"> (TE {pct(r.tracking_error, 1)})</span>
            </span>
          ))}
        </div>
      )}
    </Query>
  );
}

function HarvestCard() {
  const q = useApi<Harvest[]>("/portfolio/harvest");
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Card title="Tax-loss harvesting" sub="taxable lots with unrealized losses" flush>
      <Query q={q} empty={(d) => !d.length} emptyText="No taxable lot is at a loss">
        {(rows) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Lot</th>
                  <th className="r">Loss</th>
                  <th className="r">%</th>
                  <th>Rebuy after</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((h) => {
                  const key = `${h.lot.account}|${h.lot.symbol}|${h.lot.acquired}`;
                  return (
                    <Fragment key={key}>
                      <tr className="clickable" onClick={() => setOpen(open === key ? null : key)}>
                        <td>
                          <span style={{ fontWeight: 600 }}>{h.lot.symbol}</span>{" "}
                          <span className="small muted">{h.lot.acquired ? date(h.lot.acquired) : "acquired date unknown"}</span>
                        </td>
                        <td className="r num down">{money(-h.loss)}</td>
                        <td className="r num down">{pct(-h.loss_pct, 0)}</td>
                        <td className="num">{date(h.rebuy_after)}</td>
                        <td>
                          {h.blocking_purchases.length > 0 ? (
                            <Badge tone="warn">wash sale risk</Badge>
                          ) : !h.history_known ? (
                            <Badge>check history</Badge>
                          ) : (
                            <Badge tone="ok">clear</Badge>
                          )}
                        </td>
                      </tr>
                      {open === key && (
                        <tr>
                          <td colSpan={5} style={{ background: "var(--surface-2)" }}>
                            <Replacements ticker={h.lot.symbol} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
            <div className="small muted" style={{ padding: "8px 16px" }}>
              Total harvestable: <span className="down num">{money(-rows.reduce((s, h) => s + h.loss, 0))}</span>. Click a lot for
              ETFs that keep the exposure during the 30-day window.
            </div>
          </div>
        )}
      </Query>
    </Card>
  );
}

function RealizedCard() {
  const year = new Date().getFullYear();
  const q = useApi<Realized[]>("/portfolio/realized", { year });
  return (
    <Card title={`Realized in ${year}`} flush>
      <Query q={q} empty={(d) => !d.length} emptyText="No sales this year">
        {(rows) => {
          const short = rows.filter((r) => r.term === "short").reduce((s, r) => s + (r.gain ?? 0), 0);
          const long = rows.filter((r) => r.term === "long").reduce((s, r) => s + (r.gain ?? 0), 0);
          return (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Sold</th>
                    <th>Symbol</th>
                    <th>Term</th>
                    <th className="r">Gain</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i}>
                      <td className="num muted">{date(r.sold)}</td>
                      <td>{r.symbol}</td>
                      <td>{r.term ?? "—"}</td>
                      <td className={`r num ${tone(r.gain)}`}>{money(r.gain)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="small muted" style={{ padding: "8px 16px" }}>
                Short-term <span className={tone(short)}>{money(short)}</span> · long-term{" "}
                <span className={tone(long)}>{money(long)}</span>
              </div>
            </div>
          );
        }}
      </Query>
    </Card>
  );
}

export default function Portfolio() {
  const positions = useApi<Position[]>("/portfolio/positions");
  const accounts = useApi<Account[]>("/portfolio/accounts");
  const [account, setAccount] = useState("all");
  const all = positions.data ?? [];
  const rows = all.filter((p) => account === "all" || p.account === account);
  const value = rows.reduce((s, p) => s + (p.market_value ?? 0), 0);
  const cost = rows.reduce((s, p) => s + (p.cost_basis ?? 0), 0);
  const gain = rows.reduce((s, p) => s + (p.unrealized ?? 0), 0);
  const sorted = [...rows].sort((a, b) => (b.market_value ?? 0) - (a.market_value ?? 0));
  const winners = rows.filter((p) => (p.unrealized ?? 0) > 0).length;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Portfolio</h1>
          <p>Positions from broker imports, with cost basis, unrealized gains and tax lots.</p>
        </div>
        {accounts.data && accounts.data.length > 1 && (
          <Seg
            value={account}
            options={[{ value: "all", label: "All accounts" }, ...accounts.data.map((a) => ({ value: a.name, label: a.name }))]}
            onChange={setAccount}
          />
        )}
      </div>

      <Query q={positions} rows={6} empty={(d) => !d.length} emptyText="No positions imported yet (fin-intel import-fidelity)">
        {() => (
          <>
            <div className="kpis">
              <Kpi label="Market value" value={money(value)} foot={<span className="muted">{rows.length} positions</span>} />
              <Kpi label="Cost basis" value={money(cost)} />
              <Kpi
                label="Unrealized gain"
                value={<span className={tone(gain)}>{money(gain)}</span>}
                foot={<span className={tone(gain)}>{pct(cost ? gain / cost : null, 1, true)}</span>}
              />
              <Kpi label="Winners / losers" value={`${winners} / ${rows.length - winners}`} />
            </div>
            <div className="stack">
              <Card title="Positions" flush>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Symbol</th>
                        {account === "all" && <th>Account</th>}
                        <th className="r">Quantity</th>
                        <th className="r">Price</th>
                        <th className="r">Cost</th>
                        <th className="r">Value</th>
                        <th className="r">Gain</th>
                        <th className="r">%</th>
                        <th style={{ width: 130 }}>Weight</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sorted.map((p) => {
                        const g = p.cost_basis ? (p.unrealized ?? 0) / p.cost_basis : null;
                        const w = value ? (p.market_value ?? 0) / value : 0;
                        return (
                          <tr key={`${p.account}|${p.symbol}`} className="clickable" onClick={() => navigate(`/company/${p.symbol}`)}>
                            <td style={{ fontWeight: 600 }}>
                              {p.symbol}
                              {p.reconciled === false && (
                                <span title="Derived quantity differs from the broker's snapshot">
                                  {" "}
                                  <Badge tone="warn">mismatch</Badge>
                                </span>
                              )}
                            </td>
                            {account === "all" && <td className="small muted">{p.account}</td>}
                            <td className="r num">{num(p.quantity, p.quantity % 1 ? 3 : 0)}</td>
                            <td className="r num">{p.market_value && p.quantity ? money(p.market_value / p.quantity, 2) : "—"}</td>
                            <td className="r num muted">{money(p.cost_basis)}</td>
                            <td className="r num">{money(p.market_value)}</td>
                            <td className={`r num ${tone(p.unrealized)}`}>{money(p.unrealized)}</td>
                            <td className={`r num ${tone(g)}`}>{pct(g, 1, true)}</td>
                            <td>
                              <div className="row" style={{ gap: 8 }}>
                                <div className="bar-track" style={{ flex: 1 }}>
                                  <div className="bar-fill" style={{ left: 0, width: `${w * 100}%`, background: "var(--accent)" }} />
                                </div>
                                <span className="num small muted" style={{ width: 36, textAlign: "right" }}>
                                  {pct(w, 0)}
                                </span>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Card>
              <div className="grid cols-2">
                <HarvestCard />
                <RealizedCard />
              </div>
              {all.length === 0 && <Empty>No positions</Empty>}
            </div>
          </>
        )}
      </Query>
    </>
  );
}
