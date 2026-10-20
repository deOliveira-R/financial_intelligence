const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 });

export function num(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function big(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return compact.format(v);
}

export function money(v: number | null | undefined, digits = 0): string {
  if (v == null || Number.isNaN(v)) return "—";
  const s = Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return `${v < 0 ? "−" : ""}$${s}`;
}

export function bigMoney(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v < 0 ? "−" : ""}$${compact.format(Math.abs(v))}`;
}

export function pct(v: number | null | undefined, digits = 1, signed = false): string {
  if (v == null || Number.isNaN(v)) return "—";
  const s = (v * 100).toFixed(digits);
  return `${signed && v > 0 ? "+" : ""}${s}%`;
}

export function signed(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}`;
}

export function tone(v: number | null | undefined): string {
  return v == null || v === 0 ? "" : v > 0 ? "up" : "down";
}

export function date(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(v.length === 10 ? `${v}T00:00:00` : v);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function ago(v: string | null | undefined): string {
  if (!v) return "never";
  const seconds = (Date.now() - new Date(v).getTime()) / 1000;
  const future = seconds < 0;
  const s = Math.abs(seconds);
  const unit =
    s < 90
      ? "just now"
      : s < 3600
        ? `${Math.round(s / 60)} min`
        : s < 86400 * 1.5
          ? `${Math.round(s / 3600)} h`
          : `${Math.round(s / 86400)} d`;
  if (unit === "just now") return unit;
  return future ? `in ${unit}` : `${unit} ago`;
}

export function isoDaysAgo(days: number): string {
  const d = new Date(Date.now() - days * 86400_000);
  return d.toISOString().slice(0, 10);
}
