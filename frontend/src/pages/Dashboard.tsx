import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchSummary, fetchTopSongs } from "../lib/api";
import type { Summary, TopSong } from "../lib/types";
import { compactNum, shortDate } from "../lib/format";
import { Card, Loading, PageHead, SampleBanner, StatCard } from "../components/ui";

export default function Dashboard() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [top, setTop] = useState<TopSong[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();

  useEffect(() => {
    Promise.all([fetchSummary(), fetchTopSongs()])
      .then(([s, t]) => {
        setSummary(s);
        setTop(t);
      })
      .catch((e) => setErr(String(e)));
  }, []);

  if (err) return <div className="empty">Could not load data. {err}</div>;
  if (!summary) return <Loading what="dashboard" />;

  const dq = summary.num_missing_dates + summary.num_partial_dates;

  return (
    <>
      <PageHead
        title="Dashboard"
        subtitle="Spotify Global Daily Top 200 — dataset overview"
      />
      <SampleBanner show={summary.is_sample_data} />

      <div className="grid stat-grid">
        <StatCard label="Songs in dataset" value={compactNum(summary.total_songs)} sub={`${summary.total_rows.toLocaleString()} chart rows`} />
        <StatCard
          label="Date range"
          value={shortDate(summary.date_range.start)}
          sub={`through ${shortDate(summary.date_range.end)}`}
        />
        <StatCard label="Chart days" value={summary.num_chart_days.toLocaleString()} sub="distinct dates" />
        <StatCard
          label="Data gaps"
          value={dq.toLocaleString()}
          sub={`${summary.num_missing_dates} missing · ${summary.num_partial_dates} partial`}
          tone={dq === 0 ? "ok" : "warn"}
        />
      </div>

      <div className="spacer-lg" />

      <Card title="Top songs by cumulative streams">
        <div className="table-wrap">
          <table className="t">
            <thead>
              <tr>
                <th style={{ width: 56 }}>#</th>
                <th>Track</th>
                <th>Artist</th>
                <th className="num">Cumulative streams</th>
                <th className="num">Peak</th>
                <th className="num">Days</th>
              </tr>
            </thead>
            <tbody>
              {top.slice(0, 25).map((s, i) => (
                <tr key={s.id} onClick={() => nav(`/search/${s.id}`)}>
                  <td>
                    <span className={`rankpill ${i < 3 ? "top" : ""}`}>{i + 1}</span>
                  </td>
                  <td style={{ fontWeight: 600 }}>{s.track_name}</td>
                  <td className="muted">{s.artist_names}</td>
                  <td className="num">{compactNum(s.cumulative_streams)}</td>
                  <td className="num">{s.peak_rank}</td>
                  <td className="num">{s.days_charted}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
