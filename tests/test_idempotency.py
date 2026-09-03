"""
PRD §5.3 — Idempotency tests.

Tests:
  1. finalize_bill with same idempotency_key → stock decremented once, same result
  2. finalize_bill on already-FINALIZED bill → returns original result
  3. receive_stock with same key → one ledger entry
"""

from src.tools.billing import open_draft_bill, update_draft_bill, finalize_bill
from src.tools.inventory import receive_stock, lookup_product
from src.db.connection import get_connection


class TestIdempotency:
    """PRD §4.4.3 — three-layer idempotency model."""

    def test_double_finalize_same_key(self):
        """
        Call finalize_bill twice with identical idempotency_key.
        Stock decremented exactly once. Both calls return same result.
        """
        bill = open_draft_bill("owner1", "chat_idem1")
        update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 5)

        key = "idempotent_finalize_test"
        result1 = finalize_bill(bill["bill_id"], "CASH", idempotency_key=key)
        result2 = finalize_bill(bill["bill_id"], "CASH", idempotency_key=key)

        # Both should indicate FINALIZED
        assert result1["status"] == "FINALIZED"
        assert result2["status"] == "FINALIZED"
        assert result1["total_paise"] == result2["total_paise"]

        # Stock should be decremented exactly once: 100 - 5 = 95
        product = lookup_product("MAGGI_70G")
        assert product["matches"][0]["stock_qty"] == 95.0

        # Verify only ONE sale entry in the ledger for this bill
        conn = get_connection()
        sales = conn.execute(
            "SELECT COUNT(*) as cnt FROM stock_ledger "
            "WHERE ref_id = ? AND reason = 'SALE'",
            (bill["bill_id"],),
        ).fetchone()
        assert sales["cnt"] == 1

    def test_finalize_already_finalized_bill(self):
        """
        PRD §4.4.3 layer 3: bill status check.
        Even without a matching idempotency_key, a FINALIZED bill
        returns the stored result instead of re-executing.
        """
        bill = open_draft_bill("owner1", "chat_idem2")
        update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 3)

        result1 = finalize_bill(bill["bill_id"], "UPI", idempotency_key="first_key")
        assert result1["status"] == "FINALIZED"

        # Call again with a DIFFERENT key
        result2 = finalize_bill(bill["bill_id"], "UPI", idempotency_key="second_key")
        assert result2["status"] == "FINALIZED"
        assert result2.get("idempotent_replay") is True

        # Stock decremented only once
        product = lookup_product("MAGGI_70G")
        assert product["matches"][0]["stock_qty"] == 97.0

    def test_receive_stock_idempotent(self):
        """
        Same idempotency_key on receive_stock → one ledger entry.
        """
        key = "receive_idem_test"
        result1 = receive_stock("MAGGI_70G", 20, idempotency_key=key)
        result2 = receive_stock("MAGGI_70G", 20, idempotency_key=key)

        assert result1["new_balance"] == 120.0  # 100 + 20
        assert result2["new_balance"] == 120.0  # same
        assert result2.get("idempotent_replay") is True

        # Verify only one receipt entry with this key
        conn = get_connection()
        receipts = conn.execute(
            "SELECT COUNT(*) as cnt FROM stock_ledger WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        assert receipts["cnt"] == 1
