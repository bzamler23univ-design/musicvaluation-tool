#!/usr/bin/env python3
"""Import official charts.spotify.com CSV exports into a chart master.

You download a chart CSV from https://charts.spotify.com (the "Download data as
CSV" button) for each week (or day) and drop the files in ``data/spotify_csv/``
(or pass paths). This script normalizes them into the same clean schema the rest
of the pipeline uses and appends them — deduped on (chart_date, rank) — into:

    data/processed/spotify_global_200_weekly.csv   (+ .parquet)   [weekly]
    data/processed/spotify_global_200_daily.csv    (+ .parquet)   [daily]

The chart date is read from the filename (the trailing YYYYMMDD, e.g.
``regionalglobalweekly20260618.csv`` -> 2026-06-18) unless ``--date`` is given,
and the cadence is detected from "weekly"/"daily" in the filename unless
``--cadence`` is given.

Expected CSV columns (Spotify export):
    rank, uri, artist_names, track_name, source, peak_rank, previous_rank,
    weeks_on_chart|days_on_chart, streams

Usage:
    python scripts/import_spotify_csv.py data/spotify_csv/*.csv
    python scripts/import_spotify_csv.py myfile.csv --date 2026-06-18 --cadence weekly
"""
from __future__ import annotations

import argparse
import glob
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED = PROJECT_ROOT / "data" / "processed"
DEFAULT_DROP = PROJECT_ROOT / "data" / "spotify_csv"

TRACK_ID_RE = re.compile(r"track[:/]([A-Za-z0-9]{22})")
DATE8_RE = re.compile(r"(\d{8})")

CLEAN_COLUMNS = [
    "chart_date", "rank", "track_name", "artist_names", "streams",
    "spotify_url", "spotify_track_id", "peak_rank", "previous_rank",
    "weeks_on_chart", "source_url", "scraped_at",
]


def infer_date(path: Path, override: str | None) -> str:
    if override:
        return override
    m = DATE8_RE.search(path.stem)
    if not m:
        raise SystemExit(
            f"Could not find a YYYYMMDD date in '{path.name}'. Pass --date YYYY-MM-DD."
        )
    d = m.group(1)
    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"


def infer_cadence(path: Path, override: str | None) -> str:
    if override:
        return override
    name = path.stem.lower()
    if "weekly" in name:
        return "weekly"
    if "daily" in name:
        return "daily"
    raise SystemExit(
        f"Could not tell weekly vs daily from '{path.name}'. Pass --cadence weekly|daily."
    )


def normalize_one(path: Path, *, date_override: str | None, cadence_override: str | None) -> tuple[pd.DataFrame, str]:
    chart_date = infer_date(path, date_override)
    cadence = infer_cadence(path, cadence_override)
    raw = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in raw.columns}

    def col(*names: str) -> str | None:
        for n in names:
            if n in cols:
                return cols[n]
        return None

    rank_c = col("rank", "position")
    uri_c = col("uri", "url", "spotify_uri")
    artist_c = col("artist_names", "artist", "artists")
    track_c = col("track_name", "track name", "title")
    streams_c = col("streams", "stream")
    if not all([rank_c, artist_c, track_c, streams_c]):
        raise SystemExit(f"{path.name}: missing required columns; found {list(raw.columns)}")

    out = pd.DataFrame()
    out["rank"] = pd.to_numeric(raw[rank_c], errors="coerce").astype("Int64")
    out["track_name"] = raw[track_c].astype(str).str.strip()
    out["artist_names"] = raw[artist_c].astype(str).str.strip()
    out["streams"] = pd.to_numeric(
        raw[streams_c].astype(str).str.replace(r"[,\s]", "", regex=True), errors="coerce"
    ).astype("Int64")

    uri = raw[uri_c].astype(str) if uri_c else pd.Series([""] * len(raw))
    tid = uri.str.extract(TRACK_ID_RE, expand=False)
    out["spotify_track_id"] = tid
    out["spotify_url"] = tid.map(
        lambda t: f"https://open.spotify.com/track/{t}" if isinstance(t, str) else pd.NA
    )
    out["peak_rank"] = pd.to_numeric(raw.get(col("peak_rank") or "", pd.NA), errors="coerce").astype("Int64")
    out["previous_rank"] = pd.to_numeric(raw.get(col("previous_rank") or "", pd.NA), errors="coerce").astype("Int64")
    woc = col("weeks_on_chart", "days_on_chart")
    out["weeks_on_chart"] = pd.to_numeric(raw[woc], errors="coerce").astype("Int64") if woc else pd.NA

    out.insert(0, "chart_date", chart_date)
    out["source_url"] = f"file://{path.name}"
    out["scraped_at"] = datetime.now(timezone.utc).isoformat()
    out = out.dropna(subset=["rank"]).reset_index(drop=True)
    return out[CLEAN_COLUMNS], cadence


