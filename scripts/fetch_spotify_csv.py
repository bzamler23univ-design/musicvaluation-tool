#!/usr/bin/env python3
"""Bulk-download charts.spotify.com CSV exports over a date range.

This automates what you'd do by hand on charts.spotify.com: for each week (or
day) in a range, download the "Download data as CSV" file. The CSVs land in
``data/spotify_csv/`` where ``import_spotify_csv.py`` ingests them.

AUTH — REQUIRED, AND IT USES *YOUR* SESSION
-------------------------------------------
charts.spotify.com requires a logged-in Spotify account; there is no public,
unauthenticated endpoint. This tool does NOT bypass the login — you supply
credentials for an account you are entitled to use, via env vars:

    SPOTIFY_CHARTS_BEARER   Authorization: Bearer <token>   (from your session)
    SPOTIFY_SP_DC           the sp_dc cookie from your browser
    SPOTIFY_CHARTS_COOKIE   (advanced) a full raw Cookie header

The exact download URL is undocumented and changes; override if needed:
    SPOTIFY_CHARTS_DOWNLOAD_URL="https://charts.spotify.com/api/v2/charts/{chart}/{date}/download"

If the HTTP endpoint returns 401/403, fall back to ``--playwright`` which drives
a real browser using a saved login (run scrape_spotify_global_200.py
--playwright-login once to create playwright_state.json).

EXAMPLES
--------
    # Every weekly Global chart from 2022 through a known recent week:
    python scripts/fetch_spotify_csv.py --chart regional-global-weekly \
        --start-date 2022-01-01 --end-date 2026-06-18

    # Then normalize + rebuild the site data:
    python scripts/import_spotify_csv.py && python scripts/generate_frontend_data.py

Be polite: randomized delays + capped backoff are on. Respect Spotify's Terms
and don't hammer the service.
"""
from __future__ import annotations

import argparse
import os
import random
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = PROJECT_ROOT / "data" / "spotify_csv"

URL_TEMPLATE = os.environ.get(
    "SPOTIFY_CHARTS_DOWNLOAD_URL",
    "https://charts.spotify.com/api/v2/charts/{chart}/{date}/download",
)
PAGE_TEMPLATE = os.environ.get(
    "SPOTIFY_CHARTS_PAGE_URL",
    "https://charts.spotify.com/charts/view/{chart}/{date}",
)
PLAYWRIGHT_STATE = os.environ.get(
    "SPOTIFY_PLAYWRIGHT_STATE", str(PROJECT_ROOT / "playwright_state.json")
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def log(msg: str) -> None:
    print(f"{datetime.now():%H:%M:%S} | {msg}", flush=True)


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "text/csv,*/*"})
    bearer = os.environ.get("SPOTIFY_CHARTS_BEARER")
    if bearer:
        s.headers["Authorization"] = f"Bearer {bearer.strip()}"
    cookie = os.environ.get("SPOTIFY_CHARTS_COOKIE")
    sp_dc = os.environ.get("SPOTIFY_SP_DC")
    if cookie:
        s.headers["Cookie"] = cookie.strip()
    elif sp_dc:
        s.headers["Cookie"] = f"sp_dc={sp_dc.strip()}"
    return s


def looks_like_csv(content: bytes) -> bool:
    head = content[:512].lstrip().lower()
    if not head or head.startswith(b"<!doctype") or head.startswith(b"<html"):
        return False
    first = content.split(b"\n", 1)[0].lower()
    return b"," in first and (b"rank" in first or b"uri" in first or b"streams" in first)


# A known real Spotify weekly chart date (a Thursday). All weekly charts fall
# on anchor + 7*k, so we can align any [start, end] window to real chart days
# regardless of what weekday the caller passes.
WEEKLY_ANCHOR = date(2016, 12, 29)


