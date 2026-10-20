import { useState } from "react";
import { useApi, type Catalog, type Feed, type Job, type JobEvent, type Status } from "../api";
import { Badge, Card, Empty, Icon, Query } from "../components/ui";
import { ago, num } from "../format";
import { useHealth } from "../App";
import { navigate } from "../router";

const TONE: Record<string, string> = {
  ok: "ok",
  due: "info",
  running: "info",
  retrying: "warn",
  failed: "bad",
  blocked: "bad",
  stale: "warn",
  failing: "bad",
};

const GROUPS: [string, string][] = [
  ["daily", "Daily (Mon–Fri 23:30 UTC)"],
  ["weekly", "Weekly (Sun 06:00 UTC)"],
  ["news", "Hourly news"],
  ["backup", "Backup (04:00 UTC)"],
];

function dotFor(status: string) {
  return status === "ok" ? "ok" : status === "retrying" || status === "due" || status === "running" ? "warn" : status === "never run" ? "" : "bad";
}

function StepDetail({ job }: { job: Job }) {
  const events = useApi<JobEvent[]>(`/jobs/${job.name}/events`, { limit: 10 });
  return (
    <Card
      title={job.label}
      sub={<span className="mono">{job.name}</span>}
      actions={
        <button className="btn ghost" onClick={() => navigate("/status")}>
          Close
        </button>
      }
    >
      <div className="stack" style={{ gap: 12 }}>
        <div className="row" style={{ flexWrap: "wrap", gap: 16 }}>
          <Badge tone={TONE[job.status]}>{job.status}</Badge>
          <span className="small muted">Last success: {ago(job.last_success)}</span>
          {job.next_attempt && job.status !== "ok" && (
            <span className="small muted">Next attempt: {ago(job.next_attempt)}</span>
          )}
          {job.attempts > 0 && <span className="small muted">{job.attempts} failed in a row</span>}
          {job.after.length > 0 && <span className="small muted">Runs after: {job.after.join(", ")}</span>}
        </div>
        {job.error && (
          <div>
            <div className="small muted" style={{ marginBottom: 4 }}>
              Error{job.error_kind ? ` (${job.error_kind})` : ""}
            </div>
            <pre className="output">{job.error}</pre>
          </div>
        )}
        {job.hint && (
          <div className="notice warn" style={{ margin: 0 }}>
            <Icon name="alert" />
            <div>{job.hint}</div>
          </div>
        )}
        <div>
          <div className="small muted" style={{ marginBottom: 6 }}>
            Recent attempts
          </div>
          <Query q={events} empty={(d) => d.length === 0} emptyText="No attempts recorded yet">
            {(rows) => (
              <div className="stack" style={{ gap: 8 }}>
                {rows.map((e, i) => (
                  <details key={i} open={i === 0 && e.status !== "ok"}>
                    <summary className="row small" style={{ cursor: "pointer" }}>
                      <Badge tone={TONE[e.status]}>{e.status}</Badge>
                      <span className="num">{new Date(e.started_at).toLocaleString()}</span>
                      <span className="muted">
                        {Math.round((new Date(e.finished_at).getTime() - new Date(e.started_at).getTime()) / 1000)} s
                      </span>
                      {e.error && <span className="muted" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.error}</span>}
                    </summary>
                    {e.output && <pre className="output" style={{ marginTop: 6 }}>{e.output}</pre>}
                  </details>
                ))}
              </div>
            )}
          </Query>
        </div>
      </div>
    </Card>
  );
}

