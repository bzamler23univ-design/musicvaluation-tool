#!/usr/bin/env python3
"""Spotify Global Daily Top 200 chart scraper / data pipeline.

Downloads the Spotify *Global – Daily Top Songs (Top 200)* chart for every day
in a date range, stores each day's raw CSV, then builds a single cleaned,
validated master dataset.

IMPORTANT — read this before running
-------------------------------------
Spotify's chart data is **no longer available from an open, unauthenticated
endpoint**:

* The legacy ``spotifycharts.com/regional/global/daily/<date>/download`` URL
  (referenced by many old tutorials and the ``fycharts`` package) was shut
  down by Spotify in early 2022. It will not work.
* It was replaced by ``charts.spotify.com``, which **requires a logged-in
  Spotify account** to view charts and download the CSV. There is no public
  API.

Because of that, this tool is built around *your own* legitimate, logged-in
session. It will **never** try to bypass the login or any anti-bot control —
you supply credentials/cookies yourself (an account you are entitled to use),
exactly as the Spotify Terms require. Two strategies are attempted, in order:

1. ``csv-api``  – an authenticated HTTP GET to the chart CSV download URL,
   using a bearer token and/or ``sp_dc`` cookie that you provide via
   environment variables. The URL template is configurable because Spotify
   changes it from time to time and it is not publicly documented.
2. ``playwright`` – drive a real browser using a saved login session
   (``storage_state``) and click the page's own "Download CSV" button.

If both fail for a given date, the reason is logged and the date is recorded
in ``missing_dates.csv``.

See ``README.md`` for setup, auth, legal/ethical notes, and known limitations.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import requests

# --------------------------------------------------------------------------- #
# Configuration / constants
# --------------------------------------------------------------------------- #

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # repo root (scripts/ -> ..)
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
LOG_DIR = PROJECT_ROOT / "logs"

DEFAULT_START_DATE = "2017-01-01"

# Region / chart this pipeline targets.
CHART_ID = "regional-global-daily"

# Endpoint templates. These are configurable via environment variables because
# Spotify's authenticated endpoints are undocumented and change over time.
# {date} is substituted with an ISO date (YYYY-MM-DD).
CSV_API_URL_TEMPLATE = os.environ.get(
    "SPOTIFY_CHARTS_DOWNLOAD_URL",
    "https://charts.spotify.com/api/v2/charts/regional-global-daily/{date}/download",
)
# Legacy, deprecated endpoint. Kept only so the failure is explicit/logged.
LEGACY_URL_TEMPLATE = os.environ.get(
    "SPOTIFY_CHARTS_LEGACY_URL",
    "https://spotifycharts.com/regional/global/daily/{date}/download",
)
# Human-facing chart page (used by the Playwright fallback).
CHART_PAGE_URL_TEMPLATE = os.environ.get(
    "SPOTIFY_CHARTS_PAGE_URL",
    "https://charts.spotify.com/charts/view/regional-global-daily/{date}",
)

# Auth material supplied by the user (their own account). Never hard-coded.
ENV_BEARER = "SPOTIFY_CHARTS_BEARER"          # -> Authorization: Bearer <...>
ENV_SP_DC = "SPOTIFY_SP_DC"                    # -> Cookie: sp_dc=<...>
ENV_COOKIE = "SPOTIFY_CHARTS_COOKIE"           # -> raw Cookie header (advanced)
PLAYWRIGHT_STATE = os.environ.get(
    "SPOTIFY_PLAYWRIGHT_STATE", str(PROJECT_ROOT / "playwright_state.json")
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Clean output schema (column order matters for the master file).
CLEAN_COLUMNS = [
    "chart_date",
    "rank",
    "track_name",
    "artist_names",
    "streams",
    "spotify_url",
    "spotify_track_id",
    "source_url",
    "scraped_at",
]

EXPECTED_ROWS = 200
TRACK_ID_RE = re.compile(r"track[:/]([A-Za-z0-9]{22})")

logger = logging.getLogger("spotify_charts")


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class FetchError(Exception):
    """Raised when a chart CSV could not be fetched."""


class AuthError(FetchError):
    """Raised when the endpoint refused the request due to missing/invalid auth."""


class ParseError(Exception):
    """Raised when a downloaded CSV could not be parsed into the clean schema."""


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #


def setup_logging(verbose: bool = False) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = LOG_DIR / f"scrape_{datetime.now():%Y%m%d_%H%M%S}.log"

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


# --------------------------------------------------------------------------- #
# Date helpers
# --------------------------------------------------------------------------- #


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def daterange(start: date, end: date) -> Iterable[date]:
    cur = start
    step = timedelta(days=1)
    while cur <= end:
        yield cur
        cur += step


def raw_path_for(chart_date: date) -> Path:
    return RAW_DIR / f"global_daily_{chart_date:%Y-%m-%d}.csv"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Fetchers
# --------------------------------------------------------------------------- #


@dataclass
class FetchResult:
    chart_date: date
    source_url: str
    content: bytes
    strategy: str


def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/csv,*/*"})

    bearer = os.environ.get(ENV_BEARER)
    if bearer:
        session.headers["Authorization"] = f"Bearer {bearer.strip()}"

    raw_cookie = os.environ.get(ENV_COOKIE)
    sp_dc = os.environ.get(ENV_SP_DC)
    if raw_cookie:
        session.headers["Cookie"] = raw_cookie.strip()
    elif sp_dc:
        session.headers["Cookie"] = f"sp_dc={sp_dc.strip()}"

    return session


def _looks_like_csv(content: bytes) -> bool:
    """Reject HTML login/anti-bot pages masquerading as a 200 response."""
    head = content[:512].lstrip().lower()
    if not head:
        return False
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        return False
    # A real chart CSV contains a comma-separated header row.
    first_line = content.split(b"\n", 1)[0].lower()
    return b"," in first_line


def fetch_csv_api(
    session: requests.Session,
    chart_date: date,
    *,
    max_retries: int,
) -> FetchResult:
    """Strategy 1: authenticated HTTP GET to the CSV download endpoint."""
    url = CSV_API_URL_TEMPLATE.format(date=chart_date.isoformat())
    content = _http_get_with_backoff(session, url, chart_date, max_retries=max_retries)
    return FetchResult(chart_date, url, content, strategy="csv-api")


def fetch_legacy(
    session: requests.Session,
    chart_date: date,
    *,
    max_retries: int,
) -> FetchResult:
    """Deprecated legacy spotifycharts.com endpoint (almost certainly dead)."""
    url = LEGACY_URL_TEMPLATE.format(date=chart_date.isoformat())
    content = _http_get_with_backoff(session, url, chart_date, max_retries=max_retries)
    return FetchResult(chart_date, url, content, strategy="legacy")


def _http_get_with_backoff(
    session: requests.Session,
    url: str,
    chart_date: date,
    *,
    max_retries: int,
) -> bytes:
    """GET with exponential backoff + jitter. Raises FetchError/AuthError."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(url, timeout=45, allow_redirects=True)
        except requests.RequestException as exc:
            last_exc = exc
            wait = _backoff_seconds(attempt)
            logger.warning(
                "[%s] network error on attempt %d/%d: %s (retry in %.1fs)",
                chart_date, attempt, max_retries, exc, wait,
            )
            time.sleep(wait)
            continue

        status = resp.status_code
        if status == 200:
            if _looks_like_csv(resp.content):
                return resp.content
            raise FetchError(
                f"[{chart_date}] HTTP 200 but body is not CSV "
                f"(likely a login/anti-bot HTML page). url={url}"
            )
        if status in (401, 403):
            # Auth/anti-bot: do NOT hammer or attempt to bypass. Fail fast.
            raise AuthError(
                f"[{chart_date}] HTTP {status} from {url}. "
                "The endpoint requires a valid logged-in session. Provide "
                f"${ENV_BEARER} and/or ${ENV_SP_DC}, or use the Playwright strategy."
            )
        if status == 404:
            raise FetchError(f"[{chart_date}] HTTP 404 — chart not available for this date.")
        if status == 429 or 500 <= status < 600:
            wait = _backoff_seconds(attempt, retry_after=resp.headers.get("Retry-After"))
            logger.warning(
                "[%s] HTTP %d on attempt %d/%d (retry in %.1fs)",
                chart_date, status, attempt, max_retries, wait,
            )
            last_exc = FetchError(f"HTTP {status}")
            time.sleep(wait)
            continue

        raise FetchError(f"[{chart_date}] unexpected HTTP {status} from {url}")

    raise FetchError(
        f"[{chart_date}] exhausted {max_retries} retries; last error: {last_exc}"
    )


