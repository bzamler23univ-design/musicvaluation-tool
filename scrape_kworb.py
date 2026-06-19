#!/usr/bin/env python3
"""kworb.net Spotify stats scraper.

kworb.net publishes Spotify statistics as plain HTML tables (no login, no
anti-bot wall). This pipeline scrapes three datasets and builds cleaned,
validated, *append-only* master files:

  global-daily : country/global_daily.html
                 The Global Daily Top 200. kworb only shows the **latest day**,
                 so this scraper snapshots forward: run it on a daily schedule
                 and each day is appended to a growing per-date master.

  songs        : songs.html
                 All-time most-streamed tracks (total + daily streams). Captured
                 as a time series keyed by the date it was retrieved.

  artists      : artists.html
                 Most-streamed artists (total + daily, lead/solo/feature).
                 Also captured as a retrieved-date time series.

Design notes
------------
kworb's exact column set changes occasionally, so parsing is intentionally
*generic*: every column in the HTML table is captured under a normalized
snake_case name, known kworb headers are mapped to friendly names, and Spotify
track/artist IDs are pulled out of the in-cell links. That keeps the scraper
working even if a column is added or renamed.

Legal/ethical: public data, but be polite — randomized delays + capped
exponential backoff are on by default; respect kworb.net's terms and robots.txt.

NOTE: some sandboxed/CI environments block outbound traffic to kworb.net via an
egress allowlist (HTTP 403 "host_not_allowed"). If you see that, run locally or
allowlist kworb.net — it is not a kworb-side block.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

PROJECT_ROOT = Path(__file__).resolve().parent
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "kworb"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
LOG_DIR = PROJECT_ROOT / "logs"

BASE = "https://kworb.net/spotify"

DATASETS = {
    "global-daily": {
        "url": f"{BASE}/country/global_daily.html",
        "history_key": ["chart_date", "pos"],
        "date_field": "chart_date",
    },
    "songs": {
        "url": f"{BASE}/songs.html",
        "history_key": ["retrieved_date", "rank"],
        "date_field": "retrieved_date",
    },
    "artists": {
        "url": f"{BASE}/artists.html",
        "history_key": ["retrieved_date", "rank"],
        "date_field": "retrieved_date",
    },
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Map kworb's column headers -> friendly normalized names.
HEADER_ALIASES = {
    "pos": "pos",
    "p+": "pos_change",
    "pos+": "pos_change",
    "artist and title": "artist_and_title",
    "artist": "artist",
    "title": "title",
    "days": "days_on_chart",
    "pk": "peak",
    "(x?)": "peak_count",
    "streams": "streams",
    "streams+": "streams_change",
    "total": "total_streams",
    "daily": "daily_streams",
    "as lead": "as_lead",
    "solo": "solo",
    "as feature": "as_feature",
}

# Columns that should be coerced to integers (commas stripped) when present.
NUMERIC_COLUMNS = {
    "pos", "pos_change", "days_on_chart", "peak", "peak_count", "streams",
    "streams_change", "total_streams", "daily_streams", "as_lead", "solo",
    "as_feature", "rank",
}

SPOTIFY_ID_RE = re.compile(r"(?:artist|track)/([A-Za-z0-9]{22})")
TRACK_ID_RE = re.compile(r"track/([A-Za-z0-9]{22})")
ARTIST_ID_RE = re.compile(r"artist/([A-Za-z0-9]{22})")
LAST_UPDATED_RE = re.compile(r"(\d{4})[/-](\d{2})[/-](\d{2})")

logger = logging.getLogger("kworb")


class FetchError(Exception):
    """Raised when a page could not be fetched."""


class ParseError(Exception):
    """Raised when a page could not be parsed into a table."""


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #


def setup_logging(verbose: bool = False) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = LOG_DIR / f"kworb_{datetime.now():%Y%m%d_%H%M%S}.log"
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s")
    fh = logging.FileHandler(logfile, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.info("Logging to %s", logfile)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


# --------------------------------------------------------------------------- #
# Fetch
# --------------------------------------------------------------------------- #


def fetch_html(session: requests.Session, url: str, *, max_retries: int) -> str:
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(url, timeout=45)
        except requests.RequestException as exc:
            last_exc = exc
            wait = _backoff(attempt)
            logger.warning("network error on %s (attempt %d/%d): %s (retry %.1fs)",
                           url, attempt, max_retries, exc, wait)
            time.sleep(wait)
            continue

        if resp.status_code == 200:
            text = resp.text
            if "host_not_allowed" in text or resp.headers.get("x-deny-reason"):
                raise FetchError(
                    f"{url} blocked by network egress allowlist "
                    "(host_not_allowed). Run locally or allowlist kworb.net."
                )
            return text
        if resp.status_code == 404:
            raise FetchError(f"{url} -> HTTP 404 (page not found).")
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            wait = _backoff(attempt, resp.headers.get("Retry-After"))
            logger.warning("HTTP %d on %s (attempt %d/%d), retry %.1fs",
                           resp.status_code, url, attempt, max_retries, wait)
            last_exc = FetchError(f"HTTP {resp.status_code}")
            time.sleep(wait)
            continue
        if resp.status_code == 403:
            raise FetchError(
                f"{url} -> HTTP 403. If this says 'host_not_allowed' it is your "
                "environment's egress policy, not kworb."
            )
        raise FetchError(f"{url} -> unexpected HTTP {resp.status_code}")

    raise FetchError(f"{url}: exhausted {max_retries} retries; last error {last_exc}")


def _backoff(attempt: int, retry_after: Optional[str] = None) -> float:
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    base = min(2 ** attempt, 60)
    return base + random.uniform(0, base * 0.25)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def _normalize_header(text: str) -> str:
    key = re.sub(r"\s+", " ", text).strip().lower()
    if key in HEADER_ALIASES:
        return HEADER_ALIASES[key]
    slug = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    return slug or "col"


def _pick_main_table(soup: BeautifulSoup):
    tables = soup.find_all("table")
    if not tables:
        return None
    return max(tables, key=lambda t: len(t.find_all("tr")))


def _header_cells(table) -> list[str]:
    thead = table.find("thead")
    header_row = None
    if thead:
        header_row = thead.find("tr")
    if header_row is None:
        first = table.find("tr")
        if first and first.find("th"):
            header_row = first
    if header_row is None:
        return []
    return [_normalize_header(c.get_text(" ", strip=True))
            for c in header_row.find_all(["th", "td"])]


def _body_rows(table):
    tbody = table.find("tbody")
    rows = tbody.find_all("tr") if tbody else table.find_all("tr")
    # Drop pure-header rows (all <th>).
    return [r for r in rows if r.find("td")]


def parse_kworb_table(html: str, source_url: str) -> tuple[pd.DataFrame, Optional[str]]:
    """Parse the main HTML table generically. Returns (df, last_updated_str)."""
    soup = BeautifulSoup(html, "lxml")
    table = _pick_main_table(soup)
    if table is None:
        raise ParseError(f"No <table> found on {source_url}")

    headers = _header_cells(table)
    body = _body_rows(table)
    if not body:
        raise ParseError(f"Table on {source_url} had no data rows")

    records: list[dict] = []
    for tr in body:
        cells = tr.find_all(["td", "th"])
        rec: dict[str, object] = {}
        track_ids: list[str] = []
        artist_ids: list[str] = []
        for idx, cell in enumerate(cells):
            name = headers[idx] if idx < len(headers) else f"col_{idx}"
            rec[name] = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
            for a in cell.find_all("a", href=True):
                href = a["href"]
                m_t = TRACK_ID_RE.search(href)
                m_a = ARTIST_ID_RE.search(href)
                if m_t:
                    track_ids.append(m_t.group(1))
                if m_a:
                    artist_ids.append(m_a.group(1))
        rec["spotify_track_id"] = track_ids[0] if track_ids else None
        rec["spotify_artist_id"] = artist_ids[0] if artist_ids else None
        records.append(rec)

    df = pd.DataFrame.from_records(records)

    # Split the combined "Artist and Title" column when present.
    if "artist_and_title" in df.columns:
        split = df["artist_and_title"].fillna("").str.split(" - ", n=1, expand=True)
        df["artist"] = df.get("artist", split[0].str.strip())
        if split.shape[1] > 1:
            df["title"] = df.get("title", split[1].str.strip())

    # Derive a canonical Spotify URL where we have an ID.
    df["spotify_track_url"] = df["spotify_track_id"].map(
        lambda t: f"https://open.spotify.com/track/{t}" if isinstance(t, str) and t else pd.NA
    )
    df["spotify_artist_url"] = df["spotify_artist_id"].map(
        lambda a: f"https://open.spotify.com/artist/{a}" if isinstance(a, str) and a else pd.NA
    )

    # Coerce known-numeric columns.
    for col in df.columns:
        if col in NUMERIC_COLUMNS:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(r"[,\s]", "", regex=True),
                errors="coerce",
            ).astype("Int64")

    last_updated = None
    text = soup.get_text(" ", strip=True)
    m = re.search(r"updated[^0-9]*" + LAST_UPDATED_RE.pattern, text, re.IGNORECASE)
    if m:
        last_updated = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return df, last_updated


# --------------------------------------------------------------------------- #
# Dataset normalization
# --------------------------------------------------------------------------- #


def normalize_dataset(
    dataset: str, df: pd.DataFrame, *, source_url: str, last_updated: Optional[str], retrieved_at: str
) -> pd.DataFrame:
    df = df.copy()
    retrieved_date = retrieved_at[:10]

    if dataset == "global-daily":
        # kworb's daily page represents the day in its "last updated" stamp.
        df.insert(0, "chart_date", last_updated or retrieved_date)
        if "pos" not in df.columns:
            df.insert(1, "pos", range(1, len(df) + 1))
    else:
        # All-time lists: stamp with the retrieval date and a 1..N rank.
        df.insert(0, "retrieved_date", retrieved_date)
        df.insert(1, "rank", range(1, len(df) + 1))

    df["source_url"] = source_url
    df["retrieved_at"] = retrieved_at
    return df


# --------------------------------------------------------------------------- #
# Persistence (append-only master)
# --------------------------------------------------------------------------- #


def append_master(dataset: str, new_df: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    """Merge new rows into the master, dedupe on key (keep newest), persist."""
    csv_path = PROCESSED_DIR / f"kworb_{dataset.replace('-', '_')}.csv"
    parquet_path = PROCESSED_DIR / f"kworb_{dataset.replace('-', '_')}.parquet"

    if parquet_path.exists():
        try:
            existing = pd.read_parquet(parquet_path)
        except Exception:  # noqa: BLE001 - fall back to CSV if parquet unreadable
            existing = pd.read_csv(csv_path) if csv_path.exists() else pd.DataFrame()
    elif csv_path.exists():
        existing = pd.read_csv(csv_path)
    else:
        existing = pd.DataFrame()

    combined = pd.concat([existing, new_df], ignore_index=True)
    # Keep the most recently scraped version of each keyed row.
    if "retrieved_at" in combined.columns:
        combined = combined.sort_values("retrieved_at")
    key = [k for k in key if k in combined.columns]
    if key:
        combined = combined.drop_duplicates(subset=key, keep="last")
        combined = combined.sort_values(key).reset_index(drop=True)

    combined.to_csv(csv_path, index=False)
    try:
        combined.to_parquet(parquet_path, index=False)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not write parquet for %s (%s). Install pyarrow.", dataset, exc)
    logger.info("Master kworb_%s: +%d new rows -> %d total rows",
                dataset.replace('-', '_'), len(new_df), len(combined))
    return combined


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def validate(dataset: str, df: pd.DataFrame, key: list[str]) -> dict:
    report: dict = {"rows": int(len(df))}
    key = [k for k in key if k in df.columns]
    if key:
        report["duplicate_key_rows"] = int(df.duplicated(subset=key).sum())

    if dataset == "global-daily":
        if "pos" in df.columns:
            pos = pd.to_numeric(df["pos"], errors="coerce")
            report["pos_out_of_range_1_200"] = int(((pos < 1) | (pos > 200)).sum())
        if "chart_date" in df.columns:
            per_day = df.groupby("chart_date").size()
            short = per_day[per_day < 200]
            report["distinct_chart_dates"] = int(df["chart_date"].nunique())
            report["dates_under_200_rows"] = {str(k): int(v) for k, v in short.items()}
        report["missing_track_id"] = int(df["spotify_track_id"].isna().sum()) \
            if "spotify_track_id" in df.columns else None
    else:
        date_col = "retrieved_date"
        if date_col in df.columns:
            report["distinct_dates"] = int(df[date_col].nunique())
        id_col = "spotify_artist_id" if dataset == "artists" else "spotify_track_id"
        if id_col in df.columns:
            report["missing_spotify_id"] = int(df[id_col].isna().sum())

    streams_col = "streams" if dataset == "global-daily" else "total_streams"
    if streams_col in df.columns:
        report["streams_non_numeric"] = int(pd.to_numeric(df[streams_col], errors="coerce").isna().sum())

    report["passed"] = report.get("duplicate_key_rows", 0) == 0
    return report


# --------------------------------------------------------------------------- #
# Per-dataset run
# --------------------------------------------------------------------------- #


@dataclass
class RunStats:
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: Optional[str] = None
    datasets: dict = field(default_factory=dict)


def run_dataset(
    session: requests.Session, dataset: str, *, force: bool, max_retries: int
) -> dict:
    cfg = DATASETS[dataset]
    url = cfg["url"]
    raw_subdir = RAW_DIR / dataset.replace("-", "_")
    raw_subdir.mkdir(parents=True, exist_ok=True)

    retrieved_at = utc_now_iso()
    logger.info("[%s] fetching %s", dataset, url)
    html = fetch_html(session, url, max_retries=max_retries)
    df, last_updated = parse_kworb_table(html, url)

    norm = normalize_dataset(
        dataset, df, source_url=url, last_updated=last_updated, retrieved_at=retrieved_at
    )

    # Determine the snapshot date used for the raw filename + resume check.
    date_field = cfg["date_field"]
    snap_date = norm[date_field].iloc[0] if len(norm) else utc_today().isoformat()
    raw_path = raw_subdir / f"{dataset.replace('-', '_')}_{snap_date}.html"

    if raw_path.exists() and not force:
        logger.info("[%s] raw snapshot for %s already exists; skipping fetch-write "
                    "(use --force to overwrite). Re-merging into master anyway.",
                    dataset, snap_date)
    else:
        raw_path.write_text(html, encoding="utf-8")
        logger.info("[%s] saved raw snapshot -> %s", dataset, raw_path.name)

    master = append_master(dataset, norm, cfg["history_key"])
    report = validate(dataset, master, cfg["history_key"])
    report.update({
        "url": url,
        "snapshot_date": snap_date,
        "last_updated_on_page": last_updated,
        "new_rows": int(len(norm)),
        "columns": list(norm.columns),
    })
    logger.info("[%s] done: snapshot=%s rows_this_run=%d master_rows=%d passed=%s",
                dataset, snap_date, len(norm), report["rows"], report["passed"])
    return report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Scrape Spotify stats from kworb.net (global daily, songs, artists).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--datasets",
        default="global-daily,songs,artists",
        help="Comma-separated datasets to scrape (choices: global-daily, songs, artists).",
    )
    p.add_argument("--force", action="store_true",
                   help="Overwrite today's raw snapshot if it already exists.")
    p.add_argument("--sleep-min", type=float, default=1.5,
                   help="Min seconds of randomized delay between page fetches.")
    p.add_argument("--sleep-max", type=float, default=4.0,
                   help="Max seconds of randomized delay between page fetches.")
    p.add_argument("--max-retries", type=int, default=5, help="Retries per request.")
    p.add_argument("--verbose", action="store_true", help="Verbose console logging.")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    setup_logging(verbose=args.verbose)

    for d in (RAW_DIR, PROCESSED_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)

    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]
    bad = set(datasets) - set(DATASETS)
    if bad:
        logger.error("Unknown datasets: %s (valid: %s)", bad, list(DATASETS))
        return 2
    if args.sleep_min > args.sleep_max:
        logger.error("--sleep-min cannot exceed --sleep-max")
        return 2

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})

    stats = RunStats()
    any_ok = False
    for i, dataset in enumerate(datasets):
        try:
            stats.datasets[dataset] = run_dataset(
                session, dataset, force=args.force, max_retries=args.max_retries
            )
            any_ok = True
        except (FetchError, ParseError) as exc:
            logger.error("[%s] FAILED: %s", dataset, exc)
            stats.datasets[dataset] = {"error": str(exc), "passed": False}
        if i < len(datasets) - 1:
            time.sleep(random.uniform(args.sleep_min, args.sleep_max))

    stats.finished_at = utc_now_iso()
    summary_path = PROCESSED_DIR / "kworb_scrape_summary.json"
    summary_path.write_text(json.dumps({
        "started_at": stats.started_at,
        "finished_at": stats.finished_at,
        "datasets": stats.datasets,
    }, indent=2))
    logger.info("Wrote %s", summary_path.name)
    return 0 if any_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