def weekly_dates(start: date, end: date, anchor: date = WEEKLY_ANCHOR) -> list[date]:
    """Every real weekly chart date (anchor + 7*k) within [start, end]."""
    import math
    k = math.ceil((start - anchor).days / 7)
    cur = anchor + timedelta(days=7 * k)
    out = []
    while cur <= end:
        if cur >= start:
            out.append(cur)
        cur += timedelta(days=7)
    return out


def daily_dates(start: date, end: date) -> list[date]:
    out, cur = [], start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def backoff(attempt: int, retry_after: Optional[str] = None) -> float:
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    base = min(2 ** attempt, 60)
    return base + random.uniform(0, base * 0.25)


def fetch_http(session: requests.Session, chart: str, d: date, max_retries: int) -> bytes:
    url = URL_TEMPLATE.format(chart=chart, date=d.isoformat())
    last = None
    for attempt in range(1, max_retries + 1):
        try:
            r = session.get(url, timeout=45, allow_redirects=True)
        except requests.RequestException as exc:
            last = exc
            time.sleep(backoff(attempt))
            continue
        if r.status_code == 200:
            if looks_like_csv(r.content):
                return r.content
            raise PermissionError(
                "HTTP 200 but body is not CSV (likely a login/anti-bot page). "
                "Provide valid auth or use --playwright."
            )
        if r.status_code in (401, 403):
            raise PermissionError(
                f"HTTP {r.status_code} — endpoint needs a valid logged-in session. "
                "Set SPOTIFY_CHARTS_BEARER / SPOTIFY_SP_DC, or use --playwright."
            )
        if r.status_code == 404:
            raise FileNotFoundError(f"HTTP 404 — no chart for {d} (before earliest?).")
        if r.status_code == 429 or 500 <= r.status_code < 600:
            last = RuntimeError(f"HTTP {r.status_code}")
            time.sleep(backoff(attempt, r.headers.get("Retry-After")))
            continue
        raise RuntimeError(f"unexpected HTTP {r.status_code}")
    raise RuntimeError(f"exhausted retries: {last}")


def _download_csv(page) -> bytes:
    """Click the chart's 'Download data as CSV' control and return the bytes.
    The control is an icon button, so we try several ways to find it."""
    selectors = [
        "[aria-label*='Download data as CSV' i]",
        "button[aria-label*='Download' i]",
        "[title*='Download data as CSV' i]",
        "button[title*='Download' i]",
        "a[download]",
        "button:has-text('Download')",
        "[data-testid='charts-download']",
    ]
    last_err: Optional[Exception] = None
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() == 0:
                continue
            with page.expect_download(timeout=30_000) as dl:
                loc.first.click()
            return Path(dl.value.path()).read_bytes()
        except Exception as exc:  # noqa: BLE001 - try the next strategy
            last_err = exc
    # Last resort: any button whose accessible name mentions "download".
    try:
        btn = page.get_by_role("button", name=re.compile("download", re.I))
        with page.expect_download(timeout=30_000) as dl:
            btn.first.click()
        return Path(dl.value.path()).read_bytes()
    except Exception as exc:  # noqa: BLE001
        last_err = exc
    raise RuntimeError(f"could not find/trigger the CSV download control ({last_err})")


