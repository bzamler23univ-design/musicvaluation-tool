#!/usr/bin/env python3
"""Replay a charts.spotify.com "Copy as cURL" request across a date range.

Why: the chart CSV download is gated behind your login, and the exact endpoint
/ auth scheme is undocumented. Rather than reverse-engineer it, you capture ONE
working request straight from your browser ("Copy as cURL"), and this script
reuses its URL + headers + cookies, swapping the date for each week.

YOUR TOKEN STAYS LOCAL — it lives only in the cURL file on your machine.

HOW TO USE
----------
1. Chrome → charts.spotify.com → a Weekly · Global chart, logged in.
2. DevTools (Option+Cmd+I) → Network tab.
3. Click the chart's "Download data as CSV" arrow.
4. Right-click the new request row → Copy → "Copy as cURL".
5. In Terminal (in the project folder):  pbpaste > request.txt
6. Run:  python3 scripts/fetch_from_curl.py
   (defaults: weekly, 2016-12-29 → 2026-06-18; override with flags)

It saves one CSV per week into data/spotify_csv/, then you commit them.
Tokens expire (~1h); if it stops with AUTH ERROR, recapture (steps 3-5) and
re-run — it skips weeks already downloaded.
"""
from __future__ import annotations

import argparse
import re
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_spotify_csv import (  # noqa: E402  (local helper module)
    CSV_DIR, backoff, daily_dates, log, looks_like_csv, weekly_dates,
)

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ParsedCurl:
    def __init__(self, url: str, method: str, headers: dict, data: str | None):
        self.url, self.method, self.headers, self.data = url, method, headers, data


def parse_curl(text: str) -> ParsedCurl:
    # Join line continuations, then shell-split (handles the single quotes
    # Chrome uses on macOS/Linux "Copy as cURL").
    text = text.replace("\\\n", " ").replace("^\n", " ").strip()
    if text.lower().startswith("curl "):
        tokens = shlex.split(text, posix=True)[1:]
    else:
        tokens = shlex.split(text, posix=True)

    url = None
    method = "GET"
    headers: dict[str, str] = {}
    data = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("-X", "--request"):
            method = tokens[i + 1]; i += 2; continue
        if t in ("-H", "--header"):
            h = tokens[i + 1]; i += 2
            if ":" in h:
                k, v = h.split(":", 1)
                headers[k.strip()] = v.strip()
            continue
        if t in ("-b", "--cookie"):
            headers["Cookie"] = tokens[i + 1]; i += 2; continue
        if t in ("--data", "--data-raw", "--data-binary", "-d"):
            data = tokens[i + 1]; method = "POST"; i += 2; continue
        if t in ("--compressed", "--location", "-L", "-s", "-S", "-k", "-i"):
            i += 1; continue
        if t.startswith("http"):
            url = t; i += 1; continue
        i += 1  # ignore anything else

    if not url:
        raise SystemExit(
            "Could not find a URL in the cURL. Make sure you used "
            "'Copy as cURL' on the download request and saved it to the file."
        )
    return ParsedCurl(url, method, headers, data)


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay a captured cURL across many chart dates.")
    ap.add_argument("--curl-file", default="request.txt", help="File with the copied cURL.")
    ap.add_argument("--start-date", default="2016-12-29")
    ap.add_argument("--end-date", default="2026-06-18")
    ap.add_argument("--cadence", choices=["weekly", "daily"], default="weekly")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sleep-min", type=float, default=2.0)
    ap.add_argument("--sleep-max", type=float, default=5.0)
    ap.add_argument("--max-retries", type=int, default=4)
    args = ap.parse_args()

    cf = Path(args.curl_file)
    if not cf.exists():
        raise SystemExit(f"{cf} not found. Capture it with: pbpaste > {cf}")
    parsed = parse_curl(cf.read_text())

    m = DATE_RE.search(parsed.url)
    if not m:
        raise SystemExit(
            "No YYYY-MM-DD date found in the captured URL — did you copy the "
            f"download request? URL was:\n  {parsed.url}"
        )
    url_template = parsed.url[: m.start()] + "{date}" + parsed.url[m.end():]
    log(f"endpoint: {url_template}")
    # The auth header in the cURL is what makes this work; warn if absent.
    if not any(k.lower() == "authorization" for k in parsed.headers) and "Cookie" not in parsed.headers:
        log("WARNING: no authorization/cookie header found in the cURL — it may not authenticate.")

    CSV_DIR.mkdir(parents=True, exist_ok=True)
    start = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    dates = weekly_dates(start, end) if args.cadence == "weekly" else daily_dates(start, end)
    log(f"{args.cadence}: {len(dates)} dates {dates[0]} → {dates[-1]}")

    # Derive a filename prefix (chart slug) from the URL if possible.
    slug_m = re.search(r"(regional-[a-z0-9-]+?-(?:weekly|daily))", parsed.url)
    slug = slug_m.group(1) if slug_m else "regional-global-weekly"

    import random
    fetched = skipped = failed = 0
    for i, d in enumerate(dates):
        out = CSV_DIR / f"{slug}-{d.isoformat()}.csv"
        if out.exists() and not args.force:
            skipped += 1
            continue
        url = url_template.format(date=d.isoformat())
        ok = False
        for attempt in range(1, args.max_retries + 1):
            try:
                r = requests.request(parsed.method, url, headers=parsed.headers,
                                     data=parsed.data, timeout=45)
            except requests.RequestException as exc:
                log(f"[{d}] network error: {exc}"); time.sleep(backoff(attempt)); continue
            if r.status_code == 200 and looks_like_csv(r.content):
                out.write_bytes(r.content); fetched += 1; ok = True
                log(f"[{d}] saved {out.name} ({len(r.content):,} bytes)"); break
            if r.status_code in (401, 403):
                log(f"[{d}] AUTH ERROR (HTTP {r.status_code}) — your token expired. "
                    f"Recapture the cURL (pbpaste > {cf}) and re-run; it resumes.")
                log(f"Done. fetched={fetched} skipped={skipped} failed={failed}")
                return 1
            if r.status_code == 404:
                log(f"[{d}] HTTP 404 (no chart / before earliest)"); failed += 1; break
            if r.status_code == 429 or 500 <= r.status_code < 600:
                time.sleep(backoff(attempt, r.headers.get("Retry-After"))); continue
            log(f"[{d}] HTTP {r.status_code}"); failed += 1; break
        if not ok and r.status_code == 200:
            log(f"[{d}] got 200 but not CSV (login page?) — recapture the cURL."); failed += 1
        if i < len(dates) - 1:
            time.sleep(random.uniform(args.sleep_min, args.sleep_max))

    log(f"Done. fetched={fetched} skipped={skipped} failed={failed}")
    if fetched:
        log("Next: python3 scripts/import_spotify_csv.py && "
            "python3 scripts/generate_frontend_data.py  (or just commit the CSVs)")
    return 0 if fetched else 1


if __name__ == "__main__":
    raise SystemExit(main())
