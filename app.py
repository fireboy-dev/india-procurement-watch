"""
app.py
======
Flask API backend for the India Procurement Analytics Dashboard.
Serves pre-computed data from summary.db and live search from aoc_tenders.db.
"""

import sqlite3
import json
import os
import threading
from flask import Flask, jsonify, request, send_from_directory, abort
from flask_cors import CORS

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
SUM_DB    = os.path.join(BASE_DIR, "summary.db")
AOC_DB    = os.path.join(BASE_DIR, "aoc_tenders.db")
VPS_DB    = os.path.join(BASE_DIR, "tenders_vps.db")
SEARCH_DB = os.path.join(BASE_DIR, "search.db")
NET_DB    = os.path.join(BASE_DIR, "network.db")   # Contract Network feature (build_network.py)
STATIC_DIR= os.path.join(BASE_DIR, "frontend")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
CORS(app)

# ─────────────────────────────────────────────
# DB HELPERS  —  thread-local persistent connections
# Each worker thread opens the DB once and reuses the connection,
# avoiding the overhead of opening a 1.8 GB file on every request.
# ─────────────────────────────────────────────

_tl = threading.local()

def _get_conn(attr, path, read_only=False):
    """Return a thread-local SQLite connection, opening it once per thread.

    Some endpoints call conn.close() after use. Under a reusing worker-thread pool
    (e.g. gunicorn) that would leave a *closed* connection cached for the next
    request on the same thread, raising "Cannot operate on a closed database". So
    we verify the cached connection is still usable and transparently reopen it.
    """
    conn = getattr(_tl, attr, None)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
        except sqlite3.Error:
            conn = None          # was closed/broken → reopen below
    if conn is None:
        if read_only:
            uri = f"file:{path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            conn.execute("PRAGMA query_only=1")
            conn.execute("PRAGMA cache_size=-32000")  # 32 MB page cache
        else:
            conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        setattr(_tl, attr, conn)
    return conn

def get_sum_conn():
    return _get_conn('sum', SUM_DB)

def get_aoc_conn():
    return _get_conn('aoc', AOC_DB, read_only=True)

def get_search_conn():
    return _get_conn('search', SEARCH_DB, read_only=True)

def get_net_conn():
    return _get_conn('net', NET_DB, read_only=True)

def rows_to_list(cursor_result):
    return [dict(row) for row in cursor_result]

# ─────────────────────────────────────────────
# FRONTEND SERVE
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")

@app.route("/<path:path>")
def static_proxy(path):
    return send_from_directory(STATIC_DIR, path)

# ─────────────────────────────────────────────
# API: KPIs
# ─────────────────────────────────────────────

@app.route("/api/kpis")
def api_kpis():
    if not os.path.exists(SUM_DB):
        return jsonify({"error": "summary.db not found. Run build_summary.py first."}), 503
    conn = get_sum_conn()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM kpi_stats")
    data = {row["key"]: row["value"] for row in cur.fetchall()}
    conn.close()
    return jsonify(data)

# ─────────────────────────────────────────────
# API: TRENDS
# ─────────────────────────────────────────────

MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

@app.route("/api/trends")
def api_trends():
    grain = request.args.get("grain", "monthly")  # monthly | yearly
    dataset = request.args.get("dataset", "aoc")   # aoc | published

    conn = get_sum_conn()
    cur = conn.cursor()

    if dataset == "published":
        if grain == "yearly":
            cur.execute("""
                SELECT year, SUM(count) as count
                FROM published_monthly
                WHERE year BETWEEN 2015 AND 2030
                GROUP BY year ORDER BY year
            """)
            rows = cur.fetchall()
            labels = [str(r["year"]) for r in rows]
            counts = [r["count"] for r in rows]
            conn.close()
            return jsonify({"labels": labels, "counts": counts, "values": []})
        else:
            cur.execute("""
                SELECT year, month, count
                FROM published_monthly
                WHERE year BETWEEN 2018 AND 2030
                ORDER BY year, month
            """)
            rows = cur.fetchall()
            labels = [f"{MONTH_NAMES[r['month']]} {r['year']}" for r in rows]
            counts = [r["count"] for r in rows]
            conn.close()
            return jsonify({"labels": labels, "counts": counts, "values": []})

    # AOC dataset
    if grain == "yearly":
        cur.execute("""
            SELECT year, SUM(count) as count, SUM(total_value_crore) as total_value_crore
            FROM yearly_trends
            WHERE year BETWEEN 2015 AND 2030
            GROUP BY year ORDER BY year
        """)
        rows = cur.fetchall()
        labels = [str(r["year"]) for r in rows]
        counts = [r["count"] for r in rows]
        values = [round(r["total_value_crore"] or 0, 2) for r in rows]
    else:
        cur.execute("""
            SELECT year, month, count, total_value_crore
            FROM monthly_trends
            WHERE year BETWEEN 2018 AND 2030
            ORDER BY year, month
        """)
        rows = cur.fetchall()
        labels = [f"{MONTH_NAMES[r['month']]} {r['year']}" for r in rows]
        counts = [r["count"] for r in rows]
        values = [round(r["total_value_crore"] or 0, 2) for r in rows]

    conn.close()
    return jsonify({"labels": labels, "counts": counts, "values": values})