class PlaywrightFetcher:
    """Opens one logged-in headless browser and reuses it for every date."""

    def __init__(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright not installed. Run: pip3 install playwright && "
                "python3 -m playwright install chromium"
            ) from exc
        state = Path(PLAYWRIGHT_STATE)
        if not state.exists():
            raise PermissionError(
                f"No saved login at {state}. Run: python3 scripts/scrape_spotify_global_200.py "
                "--playwright-login"
            )
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=True)
        self.ctx = self.browser.new_context(
            storage_state=str(state), user_agent=USER_AGENT, accept_downloads=True
        )
        self.page = self.ctx.new_page()

    def fetch(self, chart: str, d: date) -> bytes:
        url = PAGE_TEMPLATE.format(chart=chart, date=d.isoformat())
        self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        self.page.wait_for_timeout(1500)  # let the chart + download button render
        content = _download_csv(self.page)
        if not looks_like_csv(content):
            raise RuntimeError("downloaded file was not a CSV (login expired?)")
        return content

    def close(self) -> None:
        for fn in (lambda: self.ctx.close(), lambda: self.browser.close(), self._pw.stop):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Bulk-download Spotify chart CSVs (your own login).")
    ap.add_argument("--chart", default="regional-global-weekly",
                    help="e.g. regional-global-weekly, regional-global-daily, regional-us-weekly")
    ap.add_argument("--start-date", required=True, help="ISO start date (inclusive).")
    ap.add_argument("--end-date", required=True,
                    help="ISO end date — for weekly, set this to a KNOWN chart date "
                    "(stepping is anchored here, going back by 7 days).")
    ap.add_argument("--cadence", choices=["weekly", "daily"],
                    help="Override; default inferred from --chart name.")
    ap.add_argument("--force", action="store_true", help="Re-download even if the CSV exists.")
    ap.add_argument("--playwright", action="store_true",
                    help="Use the browser (saved login) instead of the HTTP endpoint.")
    ap.add_argument("--sleep-min", type=float, default=2.0)
    ap.add_argument("--sleep-max", type=float, default=5.0)
    ap.add_argument("--max-retries", type=int, default=5)
    args = ap.parse_args()

    CSV_DIR.mkdir(parents=True, exist_ok=True)
    cadence = args.cadence or ("weekly" if "weekly" in args.chart else "daily")
    start = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    if start > end:
        log("start-date is after end-date")
        return 2
    dates = weekly_dates(start, end) if cadence == "weekly" else daily_dates(start, end)

    if not args.playwright and not any(
        os.environ.get(k) for k in ("SPOTIFY_CHARTS_BEARER", "SPOTIFY_SP_DC", "SPOTIFY_CHARTS_COOKIE")
    ):
        log("WARNING: no auth env vars set; the HTTP endpoint will likely 401/403. "
            "Set SPOTIFY_CHARTS_BEARER / SPOTIFY_SP_DC, or use --playwright.")

    session = build_session()
    pf: Optional[PlaywrightFetcher] = None
    if args.playwright:
        try:
            pf = PlaywrightFetcher()
        except (PermissionError, RuntimeError) as exc:
            log(f"AUTH/SETUP ERROR: {exc}")
            return 1

    fetched = skipped = failed = 0
    auth_failed = False
    log(f"{cadence} '{args.chart}': {len(dates)} dates {dates[0]} → {dates[-1]}"
        f"{' (browser mode)' if pf else ''}")

    try:
        for i, d in enumerate(dates):
            out = CSV_DIR / f"{args.chart}-{d.isoformat()}.csv"
            if out.exists() and not args.force:
                skipped += 1
                continue
            try:
                content = (pf.fetch(args.chart, d) if pf
                           else fetch_http(session, args.chart, d, args.max_retries))
                out.write_bytes(content)
                fetched += 1
                log(f"[{d}] saved {out.name} ({len(content):,} bytes)")
            except PermissionError as exc:
                log(f"[{d}] AUTH ERROR: {exc}")
                auth_failed = True
                break  # token/login problem won't fix itself; stop early
            except FileNotFoundError as exc:
                log(f"[{d}] {exc}")
                failed += 1
            except Exception as exc:  # noqa: BLE001
                log(f"[{d}] failed: {exc}")
                failed += 1
            if i < len(dates) - 1:
                time.sleep(random.uniform(args.sleep_min, args.sleep_max))
    finally:
        if pf:
            pf.close()

    log(f"Done. fetched={fetched} skipped={skipped} failed={failed}")
    if fetched:
        log("Next: python scripts/import_spotify_csv.py && "
            "python scripts/generate_frontend_data.py")
    return 1 if auth_failed or (fetched == 0 and failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
