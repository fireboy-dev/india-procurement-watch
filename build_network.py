"""
build_network.py
================
Precompute the **Contract Network** feature's serving database.

Reads the linkage graph produced by the india-procurement-network pipeline
(`network.duckdb`) and writes a small, indexed SQLite file (`network.db`) that the
Flask app serves with the standard library only — same precompute → tiny-SQLite → serve
pattern as build_summary.py.

The heavy entity-resolution (award bidder names → MCA/ROC company registry) lives in the
separate pipeline; this script just packages its output (companies, government buyers,
and the edges between them) for interactive exploration.

Usage:
    python build_network.py                      # looks for ./network.duckdb
    python build_network.py /path/to/network.duckdb
    NETWORK_DUCKDB=/path/to/network.duckdb python build_network.py

Requires `duckdb` at build time only (pip install duckdb). The Flask runtime needs
nothing extra.
"""

import os
import sys
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DB = os.path.join(BASE_DIR, "network.db")

# edges that describe the *company* network (FROM_TENDER + tender nodes are excluded —
# they belong to the per-tender view, not the company/buyer explorer)
KEEP_EDGES = ("AWARDED", "CO_BIDDER", "SHARES_EMAIL", "SHARES_ADDRESS")

RUPEES_PER_CRORE = 1e7


def find_source() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    if os.environ.get("NETWORK_DUCKDB"):
        return os.environ["NETWORK_DUCKDB"]
    # common locations
    candidates = [
        os.path.join(BASE_DIR, "network.duckdb"),
        os.path.join(BASE_DIR, "data", "network.duckdb"),
        os.path.join(BASE_DIR, "..", "tenders_aoc", "data", "network.duckdb"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


def main() -> int:
    src = find_source()
    if not os.path.exists(src):
        print(f"❌ source graph not found: {src}")
        print("   Build it first in the india-procurement-network repo:")
        print("     python pipeline/run.py all")
        print("   then pass its path:  python build_network.py /path/to/network.duckdb")
        return 1

    try:
        import duckdb
    except ImportError:
        print("❌ duckdb is required to build network.db:  pip install duckdb")
        return 1

    print(f"  source : {src}")
    print(f"  target : {OUT_DB}")
    duck = duckdb.connect(src, read_only=True)

    if os.path.exists(OUT_DB):
        os.remove(OUT_DB)
    sq = sqlite3.connect(OUT_DB)
    sq.executescript("""
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous  = OFF;
        CREATE TABLE net_nodes (
            node_id        TEXT PRIMARY KEY,
            ntype          TEXT,          -- 'company' | 'buyer'
            label          TEXT,
            state          TEXT,
            cin            TEXT,
            status         TEXT,
            email          TEXT,
            address        TEXT,
            roc            TEXT,
            activity       TEXT,
            n_contracts    INTEGER,
            n_partners     INTEGER,       -- buyers (for a company) / vendors (for a buyer)
            total_value_cr REAL,
            degree         INTEGER
        );
        CREATE TABLE net_edges (
            src            TEXT,
            dst            TEXT,
            etype          TEXT,
            weight         REAL,
            total_value_cr REAL,
            label          TEXT
        );
        CREATE TABLE net_meta (key TEXT PRIMARY KEY, value TEXT);
    """)

    # ── companies ──
    print("  loading companies …")
    companies = duck.execute("""
        SELECT n.node_id, 'company', n.company_name, n.registered_state, n.cin,
               n.company_status, n.email, n.reg_address, n.roc, n.business_activity,
               n.n_contracts, n.n_buyers, n.total_value, COALESCE(d.degree, 0)
        FROM company_nodes n
        LEFT JOIN node_degree d ON d.node = n.node_id
    """).fetchall()
    companies = [
        (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9],
         int(r[10] or 0), int(r[11] or 0), round((r[12] or 0) / RUPEES_PER_CRORE, 2), int(r[13] or 0))
        for r in companies
    ]

    # ── buyers ──
    print("  loading buyers …")
    buyers = duck.execute("""
        SELECT n.node_id, 'buyer', n.buyer_name, n.region,
               n.n_contracts, n.n_vendors, n.total_value, COALESCE(d.degree, 0)
        FROM buyer_nodes n
        LEFT JOIN node_degree d ON d.node = n.node_id
    """).fetchall()
    buyers = [
        (r[0], r[1], r[2], r[3], None, None, None, None, None, None,
         int(r[4] or 0), int(r[5] or 0), round((r[6] or 0) / RUPEES_PER_CRORE, 2), int(r[7] or 0))
        for r in buyers
    ]

    sq.executemany("INSERT OR REPLACE INTO net_nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   companies + buyers)
    print(f"    nodes: {len(companies):,} companies + {len(buyers):,} buyers")

    # ── edges ──
    print("  loading edges …")
    placeholders = ",".join("?" * len(KEEP_EDGES))
    edges = duck.execute(f"""
        SELECT edge_type, src, dst, weight, total_value, label
        FROM edges WHERE edge_type IN ({placeholders})
    """, list(KEEP_EDGES)).fetchall()
    edges = [
        (r[1], r[2], r[0], float(r[3] or 0), round((r[4] or 0) / RUPEES_PER_CRORE, 2), r[5])
        for r in edges
    ]
    sq.executemany("INSERT INTO net_edges VALUES (?,?,?,?,?,?)", edges)
    print(f"    edges: {len(edges):,}")

    # ── indexes + search ──
    print("  indexing …")
    sq.executescript("""
        CREATE INDEX ix_edge_src ON net_edges(src);
        CREATE INDEX ix_edge_dst ON net_edges(dst);
        CREATE INDEX ix_node_type ON net_nodes(ntype);
    """)
    # FTS5 for fast company/buyer name search (falls back to LIKE in the app if absent)
    try:
        sq.executescript("""
            CREATE VIRTUAL TABLE net_search USING fts5(
                node_id UNINDEXED, label, ntype UNINDEXED, tokenize='unicode61'
            );
            INSERT INTO net_search(node_id, label, ntype)
                SELECT node_id, label, ntype FROM net_nodes;
        """)
        print("    FTS5 search index built")
    except sqlite3.OperationalError as e:
        print(f"    (FTS5 unavailable, search will use LIKE) — {e}")

    # ── meta / stats ──
    def one(sql, *p):
        return sq.execute(sql, p).fetchone()[0]
    meta = {
        "companies": one("SELECT COUNT(*) FROM net_nodes WHERE ntype='company'"),
        "buyers": one("SELECT COUNT(*) FROM net_nodes WHERE ntype='buyer'"),
        "edges_total": len(edges),
        "edges_awarded": one("SELECT COUNT(*) FROM net_edges WHERE etype='AWARDED'"),
        "edges_cobidder": one("SELECT COUNT(*) FROM net_edges WHERE etype='CO_BIDDER'"),
        "edges_shared_email": one("SELECT COUNT(*) FROM net_edges WHERE etype='SHARES_EMAIL'"),
        "edges_shared_address": one("SELECT COUNT(*) FROM net_edges WHERE etype='SHARES_ADDRESS'"),
    }
    sq.executemany("INSERT OR REPLACE INTO net_meta VALUES (?,?)",
                   [(k, str(v)) for k, v in meta.items()])

    sq.commit()
    sq.close()
    duck.close()

    size_mb = os.path.getsize(OUT_DB) / 1e6
    print(f"  meta   : {meta}")
    print(f"✅ DONE — wrote {OUT_DB} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
