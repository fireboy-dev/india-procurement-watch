# Deploy on Cloudflare (free)

**Read this first.** Cloudflare's *free hosting* (Pages / Workers) is **serverless
JavaScript/Wasm** — it can't run a long-lived **Flask** server or open this project's
**multi-GB SQLite** files. So you can't "just upload `app.py`" to Cloudflare.

There are two genuinely-free ways to put this dashboard on Cloudflare. Path A runs the
**whole** app (charts, search, Contract Network, Sector map) and needs no rewrite — start
here.

---

## Path A — Cloudflare Tunnel (recommended · full app · free)

Run the app anywhere (your machine, a free VM/container host) and expose it through
Cloudflare's edge with a [Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).
Zero code changes; everything works.

### Quick tunnel — no account, no domain, 1 command

```bash
pip install -r requirements.txt        # includes gunicorn
brew install cloudflared               # macOS (or see Cloudflare docs for Linux)

# build the data once (if you haven't):
python build_summary.py                # dashboard charts
python build_network.py /path/to/network.duckdb   # Contract Network + Sector map

bash deploy/cloudflare/run-tunnel.sh
```

`cloudflared` prints your public URL in a box, e.g.:

```
+----------------------------------------------------------+
|  Your quick Tunnel has been created! Visit it at:        |
|  https://shareware-provisions-biz-bluetooth.trycloudflare.com  |
+----------------------------------------------------------+
```

Override the port with `PORT=8080 bash deploy/cloudflare/run-tunnel.sh`.

**Stop it:** press `Ctrl-C` in that terminal — the script traps it and kills both the
tunnel and the app. If you started things in the background, stop them with:

```bash
pkill -f "cloudflared tunnel --url"      # stop the tunnel
lsof -ti:5055 | xargs kill               # stop the app (use your port)
```

### Manual alternative (two terminals, no script)

```bash
# terminal 1 — run the app
gunicorn -w 2 --threads 8 -b 127.0.0.1:5055 app:app
#   (or, for a quick local run: python app.py   → serves on :5000)

# terminal 2 — expose it through Cloudflare (free)
cloudflared tunnel --url http://127.0.0.1:5055
#   → prints  https://<random>.trycloudflare.com
```

Verify it's live: `curl -s https://<random>.trycloudflare.com/api/status`.

> Quick-tunnel URLs are **ephemeral** (new URL each run) and require this machine to stay
> running. For a **stable** URL use a named tunnel ↓; to run it always-on, put the
> `Dockerfile` on a free host and point a named tunnel at it.

### Named tunnel — stable custom domain (free account + a domain on Cloudflare)

```bash
cloudflared login
cloudflared tunnel create ipw
cloudflared tunnel route dns ipw procurement.<your-domain>
# edit cloudflared.config.example.yml → ~/.cloudflared/config.yml (fill the UUID)
gunicorn -w 2 --threads 8 -b 127.0.0.1:5055 app:app &   # run the app
cloudflared tunnel run ipw                               # run the tunnel
```

See [`cloudflared.config.example.yml`](cloudflared.config.example.yml).

### Run the app on a free host (so the tunnel always has something to point at)

The tunnel needs the Flask app running somewhere always-on. Containerise it with
[`Dockerfile`](Dockerfile) and deploy to any free tier (Fly.io / Render / Railway / a
free VM), then point a tunnel at it:

```bash
docker build -f deploy/cloudflare/Dockerfile -t ipw .
docker run -p 5055:5055 -v "$PWD:/app/data:ro" ipw
```

Mount the SQLite files at runtime (they're large — don't bake them into the image).
For the Contract Network feature, ship `network.db` (~130 MB) alongside; the giant
`aoc_tenders.db` / `tenders_vps.db` are only needed for full-text search + tender-detail
popups, so you can omit them on a small host (those features degrade gracefully).

---

## Path B — Fully serverless on Cloudflare (Pages + Workers + D1)

If you want it *hosted on Cloudflare itself* (no always-on server), you have to swap the
runtime: **static frontend on Pages**, and the read-only API rewritten as **Pages
Functions (Workers)** backed by **D1** (Cloudflare's SQLite). This is a real project, not
a config change. Sketch:

1. **Frontend → Pages.** The `frontend/` dir is already static; publish it:
   ```bash
   npx wrangler pages deploy frontend --project-name ipw
   ```
2. **API → Pages Functions.** Port each `/api/*` route from `app.py` to a function under
   `functions/api/…` that queries `env.DB` (D1) instead of `sqlite3`.
3. **Data → D1.** Import the precomputed tables:
   ```bash
   npx wrangler d1 create ipw
   # dump summary.db / network.db tables to SQL, then:
   npx wrangler d1 execute ipw --file=summary_d1.sql
   ```

**Free-tier limits that bite here:** D1 free allows **~100k row writes/day** and **5 GB**
storage. `summary.db` fits easily, but the Contract Network data (~600k node+edge rows)
exceeds the daily write cap for a one-shot import, and full-text `search.db` /
`aoc_tenders.db` are far too big. So a free Pages+D1 build realistically covers the
**charts/anomalies dashboard**, while the **Contract Network / Sector map** stay on Path A.

A clean compromise: **frontend on Pages**, **API via a Pages Functions proxy** to a
Tunnel-exposed backend (Path A) — CDN-fast static assets, full Python API, all free:

```js
// functions/api/[[path]].js  — proxy /api/* to your tunnel backend
export async function onRequest(context) {
  const url = new URL(context.request.url);
  const backend = context.env.BACKEND_URL;           // e.g. https://procurement.<domain>
  return fetch(backend + url.pathname + url.search, context.request);
}
```

---

## TL;DR

| Want | Use |
|---|---|
| The whole app live for free, fastest | **Path A quick tunnel** (`run-tunnel.sh`) |
| Stable URL, full app | **Path A named tunnel** + `Dockerfile` on a free host |
| Hosted entirely on Cloudflare, charts only | **Path B** (Pages + Functions + D1) |
| Best of both | Pages (static) + Functions **proxy** → Tunnel backend |
