import { useEffect, useState } from "react";
import { fetchDataHealth } from "../lib/api";
import type { DataHealth } from "../lib/types";
import { dateTime, shortDate } from "../lib/format";
import { Card, Loading, PageHead, SampleBanner, StatCard } from "../components/ui";

export default function DataHealthPage() {
  const [health, setHealth] = useState<DataHealth | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchDataHealth().then(setHealth).catch((e) => setErr(String(e)));
  }, []);

  if (err) return <div className="empty">Could not load data health. {err}</div>;
  if (!health) return <Loading what="data health" />;

  const summaryEntries = Object.entries(health.scraper_summary ?? {}).filter(
    ([, v]) => typeof v !== "object" || v === null,
  );

  return (
    <>
      <PageHead title="Data Health" subtitle="Coverage, gaps, and scraper status" />
      <SampleBanner show={health.is_sample_data} />

      <div className="grid stat-grid">
        <StatCard label="Source" value={<span style={{ fontSize: 18 }}>{health.source}</span>} />
        <StatCard label="Chart days" value={health.num_chart_days.toLocaleString()} sub={`${shortDate(health.date_range.start)} → ${shortDate(health.date_range.end)}`} />
        <StatCard label="Missing dates" value={health.missing_dates.length} tone={health.missing_dates.length ? "warn" : "ok"} />
        <StatCard label="Partial dates" value={health.partial_dates.length} sub="fewer than 200 rows" tone={health.partial_dates.length ? "warn" : "ok"} />
      </div>

      <div className="spacer-lg" />
      <div className="grid cols-2">
        <Card title={`Missing dates (${health.missing_dates.length})`}>
          {health.missing_dates.length === 0 ? (
            <p className="muted">No gaps — every day in range is present. ✓</p>
          ) : (
            <div className="chips">
              {health.missing_dates.map((d) => (
                <span key={d} className="chip danger">{shortDate(d)}</span>
              ))}
            </div>
          )}
        </Card>

        <Card title={`Partial dates (${health.partial_dates.length})`}>
          {health.partial_dates.length === 0 ? (
            <p className="muted">Every charted day has a full 200 rows. ✓</p>
          ) : (
            <div className="chips">
              {health.partial_dates.map((p) => (
                <span key={p.chart_date} className="chip warn">
                  {shortDate(p.chart_date)} · {p.rows}
                </span>
              ))}
            </div>
          )}
        </Card>
      </div>

      <div className="spacer-lg" />
      <Card title="Scraper summary">
        {summaryEntries.length === 0 ? (
          <p className="muted">No scraper summary available.</p>
        ) : (
          <div className="table-wrap">
            <table className="t">
              <tbody>
                {summaryEntries.map(([k, v]) => (
                  <tr key={k} style={{ cursor: "default" }}>
                    <td className="muted" style={{ textTransform: "capitalize" }}>{k.replace(/_/g, " ")}</td>
                    <td className="num">{String(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted" style={{ fontSize: 12.5, marginTop: 14, marginBottom: 0 }}>
          Last updated {dateTime(health.last_updated)}
        </p>
      </Card>
    </>
  );
}
