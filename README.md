# Spotify Global Daily Top 200 — Scraper / Data Pipeline

Downloads the Spotify **Global – Daily Top Songs (Top 200)** chart for every
day in a date range, stores each day's raw CSV, and builds one cleaned,
validated master dataset (CSV + Parquet).

```
chart_date | rank | track_name | artist_names | streams | spotify_url | spotify_track_id | source_url | scraped_at
```

---

## ⚠️ Read this first — how Spotify chart access actually works today

A lot of older tutorials and packages (e.g. `fycharts`) point at
`https://spotifycharts.com/regional/global/daily/<date>/download` and claim it
returns an open CSV with no login. **That is no longer true.**

- The legacy `spotifycharts.com` site was **shut down by Spotify in early
  2022**. That endpoint no longer serves chart CSVs.
- It was replaced by **`charts.spotify.com`**, which **requires you to be
  logged into a Spotify account** to view charts and download the CSV. There
  is **no public, unauthenticated chart API**.

Because of that, this tool is built around **your own legitimate logged-in
session**. It will **never** try to defeat the login page, solve a CAPTCHA, or
otherwise bypass an anti-bot control. You provide the credentials/cookies for
an account you are entitled to use, and the tool acts as that session — exactly
as the Spotify Terms of Service require.

There is no guarantee daily charts go all the way back to 2017-01-01 on the
current site; the legacy archive may not be fully exposed. Use
`--detect-earliest` to find the true earliest date your session can reach
(requirement: "Validate the true earliest available date programmatically").

> **Note about this hosted environment:** if you are running inside the
> Claude Code web/remote container, outbound traffic to `*.spotify.com` and
> `spotifycharts.com` is blocked by the environment's network egress allowlist
> (`HTTP 403 host_not_allowed`). The pipeline therefore **cannot reach Spotify
> from that container** — run it locally, or in an environment whose network
> policy allowlists the Spotify hosts.

---

## Project layout

```
scrape_spotify_global_200.py   # the pipeline
requirements.txt
README.md
data/raw/                      # one raw CSV per day: global_daily_YYYY-MM-DD.csv
data/processed/                # cleaned master + reports
logs/                          # timestamped run logs
```

Outputs written to `data/processed/`:

| file | contents |
|------|----------|
| `spotify_global_200_daily.csv`     | cleaned master, all days, sorted by date+rank |
| `spotify_global_200_daily.parquet` | same data, Parquet |
| `missing_dates.csv`                | dates that failed (with the reason) |
| `scrape_summary.json`              | run stats + the full validation report |

---

## 1. Install dependencies

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Only needed if you intend to use the Playwright fallback:
playwright install chromium
```

## 2. Provide authentication (your own account)

The HTTP `csv-api` strategy sends your session credentials with each request.
Set whichever you have (a logged-in Spotify session):

```bash
# A bearer/access token captured from your own logged-in charts.spotify.com session:
export SPOTIFY_CHARTS_BEARER="eyJ..."

# and/or the sp_dc cookie from your browser:
export SPOTIFY_SP_DC="AQ..."

# (advanced) or a full raw Cookie header:
export SPOTIFY_CHARTS_COOKIE="sp_dc=...; sp_t=..."
```

> The exact authenticated download endpoint on `charts.spotify.com` is
> undocumented and changes periodically. If yours differs, override it without
> editing code:
> ```bash
> export SPOTIFY_CHARTS_DOWNLOAD_URL="https://charts.spotify.com/.../{date}/download"
> ```

**Or** skip tokens entirely and use the browser fallback. Log in once:

```bash
python scrape_spotify_global_200.py --playwright-login
# A browser opens -> log into Spotify -> press Enter -> session saved to playwright_state.json
```

## 3. Run a small test scrape (7 days)

```bash
python scrape_spotify_global_200.py \
  --start-date 2023-01-01 --end-date 2023-01-07 --verbose
```

## 4. Run the full scrape

```bash
# Find the true earliest reachable date, then scrape through today:
python scrape_spotify_global_200.py --detect-earliest \
  --start-date 2017-01-01 --end-date $(date +%F)
```

This is resumable: re-running skips any date whose raw CSV already exists.
Use `--force` to refetch.

---

## CLI reference

| flag | default | meaning |
|------|---------|---------|
| `--start-date` | `2017-01-01` | inclusive ISO start date |
| `--end-date` | today | inclusive ISO end date |
| `--force` | off | refetch even if the raw CSV already exists |
| `--sleep-min` | `1.5` | min seconds of randomized delay between requests |
| `--sleep-max` | `4.0` | max seconds of randomized delay between requests |
| `--max-retries` | `5` | retries per request (exponential backoff + jitter) |
| `--strategies` | `csv-api,playwright` | fetch strategies in priority order (`csv-api`, `legacy`, `playwright`) |
| `--detect-earliest` | off | probe forward to find the earliest valid date, then start there |
| `--playwright-login` | — | open a browser to log in and save the session, then exit |
| `--verbose` | off | verbose console logging |

### Fetch strategies (tried in the order you list them)

1. **`csv-api`** — authenticated HTTP GET to the chart CSV download URL using
   your token/cookie. Fast. Fails fast (no hammering) on `401/403`.
2. **`legacy`** — the deprecated `spotifycharts.com` endpoint. Included only so
   its failure is logged explicitly; it is essentially always dead now.
3. **`playwright`** — drives a real headless browser using your saved login
   session and clicks the page's own **Download** button. Slower but resilient
   to endpoint changes.

---

## Data cleaning & validation

The raw CSV (new `charts.spotify.com` export *or* the old `spotifycharts.com`
two-line-header format) is normalized into the clean schema. The Spotify track
ID is extracted from the `uri`/`url` column and a canonical
`https://open.spotify.com/track/<id>` URL is built.

Validation (written to `scrape_summary.json`):

- `rank` is within **1–200**;
- `streams` is **numeric**;
- **no duplicate** `chart_date + rank`;
- **no duplicate** `chart_date + spotify_track_id + rank`;
- every date with **fewer than 200 rows** is reported;
- the **earliest available date** actually reached is recorded.

---

## Known limitations

- **Auth required.** Without a valid logged-in session, `csv-api` returns
  `401/403` and only the `playwright` strategy (with a saved login) can work.
- **Undocumented endpoint.** The authenticated CSV URL is not published and may
  change; override it via `SPOTIFY_CHARTS_DOWNLOAD_URL` or use `playwright`.
- **Historical depth is not guaranteed.** Whether daily charts reach back to
  2017-01-01 depends on what your session can fetch; `--detect-earliest`
  reports the real floor.
- **Selectors may drift.** The Playwright download-button selectors may need
  updating if Spotify redesigns the page.
- **`parquet` needs `pyarrow`** (in `requirements.txt`). If it's missing, the
  CSV is still written and a warning is logged.

---

## Legal & ethical note

This tool is for **personal, lawful** data collection.

- **Obey Spotify's Terms of Service** and `robots.txt`. You are responsible for
  how you use it.
- **Use your own account.** Do not share or scrape with credentials you are not
  entitled to use.
- **Do not bypass authentication or anti-bot controls.** This tool deliberately
  does not attempt to. It fails fast on `401/403` rather than hammering, and the
  browser fallback uses *your* interactive login — it does not solve CAPTCHAs or
  evade bot detection.
- **Rate-limit yourself.** Randomized delays (`--sleep-min/--sleep-max`) and
  capped exponential backoff are on by default; keep them reasonable.
- **Respect data rights.** Chart data and the underlying catalog are Spotify's;
  redistribute only as permitted.
