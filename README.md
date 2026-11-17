# AMARYT Catalog API

Independent GitHub-only static catalog API for AMARYT STORE. It does not change Feather or Mstoda.

## How it works

GitHub Actions downloads and streams AltStore-compatible sources into a temporary SQLite database. The builder then publishes JSON cards in pages of 50 applications. Full descriptions and IPA download URLs live in separate on-demand detail pages. GitHub Pages serves these prebuilt files.

## Setup

1. Open repository Settings → Pages and set Build and deployment Source to GitHub Actions.
2. Open Actions → Build and publish catalog → Run workflow.
3. Confirm both build and deploy jobs finish successfully.
4. Open https://amaryt1.github.io/AMARYT-Catalog-API/api/manifest.json

## Static API (schema version 2)

- api/manifest.json — totalApps, pageSize, pageCount and source counts. This stays small even for very large catalogs.
- api/home.json — initial 50 app summaries.
- api/pages/000000.json — paginated card summaries.
- api/details/000000.json — full metadata for the corresponding cards, including IPA URL.
- api/search/index.json — static name-prefix lookup map.
- api/search/<hex-prefix>/000000.json — paginated search candidates. This is not a dynamic full-text search service.

Use a card's detailPath and id to fetch exactly the detail item the user selected. The app should request only the first page, lazy-load subsequent pages on scroll, and cache the first page locally for fast reopening.

## Add sources

Add a unique lowercase id, HTTPS url and enabled=true in sources.json. This project starts with AppTesters. Each source must contain a top-level apps array in the AltStore JSON format. The workflow refreshes every six hours or on demand; a failed build does not replace a previously deployed site.

## Tests

Python 3.12+ and standard library only. Run:

    python -m unittest discover -s tests -v
    python scripts/build.py
    python scripts/validate.py

## Limitations

- This GitHub-only solution publishes a static JSON API; it cannot run arbitrary server-side SQL/search requests.
- First opening without any cache still requires network access, so literally immediate first-open display is not guaranteed.
- To fix the original iOS delay, Feather needs a separate paginated catalog provider; merely pointing the existing full-feed parser at this API is insufficient.
- GitHub Pages size/bandwidth limits mean very large catalogs or heavy search will eventually require a dedicated object storage/CDN and search API.
- Performance in Feather has not been measured yet. This repository is a backend prototype.

Reference: https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits