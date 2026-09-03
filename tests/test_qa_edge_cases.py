"""
QA Edge Cases and Malicious Inputs.

Exhaustive pytest suite that aggressively tries to break the system.
Covers database invariants, tool constraints, state machine guards,
and LLM hallucination mitigations.
"""

import threading
import pytest
from src.db.connection import get_connection, set_db_path, init_db, close_connection
from src.tools.inventory import receive_stock, lookup_product
from src.tools.billing import open_draft_bill, update_draft_bill, finalize_bill, void_bill
from src.tools.khata import settle_khata, charge_khata
from src.tools.gst import compute_line_tax, compute_bill_totals
from src.db.context import save_turn, load_recent_turns, MAX_HISTORY_TURNS

# ── 1. Database & Concurrency ──

def test_qa_oversell_race_conditions(fresh_db):
    """
    Oversell Race Conditions: Spin up 50 concurrent async tasks trying to buy 
    1 unit of a SKU that only has 10 units in stock. 
    Assert exactly 10 succeed, 40 fail, and balance_after is exactly 0.
    """
    db_path = fresh_db
    conn = get_connection()
    conn.execute("DELETE FROM stock_ledger WHERE sku_id = 'TATA_SALT_1KG'")
    conn.commit()
    receive_stock("TATA_SALT_1KG", 10, idempotency_key="qa_reseed_salt")

    N = 50
    results = [None] * N

    def make_purchase(idx):
        try:
            # Need thread-local connection
            set_db_path(db_path)
            close_connection()
            init_db()

            bill = open_draft_bill("qa_owner", f"chat_qa_{idx}")
            update_draft_bill(bill["bill_id"], "ADD", "TATA_SALT_1KG", 1)
            results[idx] = finalize_bill(bill["bill_id"], "CASH", idempotency_key=f"qa_buy_{idx}")
        except Exception as e:
            results[idx] = {"error": "exception", "message": str(e)}

    threads = [threading.Thread(target=make_purchase, args=(i,)) for i in range(N)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=30)

    successes = [r for r in results if r and r.get("status") == "FINALIZED"]
    failures = [r for r in results if r and r.get("error")]

    assert len(successes) == 10, f"Expected 10 successes, got {len(successes)}"
    assert len(failures) == 40, f"Expected 40 failures, got {len(failures)}"

    # Assert balance is exactly 0
    close_connection()
    set_db_path(db_path)
    final_stock = lookup_product("TATA_SALT_1KG")["matches"][0]["stock_qty"]
    assert final_stock == 0.0


def test_qa_idempotency_replays():
    """
    Simulate a Telegram network retry by calling finalize_bill and receive_stock 
    three times with the exact same idempotency_key. 
    Assert the database state mutates only once.
    """
    # 1. receive_stock replay
    key_recv = "qa_idem_recv"
    r1 = receive_stock("TATA_SALT_1KG", 15, idempotency_key=key_recv)
    r2 = receive_stock("TATA_SALT_1KG", 15, idempotency_key=key_recv)
    r3 = receive_stock("TATA_SALT_1KG", 15, idempotency_key=key_recv)

    assert r1["new_balance"] == r2["new_balance"] == r3["new_balance"]
    assert r2.get("idempotent_replay") is True
    assert r3.get("idempotent_replay") is True

    conn = get_connection()
    count_recv = conn.execute(
        "SELECT COUNT(*) as cnt FROM stock_ledger WHERE idempotency_key = ?",
        (key_recv,)
    ).fetchone()["cnt"]
    assert count_recv == 1

    # 2. finalize_bill replay
    bill = open_draft_bill("qa_owner", "chat_idem")
    update_draft_bill(bill["bill_id"], "ADD", "PARLE_G", 2)

    key_fin = "qa_idem_fin"
    f1 = finalize_bill(bill["bill_id"], "UPI", idempotency_key=key_fin)
    f2 = finalize_bill(bill["bill_id"], "UPI", idempotency_key=key_fin)
    f3 = finalize_bill(bill["bill_id"], "UPI", idempotency_key=key_fin)

    assert f1["status"] == f2["status"] == f3["status"] == "FINALIZED"
    assert f1["total_paise"] == f2["total_paise"] == f3["total_paise"]
    assert f2.get("idempotent_replay") is True

    count_sales = conn.execute(
        "SELECT COUNT(*) as cnt FROM stock_ledger WHERE ref_id = ? AND reason = 'SALE'",
        (bill["bill_id"],)
    ).fetchone()["cnt"]
    assert count_sales == 1


def test_qa_floating_point_drift():
    """
    Ensure GST calculations strictly use integer paise with round-half-up math.
    Malicious numbers: ₹33.33 @ 12% for 3 items.
    """
    # 3333 paise, qty 3. Taxable = 9999 paise.
    tax = compute_line_tax(3333, 3, 1200)
    assert tax["line_subtotal_paise"] == 9999
    
    # CGST = (9999 * 600 + 5000) // 10000 = 600
    assert tax["line_cgst_paise"] == 600
    assert tax["line_sgst_paise"] == 600
    assert tax["line_total_paise"] == 11199

    totals = compute_bill_totals([tax])
    assert totals["total_paise"] == 11199


