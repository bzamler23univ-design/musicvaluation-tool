import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchSong, fetchSongIndex, fetchSummary } from "../lib/api";
import type { IndexSong, SongDetail } from "../lib/types";
import { compactNum, money, pct } from "../lib/format";
import {
  DEFAULT_INPUTS,
  sensitivity,
  valuate,
  type ValuationInputs,
} from "../lib/valuation";
import { BENCHMARKS, SOURCES } from "../lib/benchmarks";
import { Card, Empty, Loading, PageHead } from "../components/ui";

interface FieldDef {
  key: keyof ValuationInputs;
  label: string;
  hint: string;
  min: number;
  max: number;
  step: number;
  asPct?: boolean;
}

const FIELDS: FieldDef[] = [
  { key: "royaltyRatePerStream", label: "Royalty rate / stream ($)", hint: "Gross payout per stream", min: 0.001, max: 0.02, step: 0.0005 },
  { key: "ownershipPct", label: "Ownership %", hint: "Share of rights you own", min: 0, max: 1, step: 0.05, asPct: true },
  { key: "royaltyShare", label: "Master / publishing share", hint: "Portion of royalties your rights cover", min: 0, max: 1, step: 0.05, asPct: true },
  { key: "annualDecayRate", label: "Annual decay %", hint: "Yearly decline in streams", min: 0, max: 0.6, step: 0.01, asPct: true },
  { key: "discountRate", label: "Discount rate %", hint: "Annual discount for PV", min: 0.01, max: 0.3, step: 0.005, asPct: true },
  { key: "terminalMultiple", label: "Terminal multiple (x)", hint: "Applied to final-year net", min: 0, max: 10, step: 0.5 },
  { key: "projectionYears", label: "Projection years", hint: "Forecast horizon", min: 1, max: 25, step: 1 },
];

const DISCOUNTS = [0.06, 0.08, 0.1, 0.12, 0.15];
const DECAYS = [0.1, 0.15, 0.2, 0.3, 0.4];

