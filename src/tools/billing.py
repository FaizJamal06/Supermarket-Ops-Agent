"""
Billing tools — PRD §4.3 (Billing Tools).

Key invariants enforced here:
  - Draft bills only touch bill_lines, never stock_ledger (PRD §4.4.1)
  - finalize_bill is the SINGLE atomic transaction boundary that:
    1. Checks stock via stock_ledger (latest balance_after)
    2. Decrements stock (inserts negative ledger entries)
    3. Computes final GST per-line
    4. Flips bill status to FINALIZED
    5. Optionally charges khata
    All inside one BEGIN IMMEDIATE ... COMMIT (PRD §4.4.4)
  - void_bill writes compensating REVERSAL entries, never DELETEs
  - Idempotency via finalize_idempotency_key (PRD §4.4.3)
"""

import uuid
from datetime import datetime, timezone
from src.db.connection import get_connection, transaction
from src.tools.gst import compute_line_tax, compute_bill_totals


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_draft_bill(owner_id: str, chat_id: str, customer_name: str | None = None) -> dict:
    """
    Create a new DRAFT bill or return the existing open draft for this chat.

    Returns:
        {bill_id, status: "DRAFT", is_new: bool}
    """
    conn = get_connection()

    # Check for existing draft on this chat
    existing = conn.execute(
        "SELECT bill_id FROM bills WHERE chat_id = ? AND status = 'DRAFT'",
        (chat_id,),
    ).fetchone()

    if existing:
        return {"bill_id": existing["bill_id"], "status": "DRAFT", "is_new": False}

    # Create new draft
    bill_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO bills (bill_id, owner_id, chat_id, status, customer_name, created_at)
           VALUES (?, ?, ?, 'DRAFT', ?, ?)""",
        (bill_id, owner_id, chat_id, customer_name, _now()),
    )
    conn.commit()
    return {"bill_id": bill_id, "status": "DRAFT", "is_new": True}


def update_draft_bill(bill_id: str, op: str, sku_id: str, qty: float = 1.0) -> dict:
    """
    Modify a draft bill's lines. Never touches stock_ledger.

    Args:
        bill_id: must be a DRAFT bill
        op: 'ADD' | 'SET_QTY' | 'REMOVE'
        sku_id: must exist in products
        qty: quantity (for ADD, adds to existing; for SET_QTY, replaces)

    Returns:
        {bill_id, lines: [...], subtotal_paise, cgst_paise, sgst_paise, total_paise}
        or {error: ...}
    """
    conn = get_connection()

    # Verify bill exists and is DRAFT
    bill = conn.execute(
        "SELECT bill_id, status FROM bills WHERE bill_id = ?", (bill_id,)
    ).fetchone()
    if not bill:
        return {"error": "bill_not_found", "message": f"Bill '{bill_id}' not found."}
    if bill["status"] != "DRAFT":
        return {"error": "bill_not_draft",
                "message": f"Bill '{bill_id}' is {bill['status']}, not DRAFT."}

    # Verify product exists
    product = conn.execute(
        "SELECT sku_id, name, unit, sell_price_paise, gst_rate_bps, hsn_code "
        "FROM products WHERE sku_id = ?",
        (sku_id,),
    ).fetchone()
    if not product:
        return {"error": "product_not_found",
                "message": f"SKU '{sku_id}' does not exist."}

    op = op.upper()
    if op not in ("ADD", "SET_QTY", "REMOVE"):
        return {"error": "invalid_op",
                "message": f"Operation must be ADD, SET_QTY, or REMOVE. Got '{op}'."}

    # Check existing line for this SKU on this bill
    existing_line = conn.execute(
        "SELECT line_id, qty FROM bill_lines WHERE bill_id = ? AND sku_id = ?",
        (bill_id, sku_id),
    ).fetchone()

    if op == "REMOVE":
        if not existing_line:
            return {"error": "line_not_found",
                    "message": f"'{product['name']}' is not on this bill."}
        conn.execute("DELETE FROM bill_lines WHERE line_id = ?", (existing_line["line_id"],))
        conn.commit()
        return _get_bill_lines_summary(conn, bill_id)

    # Compute new quantity
    if op == "ADD":
        new_qty = (existing_line["qty"] if existing_line else 0) + qty
    else:  # SET_QTY
        new_qty = qty

    if new_qty <= 0:
        if existing_line:
            conn.execute("DELETE FROM bill_lines WHERE line_id = ?", (existing_line["line_id"],))
            conn.commit()
            return _get_bill_lines_summary(conn, bill_id)
        return {"error": "invalid_quantity", "message": "Quantity must be positive."}

    # Compute line tax
    tax = compute_line_tax(product["sell_price_paise"], new_qty, product["gst_rate_bps"])

    if existing_line:
        # Update existing line
        conn.execute(
            """UPDATE bill_lines
               SET qty = ?, line_subtotal_paise = ?, line_cgst_paise = ?, line_sgst_paise = ?
               WHERE line_id = ?""",
            (new_qty, tax["line_subtotal_paise"], tax["line_cgst_paise"],
             tax["line_sgst_paise"], existing_line["line_id"]),
        )
    else:
        # Insert new line
        conn.execute(
            """INSERT INTO bill_lines
               (bill_id, sku_id, qty, unit_price_paise, gst_rate_bps, hsn_code,
                line_subtotal_paise, line_cgst_paise, line_sgst_paise)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (bill_id, sku_id, new_qty, product["sell_price_paise"],
             product["gst_rate_bps"], product["hsn_code"],
             tax["line_subtotal_paise"], tax["line_cgst_paise"], tax["line_sgst_paise"]),
        )
    conn.commit()
    return _get_bill_lines_summary(conn, bill_id)


