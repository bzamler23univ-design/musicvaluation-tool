# 🎵 Music Valuation Tool

A full-stack tool for exploring Spotify Global 200 chart data and modelling the
value of a music catalog.

- **Backend / data pipeline** (`scripts/`) — Python scrapers that collect chart
  data, plus generators that turn it into static JSON for the web app.
- **Frontend** (`frontend/`) — a React + Vite + TypeScript single-page app
  (dark, finance/music-tech aesthetic) with four pages: **Dashboard**,
  **Song Search**, **Valuation Prototype**, and **Data Health**.

The frontend reads **only static JSON** (`frontend/public/data/`). There is no
runtime server and **no secrets ever reach the browser** — auth tokens/cookies
are used only by the Python scrapers.

```
musicvaluation-tool/
├── scripts/
│   ├── scrape_kworb.py                # scraper: kworb.net (no login)
│   ├── scrape_spotify_global_200.py   # scraper: charts.spotify.com (login)
│   ├── make_sample_data.py            # synthesize demo data (no network)
│   └── generate_frontend_data.py      # data/processed/* -> frontend JSON
├── data/{raw,processed}/   logs/      # pipeline I/O (gitignored)
├── frontend/                          # React + Vite + TS app
│   ├── public/data/                   # generated JSON (gitignored)
│   └── src/{pages,components,lib}/
├── .github/workflows/                 # CI + GitHub Pages deploy
└── .env.example
```

## Quick start (full stack, with demo data)

No scraping needed — synthesize sample data and run the app:

```bash
# 1. Python deps + sample data
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/make_sample_data.py          # writes data/processed/*
python scripts/generate_frontend_data.py    # writes frontend/public/data/*.json

# 2. Frontend
cd frontend
npm install
npm run dev                                  # http://localhost:5173
```

To use **real** data instead, run a scraper (see Parts A/B below) before
`generate_frontend_data.py` — it auto-detects the Spotify or kworb master in
`data/processed/`.

## Generating frontend data

`scripts/generate_frontend_data.py` reads whichever master exists in
`data/processed/` (`spotify_global_200_daily.*` or `kworb_global_daily.*`) and
writes:

| file | purpose |
|------|---------|
| `summary.json` | dashboard stats (totals, date range, gaps) |
| `top_songs.json` | top songs by cumulative streams |
| `song_index.json` | lightweight search index |
| `songs/<id>.json` | per-song daily stream/rank history |
| `data_health.json` | missing/partial dates + scraper summary |
| `songs_alltime.json` / `artists_alltime.json` | all-time lists for the Charts page |

It auto-detects the data source, preferring (1) imported Spotify **weekly**
data, then (2) Spotify **daily**, then (3) kworb. The chart **cadence** (weekly
vs daily) is detected and drives gap detection and the valuation's annualization.

## Importing official Spotify chart CSVs (real history)

The live scraper only accumulates data going forward. To load **history**, use
the CSV exports from [charts.spotify.com](https://charts.spotify.com) (the
"Download data as CSV" button — pick *Weekly* and a date, weekly is easiest):

```bash
# 1. Put the downloaded CSV(s) in data/spotify_csv/ (tracked in git), then:
python scripts/import_spotify_csv.py            # imports data/spotify_csv/*.csv
python scripts/generate_frontend_data.py        # rebuild the site data
```

- The chart date is read from the filename's trailing `YYYYMMDD`
  (e.g. `...weekly20260618.csv` → 2026-06-18); override with `--date`.
- Weekly vs daily is read from the filename; override with `--cadence`.
- Each week is deduped on `(chart_date, rank)`, so re-importing is safe.

**Via GitHub (no local setup):** upload a CSV into `data/spotify_csv/` using
GitHub's *Add file → Upload files*. The deploy workflow imports it and rebuilds
the site automatically. Download one CSV per week to grow the history.

## Frontend pages