def append_master(cadence: str, new_df: pd.DataFrame) -> pd.DataFrame:
    name = "spotify_global_200_weekly" if cadence == "weekly" else "spotify_global_200_daily"
    csv_path = PROCESSED / f"{name}.csv"
    pq_path = PROCESSED / f"{name}.parquet"
    existing = pd.read_csv(csv_path) if csv_path.exists() else pd.DataFrame()
    combined = pd.concat([existing, new_df], ignore_index=True)
    if "scraped_at" in combined.columns:
        combined = combined.sort_values("scraped_at")
    combined = combined.drop_duplicates(subset=["chart_date", "rank"], keep="last")
    combined = combined.sort_values(["chart_date", "rank"]).reset_index(drop=True)
    combined.to_csv(csv_path, index=False)
    try:
        combined.to_parquet(pq_path, index=False)
    except Exception as exc:  # noqa: BLE001
        print(f"  (parquet skipped: {exc})")
    return combined


def main() -> int:
    ap = argparse.ArgumentParser(description="Import charts.spotify.com CSV exports.")
    ap.add_argument("files", nargs="*", help="CSV files (globs ok). Default: data/spotify_csv/*.csv")
    ap.add_argument("--date", help="Override chart date (YYYY-MM-DD) for all files.")
    ap.add_argument("--cadence", choices=["weekly", "daily"], help="Override cadence for all files.")
    args = ap.parse_args()

    PROCESSED.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    patterns = args.files or [str(DEFAULT_DROP / "*.csv")]
    for pat in patterns:
        paths.extend(Path(p) for p in glob.glob(pat))
    paths = sorted({p for p in paths if p.is_file()})
    if not paths:
        raise SystemExit(
            f"No CSV files found. Put exports in {DEFAULT_DROP}/ or pass paths."
        )

    by_cadence: dict[str, list[pd.DataFrame]] = {}
    imported = 0
    for p in paths:
        norm, cadence = normalize_one(p, date_override=args.date, cadence_override=args.cadence)
        by_cadence.setdefault(cadence, []).append(norm)
        imported += 1
        print(f"  {p.name}: {len(norm)} rows -> {cadence} chart {norm['chart_date'].iloc[0]}")

    summary = {"imported_files": imported, "cadences": {}, "is_sample_data": False,
               "imported_at": datetime.now(timezone.utc).isoformat()}
    for cadence, frames in by_cadence.items():
        master = append_master(cadence, pd.concat(frames, ignore_index=True))
        dates = sorted(master["chart_date"].unique())
        summary["cadences"][cadence] = {
            "master_rows": int(len(master)),
            "distinct_dates": len(dates),
            "date_range": [dates[0], dates[-1]],
        }
        print(f"{cadence}: master now {len(master)} rows across {len(dates)} dates "
              f"({dates[0]} → {dates[-1]})")

    (PROCESSED / "spotify_import_summary.json").write_text(json.dumps(summary, indent=2))
    # Also write the generic summary so the frontend marks data as real (not sample).
    (PROCESSED / "scrape_summary.json").write_text(json.dumps(
        {**summary, "source": "spotify_csv_import"}, indent=2))
    print(f"Done. Imported {imported} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
