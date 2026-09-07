"""
Inventory & Catalog tools — PRD §4.3 (Inventory & Catalog Tools).

Tools:
  - lookup_product(query) → fuzzy search, returns candidates with stock/price/GST
  - list_low_stock() → items at or below reorder level
  - receive_stock(sku_id, qty, ...) → append to stock_ledger
  - create_product(...) → onboard new SKU
"""

from datetime import datetime, timezone
from thefuzz import fuzz
from src.db.connection import get_connection, transaction


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def lookup_product(query: str) -> dict:
    """
    Fuzzy-search products by name or SKU.

    Uses in-memory fuzzy matching (thefuzz) against the full catalog since
    the catalog is small. Handles typos like "ashirvad" → "Aashirvaad".

    Returns:
        {matches: [{sku_id, name, unit, is_loose, sell_price_paise, gst_rate_bps,
                     hsn_code, cost_price_paise, mrp_paise, stock_qty}...]}
        or {matches: [], error: "not_found"} if no plausible match.

    Multiple matches → the LLM should ask the owner to disambiguate.
    """
    conn = get_connection()

    tokens = [t.strip().lower() for t in query.split() if t.strip()]
    if not tokens:
        return {"matches": [], "error": "not_found", "message": "Empty query."}

    clauses = []
    params = []
    for t in tokens:
        clauses.append("(LOWER(name) LIKE ? OR LOWER(sku_id) LIKE ?)")
        params.extend([f"%{t}%", f"%{t}%"])

    sql = f"SELECT sku_id, name, unit, is_loose, hsn_code, gst_rate_bps, cost_price_paise, mrp_paise, sell_price_paise FROM products WHERE {' AND '.join(clauses)}"
    
    products = conn.execute(sql, params).fetchall()

    if not products:
        return {"matches": [], "error": "not_found",
                "message": f"No product found matching '{query}'. Use create_product to add it."}

    matches = []
    for p in products:
        stock = _get_stock_qty(conn, p["sku_id"])
        matches.append(_product_to_dict(p, stock))

    return {"matches": matches}