- **Dashboard** — total songs, date range, chart days, data gaps, top songs.
- **Song Search** — search by track / artist / Spotify ID; charts for daily
  streams, rank over time, and cumulative streams; peak rank and days charted.
- **Valuation Prototype** — pick a song, set assumptions (royalty/stream,
  ownership %, master/publishing share, annual decay, discount rate, terminal
  multiple, horizon) → historical gross, owner net, projected revenue,
  discounted present value, implied catalog value, plus a sensitivity grid.
  *(Illustrative DCF model, not investment advice.)*
- **Data Health** — missing dates, partial (<200 row) dates, scraper summary,
  last-updated timestamp.

## Build, lint & deploy

```bash
cd frontend
npm run lint        # eslint
npm run build       # typecheck (tsc) + vite production build -> frontend/dist
npm run preview     # serve the production build locally
```

**GitHub Pages:** the `Deploy to GitHub Pages` workflow builds with sample data
and publishes `frontend/dist`. Enable it in **Settings → Pages → Source: GitHub
Actions**, then push to `main`. The Vite `base` is `./` (relative), so it works
under a project sub-path. Routing uses a hash router, so deep links survive
refreshes on static hosts.

**Vercel:** set **Root Directory** to `frontend`, build command `npm run build`,
output `dist`. Add a build step (or commit generated JSON) so
`frontend/public/data/` is populated — e.g. run the two Python generator scripts
in a pre-build step.

**CI** (`.github/workflows/ci.yml`) runs on every push/PR: generates sample
data, lints + builds the frontend, and validates the generated JSON. Scraping is
**never** scheduled in CI — to automate real scrapes, add a workflow that
provides your Spotify auth via repository **secrets** (kworb needs none).

---

# Data pipeline (backend)

Two scrapers live in `scripts/`:

