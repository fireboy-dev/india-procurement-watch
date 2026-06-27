# 🇮🇳 India Procurement Watch Dashboard

A fast, interactive dashboard for digging through India's public procurement data. 

I built this to take massive, gigabyte-sized SQLite data dumps from government e-procurement portals and turn them into something you can actually explore in your browser without your computer catching on fire.

**Perfect for data journalists, researchers, or anyone curious about where government money goes.**

---

## Where's the data from?

This project was built to analyze data scraped by [Sarthak Sidhant's India Procurement Watch](https://tender.sarthaksidhant.com). The dataset covers millions of Award of Contract (AOC) notices and published tenders from central and state portals.

---

## What's in the box?

- **Big numbers:** Total contracts, total ₹ value, unique orgs.
- **Trends:** Yearly and monthly spending charts.
- **Top spenders:** See which organizations award the most contracts.
- **Red flags (Anomalies):** 
  - Contracts awarded at suspiciously round numbers (e.g. exactly ₹10,000,000)
  - Super fast awards (awarded within a day of bidding closing)
  - Massive state contracts over ₹100 Crore.
- **Instant search:** Full-text search across ~5 million tender titles and orgs.
- **Contract Network:** Link awarded contracts to the **MCA/ROC company registry** and explore the graph — search any company or government buyer to see who awards whom, consortium (co-bidder) ties, and firms that share a registered email/address. ([details](#-contract-network-vendor--buyer-graph))

---

## Try it out right now (with fake data)

Don't have the 12GB data dump? No worries. I wrote a script that generates a bunch of synthetic data so you can test drive the dashboard immediately.

```bash
git clone https://github.com/Eren-Jaeger-DEV/India-Procurement-Watch.git
cd India-Procurement-Watch

pip install -r requirements.txt

# Create 5,000 fake records
python create_sample_data.py

# Crunch the numbers
python build_summary.py

# Build the search engine
python build_search_index.py

# Fire up the dashboard
python app.py
```
Now just open **http://localhost:5000** in your browser.

---

## Plugging in the real data

Got the real SQLite data dump? Awesome. 
Check out the **[Data Guide](DATA_GUIDE.md)** for a step-by-step walkthrough on how to hook it up.

If you're building your own scraper, the dashboard expects two SQLite databases in the project folder:

### `aoc_tenders.db` (Awarded Contracts)
Need tables:
- `aoc_tenders`: `internal_id TEXT PK`, `tender_id TEXT`, `org_name TEXT`, `title TEXT`, `year INTEGER`, `portal_type TEXT`, `tender_type TEXT`, `aoc_date TEXT`, `closing_date TEXT`
- `aoc_details`: `internal_id TEXT PK`, `details_json TEXT`

### `tenders_vps.db` (Published Tenders)
Need tables:
- `tenders`: `tender_id TEXT PK`, `org_name TEXT`, `title TEXT`, `portal_type TEXT`, `tender_type TEXT`, `e_published_date TEXT`, `tender_value TEXT`
- `tender_details`: `tender_id TEXT PK`, `details_json TEXT`

*(Pro tip: If you only have `aoc_tenders.db`, that's fine. The dashboard handles missing data gracefully.)*

---

## 🕸️ Contract Network (vendor ⇄ buyer graph)

Beyond aggregates, this tab maps the *relationships* behind the money. It links each
award to the **MCA/ROC company registry** (the awards carry a company *name* but no
registration number) and lets you explore the resulting graph:

- **Search** any company or government buyer.
- See its **ego-network**: contracts won, the buyers awarding them, **co-bidders**
  (consortia), and companies that **share a registered email / address**.
- **Click any node** to recenter on it; the side panel shows that company's registry
  card (CIN, status, state, email, address, RoC).

The graph is served from a small precomputed `network.db` — same precompute → tiny
SQLite → serve pattern as `summary.db`. The heavy entity-resolution (matching ~445k
bidder names to ~2M registry companies with Splink) lives in the companion
**india-procurement-network** pipeline, which emits a `network.duckdb`. `build_network.py`
packages that into `network.db`.

**Enable it:**
```bash
pip install duckdb                              # build-time only (not needed to run the app)
python build_network.py /path/to/network.duckdb # → network.db (~130 MB)
python app.py
```
If `network.db` is absent, the tab shows a friendly "run build_network.py" note and the
rest of the dashboard works exactly as before. Tip: `/?focus=<node_id>` deep-links
straight to a node's network.

## How it works under the hood

Loading 12GB of raw SQL data on every page load would be terrible. Instead, we do the heavy lifting once:

1. `build_summary.py` scans the giant databases and spits out a tiny `summary.db` (~50 MB).
2. `build_search_index.py` builds an optimized SQLite FTS5 index into `search.db`.
3. The Flask app (`app.py`) only ever reads from the small `summary.db` and the optimized search index.

Result? Every chart loads instantly, no matter how big the source data gets.

---

## What are all these files?

- **`create_sample_data.py`** — Spits out fake data for testing.
- **`build_summary.py`** — The number cruncher. Run this when you get new data.
- **`build_network.py`** — Builds the Contract Network graph DB (`network.db`) from the linkage pipeline's `network.duckdb`.
- **`build_search_index.py`** — Builds the search engine.
- **`optimize_fts.py`** — Makes the search engine faster.
- **`app.py`** — The web server.
- **`frontend/`** — All the HTML, CSS, and JS for the dashboard.

---

## Built with

- **Backend:** Python, Flask, SQLite (with FTS5 for search)
- **Frontend:** Vanilla HTML/CSS/JS, Chart.js for graphs, vis-network for the contract graph (both via CDN)
- No Node.js, no crazy build steps, no bloat.

---

## Deploy it (free)

Cloudflare's free hosting is serverless and can't run Flask + multi-GB SQLite directly,
so the simplest free deploy is a **Cloudflare Tunnel** — one command gives a public URL
serving the whole app (charts, search, Contract Network, Sector map), no rewrite:

```bash
pip install -r requirements.txt          # includes gunicorn
brew install cloudflared                 # or the Linux build
bash deploy/cloudflare/run-tunnel.sh     # → https://<random>.trycloudflare.com
```

Named tunnels (stable custom domain), a `Dockerfile` for free container hosts, and a
fully-serverless **Pages + Workers + D1** sketch are in
[`deploy/cloudflare/`](deploy/cloudflare/README.md).

## Want to contribute?

Feel free to open a PR! Just please keep it dependency-light. The goal is to keep this thing easy to run for anyone without a complex setup.
