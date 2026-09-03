"""
Khata (credit ledger) tools — PRD §4.3 (Khata Tools).

Invariants enforced:
  - settle_khata rejects non-existent customers (no phantom accounts — PRD §4.4.5)
  - charge_khata requires confirm_new=True to create a new customer
  - settle_khata rejects overpayment (balance can't go negative)
  - All mutations are append-only ledger entries, never DELETE
  - Idempotency via idempotency_key on khata_ledger
"""

import uuid
from datetime import datetime, timezone
from src.db.connection import get_connection, transaction


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _find_customer(conn, customer_id_or_name: str) -> dict | None:
    """Find a khata customer by ID or fuzzy name match."""
    # Try exact ID match first
    row = conn.execute(
        "SELECT customer_id, customer_name, phone, balance_paise "
        "FROM khata_accounts WHERE customer_id = ?",
        (customer_id_or_name,),
    ).fetchone()
    if row:
        return dict(row)

    # Try case-insensitive name match
    row = conn.execute(
        "SELECT customer_id, customer_name, phone, balance_paise "
        "FROM khata_accounts WHERE LOWER(customer_name) = LOWER(?)",
        (customer_id_or_name,),
    ).fetchone()
    if row:
        return dict(row)

    # Try partial name match
    rows = conn.execute(
        "SELECT customer_id, customer_name, phone, balance_paise "
        "FROM khata_accounts WHERE LOWER(customer_name) LIKE LOWER(?)",
        (f"%{customer_id_or_name}%",),
    ).fetchall()
    if len(rows) == 1:
        return dict(rows[0])
    if len(rows) > 1:
        return None  # Ambiguous — caller should handle

    return None


def get_khata_balance(customer_id_or_name: str) -> dict:
    """
    Get a customer's current khata balance.

    Returns:
        {customer_id, name, balance_paise}
        or {error: "customer_not_found"}
    """
    conn = get_connection()
    customer = _find_customer(conn, customer_id_or_name)
    if not customer:
        return {
            "error": "customer_not_found",
            "message": f"No khata customer found matching '{customer_id_or_name}'.",
        }

    return {
        "customer_id": customer["customer_id"],
        "name": customer["customer_name"],
        "balance_paise": customer["balance_paise"],
    }


