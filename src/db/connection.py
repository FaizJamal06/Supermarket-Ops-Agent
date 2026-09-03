"""
Database connection manager and schema initialization.

SQLite in WAL mode. All connections use BEGIN IMMEDIATE for write transactions
to serialize writers and prevent interleaving (PRD §4.4.4).
"""

import sqlite3
import os
import threading
from contextlib import contextmanager

_DB_PATH = os.environ.get("DATABASE_PATH", "store.db")
_local = threading.local()

# ── Schema DDL (PRD §4.2.2 — verbatim) ─────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

-- ============================================================
-- CATALOG
-- ============================================================
CREATE TABLE IF NOT EXISTS products (
    sku_id              TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    unit                TEXT NOT NULL,
    is_loose            INTEGER NOT NULL DEFAULT 0,
    hsn_code            TEXT NOT NULL,
    gst_rate_bps        INTEGER NOT NULL,
    cost_price_paise    INTEGER NOT NULL,
    mrp_paise           INTEGER NOT NULL,
    sell_price_paise    INTEGER NOT NULL,
    reorder_level_qty   REAL NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

-- ============================================================
-- STOCK LEDGER — append-only, DB-enforced oversell guard
-- ============================================================
CREATE TABLE IF NOT EXISTS stock_ledger (
    ledger_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    sku_id          TEXT NOT NULL REFERENCES products(sku_id),
    delta_qty       REAL NOT NULL,
    reason          TEXT NOT NULL,
    ref_type        TEXT,
    ref_id          TEXT,
    balance_after   REAL NOT NULL CHECK(balance_after >= 0),
    created_at      TEXT NOT NULL,
    idempotency_key TEXT UNIQUE
);

-- Convenience view for quick stock lookups
CREATE VIEW IF NOT EXISTS stock_current AS
SELECT sl.sku_id, sl.balance_after AS qty, p.name, p.reorder_level_qty
FROM stock_ledger sl
JOIN products p ON sl.sku_id = p.sku_id
WHERE sl.ledger_id = (
    SELECT MAX(ledger_id) FROM stock_ledger WHERE sku_id = sl.sku_id
);

-- ============================================================
-- BILLS — header + lines, draft/finalized/void state machine
-- ============================================================
CREATE TABLE IF NOT EXISTS bills (
    bill_id                 TEXT PRIMARY KEY,
    owner_id                TEXT NOT NULL,
    chat_id                 TEXT NOT NULL,
    status                  TEXT NOT NULL CHECK(status IN ('DRAFT','FINALIZED','VOID')),
    customer_name           TEXT,
    khata_customer_id       TEXT REFERENCES khata_accounts(customer_id),
    payment_mode            TEXT CHECK(payment_mode IN ('CASH','UPI','CARD','KHATA') OR payment_mode IS NULL),
    payment_ref             TEXT,
    subtotal_paise          INTEGER,
    cgst_paise              INTEGER,
    sgst_paise              INTEGER,
    total_paise             INTEGER,
    finalized_at            TEXT,
    finalize_idempotency_key TEXT UNIQUE,
    created_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bill_lines (
    line_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id             TEXT NOT NULL REFERENCES bills(bill_id),
    sku_id              TEXT NOT NULL REFERENCES products(sku_id),
    qty                 REAL NOT NULL,
    unit_price_paise    INTEGER NOT NULL,
    gst_rate_bps        INTEGER NOT NULL,
    hsn_code            TEXT NOT NULL,
    line_subtotal_paise INTEGER NOT NULL,
    line_cgst_paise     INTEGER NOT NULL,
    line_sgst_paise     INTEGER NOT NULL,
    UNIQUE(bill_id, sku_id)
);

-- ============================================================
-- KHATA (credit)
-- ============================================================
CREATE TABLE IF NOT EXISTS khata_accounts (
    customer_id     TEXT PRIMARY KEY,
    customer_name   TEXT NOT NULL,
    phone           TEXT,
    balance_paise   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS khata_ledger (
    entry_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id         TEXT NOT NULL REFERENCES khata_accounts(customer_id),
    delta_paise         INTEGER NOT NULL,
    reason              TEXT NOT NULL,
    ref_bill_id         TEXT REFERENCES bills(bill_id),
    balance_after_paise INTEGER NOT NULL,
    created_at          TEXT NOT NULL,
    idempotency_key     TEXT UNIQUE
);

-- ============================================================
-- PREFERENCES — durable, keyed on owner_id (not chat_id)
-- ============================================================
CREATE TABLE IF NOT EXISTS preferences (
    owner_id    TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (owner_id, key)
);

-- ============================================================
-- TELEGRAM IDEMPOTENCY GATE
-- ============================================================
CREATE TABLE IF NOT EXISTS processed_updates (
    update_id       INTEGER PRIMARY KEY,
    processed_at    TEXT NOT NULL
);

-- ============================================================
-- CONVERSATION CONTEXT
-- ============================================================
CREATE TABLE IF NOT EXISTS conversation_turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turns_chat ON conversation_turns(chat_id, id DESC);
"""


def set_db_path(path: str) -> None:
    """Override the database path (useful for testing with isolated DBs)."""
    global _DB_PATH
    _DB_PATH = path
    # Clear any cached connection on this thread
    if hasattr(_local, "conn"):
        try:
            _local.conn.close()
        except Exception:
            pass
        del _local.conn


def get_connection() -> sqlite3.Connection:
    """
    Return a thread-local SQLite connection with WAL mode and foreign keys enabled.
    Connection is reused within the same thread.
    """
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(_DB_PATH, timeout=10)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
        _local.conn.execute("PRAGMA busy_timeout=5000")
    return _local.conn


def init_db() -> None:
    """Create all tables and views if they don't exist."""
    conn = get_connection()
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def close_connection() -> None:
    """Close the thread-local connection if it exists."""
    if hasattr(_local, "conn") and _local.conn is not None:
        _local.conn.close()
        _local.conn = None


@contextmanager
def transaction():
    """
    Context manager for a write transaction using BEGIN IMMEDIATE.
    
    BEGIN IMMEDIATE acquires a write lock immediately, preventing other writers
    from interleaving. This is the concurrency mechanism from PRD §4.4.4.
    
    Usage:
        with transaction() as conn:
            conn.execute(...)
    
    Auto-commits on success, rolls back on exception.
    """
    conn = get_connection()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
