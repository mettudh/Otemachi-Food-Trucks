# Otemachi-Food-Trucks

A single-page site listing the closest kitchen-car (food truck) spots to
Urbannet Ōtemachi, with vendors for today and the next four weekdays.
`index.html` rebuilds itself automatically once a day via GitHub Actions,
scraping three source sites directly (no API keys needed).

## Set it up (5 minutes)

1. Create a new **public** GitHub repo and push everything in this folder to it
   (`index.html`, `requirements.txt`, `scripts/`, `.github/`).
2. In the repo, go to **Settings → Pages** → set Source to
   `Deploy from a branch`, branch `main`, folder `/ (root)`. Save.
3. Go to **Settings → Actions → General → Workflow permissions** and select
   **"Read and write permissions"**, then Save. (This lets the daily job
   commit the rebuilt page back to the repo.)
4. That's it — your site is live at `https://<your-username>.github.io/<repo-name>/`.

## How the daily update works

`.github/workflows/daily-rebuild.yml` runs every day at 07:30 JST (before
lunch), executes `scripts/scraper.py`, and commits the new `index.html` if
anything changed. GitHub Pages then serves the latest committed version
automatically — nothing else to do.

You can also trigger it manually any time: go to the **Actions** tab →
**Daily rebuild** → **Run workflow**.

## If a source site changes its page layout

This scrapes three sites that don't offer a public API, so it's inherently
a bit fragile — if Mellow, the Sankei Building, or Kawabata Food Garden
redesign their pages, part of the scrape may silently return less data
(the script is written to degrade gracefully rather than crash: a broken
field just falls back to a placeholder, or a location shows "no data" for
that run).

If you notice the live site looking wrong or stale:
1. Check the **Actions** tab for the most recent "Daily rebuild" run — the
   logs (`[warn]` / `[info]` lines) usually point at which source failed.
2. Paste those log lines back to Claude along with the source URL that
   broke, and it can update the parsing logic in `scripts/scraper.py`.

## Files

- `index.html` — the live site (this file is overwritten by the daily job;
  don't hand-edit it, edit `scripts/scraper.py` instead)
- `scripts/scraper.py` — fetches the three sources and regenerates `index.html`
- `.github/workflows/daily-rebuild.yml` — the daily cron job
- `requirements.txt` — Python deps for the scrape