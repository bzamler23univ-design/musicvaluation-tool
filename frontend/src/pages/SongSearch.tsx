import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchSong, fetchSongIndex } from "../lib/api";
import type { IndexSong, SongDetail } from "../lib/types";
import { compactNum, fullNum, shortDate } from "../lib/format";
import { Card, Empty, Loading, PageHead, StatCard } from "../components/ui";

const AXIS = { stroke: "#5f6e82", fontSize: 11 };
const GRID = "#16212f";

function search(index: IndexSong[], q: string): IndexSong[] {
  const term = q.trim().toLowerCase();
  if (!term) return [];
  return index
    .filter(
      (s) =>
        s.track_name.toLowerCase().includes(term) ||
        s.artist_names.toLowerCase().includes(term) ||
        (s.spotify_track_id ?? "").toLowerCase() === term,
    )
    .slice(0, 20);
}

export default function SongSearch() {
  const { id } = useParams();
  const nav = useNavigate();
  const [index, setIndex] = useState<IndexSong[]>([]);
  const [q, setQ] = useState("");

  useEffect(() => {
    fetchSongIndex().then(setIndex).catch(() => setIndex([]));
  }, []);

  const results = useMemo(() => search(index, q), [index, q]);

  return (
    <>
      <PageHead title="Song Search" subtitle="Find a track by name, artist, or Spotify ID" />

      <div className="search-hero">
        <div className="search-wrap">
          <span className="search-ico">⌕</span>
          <input
            className="search-input"
            placeholder="Search songs or artists…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            autoFocus
          />
        </div>
        {results.length > 0 && (
          <div className="search-results">
            {results.map((s) => (
              <div key={s.id} className="search-row" onClick={() => { nav(`/search/${s.id}`); setQ(""); }}>
                <div>
                  <div className="title">{s.track_name}</div>
                  <div className="sub">{s.artist_names}</div>
                </div>
                <div className="meta">
                  {compactNum(s.cumulative_streams)} streams<br />
                  peak #{s.peak_rank} · {s.days_charted}d
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="spacer-lg" />
      {id ? <SongDetailView id={id} /> : <SuggestionList index={index} onPick={(sid) => nav(`/search/${sid}`)} />}
    </>
  );
}

function SuggestionList({ index, onPick }: { index: IndexSong[]; onPick: (id: string) => void }) {
  if (index.length === 0) return null;
  return (
    <Card title="Most-streamed tracks">
      <div className="table-wrap">
        <table className="t">
          <thead>
            <tr>
              <th>Track</th>
              <th>Artist</th>
              <th className="num">Cumulative</th>
              <th className="num">Peak</th>
              <th className="num">Days</th>
            </tr>
          </thead>
          <tbody>
            {index.slice(0, 15).map((s) => (
              <tr key={s.id} onClick={() => onPick(s.id)}>
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
  );
}

function SongDetailView({ id }: { id: string }) {
  const [song, setSong] = useState<SongDetail | null>(null);
  const [err, setErr] = useState(false);
  const nav = useNavigate();

  useEffect(() => {
    setSong(null);
    setErr(false);
    fetchSong(id).then(setSong).catch(() => setErr(true));
  }, [id]);

  if (err) return <Empty>Song not found.</Empty>;
  if (!song) return <Loading what="song" />;

  const data = song.history.map((h) => ({ ...h, label: shortDate(h.date) }));

  return (
    <>
      <div className="song-hero">
        <h2>{song.track_name}</h2>
        <span className="artist">{song.artist_names}</span>
        {song.spotify_track_id && (
          <a
            className="chip"
            href={`https://open.spotify.com/track/${song.spotify_track_id}`}
            target="_blank"
            rel="noreferrer"
          >
            ▶ Open on Spotify
          </a>
        )}
        <button className="btn ghost" style={{ marginLeft: "auto" }} onClick={() => nav(`/valuation/${song.id}`)}>
          ▲ Value this song
        </button>
      </div>

      <div className="spacer-lg" />
      <div className="grid stat-grid">
        <StatCard label="Cumulative streams" value={compactNum(song.cumulative_streams)} sub={fullNum(song.cumulative_streams)} />
        <StatCard label="Peak rank" value={`#${song.peak_rank}`} sub={`${song.days_charted} days charted`} />
        <StatCard label="First charted" value={shortDate(song.first_date)} />
        <StatCard label="Last charted" value={shortDate(song.last_date)} />
      </div>

      <div className="spacer-lg" />
      <div className="grid cols-2">
        <Card title="Daily streams over time">
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
              <defs>
                <linearGradient id="g-streams" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#1ed760" stopOpacity={0.5} />
                  <stop offset="100%" stopColor="#1ed760" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="label" tick={AXIS} minTickGap={40} />
              <YAxis tick={AXIS} tickFormatter={compactNum} width={48} />
              <Tooltip
                formatter={(v: number) => [fullNum(v), "Streams"]}
                contentStyle={{ background: "#111a26", border: "1px solid #1d2a3a", borderRadius: 10 }}
                labelStyle={{ color: "#8a99ad" }}
              />
              <Area type="monotone" dataKey="streams" stroke="#1ed760" strokeWidth={2} fill="url(#g-streams)" />
            </AreaChart>
          </ResponsiveContainer>
        </Card>

        <Card title="Chart rank over time">
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="label" tick={AXIS} minTickGap={40} />
              <YAxis tick={AXIS} reversed domain={[1, 200]} width={36} />
              <Tooltip
                formatter={(v: number) => [`#${v}`, "Rank"]}
                contentStyle={{ background: "#111a26", border: "1px solid #1d2a3a", borderRadius: 10 }}
                labelStyle={{ color: "#8a99ad" }}
              />
              <Line type="monotone" dataKey="rank" stroke="#6c8eef" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      </div>

      <div className="spacer-lg" />
      <Card title="Cumulative streams">
        <ResponsiveContainer width="100%" height={240}>
          <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
            <defs>
              <linearGradient id="g-cum" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#9f7aea" stopOpacity={0.45} />
                <stop offset="100%" stopColor="#9f7aea" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="label" tick={AXIS} minTickGap={40} />
            <YAxis tick={AXIS} tickFormatter={compactNum} width={48} />
            <Tooltip
              formatter={(v: number) => [fullNum(v), "Cumulative"]}
              contentStyle={{ background: "#111a26", border: "1px solid #1d2a3a", borderRadius: 10 }}
              labelStyle={{ color: "#8a99ad" }}
            />
            <Area type="monotone" dataKey="cumulative" stroke="#9f7aea" strokeWidth={2} fill="url(#g-cum)" />
          </AreaChart>
        </ResponsiveContainer>
      </Card>
    </>
  );
}