# ── 2. State & Guardrail Bypassing ──

def test_qa_sell_below_cost_trap():
    """
    Try to finalize_bill where unit_price < cost_price without confirm_below_cost.
    Assert hard rejection requiring confirmation.
    """
    bill = open_draft_bill("qa_owner", "chat_below_cost")
    # Forcing a below-cost scenario by directly modifying the DB for testing
    conn = get_connection()
    conn.execute(
        "UPDATE products SET sell_price_paise = 500, cost_price_paise = 1000 WHERE sku_id = 'PARLE_G'"
    )
    conn.commit()

    update_draft_bill(bill["bill_id"], "ADD", "PARLE_G", 1)
    
    res = finalize_bill(bill["bill_id"], "CASH", idempotency_key="qa_below_cost")
    assert res.get("warning") == "below_cost"
    assert res.get("status") != "FINALIZED"

    # Confirm and pass
    res_confirmed = finalize_bill(bill["bill_id"], "CASH", idempotency_key="qa_below_cost_confirmed", confirm_below_cost=True)
    assert res_confirmed["status"] == "FINALIZED"


def test_qa_phantom_khata_settlements():
    """
    Attempt to settle_khata for a customer ID that does not exist.
    """
    res = settle_khata("THIS_CUSTOMER_DOES_NOT_EXIST", 5000, idempotency_key="qa_phantom")
    assert res.get("error") == "customer_not_found"


def test_qa_khata_overpayment():
    """
    Attempt to pay ₹1000 against a khata balance of ₹500.
    Assert rejection.
    """
    charge_khata("QaCustomer", 50000, confirm_new=True, idempotency_key="qa_charge_500")
    
    # Try to pay 100000 paise (Rs 1000)
    res = settle_khata("QaCustomer", 100000, idempotency_key="qa_pay_1000")
    assert res.get("error") == "overpayment"


def test_qa_voiding_finality():
    """
    Attempt to void_bill on a DRAFT.
    Attempt to modify a FINALIZED bill.
    """
    bill = open_draft_bill("qa_owner", "chat_state")
    update_draft_bill(bill["bill_id"], "ADD", "PARLE_G", 1)

    # 1. Void on DRAFT
    res_void = void_bill(bill["bill_id"], "mistake")
    assert res_void.get("error") == "bill_not_finalized"

    # Finalize it
    finalize_bill(bill["bill_id"], "CASH", idempotency_key="qa_state_fin")

    # 2. Modify FINALIZED
    res_mod = update_draft_bill(bill["bill_id"], "ADD", "PARLE_G", 1)
    assert res_mod.get("error") == "bill_not_draft"


# ── 3. LLM Hallucination Simulation ──

def test_qa_hallucinated_skus():
    """
    Mock an LLM tool call that passes a fabricated sku_id to update_draft_bill.
    Assert database rejects instantly.
    """
    bill = open_draft_bill("qa_owner", "chat_hallucinate")
    res = update_draft_bill(bill["bill_id"], "ADD", "FAKE_ITEM_123", 1)
    assert res.get("error") == "product_not_found"


def test_qa_hallucinated_prices():
    """
    Mock LLM attempting to pass its own calculated price.
    Since the tools don't accept price arguments, we just assert that
    even if it adds a line, the price is pulled strictly from DB.
    """
    # The tool schema for update_draft_bill takes: bill_id, op, sku_id, qty.
    # LLM cannot physically pass unit_price_paise.
    bill = open_draft_bill("qa_owner", "chat_price")
    
    # Even if LLM assumes Parle-G is Rs 50, backend forces Rs 10 (or whatever is in DB)
    res = update_draft_bill(bill["bill_id"], "ADD", "PARLE_G", 1)
    # Get DB price
    conn = get_connection()
    db_price = conn.execute("SELECT sell_price_paise FROM products WHERE sku_id = 'PARLE_G'").fetchone()["sell_price_paise"]
    
    assert res["lines"][0]["unit_price_paise"] == db_price


def test_qa_context_window_overflow(fresh_db):
    """
    Simulate a conversation with 50 turns. 
    Assert truncation drops older messages and retains exactly MAX_HISTORY_TURNS.
    """
    chat_id = "chat_overflow"
    
    for i in range(50):
        save_turn(chat_id, "user", f"Turn {i}")
        
    messages = load_recent_turns(chat_id)
    
    assert len(messages) == MAX_HISTORY_TURNS
    # The last turn saved was Turn 49.
    # We should have Turn 40 to Turn 49.
    assert messages[-1]["content"] == "Turn 49"
    assert messages[0]["content"] == f"Turn {50 - MAX_HISTORY_TURNS}"
