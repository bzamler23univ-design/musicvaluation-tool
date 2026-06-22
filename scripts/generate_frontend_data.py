#!/usr/bin/env python3
"""Turn processed chart data into static JSON for the frontend.

Reads a master file from ``data/processed`` (either the Spotify scraper output
``spotify_global_200_daily`` or the kworb output ``kworb_global_daily``) and
writes static JSON consumed by the React app:

    frontend/public/data/summary.json
    frontend/public/data/top_songs.json
    frontend/public/data/song_index.json      (lightweight, for search)
    frontend/public/data/data_health.json
    frontend/public/data/songs/<song_id>.json (per-song daily history)

No secrets are read or written. Safe to run in CI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED = PROJECT_ROOT / "data" / "processed"
OUT_DIR = PROJECT_ROOT / "frontend" / "public" / "data"

EXPECTED_ROWS = 200


# --------------------------------------------------------------------------- #
# Load + normalize whatever master is available into one tidy frame.
# --------------------------------------------------------------------------- #


def _read_master(name: str) -> Optional[pd.DataFrame]:
    pq = PROCESSED / f"{name}.parquet"
    csv = PROCESSED / f"{name}.csv"
    if pq.exists():
        try:
            return pd.read_parquet(pq)
        except Exception:  # noqa: BLE001
            pass
    if csv.exists():
        return pd.read_csv(csv)
    return None


def load_normalized() -> tuple[pd.DataFrame, str]:
    """Return (tidy df, source_label). Columns:
    chart_date, rank, track_name, artist_names, streams, spotify_track_id.
    """
    # Prefer the Spotify-shaped master, then the kworb-shaped one.
    spotify = _read_master("spotify_global_200_daily")
    if spotify is not None and not spotify.empty:
        df = spotify.rename(columns={})
        df = df[["chart_date", "rank", "track_name", "artist_names",
                 "streams", "spotify_track_id"]].copy()
        return _clean(df), "spotify_global_200_daily"

    kworb = _read_master("kworb_global_daily")
    if kworb is not None and not kworb.empty:
        df = kworb.rename(columns={
            "pos": "rank", "title": "track_name", "artist": "artist_names",
        })
        # kworb "streams" is the daily figure; keep total_streams if present.
        keep = ["chart_date", "rank", "track_name", "artist_names",
                "streams", "spotify_track_id"]
        for col in keep:
            if col not in df.columns:
                df[col] = pd.NA
        return _clean(df[keep]), "kworb_global_daily"

    raise SystemExit(
        "No processed master found in data/processed/. Run a scraper, or "
        "`python scripts/make_sample_data.py` for demo data."
    )


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["chart_date"] = df["chart_date"].astype(str).str.slice(0, 10)
    df["rank"] = pd.to_numeric(df["rank"], errors="coerce").astype("Int64")
    df["streams"] = pd.to_numeric(df["streams"], errors="coerce").fillna(0).astype("int64")
    df["track_name"] = df["track_name"].astype(str).str.strip()
    df["artist_names"] = df["artist_names"].astype(str).str.strip()
    df = df.dropna(subset=["rank"])
    df["rank"] = df["rank"].astype(int)
    # Stable per-song id: prefer the real Spotify id, else hash name+artist.
    def _sid(row) -> str:
        tid = row.get("spotify_track_id")
        if isinstance(tid, str) and tid.strip() and tid.strip().lower() != "nan":
            return tid.strip()
        basis = f"{row['track_name']}|{row['artist_names']}".lower()
        return "x" + hashlib.sha1(basis.encode()).hexdigest()[:21]
    df["song_id"] = df.apply(_sid, axis=1)
    return df


# --------------------------------------------------------------------------- #
# Aggregations
# --------------------------------------------------------------------------- #


def song_aggregates(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("song_id")
    agg = g.agg(
        track_name=("track_name", "last"),
        artist_names=("artist_names", "last"),
        spotify_track_id=("spotify_track_id", "last"),
        cumulative_streams=("streams", "sum"),
        peak_rank=("rank", "min"),
        days_charted=("chart_date", "nunique"),
        first_date=("chart_date", "min"),
        last_date=("chart_date", "max"),
    ).reset_index()
    return agg


def date_health(df: pd.DataFrame) -> dict:
    per_day = df.groupby("chart_date").size()
    dates = sorted(per_day.index)
    if not dates:
        return {"missing_dates": [], "partial_dates": []}
    start = date.fromisoformat(dates[0])
    end = date.fromisoformat(dates[-1])
    present = set(dates)
    missing = []
    cur = start
    while cur <= end:
        if cur.isoformat() not in present:
            missing.append(cur.isoformat())
        cur += timedelta(days=1)
    partial = [{"chart_date": d, "rows": int(per_day[d])}
               for d in dates if per_day[d] < EXPECTED_ROWS]
    return {"missing_dates": missing, "partial_dates": partial}


# --------------------------------------------------------------------------- #
# Write
# --------------------------------------------------------------------------- #


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":")))


def build(top_n: int = 100) -> None:
    df, source = load_normalized()
    agg = song_aggregates(df)
    health = date_health(df)

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Keep the tracked placeholder so the (gitignored) data dir survives in git.
    (OUT_DIR / ".gitkeep").touch()

    dates = sorted(df["chart_date"].unique())
    last_updated = datetime.now(timezone.utc).isoformat()

    # ---- scraper summary passthrough (if present) -------------------------- #
    scraper_summary = {}
    for fname in ("scrape_summary.json", "kworb_scrape_summary.json"):
        p = PROCESSED / fname
        if p.exists():
            try:
                scraper_summary = json.loads(p.read_text())
            except Exception:  # noqa: BLE001
                scraper_summary = {}
            break
    is_sample = bool(scraper_summary.get("is_sample_data"))

    # ---- summary.json ------------------------------------------------------ #
    summary = {
        "source": source,
        "is_sample_data": is_sample,
        "total_songs": int(agg.shape[0]),
        "total_rows": int(df.shape[0]),
        "date_range": {"start": dates[0], "end": dates[-1]},
        "num_chart_days": len(dates),
        "num_missing_dates": len(health["missing_dates"]),
        "num_partial_dates": len(health["partial_dates"]),
        "last_updated": last_updated,
    }
    write_json(OUT_DIR / "summary.json", summary)

    # ---- top_songs.json ---------------------------------------------------- #
    top = agg.sort_values("cumulative_streams", ascending=False).head(top_n)
    write_json(OUT_DIR / "top_songs.json", [
        {
            "id": r.song_id,
            "track_name": r.track_name,
            "artist_names": r.artist_names,
            "cumulative_streams": int(r.cumulative_streams),
            "peak_rank": int(r.peak_rank),
            "days_charted": int(r.days_charted),
        } for r in top.itertuples()
    ])

    # ---- song_index.json (search) ----------------------------------------- #
    write_json(OUT_DIR / "song_index.json", [
        {
            "id": r.song_id,
            "track_name": r.track_name,
            "artist_names": r.artist_names,
            "spotify_track_id": r.spotify_track_id if isinstance(r.spotify_track_id, str) else None,
            "cumulative_streams": int(r.cumulative_streams),
            "peak_rank": int(r.peak_rank),
            "days_charted": int(r.days_charted),
        } for r in agg.sort_values("cumulative_streams", ascending=False).itertuples()
    ])

    # ---- per-song history -------------------------------------------------- #
    songs_dir = OUT_DIR / "songs"
    songs_dir.mkdir(parents=True, exist_ok=True)
    df_sorted = df.sort_values(["song_id", "chart_date"])
    for song_id, grp in df_sorted.groupby("song_id"):
        grp = grp.sort_values("chart_date")
        cumulative = grp["streams"].cumsum()
        history = [
            {"date": d, "rank": int(rk), "streams": int(st), "cumulative": int(cu)}
            for d, rk, st, cu in zip(grp["chart_date"], grp["rank"], grp["streams"], cumulative)
        ]
        meta = agg[agg["song_id"] == song_id].iloc[0]
        sid = meta["spotify_track_id"]
        write_json(songs_dir / f"{song_id}.json", {
            "id": song_id,
            "track_name": meta["track_name"],
            "artist_names": meta["artist_names"],
            "spotify_track_id": sid if isinstance(sid, str) else None,
            "peak_rank": int(meta["peak_rank"]),
            "days_charted": int(meta["days_charted"]),
            "cumulative_streams": int(meta["cumulative_streams"]),
            "first_date": meta["first_date"],
            "last_date": meta["last_date"],
            "history": history,
        })

    # ---- data_health.json -------------------------------------------------- #
    write_json(OUT_DIR / "data_health.json", {
        "source": source,
        "is_sample_data": is_sample,
        "last_updated": last_updated,
        "date_range": {"start": dates[0], "end": dates[-1]},
        "num_chart_days": len(dates),
        "missing_dates": health["missing_dates"],
        "partial_dates": health["partial_dates"],
        "scraper_summary": scraper_summary,
    })

    print(f"Generated frontend data from '{source}': {agg.shape[0]} songs, "
          f"{len(dates)} days -> {OUT_DIR}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate static JSON for the frontend.")
    ap.add_argument("--top-n", type=int, default=100, help="How many songs in top_songs.json")
    build(top_n=ap.parse_args().top_n)


if __name__ == "__main__":
    main()