function FeedsTable({ feeds: all }: { feeds: Feed[] }) {
  const [everything, setEverything] = useState(false);
  // Unmonitored feeds (lookups, one-off backfills) have no expected cadence to check.
  const feeds = everything ? all : all.filter((f) => f.status !== "unmonitored");
  const hidden = all.length - feeds.length;
  const order = { failing: 0, stale: 1, ok: 2, unmonitored: 3 };
  const sorted = [...feeds].sort(
    (a, b) => order[a.status] - order[b.status] || a.provider.localeCompare(b.provider),
  );
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Feed</th>
            <th>Status</th>
            <th className="r">Items</th>
            <th className="r">Failing</th>
            <th>Last success</th>
            <th className="r">Expected every</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((f) => (
            <tr key={`${f.provider}/${f.dataset}`}>
              <td className="mono">
                {f.provider}/{f.dataset}
              </td>
              <td>
                <Badge tone={TONE[f.status]}>{f.status}</Badge>
              </td>
              <td className="r num">{num(f.items, 0)}</td>
              <td className={`r num ${f.failing ? "down" : "muted"}`}>{f.failing || "—"}</td>
              <td className="num">{ago(f.last_success)}</td>
              <td className="r num muted">{f.expected_days ? `${f.expected_days} d` : "—"}</td>
              <td className="small muted" style={{ maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={f.last_error ?? ""}>
                {f.last_error ?? ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {(hidden > 0 || everything) && (
        <div style={{ padding: "8px 16px" }}>
          <button className="btn ghost" onClick={() => setEverything(!everything)}>
            {everything ? "Hide unmonitored feeds" : `Show ${hidden} unmonitored feeds`}
          </button>
        </div>
      )}
    </div>
  );
}

export default function SystemStatus({ step }: { step?: string }) {
  const status = useApi<Status>("/status", undefined, { refetchInterval: 60_000, staleTime: 30_000 });
  const catalog = useApi<Catalog>("/catalog");
  const health = useHealth();
  const jobs = status.data?.jobs ?? [];
  const problems = jobs.filter((j) => j.status === "failed" || j.status === "blocked" || j.status === "retrying");
  const selected = jobs.find((j) => j.name === step);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>System</h1>
          <p>Scheduled steps retry on their own until they succeed; failed and blocked ones need a fix.</p>
        </div>
      </div>

      {status.data &&
        (health.level === "ok" ? (
          <div className="notice ok">
            <span style={{ color: "var(--up)" }}>
              <Icon name="check" />
            </span>
            <div>
              <strong>Everything is running normally.</strong>{" "}
              <span className="muted">
                {jobs.filter((j) => j.status === "ok").length} steps ok
                {jobs.some((j) => j.status === "never run") &&
                  `, ${jobs.filter((j) => j.status === "never run").length} waiting for their first scheduled run`}
                ; no feed is behind.
              </span>
            </div>
          </div>
        ) : (
          <div className={`notice ${health.level}`}>
            <span style={{ color: health.level === "bad" ? "var(--down)" : "var(--warn)" }}>
              <Icon name="alert" />
            </span>
            <div>
              <strong>
                {[
                  health.attention && `${health.attention} step${health.attention > 1 ? "s" : ""} need attention`,
                  health.retrying && `${health.retrying} retrying on their own`,
                  health.feedAlerts && `${health.feedAlerts} feed${health.feedAlerts > 1 ? "s" : ""} stale or failing`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </strong>
            </div>
          </div>
        ))}

      <div className="stack">
        {selected && <StepDetail job={selected} />}

        {problems.length > 0 && (
          <Card title="Needs a look" flush>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Step</th>
                    <th>Status</th>
                    <th>Error</th>
                    <th>Next attempt</th>
                  </tr>
                </thead>
                <tbody>
                  {problems.map((j) => (
                    <tr key={j.name} className="clickable" onClick={() => navigate(`/status/${j.name}`)}>
                      <td>
                        <div style={{ fontWeight: 550 }}>{j.label}</div>
                        <div className="small muted">{j.hint}</div>
                      </td>
                      <td>
                        <Badge tone={TONE[j.status]}>{j.status}</Badge>
                      </td>
                      <td className="small" style={{ maxWidth: 420 }}>
                        {j.error}
                      </td>
                      <td className="num small">{ago(j.next_attempt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        <Card title="Scheduled steps" sub="click one for its attempts and output" flush>
          <Query q={status} rows={6}>
            {() => (
              <>
                {GROUPS.map(([group, label]) => {
                  const items = jobs.filter((j) => j.group === group);
                  if (!items.length) return null;
                  return (
                    <div key={group}>
                      <div className="small muted" style={{ padding: "4px 16px 8px", fontWeight: 550 }}>
                        {label}
                      </div>
                      <div className="step-grid">
                        {items.map((j) => (
                          <button
                            key={j.name}
                            className={`step ${j.name === step ? "on" : ""}`}
                            onClick={() => navigate(j.name === step ? "/status" : `/status/${j.name}`)}
                          >
                            <span className={`dot ${dotFor(j.status)}`} />
                            <span style={{ flex: 1, minWidth: 0 }}>
                              <div style={{ fontWeight: 550, fontSize: 13 }}>{j.label}</div>
                              <div className="small muted">
                                {j.status === "ok" ? ago(j.last_success) : j.status}
                              </div>
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </>
            )}
          </Query>
        </Card>

        <Card title="Data feeds" sub="freshness of every source against its expected cadence" flush>
          <Query q={catalog} rows={8} empty={(c) => c.feeds.length === 0} emptyText="No feeds yet">
            {(c) => <FeedsTable feeds={c.feeds} />}
          </Query>
        </Card>

        {status.data && status.data.failing.length > 0 && (
          <Card title="Failing items" sub={`${status.data.failing.length} items whose last sync failed`} flush>
            <div className="table-wrap" style={{ maxHeight: 360, overflowY: "auto" }}>
              <table>
                <tbody>
                  {status.data.failing.slice(0, 300).map((f) => (
                    <tr key={`${f.provider}/${f.dataset}/${f.key}`}>
                      <td className="mono">
                        {f.provider}/{f.dataset}
                      </td>
                      <td className="mono">{f.key}</td>
                      <td className="small muted">{f.last_error}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
        {!status.data && !status.isLoading && <Empty>Status unavailable</Empty>}
      </div>
    </>
  );
}
