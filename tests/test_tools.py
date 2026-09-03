"""
Additional tool tests — billing flow, void_bill, preferences, inventory.

Covers:
  - Multi-turn bill editing (add, set_qty, remove)
  - Draft bill → no stock change
  - void_bill → compensating reversal entries
  - Preferences persistence
  - Product lookup and creation
  - Sell-below-cost guardrail
"""

from src.tools.billing import (
    open_draft_bill, update_draft_bill, get_draft_bill_summary,
    finalize_bill, void_bill,
)
from src.tools.inventory import lookup_product, receive_stock, create_product, list_low_stock
from src.tools.preferences import set_preference, get_preference, get_all_preferences
from src.tools.analytics import get_daily_close
from src.db.connection import get_connection


class TestBillingFlow:
    """Multi-turn bill editing and finalization."""

    def test_draft_does_not_decrement_stock(self):
        """PRD: draft bills only touch bill_lines, never stock_ledger."""
        initial = lookup_product("MAGGI_70G")
        initial_stock = initial["matches"][0]["stock_qty"]

        bill = open_draft_bill("owner1", "chat_draft")
        update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 10)

        # Stock should be unchanged
        current = lookup_product("MAGGI_70G")
        assert current["matches"][0]["stock_qty"] == initial_stock

    def test_open_returns_existing_draft(self):
        """Calling open_draft_bill again for same chat returns the same draft."""
        bill1 = open_draft_bill("owner1", "chat_same")
        bill2 = open_draft_bill("owner1", "chat_same")

        assert bill1["bill_id"] == bill2["bill_id"]
        assert bill2["is_new"] is False

    def test_add_set_remove_operations(self):
        """Full edit flow: ADD, ADD more, SET_QTY, REMOVE."""
        bill = open_draft_bill("owner1", "chat_edit")

        # ADD 3 Maggi
        r1 = update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 3)
        assert len(r1["lines"]) == 1
        assert r1["lines"][0]["qty"] == 3

        # ADD 2 more Maggi (should become 5)
        r2 = update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 2)
        assert r2["lines"][0]["qty"] == 5

        # ADD a different item
        r3 = update_draft_bill(bill["bill_id"], "ADD", "SUGAR_LOOSE", 2)
        assert len(r3["lines"]) == 2

        # SET_QTY Maggi to 6
        r4 = update_draft_bill(bill["bill_id"], "SET_QTY", "MAGGI_70G", 6)
        maggi_line = [l for l in r4["lines"] if l["sku_id"] == "MAGGI_70G"][0]
        assert maggi_line["qty"] == 6

        # REMOVE Sugar
        r5 = update_draft_bill(bill["bill_id"], "REMOVE", "SUGAR_LOOSE")
        assert len(r5["lines"]) == 1
        assert r5["lines"][0]["sku_id"] == "MAGGI_70G"

    def test_empty_bill_cannot_finalize(self):
        """Cannot finalize a bill with no items."""
        bill = open_draft_bill("owner1", "chat_empty")
        result = finalize_bill(bill["bill_id"], "CASH", idempotency_key="empty_bill")
        assert result.get("error") == "empty_bill"

    def test_bill_summary_computed_live(self):
        """get_draft_bill_summary returns live totals, not persisted."""
        bill = open_draft_bill("owner1", "chat_summary")
        update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 5)

        summary = get_draft_bill_summary(bill["bill_id"])
        assert summary["total_paise"] > 0
        assert summary["status"] == "DRAFT"


class TestVoidBill:
    """void_bill writes compensating REVERSAL entries."""

    def test_void_restores_stock(self):
        """Voiding a finalized bill restores stock via REVERSAL entries."""
        initial = lookup_product("MAGGI_70G")
        initial_stock = initial["matches"][0]["stock_qty"]

        bill = open_draft_bill("owner1", "chat_void")
        update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 10)
        finalize_bill(bill["bill_id"], "CASH", idempotency_key="void_test_fin")

        # Stock should be reduced
        after_fin = lookup_product("MAGGI_70G")
        assert after_fin["matches"][0]["stock_qty"] == initial_stock - 10

        # Void the bill
        void_result = void_bill(bill["bill_id"], "Customer returned items")
        assert void_result["status"] == "VOID"

        # Stock should be restored
        after_void = lookup_product("MAGGI_70G")
        assert after_void["matches"][0]["stock_qty"] == initial_stock

        # Verify REVERSAL entry exists (not a DELETE)
        conn = get_connection()
        reversals = conn.execute(
            "SELECT * FROM stock_ledger WHERE ref_id = ? AND reason = 'REVERSAL'",
            (bill["bill_id"],),
        ).fetchall()
        assert len(reversals) == 1
        assert reversals[0]["delta_qty"] == 10  # positive (restoring)

    def test_void_draft_rejected(self):
        """Can only void FINALIZED bills, not DRAFTs."""
        bill = open_draft_bill("owner1", "chat_void_draft")
        result = void_bill(bill["bill_id"], "mistake")
        assert result.get("error") == "bill_not_finalized"


