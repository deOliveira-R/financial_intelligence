import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { request, useApi, type Setting } from "../api";
import { Badge, Card, Icon, Query } from "../components/ui";
import { ago } from "../format";

const STATUS: Record<Setting["status"], { tone: string; label: string }> = {
  working: { tone: "ok", label: "Working" },
  rejected: { tone: "bad", label: "Rejected" },
  missing: { tone: "bad", label: "Missing" },
  "not set": { tone: "", label: "Not set" },
  unused: { tone: "", label: "Not used yet" },
  default: { tone: "warn", label: "Shared default" },
};

const SOURCE: Record<string, string> = {
  dashboard: "saved here",
  env: "from .env",
  default: "built-in default",
};

function SettingRow({ s }: { s: Setting }) {
  const client = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: string; text: string } | null>(null);
  const watchlist = s.group === "watchlist";

  const done = async (text: string) => {
    setMessage({ tone: "ok", text });
    setEditing(false);
    setValue("");
    await client.invalidateQueries({ queryKey: ["/settings"] });
    await client.invalidateQueries({ queryKey: ["/status"] });
  };
  const save = async () => {
    setBusy(true);
    try {
      const r = await request<{ retried: string[] }>(`/settings/${s.name}`, {
        method: "PUT",
        body: JSON.stringify({ value }),
      });
      await done(r.retried.length ? `Saved; retrying ${r.retried.join(", ")} now.` : "Saved; used from the next run.");
    } catch (e) {
      setMessage({ tone: "bad", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  const revert = async () => {
    setBusy(true);
    try {
      await request(`/settings/${s.name}`, { method: "DELETE" });
      await done("Removed; the .env value applies again.");
    } catch (e) {
      setMessage({ tone: "bad", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  const status = STATUS[s.status];

  return (
    <div className="setting">
      <div>
        <div style={{ fontWeight: 600 }}>{s.label}</div>
        <div className="mono muted" style={{ fontSize: 11 }}>
          {s.env}
        </div>
      </div>
      <div className="stack" style={{ gap: 6, minWidth: 0 }}>
        <div className="row" style={{ flexWrap: "wrap" }}>
          {!watchlist && <Badge tone={status.tone}>{status.label}</Badge>}
          {s.value ? (
            <span className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
              {watchlist ? s.value.split(",").join(", ") : s.value}
            </span>
          ) : (
            <span className="muted small">no value{s.required ? " (required)" : ""}</span>
          )}
          {s.source && <span className="small muted">· {SOURCE[s.source]}</span>}
          {s.status === "working" && s.last_used && (
            <span className="small muted">· last success {ago(s.last_used)}</span>
          )}
        </div>
        <div className="small muted">
          {s.help}{" "}
          {s.url && (
            <a href={s.url} target="_blank" rel="noreferrer" className="row" style={{ display: "inline-flex", gap: 3 }}>
              {s.group === "keys" ? "Get one" : "Details"} <Icon name="external" size={12} />
            </a>
          )}
        </div>
        {s.detail && <div className="small down">{s.detail}</div>}
        {editing && (
          <div className="row" style={{ marginTop: 4, alignItems: "flex-start" }}>
            {watchlist ? (
              <textarea
                className="input"
                rows={3}
                style={{ flex: 1 }}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="AAPL, MSFT, ASML"
                autoFocus
              />
            ) : (
              <input
                className="input"
                style={{ flex: 1 }}
                type={s.secret ? "password" : "text"}
                autoComplete="off"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && value.trim() && save()}
                placeholder={s.secret ? "Paste the new key" : ""}
                autoFocus
              />
            )}
            <button className="btn primary" disabled={busy || !value.trim()} onClick={save}>
              Save
            </button>
            <button className="btn ghost" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        )}
        {message && <div className={`small ${message.tone === "ok" ? "up" : "down"}`}>{message.text}</div>}
      </div>
      <div className="row">
        {!editing && (
          <button
            className="btn"
            onClick={() => {
              setEditing(true);
              setMessage(null);
              setValue(watchlist || !s.secret ? (s.value ?? "") : "");
            }}
          >
            {s.value ? (watchlist || !s.secret ? "Edit" : "Replace") : "Set"}
          </button>
        )}
        {s.source === "dashboard" && !editing && (
          <button className="btn ghost" disabled={busy} onClick={revert} title="Forget this value; use the one in .env">
            Use .env
          </button>
        )}
      </div>
    </div>
  );
}

export default function Settings() {
  const settings = useApi<Setting[]>("/settings", undefined, { staleTime: 0 });
  const section = (group: Setting["group"]) => settings.data?.filter((s) => s.group === group) ?? [];
  const keys = section("keys");
  const problems = (settings.data ?? []).filter((s) => s.status === "rejected" || s.status === "missing");

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>
            Values saved here override <span className="mono">.env</span> and apply to the next run, with no
            redeploy. Steps blocked on a key are retried when it's saved.
          </p>
        </div>
      </div>
      <Query q={settings} rows={8}>
        {() => (
          <div className="stack">
            {problems.length > 0 && (
              <div className="notice bad" style={{ marginBottom: 0 }}>
                <span style={{ color: "var(--down)" }}>
                  <Icon name="alert" />
                </span>
                <div>
                  <strong>{problems.map((p) => p.label).join(", ")}</strong>{" "}
                  {problems.length > 1 ? "need" : "needs"} a new value.
                </div>
              </div>
            )}
            <Card title="Data provider keys" sub={`${keys.filter((k) => k.status === "working").length} of ${keys.length} working`} flush>
              {keys.map((s) => (
                <SettingRow key={s.name} s={s} />
              ))}
            </Card>
            <Card title="Identity" flush>
              {section("identity").map((s) => (
                <SettingRow key={s.name} s={s} />
              ))}
            </Card>
            <Card title="Watchlist" sub="besides your holdings" flush>
              {section("watchlist").map((s) => (
                <SettingRow key={s.name} s={s} />
              ))}
            </Card>
          </div>
        )}
      </Query>
    </>
  );
}