# ─────────────────────────────────────────────
# API: TOP ORGS
# ─────────────────────────────────────────────

@app.route("/api/top-orgs")
def api_top_orgs():
    by    = request.args.get("by", "count")   # count | value
    limit = min(int(request.args.get("limit", 25)), 100)
    dataset = request.args.get("dataset", "aoc")  # aoc | published

    conn = get_sum_conn()
    cur  = conn.cursor()

    if dataset == "published":
        cur.execute(f"""
            SELECT org_name, count FROM top_published_orgs
            ORDER BY count DESC LIMIT {limit}
        """)
        rows = cur.fetchall()
        conn.close()
        return jsonify({
            "labels": [r["org_name"] for r in rows],
            "values": [r["count"] for r in rows],
            "metric": "count"
        })

    if by == "value":
        cur.execute(f"""
            SELECT org_name, total_value_crore, count
            FROM top_orgs
            WHERE total_value_crore > 0
            ORDER BY total_value_crore DESC
            LIMIT {limit}
        """)
    else:
        cur.execute(f"""
            SELECT org_name, count, total_value_crore
            FROM top_orgs
            ORDER BY count DESC
            LIMIT {limit}
        """)

    rows = cur.fetchall()
    conn.close()

    if by == "value":
        return jsonify({
            "labels": [r["org_name"] for r in rows],
            "values": [round(r["total_value_crore"], 2) for r in rows],
            "metric": "₹ Crore"
        })
    else:
        return jsonify({
            "labels": [r["org_name"] for r in rows],
            "values": [r["count"] for r in rows],
            "metric": "contracts"
        })

# ─────────────────────────────────────────────
# API: TENDER TYPES
# ─────────────────────────────────────────────

