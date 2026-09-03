"""
PRD §5.4 — Khata Invariants tests.

Tests:
  1. settle_khata on non-existent customer → rejection, no row created
  2. charge + settle sequence → balance_after matches account balance at every step
  3. settle for more than outstanding → rejection (no overpayment)
  4. charge_khata without confirm_new → ask for confirmation
  5. charge_khata with confirm_new=True → creates new account
"""

from src.tools.khata import charge_khata, settle_khata, get_khata_balance, list_khata_customers
from src.db.connection import get_connection


class TestKhataInvariants:
    """PRD §4.4.5 — khata guardrails."""

    def test_settle_nonexistent_customer_rejected(self):
        """settle_khata on non-existent customer → rejection, no row created."""
        result = settle_khata("ghost_customer", 50000, idempotency_key="settle_ghost")

        assert result.get("error") == "customer_not_found"

        # Verify no row was created
        conn = get_connection()
        ghost = conn.execute(
            "SELECT COUNT(*) as cnt FROM khata_accounts WHERE customer_id = 'ghost_customer'"
        ).fetchone()
        assert ghost["cnt"] == 0

    def test_charge_without_confirm_asks(self):
        """charge_khata on unknown customer without confirm_new → needs_confirmation."""
        result = charge_khata("Ramesh", 50000, idempotency_key="charge_noconfirm")

        assert result.get("error") == "customer_not_found"
        assert result.get("needs_confirmation") is True

    def test_charge_with_confirm_creates_customer(self):
        """charge_khata with confirm_new=True → creates account and records charge."""
        result = charge_khata("Ramesh", 50000, confirm_new=True,
                              idempotency_key="charge_new_ramesh")

        assert "error" not in result
        assert result["new_balance"] == 50000
        assert result["customer_name"] == "Ramesh"

    def test_full_khata_cycle(self):
        """
        PRD §5.4: charge + settle → balance matches at every step.
        
        1. Charge Ramesh Rs.500 (50000 paise)
        2. Query balance → 50000
        3. Settle Rs.300 (30000 paise)
        4. Query balance → 20000
        """
        # Create Ramesh and charge 500
        charge_result = charge_khata("Ramesh", 50000, confirm_new=True,
                                     idempotency_key="cycle_charge")
        assert charge_result["new_balance"] == 50000

        # Query balance
        balance = get_khata_balance("Ramesh")
        assert balance["balance_paise"] == 50000

        # Verify ledger matches account
        conn = get_connection()
        ledger = conn.execute(
            "SELECT balance_after_paise FROM khata_ledger WHERE customer_id = ? "
            "ORDER BY entry_id DESC LIMIT 1",
            (charge_result["customer_id"],),
        ).fetchone()
        account = conn.execute(
            "SELECT balance_paise FROM khata_accounts WHERE customer_id = ?",
            (charge_result["customer_id"],),
        ).fetchone()
        assert ledger["balance_after_paise"] == account["balance_paise"]

        # Settle 300
        settle_result = settle_khata("Ramesh", 30000, idempotency_key="cycle_settle")
        assert settle_result["new_balance"] == 20000

        # Query balance again
        balance2 = get_khata_balance("Ramesh")
        assert balance2["balance_paise"] == 20000

        # Verify ledger still matches account
        ledger2 = conn.execute(
            "SELECT balance_after_paise FROM khata_ledger WHERE customer_id = ? "
            "ORDER BY entry_id DESC LIMIT 1",
            (charge_result["customer_id"],),
        ).fetchone()
        account2 = conn.execute(
            "SELECT balance_paise FROM khata_accounts WHERE customer_id = ?",
            (charge_result["customer_id"],),
        ).fetchone()
        assert ledger2["balance_after_paise"] == account2["balance_paise"]
        assert account2["balance_paise"] == 20000

    def test_settle_more_than_owed_rejected(self):
        """
        PRD §5.4: settle for more than outstanding → rejection.
        Cannot overpay into negative balance.
        """
        # Create Ramesh with Rs.500 balance
        charge_khata("Ramesh", 50000, confirm_new=True,
                     idempotency_key="overpay_charge")

        # Try to settle Rs.800 (more than owed)
        result = settle_khata("Ramesh", 80000, idempotency_key="overpay_settle")

        assert result.get("error") == "overpayment"
        assert "80000" in result["message"] or "exceeds" in result["message"].lower()

        # Balance should be unchanged
        balance = get_khata_balance("Ramesh")
        assert balance["balance_paise"] == 50000

    def test_settle_when_zero_balance_rejected(self):
        """Settle when balance is 0 → rejection."""
        # Create customer with 0 balance
        charge_khata("Priya", 10000, confirm_new=True,
                     idempotency_key="zero_charge")
        settle_khata("Priya", 10000, idempotency_key="zero_settle")

        # Now try to settle again
        result = settle_khata("Priya", 5000, idempotency_key="zero_settle_again")
        assert result.get("error") == "no_outstanding_balance"

    def test_khata_linked_to_bill(self):
        """charge_khata with ref_bill_id → traceable to the bill."""
        from src.tools.billing import open_draft_bill, update_draft_bill, finalize_bill

        # Create and finalize a bill on khata
        charge_khata("Suresh", 1, confirm_new=True, idempotency_key="link_create")
        # Settle the 1 paise to clean state
        settle_khata("Suresh", 1, idempotency_key="link_settle_init")

        bill = open_draft_bill("owner1", "chat_link")
        update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 2)

        # Finalize with khata
        balance = get_khata_balance("Suresh")
        customer_id = balance["customer_id"]
        result = finalize_bill(bill["bill_id"], "KHATA",
                               khata_customer_id=customer_id,
                               idempotency_key="link_finalize")
        assert result["status"] == "FINALIZED"

        # Verify khata ledger entry links to the bill
        conn = get_connection()
        entry = conn.execute(
            "SELECT ref_bill_id FROM khata_ledger "
            "WHERE customer_id = ? AND reason = 'CHARGE' AND ref_bill_id IS NOT NULL",
            (customer_id,),
        ).fetchone()
        assert entry is not None
        assert entry["ref_bill_id"] == bill["bill_id"]
