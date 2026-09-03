"""
PRD §5.1 — Oversell / Concurrency Under Load tests.

Tests:
  1. Two concurrent finalize_bill calls requesting > N/2 units each → exactly one succeeds
  2. 50 concurrent finalize_bill calls against stock of 10, each requesting 1 → exactly 10 succeed
  3. Basic oversell guard: single finalize requesting more than available → fails
  4. Successful finalize decrements stock correctly
"""

import threading
from src.tools.inventory import receive_stock, lookup_product
from src.tools.billing import open_draft_bill, update_draft_bill, finalize_bill
from src.db.connection import get_connection, set_db_path, init_db, close_connection
from seed_data import seed_catalog


class TestOversellGuard:
    """PRD §4.4.1 — stock can't go negative, enforced at DB level."""

    def test_finalize_refuses_when_insufficient_stock(self):
        """Request more than available → error, stock unchanged."""
        # Maggi has 100 in stock, request 200
        bill = open_draft_bill("owner1", "chat1")
        update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 200)
        result = finalize_bill(bill["bill_id"], "CASH", idempotency_key="test_oversell_1")

        assert result.get("error") == "insufficient_stock"
        assert result["details"][0]["available"] == 100.0
        assert result["details"][0]["requested"] == 200.0

        # Verify stock is unchanged
        product = lookup_product("MAGGI_70G")
        assert product["matches"][0]["stock_qty"] == 100.0

    def test_finalize_succeeds_when_stock_sufficient(self):
        """Normal finalize decrements stock correctly."""
        bill = open_draft_bill("owner1", "chat1")
        update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 5)
        result = finalize_bill(bill["bill_id"], "UPI", idempotency_key="test_normal_1")

        assert result["status"] == "FINALIZED"
        assert result["total_paise"] > 0

        # Stock should be 100 - 5 = 95
        product = lookup_product("MAGGI_70G")
        assert product["matches"][0]["stock_qty"] == 95.0

    def test_all_or_nothing_multi_item_bill(self):
        """If ANY line fails oversell, the ENTIRE bill rolls back."""
        bill = open_draft_bill("owner1", "chat1")
        update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 5)  # 100 available, OK
        update_draft_bill(bill["bill_id"], "ADD", "AMUL_BUTTER_100G", 50)  # 15 available, FAIL

        result = finalize_bill(bill["bill_id"], "CASH", idempotency_key="test_all_or_nothing")

        assert result.get("error") == "insufficient_stock"
        # Maggi stock should be UNCHANGED (rollback)
        product = lookup_product("MAGGI_70G")
        assert product["matches"][0]["stock_qty"] == 100.0


class TestConcurrency:
    """PRD §5.1 — concurrent finalize calls must serialize correctly."""

    def test_two_concurrent_finalizes_one_wins(self, fresh_db):
        """
        Seed stock at 10 Maggi. Two bills each request 8.
        Exactly one must succeed, the other must fail.
        Final stock = 10 - 8 = 2 (never negative).
        """
        # Reset stock to exactly 10 Maggi
        db_path = fresh_db
        conn = get_connection()
        # Delete existing stock ledger and re-seed with 10
        conn.execute("DELETE FROM stock_ledger WHERE sku_id = 'MAGGI_70G'")
        conn.commit()
        receive_stock("MAGGI_70G", 10, idempotency_key="reseed_maggi_10")

        results = [None, None]
        errors = [None, None]

        def make_and_finalize(idx, chat_id, idem_key):
            try:
                # Each thread needs its own connection
                import sqlite3
                set_db_path(db_path)
                # Re-init connection for this thread
                close_connection()
                init_db()

                bill = open_draft_bill("owner1", chat_id)
                update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 8)
                result = finalize_bill(bill["bill_id"], "CASH", idempotency_key=idem_key)
                results[idx] = result
            except Exception as e:
                errors[idx] = str(e)

        t1 = threading.Thread(target=make_and_finalize, args=(0, "chat_a", "concurrent_1"))
        t2 = threading.Thread(target=make_and_finalize, args=(1, "chat_b", "concurrent_2"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Exactly one should succeed, one should fail
        successes = [r for r in results if r and r.get("status") == "FINALIZED"]
        failures = [r for r in results if r and r.get("error")]

        assert len(successes) + len(failures) == 2, f"Results: {results}, Errors: {errors}"
        assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}: {results}"
        assert len(failures) == 1, f"Expected exactly 1 failure, got {len(failures)}: {results}"

        # Final stock should be 10 - 8 = 2
        close_connection()
        set_db_path(db_path)
        product = lookup_product("MAGGI_70G")
        assert product["matches"][0]["stock_qty"] == 2.0

    def test_50_concurrent_finalizes_exactly_10_succeed(self, fresh_db):
        """
        PRD §5.1 property-style test:
        Stock = 10 Maggi. 50 concurrent bills each request 1.
        Exactly 10 should succeed, 40 should fail.
        CHECK(balance_after >= 0) must never be violated.
        """
        db_path = fresh_db
        conn = get_connection()
        conn.execute("DELETE FROM stock_ledger WHERE sku_id = 'MAGGI_70G'")
        conn.commit()
        receive_stock("MAGGI_70G", 10, idempotency_key="reseed_maggi_prop")

        N = 50
        results = [None] * N

        def make_and_finalize(idx):
            try:
                set_db_path(db_path)
                close_connection()
                init_db()

                bill = open_draft_bill("owner1", f"chat_prop_{idx}")
                update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 1)
                result = finalize_bill(bill["bill_id"], "CASH",
                                       idempotency_key=f"prop_test_{idx}")
                results[idx] = result
            except Exception as e:
                results[idx] = {"error": "exception", "message": str(e)}

        threads = [threading.Thread(target=make_and_finalize, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        successes = [r for r in results if r and r.get("status") == "FINALIZED"]
        failures = [r for r in results if r and r.get("error")]

        assert len(successes) == 10, \
            f"Expected 10 successes, got {len(successes)}. Failures: {len(failures)}"

        # Verify final stock is exactly 0
        close_connection()
        set_db_path(db_path)
        conn = get_connection()
        stock = conn.execute(
            "SELECT balance_after FROM stock_ledger WHERE sku_id = 'MAGGI_70G' "
            "ORDER BY ledger_id DESC LIMIT 1"
        ).fetchone()
        assert stock["balance_after"] == 0.0

        # Verify no negative balance_after entries exist
        negatives = conn.execute(
            "SELECT COUNT(*) as cnt FROM stock_ledger WHERE balance_after < 0"
        ).fetchone()
        assert negatives["cnt"] == 0