@app.route("/api/tender-types")
def api_tender_types():
    conn = get_sum_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT tender_type, count, total_value_crore
        FROM tender_type_dist
        ORDER BY count DESC
        LIMIT 20
    """)
    rows = cur.fetchall()
    conn.close()
    return jsonify({
        "labels": [r["tender_type"] for r in rows],
        "counts": [r["count"] for r in rows],
        "values": [round(r["total_value_crore"] or 0, 2) for r in rows]
    })

# ─────────────────────────────────────────────
# API: PORTAL BREAKDOWN
# ─────────────────────────────────────────────

@app.route("/api/portal-breakdown")
def api_portal_breakdown():
    conn = get_sum_conn()
    cur  = conn.cursor()
    cur.execute("SELECT portal_type, count FROM portal_breakdown ORDER BY count DESC")
    rows = cur.fetchall()
    conn.close()
    return jsonify({
        "labels": [r["portal_type"] for r in rows],
        "counts": [r["count"] for r in rows]
    })

# ─────────────────────────────────────────────
# API: VALUE DISTRIBUTION
# ─────────────────────────────────────────────

@app.route("/api/value-distribution")
def api_value_dist():
    conn = get_sum_conn()
    cur  = conn.cursor()
    cur.execute("SELECT bracket, count FROM value_brackets ORDER BY min_val")
    rows = cur.fetchall()
    conn.close()
    return jsonify({
        "labels": [r["bracket"] for r in rows],
        "counts": [r["count"] for r in rows]
    })

# ─────────────────────────────────────────────
# API: ANOMALIES
# ─────────────────────────────────────────────

@app.route("/api/anomalies")
def api_anomalies():
    atype = request.args.get("type", "round_number")
    page  = max(1, int(request.args.get("page", 1)))
    per_page = 20
    offset   = (page - 1) * per_page

    conn = get_sum_conn()
    cur  = conn.cursor()

    cur.execute("SELECT COUNT(*) as cnt FROM anomalies WHERE anom_type=?", (atype,))
    total = cur.fetchone()["cnt"]

    cur.execute("""
        SELECT anom_type, internal_id, org_name, title,
               contract_value, aoc_date, portal_type, extra_info
        FROM anomalies
        WHERE anom_type=?
        ORDER BY contract_value DESC
        LIMIT ? OFFSET ?
    """, (atype, per_page, offset))

    rows = []
    for r in cur.fetchall():
        row = dict(r)
        if row.get("extra_info"):
            try:
                row["extra_info"] = json.loads(row["extra_info"])
            except Exception:
                pass
        rows.append(row)

    conn.close()
    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "results": rows
    })

# ─────────────────────────────────────────────
# API: SINGLE-BID CONTRACTS
# ─────────────────────────────────────────────

@app.route("/api/single-bid-contracts")
def api_single_bid():
    page     = max(1, int(request.args.get("page", 1)))
    per_page = 20
    offset   = (page - 1) * per_page
    min_val  = float(request.args.get("min_val", 0))

    conn = get_sum_conn()
    cur  = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) as cnt FROM single_bid_contracts WHERE contract_value >= ?",
        (min_val,)
    )
    total = cur.fetchone()["cnt"]

    cur.execute("""
        SELECT internal_id, org_name, title, contract_value,
               aoc_date, portal_type, bidder_name, ref_no
        FROM single_bid_contracts
        WHERE contract_value >= ?
        ORDER BY contract_value DESC
        LIMIT ? OFFSET ?
    """, (min_val, per_page, offset))

    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "results": rows
    })

# ─────────────────────────────────────────────
# API: REPEAT WINNERS
# ─────────────────────────────────────────────

@app.route("/api/repeat-winners")
def api_repeat_winners():
    page     = max(1, int(request.args.get("page", 1)))
    per_page = 20
    offset   = (page - 1) * per_page
    min_wins = int(request.args.get("min_wins", 3))

    conn = get_sum_conn()
    cur  = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) as cnt FROM repeat_winners WHERE wins >= ?",
        (min_wins,)
    )
    total = cur.fetchone()["cnt"]

    cur.execute("""
        SELECT rank_n, bidder_name, org_name, wins, total_value_crore,
               first_win, last_win
        FROM repeat_winners
        WHERE wins >= ?
        ORDER BY wins DESC
        LIMIT ? OFFSET ?
    """, (min_wins, per_page, offset))

    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "results": rows
    })

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

import re as _re

def _sanitize_fts(q):
    """Strip FTS5 special chars so user input doesn't break MATCH syntax."""
    q = _re.sub(r'["\(\)\*\:\^\-]', ' ', q).strip()
    words = q.split()
    if not words:
        return None
    # Wrap each word in double-quotes → exact token match, implicit AND
    return ' '.join(f'"{w}"' for w in words)


# ─────────────────────────────────────────────
# API: SEARCH (FTS5 via search.db, fallback to LIKE)
# ─────────────────────────────────────────────

