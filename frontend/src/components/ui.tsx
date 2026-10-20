import { Component, type ReactNode } from "react";
import type { ApiError } from "../api";

export function Card(props: {
  title?: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  className?: string;
}) {
  return (
    <section className={`card ${props.className ?? ""}`}>
      {(props.title || props.actions) && (
        <div className="card-head">
          {props.title && <h2>{props.title}</h2>}
          {props.sub && <span className="sub">{props.sub}</span>}
          {props.actions && <div className="actions">{props.actions}</div>}
        </div>
      )}
      <div className={props.flush ? "card-body flush" : "card-body"}>
        <Boundary>{props.children}</Boundary>
      </div>
    </section>
  );
}

/** One card failing to render (an unexpected shape) mustn't blank the page. */
export class Boundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (this.state.error) return <div className="error-box">Couldn't show this: {this.state.error.message}</div>;
    return this.props.children;
  }
}

export function Badge({ tone, children }: { tone?: string; children: ReactNode }) {
  return <span className={`badge ${tone ?? ""}`}>{children}</span>;
}

export function Seg<T extends string>(props: {
  value: T;
  options: readonly T[] | { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  const options = (props.options as (T | { value: T; label: string })[]).map((o) =>
    typeof o === "string" ? { value: o, label: o } : o,
  );
  return (
    <div className="seg">
      {options.map((o) => (
        <button
          key={o.value}
          className={o.value === props.value ? "on" : ""}
          onClick={() => props.onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Loading({ rows = 4, height = 18 }: { rows?: number; height?: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: "4px 16px 16px" }}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ height, width: `${92 - i * 9}%` }} />
      ))}
    </div>
  );
}

export function Failed({ error }: { error: ApiError | null }) {
  if (!error) return null;
  return <div className="error-box">{error.message || "Request failed"}</div>;
}

/** Loading, error and empty states around a query's data. */
export function Query<T>(props: {
  q: { data: T | undefined; isLoading: boolean; error: ApiError | null };
  empty?: (data: T) => boolean;
  emptyText?: ReactNode;
  rows?: number;
  children: (data: T) => ReactNode;
}) {
  const { q } = props;
  if (q.isLoading) return <Loading rows={props.rows} />;
  if (q.error) {
    if (q.error.status === 404) return <Empty>{props.emptyText ?? "No data"}</Empty>;
    return <Failed error={q.error} />;
  }
  if (q.data === undefined) return null;
  if (props.empty?.(q.data)) return <Empty>{props.emptyText ?? "No data"}</Empty>;
  return <>{props.children(q.data)}</>;
}

export function Sparkline({
  values,
  width = 96,
  height = 30,
  color,
}: {
  values: (number | null)[];
  width?: number;
  height?: number;
  color?: string;
}) {
  const pts = values
    .map((v, i) => [i, v] as const)
    .filter((p): p is readonly [number, number] => p[1] != null);
  if (pts.length < 2) return <svg width={width} height={height} />;
  const ys = pts.map((p) => p[1]);
  const min = Math.min(...ys);
  const max = Math.max(...ys);
  const span = max - min || 1;
  const n = values.length - 1 || 1;
  const d = pts
    .map(([i, v], k) => {
      const x = (i / n) * width;
      const y = height - 2 - ((v - min) / span) * (height - 4);
      return `${k ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join("");
  const stroke = color ?? (ys[ys.length - 1] >= ys[0] ? "var(--up)" : "var(--down)");
  return (
    <svg width={width} height={height} style={{ display: "block", overflow: "visible" }}>
      <path d={d} fill="none" stroke={stroke} strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  );
}

/** A 0–100 (or any range) gauge bar with an optional marker. */
export function Meter({
  value,
  max = 100,
  tone,
}: {
  value: number | null;
  max?: number;
  tone?: string;
}) {
  if (value == null) return <span className="muted">—</span>;
  const w = Math.max(0, Math.min(1, value / max)) * 100;
  return (
    <div className="bar-track" title={value.toFixed(0)}>
      <div className="bar-fill" style={{ left: 0, width: `${w}%`, background: tone ?? "var(--accent)" }} />
    </div>
  );
}

const paths: Record<string, ReactNode> = {
  gear: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </>
  ),
  chart: <path d="M4 17l5-5 4 4 7-8" />,
  external: (
    <>
      <path d="M14 4h6v6" />
      <path d="M20 4l-9 9" />
      <path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
    </>
  ),
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  alert: (
    <>
      <path d="M12 3 2 20h20L12 3z" />
      <path d="M12 10v4M12 17h.01" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </>
  ),
  moon: <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />,
  back: <path d="M15 18l-6-6 6-6" />,
};

export function Icon({ name, size = 16 }: { name: keyof typeof paths; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {paths[name]}
    </svg>
  );
}