def charge_khata(
    customer_id_or_name: str,
    amount_paise: int,
    ref_bill_id: str | None = None,
    idempotency_key: str | None = None,
    confirm_new: bool = False,
) -> dict:
    """
    Add a charge to a customer's khata (they owe more).

    If the customer doesn't exist and confirm_new=True, creates a new account.
    If confirm_new=False and customer doesn't exist, returns an error asking
    the agent to confirm creation (PRD §4.4.5).

    Returns:
        {entry_id, new_balance, customer_id}
        or {error: "customer_not_found", needs_confirmation: True}
    """
    if amount_paise <= 0:
        return {"error": "invalid_amount", "message": "Charge amount must be positive."}

    try:
        with transaction() as conn:
            # Idempotency check
            if idempotency_key:
                existing = conn.execute(
                    "SELECT entry_id, balance_after_paise, customer_id "
                    "FROM khata_ledger WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing:
                    return {
                        "entry_id": existing["entry_id"],
                        "new_balance": existing["balance_after_paise"],
                        "customer_id": existing["customer_id"],
                        "idempotent_replay": True,
                    }

            customer = _find_customer(conn, customer_id_or_name)

            if not customer:
                if not confirm_new:
                    return {
                        "error": "customer_not_found",
                        "message": f"No khata customer '{customer_id_or_name}' found. "
                                   f"Set confirm_new=true to create a new account.",
                        "needs_confirmation": True,
                    }
                # Create new customer
                customer_id = str(uuid.uuid4())[:8]
                conn.execute(
                    """INSERT INTO khata_accounts
                       (customer_id, customer_name, balance_paise, created_at)
                       VALUES (?, ?, 0, ?)""",
                    (customer_id, customer_id_or_name, _now()),
                )
                customer = {
                    "customer_id": customer_id,
                    "customer_name": customer_id_or_name,
                    "balance_paise": 0,
                }

            new_balance = customer["balance_paise"] + amount_paise

            cursor = conn.execute(
                """INSERT INTO khata_ledger
                   (customer_id, delta_paise, reason, ref_bill_id,
                    balance_after_paise, created_at, idempotency_key)
                   VALUES (?, ?, 'CHARGE', ?, ?, ?, ?)""",
                (customer["customer_id"], amount_paise, ref_bill_id,
                 new_balance, _now(), idempotency_key),
            )

            conn.execute(
                "UPDATE khata_accounts SET balance_paise = ? WHERE customer_id = ?",
                (new_balance, customer["customer_id"]),
            )

            return {
                "entry_id": cursor.lastrowid,
                "new_balance": new_balance,
                "customer_id": customer["customer_id"],
                "customer_name": customer["customer_name"],
            }

    except Exception as e:
        return {"error": "charge_failed", "message": str(e)}


def settle_khata(
    customer_id_or_name: str,
    amount_paise: int,
    idempotency_key: str | None = None,
) -> dict:
    """
    Record a payment from a customer (reduces what they owe).

    Rejects if:
      - Customer doesn't exist (no phantom accounts — PRD §4.4.5)
      - Amount exceeds outstanding balance (can't overpay into negative)

    Returns:
        {entry_id, new_balance, customer_id}
        or {error: ...}
    """
    if amount_paise <= 0:
        return {"error": "invalid_amount", "message": "Payment amount must be positive."}

    try:
        with transaction() as conn:
            # Idempotency check
            if idempotency_key:
                existing = conn.execute(
                    "SELECT entry_id, balance_after_paise, customer_id "
                    "FROM khata_ledger WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing:
                    return {
                        "entry_id": existing["entry_id"],
                        "new_balance": existing["balance_after_paise"],
                        "customer_id": existing["customer_id"],
                        "idempotent_replay": True,
                    }

            customer = _find_customer(conn, customer_id_or_name)

            if not customer:
                return {
                    "error": "customer_not_found",
                    "message": f"No khata customer '{customer_id_or_name}' found. "
                               f"Cannot settle for a non-existent customer.",
                }

            if customer["balance_paise"] <= 0:
                return {
                    "error": "no_outstanding_balance",
                    "message": f"'{customer['customer_name']}' has no outstanding balance "
                               f"(current: {customer['balance_paise']} paise).",
                }

            if amount_paise > customer["balance_paise"]:
                return {
                    "error": "overpayment",
                    "message": f"Payment of {amount_paise} paise exceeds outstanding "
                               f"balance of {customer['balance_paise']} paise.",
                }

            new_balance = customer["balance_paise"] - amount_paise

            cursor = conn.execute(
                """INSERT INTO khata_ledger
                   (customer_id, delta_paise, reason, balance_after_paise,
                    created_at, idempotency_key)
                   VALUES (?, ?, 'PAYMENT', ?, ?, ?)""",
                (customer["customer_id"], -amount_paise,
                 new_balance, _now(), idempotency_key),
            )

            conn.execute(
                "UPDATE khata_accounts SET balance_paise = ? WHERE customer_id = ?",
                (new_balance, customer["customer_id"]),
            )

            return {
                "entry_id": cursor.lastrowid,
                "new_balance": new_balance,
                "customer_id": customer["customer_id"],
                "customer_name": customer["customer_name"],
            }

    except Exception as e:
        return {"error": "settle_failed", "message": str(e)}


def list_khata_customers() -> dict:
    """
    List all khata customers with their balances.

    Returns:
        {customers: [{customer_id, name, balance_paise}...]}
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT customer_id, customer_name, balance_paise FROM khata_accounts "
        "ORDER BY customer_name"
    ).fetchall()

    return {
        "customers": [
            {
                "customer_id": r["customer_id"],
                "name": r["customer_name"],
                "balance_paise": r["balance_paise"],
            }
            for r in rows
        ]
    }
