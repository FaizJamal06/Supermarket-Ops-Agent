"""
Analytics tools — PRD §4.3 (Analytics & Document Generation Tools).

Tools:
  - get_daily_close(date) → revenue, GST, mode split, top items
  - generate_invoice_pdf(bill_id) → file path (Phase 2 implementation)
  - generate_analysis_deck(date_range) → file path (Phase 2 implementation)
"""

from datetime import date
from src.db.connection import get_connection


def get_daily_close(target_date: str | None = None) -> dict:
    """
    Get daily close summary — total revenue, tax collected, payment mode split, top items.

    Args:
        target_date: ISO date string (YYYY-MM-DD). Defaults to today.

    Returns:
        {date, revenue_paise, cgst_paise, sgst_paise, total_tax_paise,
         mode_split: {CASH: x, UPI: y, CARD: z, KHATA: w},
         top_items: [{name, qty_sold, revenue_paise}...],
         bill_count: int}
    """
    conn = get_connection()

    if target_date is None:
        target_date = date.today().isoformat()

    # Aggregate finalized bills for the date
    # finalized_at is stored as ISO timestamp, so we match on date prefix
    bills = conn.execute(
        "SELECT bill_id, subtotal_paise, cgst_paise, sgst_paise, total_paise, "
        "payment_mode FROM bills "
        "WHERE status = 'FINALIZED' AND finalized_at LIKE ?",
        (f"{target_date}%",),
    ).fetchall()

    if not bills:
        return {
            "date": target_date,
            "revenue_paise": 0,
            "cgst_paise": 0,
            "sgst_paise": 0,
            "total_tax_paise": 0,
            "mode_split": {},
            "top_items": [],
            "bill_count": 0,
        }

    revenue = sum(b["total_paise"] for b in bills)
    cgst = sum(b["cgst_paise"] for b in bills)
    sgst = sum(b["sgst_paise"] for b in bills)

    # Payment mode split
    mode_split = {}
    for b in bills:
        mode = b["payment_mode"] or "UNKNOWN"
        mode_split[mode] = mode_split.get(mode, 0) + b["total_paise"]

    # Top items across all bills for the day
    bill_ids = [b["bill_id"] for b in bills]
    placeholders = ",".join("?" * len(bill_ids))
    items = conn.execute(
        f"SELECT bl.sku_id, p.name, SUM(bl.qty) as qty_sold, "
        f"SUM(bl.line_subtotal_paise + bl.line_cgst_paise + bl.line_sgst_paise) as revenue "
        f"FROM bill_lines bl JOIN products p ON bl.sku_id = p.sku_id "
        f"WHERE bl.bill_id IN ({placeholders}) "
        f"GROUP BY bl.sku_id ORDER BY qty_sold DESC LIMIT 10",
        bill_ids,
    ).fetchall()

    top_items = [
        {
            "sku_id": i["sku_id"],
            "name": i["name"],
            "qty_sold": i["qty_sold"],
            "revenue_paise": i["revenue"],
        }
        for i in items
    ]

    return {
        "date": target_date,
        "revenue_paise": revenue,
        "cgst_paise": cgst,
        "sgst_paise": sgst,
        "total_tax_paise": cgst + sgst,
        "mode_split": mode_split,
        "top_items": top_items,
        "bill_count": len(bills),
    }
