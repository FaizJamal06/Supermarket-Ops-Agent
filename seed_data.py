"""
Seed the catalog with the SKUs specified in PRD §4.2.1.

All 7 named branded SKUs + 4 loose items from the assignment brief.
Run this once after init_db() to populate the products table and initial stock.
"""

from datetime import datetime, timezone
from src.db.connection import get_connection, init_db, transaction

# PRD §4.2.1 — exact products from the assignment brief
# Prices are realistic Indian kirana prices, stored as integer paise
SEED_PRODUCTS = [
    # (sku_id, name, unit, is_loose, hsn_code, gst_rate_bps, cost_paise, mrp_paise, sell_paise, reorder_qty)
    ("AASHIRVAAD_ATTA_5KG",   "Aashirvaad Atta 5kg",       "packet", 0, "1101", 500,  18000, 22500, 22500, 10),
    ("TATA_SALT_1KG",         "Tata Salt 1kg",              "packet", 0, "2501", 500,   1800,  2800,  2800, 20),
    ("AMUL_BUTTER_100G",      "Amul Butter 100g",           "packet", 0, "0405", 1200,  4800,  6200,  6200, 10),
    ("FORTUNE_OIL_1L",        "Fortune Sunflower Oil 1L",   "packet", 0, "1512", 500,  13000, 16500, 16500,  5),
    ("MAGGI_70G",             "Maggi 70g",                  "packet", 0, "1902", 1800,  1200,  1400,  1400, 50),
    ("PARLE_G",               "Parle-G",                    "packet", 0, "1905", 1800,   500,  1000,  1000, 30),
    ("SURF_EXCEL",            "Surf Excel",                 "packet", 0, "3402", 1800,  8000, 11500, 11500, 10),
    # Loose items — GST 0% (unbranded staples)
    ("SUGAR_LOOSE",           "Sugar (loose)",              "kg",     1, "1701",    0,  3500,  4500,  4500, 25),
    ("RICE_LOOSE",            "Rice (loose)",               "kg",     1, "1006",    0,  4000,  5500,  5500, 25),
    ("DAL_LOOSE",             "Dal (loose)",                "kg",     1, "0713",    0,  8000, 11000, 11000, 10),
    ("ATTA_LOOSE",            "Atta (loose)",               "kg",     1, "1101",    0,  3000,  4000,  4000, 25),
]

# Initial stock levels (realistic starting inventory)
SEED_STOCK = {
    "AASHIRVAAD_ATTA_5KG": 20,
    "TATA_SALT_1KG":       50,
    "AMUL_BUTTER_100G":    15,
    "FORTUNE_OIL_1L":      10,
    "MAGGI_70G":           100,
    "PARLE_G":             60,
    "SURF_EXCEL":          15,
    "SUGAR_LOOSE":         50,    # 50 kg
    "RICE_LOOSE":          50,    # 50 kg
    "DAL_LOOSE":           20,    # 20 kg
    "ATTA_LOOSE":          50,    # 50 kg
}


def seed_catalog() -> None:
    """Insert seed products and initial stock. Idempotent — skips existing SKUs."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()

    for row in SEED_PRODUCTS:
        sku_id, name, unit, is_loose, hsn, gst_bps, cost, mrp, sell, reorder = row
        conn.execute(
            """INSERT OR IGNORE INTO products
               (sku_id, name, unit, is_loose, hsn_code, gst_rate_bps,
                cost_price_paise, mrp_paise, sell_price_paise, reorder_level_qty,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sku_id, name, unit, is_loose, hsn, gst_bps, cost, mrp, sell, reorder, now, now),
        )
    conn.commit()

    # Seed initial stock via stock_ledger (append-only — never a mutable qty column)
    for sku_id, qty in SEED_STOCK.items():
        # Check if this SKU already has ledger entries (idempotent)
        existing = conn.execute(
            "SELECT 1 FROM stock_ledger WHERE sku_id = ? LIMIT 1", (sku_id,)
        ).fetchone()
        if existing:
            continue
        conn.execute(
            """INSERT INTO stock_ledger
               (sku_id, delta_qty, reason, ref_type, ref_id, balance_after,
                created_at, idempotency_key)
               VALUES (?, ?, 'RECEIPT', 'manual', 'seed', ?, ?, ?)""",
            (sku_id, qty, qty, now, f"seed_{sku_id}"),
        )
    conn.commit()


if __name__ == "__main__":
    init_db()
    seed_catalog()
    # Verify
    conn = get_connection()
    products = conn.execute("SELECT sku_id, name, gst_rate_bps, sell_price_paise FROM products").fetchall()
    print(f"Seeded {len(products)} products:")
    for p in products:
        gst_pct = p["gst_rate_bps"] / 100
        price_rs = p["sell_price_paise"] / 100
        print(f"  {p['sku_id']:30s} | {p['name']:30s} | GST {gst_pct:5.1f}% | Rs.{price_rs:.2f}")

    stock = conn.execute("SELECT sku_id, qty FROM stock_current").fetchall()
    print(f"\nInitial stock ({len(stock)} items):")
    for s in stock:
        print(f"  {s['sku_id']:30s} | {s['qty']} units")