@app.route("/api/search")
def api_search():
    q        = request.args.get("q", "").strip()
    year     = request.args.get("year", "")
    portal   = request.args.get("portal", "")
    page     = max(1, int(request.args.get("page", 1)))
    per_page = 20
    offset   = (page - 1) * per_page

    if not q and not year and not portal:
        return jsonify({"total": 0, "results": [], "page": 1})

    # ── FTS5 path (fast) ──
    if os.path.exists(SEARCH_DB) and q:
        fts_q = _sanitize_fts(q)
        if not fts_q:
            return jsonify({"total": 0, "results": [], "page": 1})

        conn = get_search_conn()
        cur  = conn.cursor()

        # Build extra WHERE filters for year / portal
        extra_where  = []
        extra_params = []
        if year:
            extra_where.append("year = ?")
            extra_params.append(str(year))
        if portal:
            extra_where.append("portal_type = ?")
            extra_params.append(portal)

        extra_sql = (" AND " + " AND ".join(extra_where)) if extra_where else ""

        # Fetch per_page+1 rows; the extra tells us if there is a next page.
        # This avoids an expensive COUNT(*) scan on large FTS5 result sets.
        try:
            cur.execute(
                f"""
                SELECT internal_id, org_name, title, year, portal_type, aoc_date,
                       '' as closing_date
                FROM aoc_fts
                WHERE aoc_fts MATCH ?{extra_sql}
                LIMIT ? OFFSET ?
                """,
                [fts_q] + extra_params + [per_page + 1, offset]
            )
            all_rows = cur.fetchall()
        except Exception as e:
            return jsonify({"error": str(e), "total": 0, "results": [], "page": 1}), 400

        has_more = len(all_rows) > per_page
        results  = rows_to_list(all_rows[:per_page])
        # Approximate total: exact when small, offset+21 when more pages exist
        total    = (offset + per_page + 1) if has_more else (offset + len(results))

        return jsonify({"total": total, "page": page, "per_page": per_page,
                        "has_more": has_more, "results": results})

    # ── Fallback: LIKE on aoc_tenders.db (slow, only used before search.db is built) ──
    conn = get_aoc_conn()
    cur  = conn.cursor()

    where_parts = []
    params      = []

    if q:
        where_parts.append("(org_name LIKE ? OR title LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if year:
        where_parts.append("year = ?")
        params.append(int(year))
    if portal:
        where_parts.append("portal_type = ?")
        params.append(portal)

    where_sql = "WHERE " + " AND ".join(where_parts) if where_parts else ""

    cur.execute(f"SELECT COUNT(*) as cnt FROM aoc_tenders {where_sql}", params)
    total = cur.fetchone()["cnt"]

    cur.execute(f"""
        SELECT internal_id, org_name, title, year,
               portal_type, aoc_date, closing_date
        FROM aoc_tenders
        {where_sql}
        ORDER BY year DESC, aoc_date DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])

    results = rows_to_list(cur.fetchall())
    conn.close()
    return jsonify({"total": total, "page": page, "per_page": per_page, "results": results})

# ─────────────────────────────────────────────
# API: TENDER DETAIL
# ─────────────────────────────────────────────

@app.route("/api/tender/<internal_id>")
def api_tender_detail(internal_id):
    conn = get_aoc_conn()
    cur  = conn.cursor()

    cur.execute("""
        SELECT t.*, d.details_json, d.scraped_at as details_scraped_at
        FROM aoc_tenders t
        LEFT JOIN aoc_details d ON t.internal_id = d.internal_id
        WHERE t.internal_id = ?
    """, (internal_id,))

    row = cur.fetchone()
    conn.close()

    if not row:
        abort(404)

    result = dict(row)
    if result.get("details_json"):
        try:
            result["details"] = json.loads(result.pop("details_json"))
        except Exception:
            result["details"] = {}
    return jsonify(result)

# ─────────────────────────────────────────────
# API: STATUS CHECK
# ─────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    summary_ready = os.path.exists(SUM_DB)
    search_ready  = os.path.exists(SEARCH_DB)
    return jsonify({
        "summary_db_ready": summary_ready,
        "search_db_ready":  search_ready,
        "network_db_ready": os.path.exists(NET_DB),
        "aoc_db_exists":    os.path.exists(AOC_DB),
        "vps_db_exists":    os.path.exists(VPS_DB),
    })


# ─────────────────────────────────────────────
# API: CONTRACT NETWORK  (companies ⇄ buyers graph from network.db)
# Built by build_network.py from the linkage pipeline's network.duckdb.
# ─────────────────────────────────────────────

# vis colours per edge type (kept in sync with frontend/js/network.js)
_NET_FIELDS = ("node_id", "ntype", "label", "state", "cin", "status", "email",
               "address", "roc", "activity", "n_contracts", "n_partners",
               "total_value_cr", "degree")

def _node_dict(row, focus=False):
    d = {k: row[k] for k in _NET_FIELDS}
    d["focus"] = focus
    return d

@app.route("/api/network/stats")
def api_network_stats():
    if not os.path.exists(NET_DB):
        return jsonify({"ready": False})
    conn = get_net_conn()
    meta = {r["key"]: int(r["value"]) for r in conn.execute("SELECT key, value FROM net_meta")}
    meta["ready"] = True
    return jsonify(meta)

@app.route("/api/network/search")
def api_network_search():
    if not os.path.exists(NET_DB):
        return jsonify({"ready": False, "results": []})
    q     = request.args.get("q", "").strip()
    ntype = request.args.get("type", "").strip()   # company | buyer | ''
    if len(q) < 2:
        return jsonify({"results": []})

    conn = get_net_conn()
    cur  = conn.cursor()
    type_sql, type_params = ("", [])
    if ntype in ("company", "buyer"):
        type_sql, type_params = (" AND n.ntype = ?", [ntype])

    # FTS5 fast path
    fts_q = _sanitize_fts(q)
    if fts_q:
        try:
            cur.execute(f"""
                SELECT n.node_id, n.label, n.ntype, n.state,
                       n.n_contracts, n.total_value_cr, n.degree
                FROM net_search s
                JOIN net_nodes n ON n.node_id = s.node_id
                WHERE net_search MATCH ?{type_sql}
                ORDER BY n.degree DESC
                LIMIT 25
            """, [fts_q] + type_params)
            rows = [dict(r) for r in cur.fetchall()]
            if rows:
                return jsonify({"results": rows})
        except sqlite3.OperationalError:
            pass  # FTS table absent → fall through to LIKE

    # LIKE fallback
    cur.execute(f"""
        SELECT node_id, label, ntype, state, n_contracts, total_value_cr, degree
        FROM net_nodes n
        WHERE n.label LIKE ?{type_sql}
        ORDER BY n.degree DESC
        LIMIT 25
    """, [f"%{q}%"] + type_params)
    return jsonify({"results": [dict(r) for r in cur.fetchall()]})

@app.route("/api/network/ego")
def api_network_ego():
    if not os.path.exists(NET_DB):
        abort(503)
    node_id = request.args.get("id", "").strip()
    limit   = min(int(request.args.get("limit", 60)), 250)
    if not node_id:
        return jsonify({"focus": None, "nodes": [], "edges": []})

    conn = get_net_conn()
    cur  = conn.cursor()
    focus_row = cur.execute("SELECT * FROM net_nodes WHERE node_id = ?", (node_id,)).fetchone()
    if not focus_row:
        abort(404)

    # strongest edges touching the focus node, either direction
    cur.execute("""
        SELECT src, dst, etype, weight, total_value_cr, label
        FROM net_edges
        WHERE src = ? OR dst = ?
        ORDER BY weight DESC
        LIMIT ?
    """, (node_id, node_id, limit))
    edge_rows = cur.fetchall()

    neighbours, edges = set(), []
    for e in edge_rows:
        other = e["dst"] if e["src"] == node_id else e["src"]
        neighbours.add(other)
        edges.append({"src": e["src"], "dst": e["dst"], "etype": e["etype"],
                      "weight": e["weight"], "total_value_cr": e["total_value_cr"],
                      "label": e["label"]})

    ids = list(neighbours) + [node_id]
    qmarks = ",".join("?" * len(ids))
    node_rows = cur.execute(
        f"SELECT * FROM net_nodes WHERE node_id IN ({qmarks})", ids
    ).fetchall()
    nodes = [_node_dict(r, focus=(r["node_id"] == node_id)) for r in node_rows]

    return jsonify({"focus": node_id, "nodes": nodes, "edges": edges,
                    "truncated": len(edge_rows) >= limit})

@app.route("/api/network/sectors")
def api_network_sectors():
    if not os.path.exists(NET_DB):
        return jsonify({"ready": False, "sectors": []})
    conn = get_net_conn()
    rows = conn.execute("""
        SELECT category, n_awards, n_companies, n_buyers, total_value_cr
        FROM net_sectors ORDER BY n_awards DESC
    """).fetchall()
    return jsonify({"ready": True, "sectors": [dict(r) for r in rows]})

@app.route("/api/network/sector")
def api_network_sector():
    if not os.path.exists(NET_DB):
        abort(503)
    name  = request.args.get("name", "").strip()
    limit = min(int(request.args.get("limit", 90)), 250)
    if not name:
        return jsonify({"sector": None, "nodes": [], "edges": []})

    conn = get_net_conn()
    cur  = conn.cursor()
    meta_row = cur.execute("SELECT * FROM net_sectors WHERE category = ?", (name,)).fetchone()
    if not meta_row:
        abort(404)

    cur.execute("""
        SELECT company_id, buyer_id, n_contracts, total_value_cr
        FROM net_sector_edges
        WHERE category = ?
        ORDER BY n_contracts DESC
        LIMIT ?
    """, (name, limit))
    erows = cur.fetchall()

    ids, edges = set(), []
    for e in erows:
        ids.add(e["company_id"]); ids.add(e["buyer_id"])
        edges.append({"src": e["buyer_id"], "dst": e["company_id"], "etype": "AWARDED",
                      "weight": e["n_contracts"], "total_value_cr": e["total_value_cr"],
                      "label": f"{e['n_contracts']} contracts"})

    nodes = []
    if ids:
        qmarks = ",".join("?" * len(ids))
        for r in cur.execute(f"SELECT * FROM net_nodes WHERE node_id IN ({qmarks})", list(ids)):
            nodes.append(_node_dict(r, focus=False))

    return jsonify({"sector": name, "meta": dict(meta_row),
                    "nodes": nodes, "edges": edges})


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  India Procurement Analytics Dashboard")
    print("  http://localhost:5000")
    print("=" * 55)
    if not os.path.exists(SUM_DB):
        print("  ⚠️  WARNING: summary.db not found.")
        print("     Run `python build_summary.py` first!")
    app.run(debug=False, host="0.0.0.0", port=5000, threaded=True)