def list_low_stock() -> dict:
    """
    Return all SKUs at or below their reorder level.

    Returns:
        {items: [{sku_id, name, qty, reorder_level}...]}
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT sku_id, name, qty, reorder_level_qty FROM stock_current "
        "WHERE qty <= reorder_level_qty"
    ).fetchall()

    items = [
        {
            "sku_id": r["sku_id"],
            "name": r["name"],
            "qty": r["qty"],
            "reorder_level": r["reorder_level_qty"],
        }
        for r in rows
    ]

    # Also include products with NO stock_ledger entries (never received)
    conn2 = get_connection()
    all_products = conn2.execute(
        "SELECT sku_id, name, reorder_level_qty FROM products "
        "WHERE sku_id NOT IN (SELECT DISTINCT sku_id FROM stock_ledger)"
    ).fetchall()
    for p in all_products:
        items.append({
            "sku_id": p["sku_id"],
            "name": p["name"],
            "qty": 0,
            "reorder_level": p["reorder_level_qty"],
        })

    return {"items": items}


def receive_stock(
    sku_id: str,
    qty: float,
    cost_price_paise: int | None = None,
    mrp_paise: int | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """
    Receive stock for an existing SKU. Appends to stock_ledger.

    Args:
        sku_id: must exist in products table
        qty: positive quantity received
        cost_price_paise: optional new cost price (updates catalog)
        mrp_paise: optional new MRP (updates catalog)
        idempotency_key: required for safe retries (PRD §4.4.3)

    Returns:
        {new_balance, ledger_id, sku_id}
        or {error: ...} on failure
    """
    if qty <= 0:
        return {"error": "invalid_quantity", "message": "Quantity must be positive."}

    try:
        with transaction() as conn:
            # Check idempotency first
            if idempotency_key:
                existing = conn.execute(
                    "SELECT ledger_id, balance_after FROM stock_ledger WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing:
                    return {
                        "new_balance": existing["balance_after"],
                        "ledger_id": existing["ledger_id"],
                        "sku_id": sku_id,
                        "idempotent_replay": True,
                    }

            # Verify product exists
            product = conn.execute(
                "SELECT sku_id FROM products WHERE sku_id = ?", (sku_id,)
            ).fetchone()
            if not product:
                return {"error": "product_not_found",
                        "message": f"SKU '{sku_id}' does not exist. Use create_product first."}

            # Get current balance
            current = conn.execute(
                "SELECT balance_after FROM stock_ledger WHERE sku_id = ? "
                "ORDER BY ledger_id DESC LIMIT 1",
                (sku_id,),
            ).fetchone()
            current_balance = current["balance_after"] if current else 0.0
            new_balance = current_balance + qty

            # Insert ledger entry
            cursor = conn.execute(
                """INSERT INTO stock_ledger
                   (sku_id, delta_qty, reason, ref_type, balance_after, created_at, idempotency_key)
                   VALUES (?, ?, 'RECEIPT', 'manual', ?, ?, ?)""",
                (sku_id, qty, new_balance, _now(), idempotency_key),
            )

            # Optionally update catalog prices
            if cost_price_paise is not None or mrp_paise is not None:
                updates = []
                params = []
                if cost_price_paise is not None:
                    updates.append("cost_price_paise = ?")
                    params.append(cost_price_paise)
                if mrp_paise is not None:
                    updates.append("mrp_paise = ?")
                    updates.append("sell_price_paise = ?")
                    params.append(mrp_paise)
                    params.append(mrp_paise)
                updates.append("updated_at = ?")
                params.append(_now())
                params.append(sku_id)
                conn.execute(
                    f"UPDATE products SET {', '.join(updates)} WHERE sku_id = ?",
                    params,
                )

            return {
                "new_balance": new_balance,
                "ledger_id": cursor.lastrowid,
                "sku_id": sku_id,
            }
    except Exception as e:
        return {"error": "receive_stock_failed", "message": str(e)}


def create_product(
    name: str,
    unit: str,
    is_loose: bool,
    hsn_code: str,
    gst_rate_bps: int,
    cost_price_paise: int,
    mrp_paise: int,
    sell_price_paise: int,
    reorder_level_qty: float,
) -> dict:
    """
    Onboard a new product/SKU.

    Returns:
        {sku_id: str} on success
        {error: ...} if duplicate name exists
    """
    # Generate SKU ID from name
    sku_id = name.upper().replace(" ", "_").replace("(", "").replace(")", "")
    # Remove non-alphanumeric except underscores
    sku_id = "".join(c for c in sku_id if c.isalnum() or c == "_")

    conn = get_connection()

    # Check for duplicate name (case-insensitive)
    existing = conn.execute(
        "SELECT sku_id, name FROM products WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()
    if existing:
        return {
            "error": "duplicate_product",
            "message": f"Product '{existing['name']}' already exists with SKU '{existing['sku_id']}'.",
        }

    # Check for duplicate SKU ID
    existing_sku = conn.execute(
        "SELECT sku_id FROM products WHERE sku_id = ?", (sku_id,)
    ).fetchone()
    if existing_sku:
        # Append a counter
        counter = 1
        while conn.execute("SELECT 1 FROM products WHERE sku_id = ?",
                           (f"{sku_id}_{counter}",)).fetchone():
            counter += 1
        sku_id = f"{sku_id}_{counter}"

    now = _now()
    try:
        conn.execute(
            """INSERT INTO products
               (sku_id, name, unit, is_loose, hsn_code, gst_rate_bps,
                cost_price_paise, mrp_paise, sell_price_paise, reorder_level_qty,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sku_id, name, unit, int(is_loose), hsn_code, gst_rate_bps,
             cost_price_paise, mrp_paise, sell_price_paise, reorder_level_qty,
             now, now),
        )
        conn.commit()
        return {"sku_id": sku_id}
    except Exception as e:
        return {"error": "create_product_failed", "message": str(e)}


# ── Internal helpers ──

def _get_stock_qty(conn, sku_id: str) -> float:
    """Get current stock for a SKU from the latest ledger entry."""
    row = conn.execute(
        "SELECT balance_after FROM stock_ledger WHERE sku_id = ? "
        "ORDER BY ledger_id DESC LIMIT 1",
        (sku_id,),
    ).fetchone()
    return row["balance_after"] if row else 0.0


def _product_to_dict(row, stock_qty: float) -> dict:
    """Convert a sqlite3.Row product to a dict with stock info."""
    return {
        "sku_id": row["sku_id"],
        "name": row["name"],
        "unit": row["unit"],
        "is_loose": bool(row["is_loose"]),
        "sell_price_paise": row["sell_price_paise"],
        "cost_price_paise": row["cost_price_paise"],
        "mrp_paise": row["mrp_paise"],
        "gst_rate_bps": row["gst_rate_bps"],
        "hsn_code": row["hsn_code"],
        "stock_qty": stock_qty,
    }
