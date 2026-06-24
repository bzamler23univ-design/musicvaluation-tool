#!/usr/bin/env python3
"""Diagnose the charts.spotify.com 'Download data as CSV' control.

Uses your saved Playwright login to open the weekly chart page and print every
clickable element (and anything mentioning download/csv) so we can see exactly
how to target the real download button. No DevTools needed.

Run:  python3 scripts/inspect_download_button.py
Then paste the output back.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE = os.environ.get("SPOTIFY_PLAYWRIGHT_STATE", str(PROJECT_ROOT / "playwright_state.json"))
URL = "https://charts.spotify.com/charts/view/regional-global-weekly/2026-06-18"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("playwright not installed (pip3 install playwright)")

if not Path(STATE).exists():
    sys.exit(f"No saved login at {STATE}. Run --playwright-login first.")

DUMP_CLICKABLE = """() => {
  const out = [];
  document.querySelectorAll('button, a, [role=button]').forEach((e, i) => {
    out.push({
      i, tag: e.tagName,
      aria: e.getAttribute('aria-label'),
      title: e.getAttribute('title'),
      testid: e.getAttribute('data-testid'),
      cls: (e.className || '').toString().slice(0, 90),
      txt: (e.innerText || '').trim().slice(0, 40),
      download: e.hasAttribute('download'),
    });
  });
  return out;
}"""

SCAN_DOWNLOAD = """() => {
  const res = [];
  document.querySelectorAll('*').forEach(e => {
    const a = (e.getAttribute && (e.getAttribute('aria-label') || '')) || '';
    const t = (e.getAttribute && (e.getAttribute('title') || '')) || '';
    const d = (e.getAttribute && (e.getAttribute('data-testid') || '')) || '';
    const c = (e.className || '').toString();
    if (/download|csv/i.test(a + ' ' + t + ' ' + d + ' ' + c)) {
      res.push(e.outerHTML.slice(0, 280));
    }
  });
  return res.slice(0, 12);
}"""

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(storage_state=STATE, user_agent=UA, accept_downloads=True)
    page = ctx.new_page()
    page.goto(URL, wait_until="networkidle", timeout=60_000)
    page.wait_for_timeout(3000)

    print("=" * 70)
    print("FINAL URL :", page.url)
    print("PAGE TITLE:", page.title())
    if "login" in page.url or "accounts.spotify" in page.url:
        print("\n⚠️  Looks like the saved login did NOT carry — re-run --playwright-login.")
    print("=" * 70)

    print("\nCLICKABLE ELEMENTS (button / a / role=button):")
    for it in page.evaluate(DUMP_CLICKABLE):
        print(it)

    print("\nELEMENTS MENTIONING download/csv (outerHTML):")
    hits = page.evaluate(SCAN_DOWNLOAD)
    if not hits:
        print("  (none found — the control may be an icon with no download/csv text)")
    for h in hits:
        print("  -", h, "\n")

    browser.close()