| script | source | needs login? | what it gets |
|--------|--------|--------------|--------------|
| **`scrape_kworb.py`** *(recommended)* | [kworb.net](https://kworb.net/spotify/) | **no** | Global Daily Top 200, all-time songs, artists |
| `scrape_spotify_global_200.py` | charts.spotify.com | **yes** | Global Daily Top 200 straight from Spotify |

Spotify shut its open chart CSVs in 2022 and `charts.spotify.com` now requires a
login (see the second half of this README). **kworb.net republishes the same
Spotify numbers as plain, login-free HTML tables**, so `scrape_kworb.py` is the
path of least resistance and is documented first.

> **Hosted-environment note:** both `kworb.net` and `*.spotify.com` are blocked
> by the Claude Code web/remote container's egress allowlist
> (`HTTP 403 host_not_allowed`). Run these locally, or in an environment whose
> network policy allowlists those hosts.

---

# Part A — `scrape_kworb.py` (kworb.net)

Scrapes three datasets into cleaned, validated, **append-only** master files.

| dataset (`--datasets`) | page | output master |
|---------|------|---------------|
| `global-daily` | `country/global_daily.html` | `kworb_global_daily.csv` / `.parquet` |
| `songs` | `songs.html` | `kworb_songs.csv` / `.parquet` |
| `artists` | `artists.html` | `kworb_artists.csv` / `.parquet` |

Plus `data/processed/kworb_scrape_summary.json` (per-dataset stats + validation),
and a raw HTML snapshot per run under `data/raw/kworb/<dataset>/`.

### Daily history is built *forward*

kworb's `global_daily.html` only shows the **single most recent day** — it is
**not** a 2017→today archive. So this scraper **snapshots forward**: each run
captures the current day (its `chart_date` is read from the page's "Last
updated" stamp) and appends it to the master, deduped on `chart_date + pos`.
Run it once per day to accumulate history:

```bash
# cron: 06:30 UTC daily
30 6 * * *  cd /path/to/musicvaluation-tool && /path/to/.venv/bin/python scripts/scrape_kworb.py >> logs/cron.log 2>&1
```

`songs` and `artists` are all-time cumulative lists; they're captured as a time
series keyed by `retrieved_date`, so running daily yields a stream-growth history.

### Install & run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# All three core datasets:
python scripts/scrape_kworb.py --datasets global-daily,songs,artists --verbose

# Just the daily Top 200:
python scripts/scrape_kworb.py --datasets global-daily

# Per-country daily Top 200 (writes kworb_country_<cc>.csv):
python scripts/scrape_kworb.py --countries us,gb,de

# A specific artist's full song + album catalog (kworb_artist_<id>_songs/_albums.csv):
python scripts/scrape_kworb.py --artists 3TVXtAsR1Inumwj472S9r4   # Drake
```

| flag | default | meaning |
|------|---------|---------|
| `--datasets` | `global-daily,songs,artists` | named datasets to scrape |
| `--countries` | _(none)_ | ISO codes for per-country daily charts, e.g. `us,gb,de` |
| `--artists` | _(none)_ | Spotify artist IDs → full song + album catalogs |
| `--force` | off | overwrite today's raw HTML snapshot if it exists |
| `--sleep-min` / `--sleep-max` | `1.5` / `4.0` | randomized delay between page fetches |
| `--max-retries` | `5` | retries per request (exponential backoff + jitter) |
| `--verbose` | off | verbose console logging |

The website's **Charts** page shows the all-time **Songs** and **Artists** lists
(from `songs.html` / `artists.html`); if those haven't been scraped it falls
back to lists derived from the daily chart. To have the daily GitHub Action also
pull per-country charts, add `--countries ...` to the scrape step in
`.github/workflows/daily-update.yml`.

### Cleaning, schema & validation

Parsing is **generic**: every column in the HTML table is captured under a
normalized `snake_case` name (known kworb headers like `Pos`, `Artist and
Title`, `Streams+`, `Total` get friendly names), the combined "Artist and
Title" cell is split into `artist`/`title`, and Spotify **track/artist IDs are
extracted from the in-cell links** (with canonical `open.spotify.com` URLs
derived). This survives kworb adding or renaming a column.

`global-daily` master columns:
`chart_date, pos, pos_change, artist_and_title, days_on_chart, peak,
peak_count, streams, streams_change, total_streams, spotify_track_id,
spotify_artist_id, artist, title, spotify_track_url, spotify_artist_url,
source_url, retrieved_at`.

Validation (in `kworb_scrape_summary.json`): no duplicate key rows, `pos`
within 1–200, numeric streams, dates with fewer than 200 rows, and missing-ID
counts.

### kworb limitations

- **No native daily history** — you only get days you scrape from the day you
  start (forward-fill). For past dates you'd need an archive (e.g. Wayback) —
  not implemented here by choice.
- **kworb is itself a third party** republishing Spotify data; numbers can lag
  or differ slightly from Spotify's own.
- **Column drift** — handled generically, but if kworb changes the page layout
  the friendly-name mapping may need a tweak (`HEADER_ALIASES` in the script).
- Be polite: keep the randomized delays on and don't hammer the site.

---

# Part B — `scrape_spotify_global_200.py` (charts.spotify.com)

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
python scripts/scrape_spotify_global_200.py --playwright-login
# A browser opens -> log into Spotify -> press Enter -> session saved to playwright_state.json
```

## 3. Run a small test scrape (7 days)

```bash
python scripts/scrape_spotify_global_200.py \
  --start-date 2023-01-01 --end-date 2023-01-07 --verbose
```

## 4. Run the full scrape

```bash
# Find the true earliest reachable date, then scrape through today:
python scripts/scrape_spotify_global_200.py --detect-earliest \
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

These tools are for **personal, lawful** data collection. For `scrape_kworb.py`,
respect [kworb.net](https://kworb.net)'s terms and `robots.txt`, keep the
randomized delays on, and don't hammer the site. For the Spotify scraper:

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