def get_draft_bill_summary(bill_id: str) -> dict:
    """
    Return a running subtotal/tax preview for a draft bill.
    Computed live from bill_lines, not persisted.

    Returns:
        {bill_id, status, lines: [...], subtotal_paise, cgst_paise, sgst_paise, total_paise}
    """
    conn = get_connection()
    bill = conn.execute(
        "SELECT bill_id, status, customer_name FROM bills WHERE bill_id = ?",
        (bill_id,),
    ).fetchone()
    if not bill:
        return {"error": "bill_not_found", "message": f"Bill '{bill_id}' not found."}

    return _get_bill_lines_summary(conn, bill_id)


def finalize_bill(
    bill_id: str,
    payment_mode: str,
    payment_ref: str | None = None,
    khata_customer_id: str | None = None,
    idempotency_key: str | None = None,
    confirm_below_cost: bool = False,
) -> dict:
    """
    Finalize a draft bill — the SINGLE atomic transaction boundary.

    Inside one transaction (BEGIN IMMEDIATE):
      1. Idempotency check (key + status)
      2. For each line: read current stock, compute new balance, check >= 0
      3. Insert negative stock_ledger entries
      4. Compute final GST per-line and bill totals
      5. Flip status to FINALIZED
      6. Optionally charge khata

    If any SKU has insufficient stock, the ENTIRE transaction rolls back.

    Returns:
        {bill_id, total_paise, status: "FINALIZED", ...}
        or {error: "insufficient_stock", details: [...]}
        or {error: "already_finalized", ...}
        or {warning: "below_cost", items: [...]} (needs confirm_below_cost=True)
    """
    try:
        with transaction() as conn:
            # ── Layer 3: Business-level idempotency (PRD §4.4.3) ──
            bill = conn.execute(
                "SELECT bill_id, status, owner_id, chat_id, customer_name "
                "FROM bills WHERE bill_id = ?",
                (bill_id,),
            ).fetchone()
            if not bill:
                return {"error": "bill_not_found", "message": f"Bill '{bill_id}' not found."}

            if bill["status"] == "FINALIZED":
                # Return the already-finalized result (idempotent)
                return _get_finalized_bill(conn, bill_id)

            if bill["status"] == "VOID":
                return {"error": "bill_void",
                        "message": f"Bill '{bill_id}' has been voided."}

            if bill["status"] != "DRAFT":
                return {"error": "bill_not_draft",
                        "message": f"Bill '{bill_id}' is {bill['status']}, not DRAFT."}

            # ── Layer 2: Tool-level idempotency (PRD §4.4.3) ──
            if idempotency_key:
                existing_key = conn.execute(
                    "SELECT bill_id FROM bills WHERE finalize_idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing_key and existing_key["bill_id"] != bill_id:
                    return _get_finalized_bill(conn, existing_key["bill_id"])

            # Get all bill lines
            lines = conn.execute(
                "SELECT bl.*, p.name, p.cost_price_paise "
                "FROM bill_lines bl JOIN products p ON bl.sku_id = p.sku_id "
                "WHERE bl.bill_id = ?",
                (bill_id,),
            ).fetchall()

            if not lines:
                return {"error": "empty_bill",
                        "message": "Cannot finalize an empty bill. Add items first."}

            # ── Guardrail: sell-below-cost check (PRD §4.4.5) ──
            below_cost_items = []
            for line in lines:
                if line["unit_price_paise"] < line["cost_price_paise"]:
                    below_cost_items.append({
                        "sku_id": line["sku_id"],
                        "name": line["name"],
                        "sell_price_paise": line["unit_price_paise"],
                        "cost_price_paise": line["cost_price_paise"],
                    })

            if below_cost_items and not confirm_below_cost:
                return {
                    "warning": "below_cost",
                    "message": "Some items are priced below cost. Confirm to proceed.",
                    "items": below_cost_items,
                }

            # ── Oversell guard: check stock for ALL lines (PRD §4.4.1) ──
            insufficient = []
            stock_decrements = []
            for line in lines:
                current = conn.execute(
                    "SELECT balance_after FROM stock_ledger WHERE sku_id = ? "
                    "ORDER BY ledger_id DESC LIMIT 1",
                    (line["sku_id"],),
                ).fetchone()
                current_balance = current["balance_after"] if current else 0.0
                new_balance = current_balance - line["qty"]

                if new_balance < 0:
                    insufficient.append({
                        "sku_id": line["sku_id"],
                        "name": line["name"],
                        "available": current_balance,
                        "requested": line["qty"],
                    })
                else:
                    stock_decrements.append((line["sku_id"], line["qty"], new_balance))

            if insufficient:
                # Do NOT commit — transaction will rollback
                return {
                    "error": "insufficient_stock",
                    "message": "Not enough stock for some items.",
                    "details": insufficient,
                }

            # ── All checks passed: decrement stock atomically ──
            now = _now()
            for sku_id, qty, new_balance in stock_decrements:
                idem_key = f"sale_{bill_id}_{sku_id}" if idempotency_key else None
                conn.execute(
                    """INSERT INTO stock_ledger
                       (sku_id, delta_qty, reason, ref_type, ref_id, balance_after,
                        created_at, idempotency_key)
                       VALUES (?, ?, 'SALE', 'bill', ?, ?, ?, ?)""",
                    (sku_id, -qty, bill_id, new_balance, now, idem_key),
                )

            # ── Compute final bill totals from already-rounded lines ──
            line_dicts = [
                {
                    "line_subtotal_paise": l["line_subtotal_paise"],
                    "line_cgst_paise": l["line_cgst_paise"],
                    "line_sgst_paise": l["line_sgst_paise"],
                }
                for l in lines
            ]
            totals = compute_bill_totals(line_dicts)

            # Determine payment mode (apply default if needed)
            final_payment_mode = payment_mode.upper() if payment_mode else None
            if khata_customer_id:
                final_payment_mode = "KHATA"

            # ── Flip status to FINALIZED ──
            conn.execute(
                """UPDATE bills SET
                   status = 'FINALIZED',
                   payment_mode = ?,
                   payment_ref = ?,
                   khata_customer_id = ?,
                   subtotal_paise = ?,
                   cgst_paise = ?,
                   sgst_paise = ?,
                   total_paise = ?,
                   finalized_at = ?,
                   finalize_idempotency_key = ?
                   WHERE bill_id = ?""",
                (final_payment_mode, payment_ref, khata_customer_id,
                 totals["subtotal_paise"], totals["cgst_paise"],
                 totals["sgst_paise"], totals["total_paise"],
                 now, idempotency_key, bill_id),
            )

            # ── Optional: charge khata (PRD §4.3 Khata Tools) ──
            if khata_customer_id:
                # Get current khata balance
                account = conn.execute(
                    "SELECT balance_paise FROM khata_accounts WHERE customer_id = ?",
                    (khata_customer_id,),
                ).fetchone()
                if not account:
                    # Rollback will happen automatically
                    return {"error": "khata_customer_not_found",
                            "message": f"Khata customer '{khata_customer_id}' not found."}

                new_khata_balance = account["balance_paise"] + totals["total_paise"]
                khata_idem = f"khata_charge_{bill_id}" if idempotency_key else None
                conn.execute(
                    """INSERT INTO khata_ledger
                       (customer_id, delta_paise, reason, ref_bill_id,
                        balance_after_paise, created_at, idempotency_key)
                       VALUES (?, ?, 'CHARGE', ?, ?, ?, ?)""",
                    (khata_customer_id, totals["total_paise"], bill_id,
                     new_khata_balance, now, khata_idem),
                )
                conn.execute(
                    "UPDATE khata_accounts SET balance_paise = ? WHERE customer_id = ?",
                    (new_khata_balance, khata_customer_id),
                )

            return {
                "bill_id": bill_id,
                "status": "FINALIZED",
                "total_paise": totals["total_paise"],
                "subtotal_paise": totals["subtotal_paise"],
                "cgst_paise": totals["cgst_paise"],
                "sgst_paise": totals["sgst_paise"],
                "payment_mode": final_payment_mode,
            }

    except Exception as e:
        error_msg = str(e)
        # Check if it's the DB-level CHECK constraint (final backstop)
        if "CHECK constraint failed" in error_msg:
            return {
                "error": "insufficient_stock",
                "message": "Stock check failed at database level. Transaction rolled back.",
            }
        return {"error": "finalize_failed", "message": error_msg}


def void_bill(bill_id: str, reason: str) -> dict:
    """
    Void a finalized bill by writing compensating REVERSAL entries to stock_ledger.
    Never DELETEs anything (PRD §4.4.5).

    Returns:
        {bill_id, status: "VOID"}
        or {error: ...}
    """
    try:
        with transaction() as conn:
            bill = conn.execute(
                "SELECT bill_id, status FROM bills WHERE bill_id = ?",
                (bill_id,),
            ).fetchone()
            if not bill:
                return {"error": "bill_not_found", "message": f"Bill '{bill_id}' not found."}

            if bill["status"] != "FINALIZED":
                return {"error": "bill_not_finalized",
                        "message": f"Only FINALIZED bills can be voided. This bill is {bill['status']}."}

            # Get bill lines to know what stock to restore
            lines = conn.execute(
                "SELECT sku_id, qty FROM bill_lines WHERE bill_id = ?",
                (bill_id,),
            ).fetchall()

            now = _now()

            # Write compensating REVERSAL entries (positive deltas to restore stock)
            for line in lines:
                current = conn.execute(
                    "SELECT balance_after FROM stock_ledger WHERE sku_id = ? "
                    "ORDER BY ledger_id DESC LIMIT 1",
                    (line["sku_id"],),
                ).fetchone()
                current_balance = current["balance_after"] if current else 0.0
                new_balance = current_balance + line["qty"]

                conn.execute(
                    """INSERT INTO stock_ledger
                       (sku_id, delta_qty, reason, ref_type, ref_id, balance_after,
                        created_at, idempotency_key)
                       VALUES (?, ?, 'REVERSAL', 'bill', ?, ?, ?, ?)""",
                    (line["sku_id"], line["qty"], bill_id, new_balance,
                     now, f"void_{bill_id}_{line['sku_id']}"),
                )

            # Reverse khata charge if applicable
            khata_entry = conn.execute(
                "SELECT customer_id, delta_paise FROM khata_ledger "
                "WHERE ref_bill_id = ? AND reason = 'CHARGE'",
                (bill_id,),
            ).fetchone()
            if khata_entry:
                account = conn.execute(
                    "SELECT balance_paise FROM khata_accounts WHERE customer_id = ?",
                    (khata_entry["customer_id"],),
                ).fetchone()
                new_khata_balance = account["balance_paise"] - khata_entry["delta_paise"]
                conn.execute(
                    """INSERT INTO khata_ledger
                       (customer_id, delta_paise, reason, ref_bill_id,
                        balance_after_paise, created_at, idempotency_key)
                       VALUES (?, ?, 'PAYMENT', ?, ?, ?, ?)""",
                    (khata_entry["customer_id"], -khata_entry["delta_paise"], bill_id,
                     new_khata_balance, now, f"void_khata_{bill_id}"),
                )
                conn.execute(
                    "UPDATE khata_accounts SET balance_paise = ? WHERE customer_id = ?",
                    (new_khata_balance, khata_entry["customer_id"]),
                )

            # Flip status to VOID
            conn.execute(
                "UPDATE bills SET status = 'VOID' WHERE bill_id = ?",
                (bill_id,),
            )

            return {"bill_id": bill_id, "status": "VOID", "reason": reason}

    except Exception as e:
        return {"error": "void_failed", "message": str(e)}


# ── Internal helpers ──

def _get_bill_lines_summary(conn, bill_id: str) -> dict:
    """Get the current state of a bill with lines and computed totals."""
    bill = conn.execute(
        "SELECT bill_id, status, customer_name FROM bills WHERE bill_id = ?",
        (bill_id,),
    ).fetchone()

    lines = conn.execute(
        "SELECT bl.sku_id, p.name, p.unit, bl.qty, bl.unit_price_paise, "
        "bl.gst_rate_bps, bl.hsn_code, bl.line_subtotal_paise, "
        "bl.line_cgst_paise, bl.line_sgst_paise "
        "FROM bill_lines bl JOIN products p ON bl.sku_id = p.sku_id "
        "WHERE bl.bill_id = ?",
        (bill_id,),
    ).fetchall()

    line_list = []
    for l in lines:
        line_list.append({
            "sku_id": l["sku_id"],
            "name": l["name"],
            "unit": l["unit"],
            "qty": l["qty"],
            "unit_price_paise": l["unit_price_paise"],
            "gst_rate_bps": l["gst_rate_bps"],
            "hsn_code": l["hsn_code"],
            "line_subtotal_paise": l["line_subtotal_paise"],
            "line_cgst_paise": l["line_cgst_paise"],
            "line_sgst_paise": l["line_sgst_paise"],
        })

    totals = compute_bill_totals(line_list) if line_list else {
        "subtotal_paise": 0, "cgst_paise": 0, "sgst_paise": 0, "total_paise": 0
    }

    # Generate a human-readable summary string for the LLM
    cart_summary_lines = []
    for l in line_list:
        cart_summary_lines.append(f"{l['qty']}x {l['name']} (@ Rs.{l['unit_price_paise']/100:.2f})")
    
    cart_summary_text = (
        f"Cart has {len(line_list)} items. "
        f"Current contents: {', '.join(cart_summary_lines) if cart_summary_lines else 'Empty'}. "
        f"Total: Rs.{totals['total_paise']/100:.2f}"
    )

    return {
        "bill_id": bill_id,
        "status": bill["status"],
        "customer_name": bill["customer_name"],
        "lines": line_list,
        "cart_summary_text": cart_summary_text,
        **totals,
    }


def _get_finalized_bill(conn, bill_id: str) -> dict:
    """Return the stored totals of an already-finalized bill (idempotent replay)."""
    bill = conn.execute(
        "SELECT bill_id, status, total_paise, subtotal_paise, cgst_paise, "
        "sgst_paise, payment_mode FROM bills WHERE bill_id = ?",
        (bill_id,),
    ).fetchone()
    return {
        "bill_id": bill["bill_id"],
        "status": bill["status"],
        "total_paise": bill["total_paise"],
        "subtotal_paise": bill["subtotal_paise"],
        "cgst_paise": bill["cgst_paise"],
        "sgst_paise": bill["sgst_paise"],
        "payment_mode": bill["payment_mode"],
        "idempotent_replay": True,
    }