export default function Valuation() {
  const { id } = useParams();
  const [index, setIndex] = useState<IndexSong[]>([]);
  const [songId, setSongId] = useState<string | undefined>(id);
  const [song, setSong] = useState<SongDetail | null>(null);
  const [err, setErr] = useState(false);
  const [input, setInput] = useState<ValuationInputs>(DEFAULT_INPUTS);
  const [periodsPerYear, setPeriodsPerYear] = useState(365);

  useEffect(() => {
    fetchSongIndex().then((idx) => {
      setIndex(idx);
      setSongId((cur) => cur ?? idx[0]?.id);
    });
    fetchSummary()
      .then((s) => setPeriodsPerYear(s.cadence === "weekly" ? 52 : 365))
      .catch(() => setPeriodsPerYear(365));
  }, []);

  useEffect(() => {
    if (!songId) return;
    setSong(null);
    setErr(false);
    fetchSong(songId).then(setSong).catch(() => setErr(true));
  }, [songId]);

  const result = useMemo(
    () => (song ? valuate(song.history, input, periodsPerYear) : null),
    [song, input, periodsPerYear],
  );
  const grid = useMemo(
    () => (song ? sensitivity(song.history, input, DISCOUNTS, DECAYS, periodsPerYear) : null),
    [song, input, periodsPerYear],
  );

  const setField = (k: keyof ValuationInputs, v: number) =>
    setInput((s) => ({ ...s, [k]: v }));

  const values = grid?.flat() ?? [];
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const heat = (v: number) => {
    if (hi === lo) return "rgba(30,215,96,0.12)";
    const t = (v - lo) / (hi - lo);
    return `rgba(30,215,96,${0.08 + t * 0.32})`;
  };

  return (
    <>
      <PageHead title="Valuation Prototype" subtitle="Estimate catalog value with a simple discounted-cashflow model" />

      <Card title="Song">
        <select
          className="search-input"
          style={{ paddingLeft: 16 }}
          value={songId ?? ""}
          onChange={(e) => setSongId(e.target.value)}
        >
          {index.slice(0, 300).map((s) => (
            <option key={s.id} value={s.id}>
              {s.track_name} — {s.artist_names}
            </option>
          ))}
        </select>
      </Card>

      <div className="spacer-lg" />
      <Card title="Assumptions">
        <div className="form-grid">
          {FIELDS.map((f) => (
            <div className="field" key={f.key}>
              <label>{f.label}</label>
              <div className="row">
                <input
                  type="range"
                  min={f.min}
                  max={f.max}
                  step={f.step}
                  value={input[f.key]}
                  onChange={(e) => setField(f.key, parseFloat(e.target.value))}
                />
                <input
                  type="number"
                  min={f.min}
                  max={f.max}
                  step={f.step}
                  value={input[f.key]}
                  onChange={(e) => setField(f.key, parseFloat(e.target.value) || 0)}
                  style={{ width: 92 }}
                />
              </div>
              <div className="hint">
                {f.hint}
                {f.asPct ? ` · ${pct(input[f.key])}` : ""}
                {BENCHMARKS[f.key] && (
                  <span title={BENCHMARKS[f.key].detail} style={{ color: "var(--accent)" }}>
                    {" "}· mkt {BENCHMARKS[f.key].text}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <div className="spacer-lg" />

      {err && <Empty>Song not found.</Empty>}
      {!err && !result && <Loading what="valuation" />}

      {result && song && (
        <>
          <div className="grid kpi-grid">
            <div className="kpi hero">
              <div className="l">Implied catalog value</div>
              <div className="v">{money(result.impliedCatalogValue)}</div>
            </div>
            <div className="kpi">
              <div className="l">Historical gross revenue</div>
              <div className="v">{money(result.historicalGross)}</div>
            </div>
            <div className="kpi">
              <div className="l">Owner net (historical)</div>
              <div className="v">{money(result.ownerNetHistorical)}</div>
            </div>
            <div className="kpi">
              <div className="l">Projected owner net</div>
              <div className="v">{money(result.projectedOwnerNetTotal)}</div>
            </div>
            <div className="kpi">
              <div className="l">PV of projection</div>
              <div className="v">{money(result.pvProjected)}</div>
            </div>
            <div className="kpi">
              <div className="l">PV of terminal value</div>
              <div className="v">{money(result.pvTerminal)}</div>
            </div>
            <div className="kpi">
              <div className="l">
                Implied multiple{" "}
                <span
                  title={BENCHMARKS.marketMultiple.detail}
                  style={{
                    color:
                      result.impliedMultiple >= BENCHMARKS.marketMultiple.low &&
                      result.impliedMultiple <= BENCHMARKS.marketMultiple.high
                        ? "var(--accent)"
                        : "var(--amber)",
                  }}
                >
                  (mkt {BENCHMARKS.marketMultiple.text})
                </span>
              </div>
              <div className="v">{result.impliedMultiple.toFixed(1)}×</div>
            </div>
          </div>

          <div className="spacer-lg" />
          <div className="grid cols-2">
            <Card title="Projected discounted owner net by year">
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={result.projection} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
                  <CartesianGrid stroke="#16212f" vertical={false} />
                  <XAxis dataKey="year" tick={{ stroke: "#5f6e82", fontSize: 11 }} />
                  <YAxis tick={{ stroke: "#5f6e82", fontSize: 11 }} tickFormatter={(v) => "$" + compactNum(v)} width={54} />
                  <Tooltip
                    formatter={(v: number, n) => [money(v), n === "pv" ? "Discounted PV" : "Owner net"]}
                    contentStyle={{ background: "#111a26", border: "1px solid #1d2a3a", borderRadius: 10 }}
                    labelFormatter={(y) => `Year ${y}`}
                  />
                  <Bar dataKey="pv" fill="#1ed760" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </Card>

            <Card title="Projection detail">
              <div className="table-wrap" style={{ maxHeight: 260, overflowY: "auto" }}>
                <table className="t">
                  <thead>
                    <tr>
                      <th>Yr</th>
                      <th className="num">Streams</th>
                      <th className="num">Owner net</th>
                      <th className="num">PV</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.projection.map((p) => (
                      <tr key={p.year}>
                        <td>{p.year}</td>
                        <td className="num">{compactNum(p.streams)}</td>
                        <td className="num">{money(p.ownerNet, { compact: true })}</td>
                        <td className="num">{money(p.pv, { compact: true })}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>

          <div className="spacer-lg" />
          <Card title="Sensitivity — implied value by discount rate × annual decay">
            <div className="table-wrap">
              <table className="t">
                <thead>
                  <tr>
                    <th>Discount ↓ / Decay →</th>
                    {DECAYS.map((d) => (
                      <th key={d} className="num">{pct(d, 0)}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {DISCOUNTS.map((dr, i) => (
                    <tr key={dr}>
                      <td style={{ color: "var(--muted)" }}>{pct(dr, 0)}</td>
                      {DECAYS.map((_, j) => (
                        <td key={j} className="num">
                          <span className="heatcell" style={{ background: heat(grid![i][j]) }}>
                            {money(grid![i][j], { compact: true })}
                          </span>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <div className="spacer-lg" />
          <Card title="Assumptions used">
            <div className="chips">
              {FIELDS.map((f) => (
                <span className="chip" key={f.key}>
                  {f.label.replace(/ \(.*\)| %/g, "")}:{" "}
                  {f.asPct ? pct(input[f.key]) : f.key === "royaltyRatePerStream" ? `$${input[f.key]}` : input[f.key]}
                </span>
              ))}
            </div>
            <p className="muted" style={{ fontSize: 12.5, marginBottom: 0, marginTop: 14 }}>
              Prototype model for illustration only — not investment advice. Forward streams are
              estimated from the trailing 90-day run-rate, decayed annually; the terminal value applies
              your multiple to final-year owner net, discounted to present value.
            </p>
          </Card>

          <div className="spacer-lg" />
          <Card title="Benchmarks & sources backing these assumptions">
            <div className="table-wrap">
              <table className="t">
                <thead>
                  <tr>
                    <th>Assumption</th>
                    <th>Market benchmark</th>
                    <th>Supporting data</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(BENCHMARKS).map(([key, b]) => (
                    <tr key={key} style={{ cursor: "default" }}>
                      <td style={{ fontWeight: 600, textTransform: "capitalize" }}>
                        {key.replace(/([A-Z])/g, " $1").replace(/^./, (c) => c.toUpperCase())}
                      </td>
                      <td style={{ color: "var(--accent)", whiteSpace: "nowrap" }}>{b.text}</td>
                      <td className="muted" style={{ fontSize: 13 }}>{b.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="chips" style={{ marginTop: 14 }}>
              {SOURCES.map((s) => (
                <a key={s.id} className="chip" href={s.url} target="_blank" rel="noreferrer">
                  {s.publisher}: {s.title} ↗
                </a>
              ))}
            </div>
          </Card>
        </>
      )}
    </>
  );
}
