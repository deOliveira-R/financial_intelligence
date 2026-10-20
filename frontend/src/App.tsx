import { useEffect, useState } from "react";
import { useApi, type Catalog, type Status } from "./api";
import { Icon } from "./components/ui";
import Company from "./pages/Company";
import Markets from "./pages/Markets";
import Portfolio from "./pages/Portfolio";
import Settings from "./pages/Settings";
import SystemStatus from "./pages/Status";
import { useRoute } from "./router";

const TABS = [
  { path: "markets", label: "Markets" },
  { path: "company", label: "Company" },
  { path: "portfolio", label: "Portfolio" },
];

export type Health = {
  level: "ok" | "warn" | "bad" | "unknown";
  attention: number; // failed or blocked steps: these need a person (or a model)
  retrying: number; // will retry on their own
  feedAlerts: number; // stale or failing feeds
};

/** The system's state in one word, for the header: quiet while everything is fine. */
export function useHealth(): Health {
  const status = useApi<Status>("/status", undefined, { refetchInterval: 60_000, staleTime: 30_000 });
  const catalog = useApi<Catalog>("/catalog", { alerts_only: true }, { refetchInterval: 300_000 });
  if (!status.data) return { level: "unknown", attention: 0, retrying: 0, feedAlerts: 0 };
  const attention = status.data.jobs.filter((j) => j.status === "failed" || j.status === "blocked").length;
  const retrying = status.data.jobs.filter((j) => j.status === "retrying").length;
  const feedAlerts = catalog.data?.feeds.length ?? 0;
  const level = attention ? "bad" : retrying || feedAlerts ? "warn" : "ok";
  return { level, attention, retrying, feedAlerts };
}

function StatusPill({ active }: { active: boolean }) {
  const h = useHealth();
  const label =
    h.level === "bad"
      ? `${h.attention} need${h.attention === 1 ? "s" : ""} attention`
      : h.level === "warn"
        ? h.retrying
          ? `${h.retrying} retrying`
          : `${h.feedAlerts} feed${h.feedAlerts === 1 ? "" : "s"} behind`
        : null;
  const title =
    h.level === "ok"
      ? "All scheduled steps are fine"
      : h.level === "unknown"
        ? "Checking the system…"
        : label ?? "";
  return (
    <a
      href="#/status"
      className={`status-pill ${label ? "" : "quiet"}`}
      title={title}
      style={active ? { borderColor: "var(--accent)" } : undefined}
    >
      <span className={`dot ${h.level === "unknown" ? "" : h.level}`} />
      {label && <span className="label">{label}</span>}
    </a>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<string | null>(() => {
    try {
      return localStorage.getItem("theme");
    } catch {
      return null;
    }
  });
  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme;
    else delete document.documentElement.dataset.theme;
  }, [theme]);
  const dark =
    theme === "dark" || (theme == null && window.matchMedia("(prefers-color-scheme: dark)").matches);
  const flip = () => {
    const next = dark ? "light" : "dark";
    setTheme(next);
    try {
      localStorage.setItem("theme", next);
    } catch {
      /* private mode */
    }
  };
  return (
    <button className="icon-btn" onClick={flip} title={dark ? "Light theme" : "Dark theme"}>
      <Icon name={dark ? "sun" : "moon"} />
    </button>
  );
}

export default function App() {
  const [page = "markets", ...rest] = useRoute();
  let content;
  switch (page) {
    case "company":
      content = <Company ticker={rest[0]?.toUpperCase()} />;
      break;
    case "portfolio":
      content = <Portfolio />;
      break;
    case "settings":
      content = <Settings />;
      break;
    case "status":
      content = <SystemStatus step={rest[0]} />;
      break;
    default:
      content = <Markets />;
  }
  return (
    <>
      <header className="topbar">
        <a href="#/markets" className="brand">
          <span className="brand-mark">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 17l6-6 4 4 8-9" />
            </svg>
          </span>
          <span>fin-intel</span>
        </a>
        <nav className="tabs">
          {TABS.map((t) => (
            <a key={t.path} href={`#/${t.path}`} className={`tab ${page === t.path ? "active" : ""}`}>
              {t.label}
            </a>
          ))}
        </nav>
        <div className="topbar-right">
          <StatusPill active={page === "status"} />
          <ThemeToggle />
          <a href="#/settings" className={`icon-btn ${page === "settings" ? "active" : ""}`} title="Settings">
            <Icon name="gear" />
          </a>
        </div>
      </header>
      <main>{content}</main>
    </>
  );
}
