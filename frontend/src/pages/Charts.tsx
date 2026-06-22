import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchArtistsAllTime, fetchSongsAllTime } from "../lib/api";
import type { AllTimeArtist, AllTimeSong } from "../lib/types";
import { compactNum } from "../lib/format";
import { Card, Empty, Loading, PageHead } from "../components/ui";

type Tab = "songs" | "artists";

export default function Charts() {
  const [tab, setTab] = useState<Tab>("songs");
  const [songs, setSongs] = useState<AllTimeSong[] | null>(null);
  const [artists, setArtists] = useState<AllTimeArtist[] | null>(null);
  const [songSrc, setSongSrc] = useState("");
  const [artistSrc, setArtistSrc] = useState("");
  const [q, setQ] = useState("");
  const nav = useNavigate();

  useEffect(() => {
    fetchSongsAllTime()
      .then((d) => { setSongs(d.rows); setSongSrc(d.source); })
      .catch(() => setSongs([]));
    fetchArtistsAllTime()
      .then((d) => { setArtists(d.rows); setArtistSrc(d.source); })
      .catch(() => setArtists([]));
  }, []);

  const term = q.trim().toLowerCase();
  const fSongs = useMemo(
    () => (songs ?? []).filter((s) =>
      !term || s.track_name.toLowerCase().includes(term) || s.artist_names.toLowerCase().includes(term)),
    [songs, term],
  );
  const fArtists = useMemo(
    () => (artists ?? []).filter((a) => !term || a.artist_names.toLowerCase().includes(term)),
    [artists, term],
  );

  const derived = (tab === "songs" ? songSrc : artistSrc) === "derived_from_chart";

  return (
    <>
      <PageHead title="Charts" subtitle="All-time most-streamed songs and artists" />

      <div className="chips" style={{ marginBottom: 16 }}>
        <button className={`btn ${tab === "songs" ? "" : "ghost"}`} onClick={() => setTab("songs")}>
          Songs
        </button>
        <button className={`btn ${tab === "artists" ? "" : "ghost"}`} onClick={() => setTab("artists")}>
          Artists
        </button>
        <input
          className="search-input"
          style={{ flex: 1, minWidth: 200, paddingLeft: 16 }}
          placeholder={`Filter ${tab}…`}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>

      {derived && (
        <div className="banner" style={{ marginBottom: 16 }}>
          <span>ℹ</span>
          <span>
            Derived from the daily chart (kworb all-time list not scraped yet). Run the scraper with{" "}
            <code>--datasets songs,artists</code> for the true all-time totals.
          </span>
        </div>
      )}

      {tab === "songs" ? (
        songs === null ? <Loading what="songs" /> :
        fSongs.length === 0 ? <Empty>No songs.</Empty> : (
          <Card title={`All-time songs (${fSongs.length})`}>
            <div className="table-wrap">
              <table className="t">
                <thead>
                  <tr>
                    <th style={{ width: 56 }}>#</th>
                    <th>Track</th>
                    <th>Artist</th>
                    <th className="num">Total streams</th>
                    <th className="num">Daily</th>
                  </tr>
                </thead>
                <tbody>
                  {fSongs.map((s, i) => (
                    <tr
                      key={`${s.track_name}-${i}`}
                      onClick={() => s.spotify_track_id && nav(`/search/${s.spotify_track_id}`)}
                    >
                      <td><span className={`rankpill ${i < 3 ? "top" : ""}`}>{s.rank || i + 1}</span></td>
                      <td style={{ fontWeight: 600 }}>{s.track_name}</td>
                      <td className="muted">{s.artist_names}</td>
                      <td className="num">{s.total_streams != null ? compactNum(s.total_streams) : "—"}</td>
                      <td className="num">{s.daily_streams != null ? compactNum(s.daily_streams) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )
      ) : (
        artists === null ? <Loading what="artists" /> :
        fArtists.length === 0 ? <Empty>No artists.</Empty> : (
          <Card title={`All-time artists (${fArtists.length})`}>
            <div className="table-wrap">
              <table className="t">
                <thead>
                  <tr>
                    <th style={{ width: 56 }}>#</th>
                    <th>Artist</th>
                    <th className="num">Total streams</th>
                    <th className="num">Daily</th>
                  </tr>
                </thead>
                <tbody>
                  {fArtists.map((a, i) => (
                    <tr key={`${a.artist_names}-${i}`} style={{ cursor: "default" }}>
                      <td><span className={`rankpill ${i < 3 ? "top" : ""}`}>{a.rank || i + 1}</span></td>
                      <td style={{ fontWeight: 600 }}>{a.artist_names}</td>
                      <td className="num">{a.total_streams != null ? compactNum(a.total_streams) : "—"}</td>
                      <td className="num">{a.daily_streams != null ? compactNum(a.daily_streams) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )
      )}
    </>
  );
}
