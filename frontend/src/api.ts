import { useQuery, type UseQueryOptions } from "@tanstack/react-query";

// Built, the dashboard is served by the API itself; in development Vite proxies /api.
const BASE = import.meta.env.DEV ? "/api" : "";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

type Params = Record<string, string | number | boolean | string[] | null | undefined>;

export function url(path: string, params?: Params): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v == null) continue;
    for (const item of Array.isArray(v) ? v : [v]) q.append(k, String(item));
  }
  const qs = q.toString();
  return `${BASE}${path}${qs ? `?${qs}` : ""}`;
}

export async function request<T>(path: string, init?: RequestInit & { params?: Params }) {
  const res = await fetch(url(path, init?.params), {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export function useApi<T>(
  path: string | null,
  params?: Params,
  options?: Omit<UseQueryOptions<T, ApiError>, "queryKey" | "queryFn">,
) {
  return useQuery<T, ApiError>({
    queryKey: [path, params],
    queryFn: () => request<T>(path!, { params }),
    enabled: path != null,
    ...options,
  });
}

// --- shapes ------------------------------------------------------------------------

export interface Job {
  name: string;
  label: string;
  group: string;
  after: string[];
  status: "ok" | "due" | "running" | "retrying" | "failed" | "blocked" | "never run";
  attempts: number;
  last_success: string | null;
  last_failure: string | null;
  next_attempt: string | null;
  error_kind: string | null;
  error: string | null;
  hint: string | null;
}

export interface JobEvent {
  started_at: string;
  finished_at: string;
  status: string;
  attempt: number;
  error_kind: string | null;
  error: string | null;
  output: string | null;
}

export interface Status {
  jobs: Job[];
  failing: { provider: string; dataset: string; key: string; last_error: string | null }[];
}

export interface Feed {
  provider: string;
  dataset: string;
  status: "ok" | "stale" | "failing" | "unmonitored";
  items: number;
  failing: number;
  last_success: string | null;
  age_days: number | null;
  expected_days: number | null;
  last_error: string | null;
  tables: string[];
  raw_responses: number;
}

export interface Catalog {
  feeds: Feed[];
  tables: Record<string, number>;
}

export interface Setting {
  name: string;
  env: string;
  label: string;
  group: "keys" | "identity" | "watchlist";
  help: string;
  url: string | null;
  secret: boolean;
  required: boolean;
  source: "dashboard" | "env" | "default" | null;
  value: string | null;
  updated_at: string | null;
  status: "working" | "rejected" | "missing" | "not set" | "unused" | "default";
  detail: string | null;
  last_used: string | null;
}

export interface Timeseries {
  dates: string[];
  series: Record<string, (number | null)[]>;
}

export interface Bar {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  adj_open: number | null;
  adj_high: number | null;
  adj_low: number | null;
  adj_close: number | null;
}

export interface SecurityHit {
  id: number;
  ticker: string | null;
  name: string | null;
  security_type: string | null;
  exchange: string | null;
  active: boolean;
  cik: number | null;
}

export interface TimelineEvent {
  date: string;
  kind: string;
  title: string;
  ticker: string | null;
  amount: number | null;
  actor: string | null;
  ref: string;
}

export interface Dossier {
  as_of: string;
  identity: {
    cik: number;
    name: string;
    ticker: string;
    tickers: string[];
    sic: number | null;
    sic_description: string | null;
    country: string | null;
    industries: { node: string; label: string; weight: number }[];
  };
  metrics: Record<string, number | string | null> | null;
  ownership: { period: string; holders: number; value: number; shares: number } | null;
  short_interest: {
    settlement_date: string;
    short_position: number;
    days_to_cover: number;
    average_daily_volume: number;
    change: number;
  } | null;
  index_funds: { fund: string; weight: number; [k: string]: unknown }[];
  filing_text: { accession: string; form: string; section: string; filed: string }[];
  timeline: TimelineEvent[];
  themes: { theme: string }[];
}

export interface Position {
  account: string;
  symbol: string;
  quantity: number;
  cost_basis: number | null;
  market_value: number | null;
  unrealized: number | null;
  unrealized_short: number | null;
  unrealized_long: number | null;
  broker_quantity: number | null;
  reconciled: boolean | null;
  basis_source: string;
}

export interface Lot {
  account: string;
  taxable: boolean;
  symbol: string;
  acquired: string | null;
  quantity: number;
  cost_basis: number | null;
  price: number | null;
  market_value: number | null;
  unrealized: number | null;
  term: string;
  basis_source: string;
}

export interface Harvest {
  lot: Lot;
  loss: number;
  loss_pct: number;
  blocking_purchases: unknown[];
  rebuy_after: string;
  history_known: boolean;
}

export interface Context {
  symbol: string;
  as_of: string;
  price: number;
  high_52w: number;
  low_52w: number;
  drawdown_from_high: number;
  vs_200d_avg: number;
  returns: Record<string, number | null>;
  spy_returns: Record<string, number | null>;
}