class TestSellBelowCost:
    """PRD §4.4.5 — sell-below-cost guardrail."""

    def test_below_cost_warning(self):
        """
        If sell_price < cost_price, finalize returns a warning
        requiring confirm_below_cost=True.
        """
        # Create a product where sell < cost
        create_product(
            name="Clearance Item",
            unit="packet",
            is_loose=False,
            hsn_code="9999",
            gst_rate_bps=1800,
            cost_price_paise=10000,  # Rs.100 cost
            mrp_paise=8000,         # Rs.80 MRP (below cost!)
            sell_price_paise=8000,
            reorder_level_qty=5,
        )
        receive_stock("CLEARANCE_ITEM", 10, idempotency_key="clearance_stock")

        bill = open_draft_bill("owner1", "chat_below_cost")
        update_draft_bill(bill["bill_id"], "ADD", "CLEARANCE_ITEM", 1)
        result = finalize_bill(bill["bill_id"], "CASH", idempotency_key="below_cost_test")

        assert result.get("warning") == "below_cost"
        assert len(result["items"]) == 1

        # Confirm and proceed
        result2 = finalize_bill(bill["bill_id"], "CASH",
                                idempotency_key="below_cost_confirm",
                                confirm_below_cost=True)
        assert result2["status"] == "FINALIZED"


class TestInventory:
    """Product lookup and creation."""

    def test_fuzzy_lookup(self):
        """Fuzzy search finds products by partial name."""
        result = lookup_product("maggi")
        assert len(result["matches"]) >= 1
        assert result["matches"][0]["sku_id"] == "MAGGI_70G"

    def test_ambiguous_atta_lookup(self):
        """'atta' should return BOTH Aashirvaad Atta and loose atta."""
        result = lookup_product("atta")
        assert len(result["matches"]) >= 2
        sku_ids = {m["sku_id"] for m in result["matches"]}
        assert "AASHIRVAAD_ATTA_5KG" in sku_ids
        assert "ATTA_LOOSE" in sku_ids

    def test_not_found(self):
        """Non-existent product returns error."""
        result = lookup_product("xyznonexistent")
        assert result.get("error") == "not_found"

    def test_create_product(self):
        """New SKU creation."""
        result = create_product(
            name="Dairy Milk Silk",
            unit="packet",
            is_loose=False,
            hsn_code="1806",
            gst_rate_bps=1800,
            cost_price_paise=8000,
            mrp_paise=10000,
            sell_price_paise=10000,
            reorder_level_qty=20,
        )
        assert "sku_id" in result
        assert "error" not in result

    def test_low_stock(self):
        """list_low_stock returns items at or below reorder level."""
        result = list_low_stock()
        # Some seeded items might already be at reorder level
        assert "items" in result


class TestPreferences:
    """Preference persistence (memory across sessions)."""

    def test_set_and_get(self):
        """Set a preference and read it back."""
        set_preference("owner1", "default_payment_mode", "UPI")
        result = get_preference("owner1", "default_payment_mode")
        assert result["value"] == "UPI"

    def test_get_unset(self):
        """Unset preference returns error."""
        result = get_preference("owner1", "nonexistent_key")
        assert result.get("error") == "not_set"

    def test_upsert(self):
        """Setting the same key again updates the value."""
        set_preference("owner1", "shop_name", "Old Name")
        set_preference("owner1", "shop_name", "Sharma General Store")
        result = get_preference("owner1", "shop_name")
        assert result["value"] == "Sharma General Store"

    def test_get_all(self):
        """Bulk load all preferences."""
        set_preference("owner1", "key1", "val1")
        set_preference("owner1", "key2", "val2")
        result = get_all_preferences("owner1")
        assert "key1" in result["preferences"]
        assert "key2" in result["preferences"]

    def test_owner_scoped(self):
        """Different owners have separate preferences."""
        set_preference("owner_a", "shop_name", "Shop A")
        set_preference("owner_b", "shop_name", "Shop B")
        assert get_preference("owner_a", "shop_name")["value"] == "Shop A"
        assert get_preference("owner_b", "shop_name")["value"] == "Shop B"


class TestAnalytics:
    """Daily close analytics."""

    def test_daily_close_empty(self):
        """No bills for a date → zero totals."""
        result = get_daily_close("2020-01-01")
        assert result["revenue_paise"] == 0
        assert result["bill_count"] == 0

    def test_daily_close_after_bill(self):
        """After finalizing a bill, daily close includes it."""
        bill = open_draft_bill("owner1", "chat_analytics")
        update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 5)
        finalize_bill(bill["bill_id"], "UPI", idempotency_key="analytics_test")

        # Use the actual finalized_at date (UTC) from the DB
        conn = get_connection()
        row = conn.execute(
            "SELECT finalized_at FROM bills WHERE bill_id = ?", (bill["bill_id"],)
        ).fetchone()
        # finalized_at is an ISO timestamp like '2026-09-03T19:50:00+00:00'
        utc_date = row["finalized_at"][:10]

        result = get_daily_close(utc_date)
        assert result["revenue_paise"] > 0
        assert result["bill_count"] >= 1
        assert "UPI" in result["mode_split"]