def _backoff_seconds(attempt: int, retry_after: Optional[str] = None) -> float:
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    base = min(2 ** attempt, 60)
    return base + random.uniform(0, base * 0.25)


def fetch_playwright(chart_date: date) -> FetchResult:
    """Strategy 2: drive a real (logged-in) browser and download the CSV.

    Requires a saved Playwright ``storage_state`` JSON produced by logging into
    charts.spotify.com once (see README ``--playwright-login``). This uses your
    own session and the page's own download button — it does not bypass auth.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise FetchError(
            "Playwright not installed. Run `pip install playwright` and "
            "`playwright install chromium`."
        ) from exc

    state = Path(PLAYWRIGHT_STATE)
    if not state.exists():
        raise AuthError(
            f"No Playwright login session at {state}. Create one with "
            "`python scrape_spotify_global_200.py --playwright-login`."
        )

    url = CHART_PAGE_URL_TEMPLATE.format(date=chart_date.isoformat())
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(state), user_agent=USER_AGENT)
        page = context.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=60_000)
            # The chart page exposes a download control; clicking it triggers a
            # CSV download. Selectors change over time, so try a few.
            selectors = [
                "button:has-text('Download')",
                "[data-testid='charts-download']",
                "a[download]",
            ]
            with page.expect_download(timeout=60_000) as dl_info:
                clicked = False
                for sel in selectors:
                    loc = page.locator(sel)
                    if loc.count() > 0:
                        loc.first.click()
                        clicked = True
                        break
                if not clicked:
                    raise FetchError(
                        f"[{chart_date}] could not find a download control on {url}"
                    )
            download = dl_info.value
            tmp = download.path()
            content = Path(tmp).read_bytes()
        finally:
            context.close()
            browser.close()

    if not _looks_like_csv(content):
        raise FetchError(f"[{chart_date}] Playwright download was not a CSV.")
    return FetchResult(chart_date, url, content, strategy="playwright")


def playwright_login() -> None:
    """Open a browser so the user can log into Spotify, then save the session."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "Playwright not installed. Run `pip install playwright` and "
            "`playwright install chromium`."
        ) from exc

    page_url = CHART_PAGE_URL_TEMPLATE.format(date=date.today().isoformat())
    print(
        "A browser window will open. Log into your Spotify account, wait until "
        "the chart is visible, then return here and press Enter."
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.goto(page_url, wait_until="domcontentloaded")
        input("Press Enter once you are logged in and the chart is visible... ")
        context.storage_state(path=PLAYWRIGHT_STATE)
        browser.close()
    print(f"Saved login session to {PLAYWRIGHT_STATE}")


# --------------------------------------------------------------------------- #
# Strategy dispatch
# --------------------------------------------------------------------------- #


def fetch_chart(
    session: requests.Session,
    chart_date: date,
    *,
    strategies: list[str],
    max_retries: int,
) -> FetchResult:
    """Try each strategy in order; return the first success or raise."""
    errors: list[str] = []
    for strat in strategies:
        try:
            if strat == "csv-api":
                return fetch_csv_api(session, chart_date, max_retries=max_retries)
            if strat == "legacy":
                return fetch_legacy(session, chart_date, max_retries=max_retries)
            if strat == "playwright":
                return fetch_playwright(chart_date)
            errors.append(f"unknown strategy '{strat}'")
        except FetchError as exc:
            logger.warning("[%s] strategy '%s' failed: %s", chart_date, strat, exc)
            errors.append(f"{strat}: {exc}")
    raise FetchError(f"[{chart_date}] all strategies failed -> " + " | ".join(errors))


# --------------------------------------------------------------------------- #
# Parsing / normalization
# --------------------------------------------------------------------------- #

# Maps a variety of known column names (new charts.spotify.com export and the
# legacy spotifycharts.com export) onto our clean schema.
COLUMN_ALIASES = {
    "rank": {"rank", "position", "pos"},
    "track_name": {"track_name", "track name", "title", "song"},
    "artist_names": {"artist_names", "artist", "artists", "artist name"},
    "streams": {"streams", "stream"},
    "_uri": {"uri", "url", "spotify_url", "track_url"},
}


def _read_csv_flexible(content: bytes) -> pd.DataFrame:
    """Read CSV, tolerating the legacy 2-line header used by spotifycharts.com."""
    text = content.decode("utf-8-sig", errors="replace")
    # Legacy files start with a junk line then the real header.
    first_line = text.split("\n", 1)[0].strip().lower()
    skiprows = 0
    if first_line and "," not in first_line:
        skiprows = 1
    return pd.read_csv(io.StringIO(text), skiprows=skiprows)


def _resolve_columns(df: pd.DataFrame) -> dict[str, str]:
    lower = {c.lower().strip(): c for c in df.columns}
    resolved: dict[str, str] = {}
    for clean, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lower:
                resolved[clean] = lower[alias]
                break
    return resolved


def normalize(result: FetchResult, scraped_at: str) -> pd.DataFrame:
    """Turn a raw CSV into a clean, schema-conformant DataFrame. Raises ParseError."""
    try:
        df = _read_csv_flexible(result.content)
    except Exception as exc:  # noqa: BLE001 - surface any pandas/parse error uniformly
        raise ParseError(f"[{result.chart_date}] could not read CSV: {exc}") from exc

    if df.empty:
        raise ParseError(f"[{result.chart_date}] CSV had no rows.")

    cols = _resolve_columns(df)
    missing = {"rank", "track_name", "artist_names", "streams"} - cols.keys()
    if missing:
        raise ParseError(
            f"[{result.chart_date}] CSV missing required columns {sorted(missing)}; "
            f"got {list(df.columns)}"
        )

    out = pd.DataFrame()
    out["rank"] = pd.to_numeric(df[cols["rank"]], errors="coerce").astype("Int64")
    out["track_name"] = df[cols["track_name"]].astype(str).str.strip()
    out["artist_names"] = df[cols["artist_names"]].astype(str).str.strip()
    out["streams"] = pd.to_numeric(
        df[cols["streams"]].astype(str).str.replace(r"[,\s]", "", regex=True),
        errors="coerce",
    ).astype("Int64")

    uri_series = df[cols["_uri"]].astype(str) if "_uri" in cols else pd.Series([""] * len(df))
    track_ids = uri_series.str.extract(TRACK_ID_RE, expand=False)
    out["spotify_track_id"] = track_ids
    out["spotify_url"] = track_ids.map(
        lambda tid: f"https://open.spotify.com/track/{tid}" if isinstance(tid, str) else pd.NA
    )

    out.insert(0, "chart_date", result.chart_date.isoformat())
    out["source_url"] = result.source_url
    out["scraped_at"] = scraped_at

    out = out.dropna(subset=["rank"]).reset_index(drop=True)
    return out[CLEAN_COLUMNS]


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


@dataclass
class RunStats:
    start_date: str
    end_date: str
    strategies: list[str]
    requested_days: int = 0
    downloaded: int = 0
    skipped_existing: int = 0
    failed: int = 0
    parsed_rows: int = 0
    earliest_available: Optional[str] = None
    missing_dates: list[dict] = field(default_factory=list)
    partial_dates: list[dict] = field(default_factory=list)
    validation: dict = field(default_factory=dict)
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: Optional[str] = None


def process_one_date(
    session: requests.Session,
    chart_date: date,
    *,
    strategies: list[str],
    force: bool,
    max_retries: int,
    stats: RunStats,
) -> Optional[pd.DataFrame]:
    """Fetch (or reuse) and normalize one day's chart. Returns clean df or None."""
    raw_path = raw_path_for(chart_date)

    if raw_path.exists() and not force:
        logger.debug("[%s] raw CSV exists, reusing (use --force to refetch).", chart_date)
        stats.skipped_existing += 1
        result = FetchResult(
            chart_date,
            source_url=f"file://{raw_path}",
            content=raw_path.read_bytes(),
            strategy="cache",
        )
    else:
        try:
            result = fetch_chart(
                session, chart_date, strategies=strategies, max_retries=max_retries
            )
        except FetchError as exc:
            logger.error("[%s] download failed: %s", chart_date, exc)
            stats.failed += 1
            stats.missing_dates.append(
                {"chart_date": chart_date.isoformat(), "reason": str(exc)}
            )
            return None
        raw_path.write_bytes(result.content)
        stats.downloaded += 1
        logger.info("[%s] downloaded via %s -> %s", chart_date, result.strategy, raw_path.name)

    try:
        clean = normalize(result, scraped_at=utc_now_iso())
    except ParseError as exc:
        logger.error("[%s] parse failed: %s", chart_date, exc)
        stats.failed += 1
        stats.missing_dates.append(
            {"chart_date": chart_date.isoformat(), "reason": f"parse: {exc}"}
        )
        return None

    n = len(clean)
    stats.parsed_rows += n
    if n < EXPECTED_ROWS:
        logger.warning("[%s] only %d rows (expected %d).", chart_date, n, EXPECTED_ROWS)
        stats.partial_dates.append({"chart_date": chart_date.isoformat(), "rows": n})
    return clean


def detect_earliest(
    session: requests.Session,
    *,
    strategies: list[str],
    max_retries: int,
    floor: date = date(2017, 1, 1),
    ceiling: Optional[date] = None,
) -> Optional[date]:
    """Find the earliest date that returns a valid chart.

    Linear scan forward from ``floor`` (charts are roughly contiguous, so the
    earliest valid date is found quickly). Stops at ``ceiling`` (today by default).
    """
    ceiling = ceiling or date.today()
    logger.info("Detecting earliest available chart date from %s ...", floor)
    probe = floor
    while probe <= ceiling:
        try:
            result = fetch_chart(session, probe, strategies=strategies, max_retries=max_retries)
            normalize(result, scraped_at=utc_now_iso())
            # Cache the successful probe so the main run can reuse it.
            raw_path_for(probe).write_bytes(result.content)
            logger.info("Earliest available chart date detected: %s", probe)
            return probe
        except (FetchError, ParseError) as exc:
            logger.debug("[%s] not available during detection: %s", probe, exc)
            probe += timedelta(days=1)
    logger.error("No available chart date found between %s and %s.", floor, ceiling)
    return None


# --------------------------------------------------------------------------- #
# Validation & outputs
# --------------------------------------------------------------------------- #


def validate(master: pd.DataFrame) -> dict:
    report: dict = {}
    report["total_rows"] = int(len(master))
    report["distinct_dates"] = int(master["chart_date"].nunique())

    ranks = pd.to_numeric(master["rank"], errors="coerce")
    report["rank_out_of_range"] = int(((ranks < 1) | (ranks > EXPECTED_ROWS)).sum())

    streams = pd.to_numeric(master["streams"], errors="coerce")
    report["streams_non_numeric"] = int(streams.isna().sum())

    dup_date_rank = master.duplicated(subset=["chart_date", "rank"]).sum()
    report["duplicate_date_rank"] = int(dup_date_rank)

    dup_triplet = master.duplicated(
        subset=["chart_date", "spotify_track_id", "rank"]
    ).sum()
    report["duplicate_date_trackid_rank"] = int(dup_triplet)

    per_day = master.groupby("chart_date").size()
    partial = per_day[per_day < EXPECTED_ROWS]
    report["dates_under_200"] = {str(k): int(v) for k, v in partial.items()}
    report["num_dates_under_200"] = int(len(partial))

    if not master.empty:
        report["min_date"] = str(master["chart_date"].min())
        report["max_date"] = str(master["chart_date"].max())
    report["passed"] = (
        report["rank_out_of_range"] == 0
        and report["streams_non_numeric"] == 0
        and report["duplicate_date_rank"] == 0
        and report["duplicate_date_trackid_rank"] == 0
    )
    return report


def write_outputs(master: pd.DataFrame, stats: RunStats) -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    csv_path = PROCESSED_DIR / "spotify_global_200_daily.csv"
    parquet_path = PROCESSED_DIR / "spotify_global_200_daily.parquet"
    missing_path = PROCESSED_DIR / "missing_dates.csv"
    summary_path = PROCESSED_DIR / "scrape_summary.json"

    if not master.empty:
        master = master.sort_values(["chart_date", "rank"]).reset_index(drop=True)
        master.to_csv(csv_path, index=False)
        logger.info("Wrote %s (%d rows)", csv_path.name, len(master))
        try:
            master.to_parquet(parquet_path, index=False)
            logger.info("Wrote %s", parquet_path.name)
        except Exception as exc:  # noqa: BLE001 - pyarrow may be missing
            logger.error("Could not write parquet (%s). Install pyarrow.", exc)
    else:
        logger.warning("No rows collected — master CSV/parquet not written.")

    pd.DataFrame(stats.missing_dates or [], columns=["chart_date", "reason"]).to_csv(
        missing_path, index=False
    )
    logger.info("Wrote %s (%d missing/failed dates)", missing_path.name, len(stats.missing_dates))

    stats.finished_at = utc_now_iso()
    summary = {
        "start_date": stats.start_date,
        "end_date": stats.end_date,
        "strategies": stats.strategies,
        "requested_days": stats.requested_days,
        "downloaded": stats.downloaded,
        "skipped_existing": stats.skipped_existing,
        "failed": stats.failed,
        "parsed_rows": stats.parsed_rows,
        "earliest_available": stats.earliest_available,
        "num_missing_dates": len(stats.missing_dates),
        "num_partial_dates": len(stats.partial_dates),
        "partial_dates": stats.partial_dates,
        "validation": stats.validation,
        "started_at": stats.started_at,
        "finished_at": stats.finished_at,
    }
    summary_path.write_text(json.dumps(summary, indent=2))
    logger.info("Wrote %s", summary_path.name)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Download & clean the Spotify Global Daily Top 200 chart.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--start-date", default=DEFAULT_START_DATE, help="ISO start date (inclusive).")
    p.add_argument("--end-date", default=date.today().isoformat(), help="ISO end date (inclusive).")
    p.add_argument("--force", action="store_true", help="Refetch even if a raw CSV exists.")
    p.add_argument("--sleep-min", type=float, default=1.5, help="Min seconds between requests.")
    p.add_argument("--sleep-max", type=float, default=4.0, help="Max seconds between requests.")
    p.add_argument("--max-retries", type=int, default=5, help="Retries per request (backoff).")
    p.add_argument(
        "--strategies",
        default="csv-api,playwright",
        help="Comma-separated fetch strategies in priority order "
        "(choices: csv-api, legacy, playwright).",
    )
    p.add_argument(
        "--detect-earliest",
        action="store_true",
        help="Probe forward from the start date to find the earliest valid chart date, "
        "then begin the run there.",
    )
    p.add_argument(
        "--playwright-login",
        action="store_true",
        help="Open a browser to log into Spotify and save the session, then exit.",
    )
    p.add_argument("--verbose", action="store_true", help="Verbose console logging.")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    setup_logging(verbose=args.verbose)

    for d in (RAW_DIR, PROCESSED_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)

    if args.playwright_login:
        playwright_login()
        return 0

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    valid = {"csv-api", "legacy", "playwright"}
    bad = set(strategies) - valid
    if bad:
        logger.error("Unknown strategies: %s (valid: %s)", bad, valid)
        return 2

    start = parse_date(args.start_date)
    end = parse_date(args.end_date)
    if start > end:
        logger.error("start-date %s is after end-date %s", start, end)
        return 2
    if args.sleep_min > args.sleep_max:
        logger.error("--sleep-min cannot exceed --sleep-max")
        return 2

    session = _build_session()
    if not (os.environ.get(ENV_BEARER) or os.environ.get(ENV_SP_DC) or os.environ.get(ENV_COOKIE)):
        if "csv-api" in strategies:
            logger.warning(
                "No auth provided (%s / %s / %s). The csv-api strategy will likely "
                "get HTTP 401/403 — set these or rely on the playwright strategy.",
                ENV_BEARER, ENV_SP_DC, ENV_COOKIE,
            )

    stats = RunStats(start_date=start.isoformat(), end_date=end.isoformat(), strategies=strategies)

    if args.detect_earliest:
        earliest = detect_earliest(
            session, strategies=strategies, max_retries=args.max_retries,
            floor=start, ceiling=end,
        )
        if earliest is None:
            logger.error("Could not detect any available chart date; aborting.")
            stats.validation = {"passed": False, "note": "no available dates"}
            write_outputs(pd.DataFrame(columns=CLEAN_COLUMNS), stats)
            return 1
        stats.earliest_available = earliest.isoformat()
        start = earliest

    all_dates = list(daterange(start, end))
    stats.requested_days = len(all_dates)
    logger.info(
        "Scraping %s -> %s (%d days) via strategies=%s",
        start, end, len(all_dates), strategies,
    )

    frames: list[pd.DataFrame] = []
    for i, d in enumerate(all_dates, 1):
        clean = process_one_date(
            session, d, strategies=strategies, force=args.force,
            max_retries=args.max_retries, stats=stats,
        )
        if clean is not None and not clean.empty:
            frames.append(clean)
            if stats.earliest_available is None:
                stats.earliest_available = d.isoformat()
        # Polite randomized delay between network requests (not for cache hits).
        if i < len(all_dates):
            time.sleep(random.uniform(args.sleep_min, args.sleep_max))

    if frames:
        master = pd.concat(frames, ignore_index=True)
        master = master.drop_duplicates(subset=["chart_date", "rank"]).reset_index(drop=True)
    else:
        master = pd.DataFrame(columns=CLEAN_COLUMNS)

    stats.validation = validate(master)
    write_outputs(master, stats)

    logger.info(
        "Done. downloaded=%d skipped=%d failed=%d rows=%d validation_passed=%s",
        stats.downloaded, stats.skipped_existing, stats.failed,
        len(master), stats.validation.get("passed"),
    )
    return 0 if (frames or stats.requested_days == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
