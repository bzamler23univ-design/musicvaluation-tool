#!/usr/bin/env python3
"""Generate a small *synthetic* processed dataset for demos / CI.

The real pipeline (scrape_kworb.py / scrape_spotify_global_200.py) can't run in
sandboxed environments that block Spotify/kworb, and processed data is
gitignored. This script writes a realistic-looking ``data/processed`` master so
the frontend and CI build have something to render. It is clearly fake data.

Output: data/processed/spotify_global_200_daily.{csv,parquet}
        data/processed/scrape_summary.json
"""
from __future__ import annotations

import json
import random
import string
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED = PROJECT_ROOT / "data" / "processed"

ARTISTS = [
    "Aurora Vale", "Neon Tide", "The Midnight Owls", "Luca Reyes", "SABLE",
    "Kira Moon", "Velvet Static", "Drew Castellano", "Nova Reign", "Echo Park",
    "Marigold", "Phantom Lane", "Ivy & Oak", "Rico Santos", "Glass Animals Jr",
    "Lyric Sterling", "Coastline", "Maya Bloom", "The Hollow", "Jett Sora",
]
WORDS = ["Golden", "Midnight", "Paper", "Electric", "Velvet", "Hollow", "Neon",
         "Crystal", "Saltwater", "Gravity", "Echoes", "Ember", "Fever", "Wild",
         "Silver", "Dreaming", "Horizon", "Static", "Lonely", "Forever"]
NOUNS = ["Hearts", "Lights", "Roads", "Summer", "Rain", "Skyline", "Ghost",
         "Fire", "Waves", "City", "Memory", "Kingdom", "Bones", "Stars", "Love"]


def rand_id() -> str:
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(22))


def main(days: int = 120, seed: int = 7) -> None:
    random.seed(seed)
    PROCESSED.mkdir(parents=True, exist_ok=True)

    # Build a pool of songs with intrinsic "popularity" and a debut date.
    n_songs = 360
    pool = []
    for _ in range(n_songs):
        pool.append({
            "spotify_track_id": rand_id(),
            "track_name": f"{random.choice(WORDS)} {random.choice(NOUNS)}",
            "artist_names": random.choice(ARTISTS),
            "strength": random.random() ** 2,          # most songs weak, few strong
            "debut": random.randint(0, days - 1),
            "life": random.randint(20, days),           # how long it stays relevant
        })

    end = date.today()
    start = end - timedelta(days=days - 1)
    rows = []
    scraped_at = datetime.now(timezone.utc).isoformat()

    for d_idx in range(days):
        day = start + timedelta(days=d_idx)
        # Score each eligible song for this day, pick the top 200.
        scored = []
        for s in pool:
            age = d_idx - s["debut"]
            if age < 0 or age > s["life"]:
                continue
            # Bell-ish curve over the song's life * its strength + daily noise.
            peakness = 1 - abs(age - s["life"] / 2) / (s["life"] / 2 + 1)
            score = s["strength"] * max(peakness, 0.05) * random.uniform(0.7, 1.3)
            scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:200]
        # Inject occasional partial day (simulate missing rows) ~ every 37 days.
        if d_idx % 37 == 36:
            top = top[:170]
        for rank, (score, s) in enumerate(top, start=1):
            base = 5_000_000 * score
            streams = int(max(180_000, base + random.uniform(-50_000, 50_000)))
            rows.append({
                "chart_date": day.isoformat(),
                "rank": rank,
                "track_name": s["track_name"],
                "artist_names": s["artist_names"],
                "streams": streams,
                "spotify_url": f"https://open.spotify.com/track/{s['spotify_track_id']}",
                "spotify_track_id": s["spotify_track_id"],
                "source_url": "synthetic://sample-data",
                "scraped_at": scraped_at,
            })

    # Simulate one fully-missing date (drop it) to exercise Data Health.
    missing_day = (start + timedelta(days=days // 2)).isoformat()
    df = pd.DataFrame(rows)
    df = df[df["chart_date"] != missing_day].reset_index(drop=True)

    csv_path = PROCESSED / "spotify_global_200_daily.csv"
    df.to_csv(csv_path, index=False)
    try:
        df.to_parquet(PROCESSED / "spotify_global_200_daily.parquet", index=False)
    except Exception as exc:  # noqa: BLE001
        print(f"parquet skipped ({exc}); install pyarrow")

    summary = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "strategies": ["synthetic"],
        "requested_days": days,
        "downloaded": days - 1,
        "skipped_existing": 0,
        "failed": 1,
        "parsed_rows": int(len(df)),
        "earliest_available": start.isoformat(),
        "num_missing_dates": 1,
        "num_partial_dates": df.groupby("chart_date").size().lt(200).sum().item(),
        "missing_dates": [missing_day],
        "is_sample_data": True,
        "started_at": scraped_at,
        "finished_at": scraped_at,
    }
    (PROCESSED / "scrape_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"Wrote {len(df):,} synthetic rows across {df['chart_date'].nunique()} days "
          f"-> {csv_path}")


if __name__ == "__main__":
    main()
