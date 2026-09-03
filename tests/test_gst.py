"""
PRD §5.2 — GST Rounding Correctness tests.

Test cases from the PRD:
  1. Rs.100 @ 5% -> CGST Rs.2.50, SGST Rs.2.50 (250 paise each)
  2. Rs.99.99 @ 18% -> verify per-line CGST+SGST within +/-1 paise
  3. Rs.33.33 @ 12% at qty=3 — the classic sum-of-thirds rounding trap
  4. Multi-item bill mixing 0%, 5%, 12%, 18% slabs
  5. Bill total = exact sum of stored (already-rounded) lines, never re-rounded
"""

from src.tools.gst import compute_line_tax, compute_bill_totals
from src.tools.billing import open_draft_bill, update_draft_bill, finalize_bill
from src.db.connection import get_connection


class TestGSTRounding:
    """PRD §4.4.2 — integer paise, round-half-up at basis-point precision."""

    def test_100_at_5_percent(self):
        """Rs.100 @ 5% -> CGST = 250 paise, SGST = 250 paise."""
        result = compute_line_tax(10000, 1, 500)  # 10000 paise = Rs.100, 500 bps = 5%

        assert result["line_subtotal_paise"] == 10000
        assert result["line_cgst_paise"] == 250
        assert result["line_sgst_paise"] == 250
        assert result["line_total_paise"] == 10500

    def test_9999_at_18_percent(self):
        """Rs.99.99 @ 18% -> verify CGST+SGST within +/-1 paise of expected."""
        result = compute_line_tax(9999, 1, 1800)  # 9999 paise = Rs.99.99, 1800 bps = 18%

        # Expected: 9999 * 900 / 10000 = 899.91 -> rounds to 900
        expected_cgst = (9999 * 900 + 5000) // 10000  # = (8999100 + 5000) // 10000 = 900
        assert result["line_cgst_paise"] == expected_cgst
        assert result["line_sgst_paise"] == expected_cgst
        assert abs(result["line_cgst_paise"] + result["line_sgst_paise"] - 1800) <= 1

    def test_3333_at_12_percent_qty_3(self):
        """
        Rs.33.33 @ 12% at qty=3 — the sum-of-thirds rounding trap.
        Total taxable = 33.33 * 3 = 99.99
        Per the PRD: we compute on the TOTAL line amount (unit_price * qty),
        not per-unit then multiply.
        """
        result = compute_line_tax(3333, 3, 1200)  # 3333 paise, qty=3, 1200 bps = 12%

        # Taxable = round(3333 * 3) = 9999 paise
        assert result["line_subtotal_paise"] == 9999

        # CGST = (9999 * 600 + 5000) // 10000 = (5999400 + 5000) // 10000 = 600
        expected_cgst = (9999 * 600 + 5000) // 10000
        assert result["line_cgst_paise"] == expected_cgst
        assert result["line_sgst_paise"] == expected_cgst

    def test_zero_gst(self):
        """0% GST items (loose staples) should have zero tax."""
        result = compute_line_tax(4500, 2, 0)  # Rs.45/kg, 2kg, 0% GST

        assert result["line_subtotal_paise"] == 9000
        assert result["line_cgst_paise"] == 0
        assert result["line_sgst_paise"] == 0
        assert result["line_total_paise"] == 9000

    def test_fractional_qty_loose_item(self):
        """Loose item with fractional qty (e.g., 0.5kg sugar)."""
        result = compute_line_tax(4500, 0.5, 0)  # Rs.45/kg, 0.5kg, 0% GST

        assert result["line_subtotal_paise"] == 2250  # 4500 * 0.5
        assert result["line_cgst_paise"] == 0
        assert result["line_total_paise"] == 2250

    def test_bill_total_is_sum_of_rounded_lines(self):
        """
        PRD §4.4.2: Bill totals = SUM of already-rounded line amounts,
        NOT a re-rounding of the subtotal.
        """
        lines = [
            compute_line_tax(3333, 1, 1200),  # 12% on Rs.33.33
            compute_line_tax(9999, 1, 1800),  # 18% on Rs.99.99
            compute_line_tax(4500, 2, 0),     # 0% on Rs.45 * 2
        ]

        totals = compute_bill_totals(lines)

        # Verify total is exactly sum of lines, not re-rounded
        assert totals["subtotal_paise"] == sum(l["line_subtotal_paise"] for l in lines)
        assert totals["cgst_paise"] == sum(l["line_cgst_paise"] for l in lines)
        assert totals["sgst_paise"] == sum(l["line_sgst_paise"] for l in lines)
        assert totals["total_paise"] == totals["subtotal_paise"] + totals["cgst_paise"] + totals["sgst_paise"]


class TestGSTInBilling:
    """End-to-end GST in the billing flow."""

    def test_mixed_slab_bill(self):
        """
        Multi-item bill mixing 0%, 5%, 12%, 18% slabs.
        Verifies that finalize_bill stores correct per-line GST.
        """
        bill = open_draft_bill("owner1", "chat_gst")

        # 0% — Sugar (loose) at Rs.45/kg, 2kg
        update_draft_bill(bill["bill_id"], "ADD", "SUGAR_LOOSE", 2)
        # 5% — Aashirvaad Atta at Rs.225/pkt
        update_draft_bill(bill["bill_id"], "ADD", "AASHIRVAAD_ATTA_5KG", 1)
        # 12% — Amul Butter at Rs.62/pkt
        update_draft_bill(bill["bill_id"], "ADD", "AMUL_BUTTER_100G", 1)
        # 18% — Maggi at Rs.14/pkt, qty 4
        update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 4)

        result = finalize_bill(bill["bill_id"], "CASH", idempotency_key="gst_mixed_test")
        assert result["status"] == "FINALIZED"

        # Verify stored per-line values
        conn = get_connection()
        lines = conn.execute(
            "SELECT sku_id, qty, unit_price_paise, gst_rate_bps, "
            "line_subtotal_paise, line_cgst_paise, line_sgst_paise "
            "FROM bill_lines WHERE bill_id = ? ORDER BY sku_id",
            (bill["bill_id"],),
        ).fetchall()

        for line in lines:
            # Verify each line's tax was computed correctly
            expected = compute_line_tax(line["unit_price_paise"], line["qty"], line["gst_rate_bps"])
            assert line["line_subtotal_paise"] == expected["line_subtotal_paise"]
            assert line["line_cgst_paise"] == expected["line_cgst_paise"]
            assert line["line_sgst_paise"] == expected["line_sgst_paise"]

        # Verify bill totals = sum of stored line amounts
        bill_row = conn.execute(
            "SELECT subtotal_paise, cgst_paise, sgst_paise, total_paise "
            "FROM bills WHERE bill_id = ?",
            (bill["bill_id"],),
        ).fetchone()

        sum_subtotal = sum(l["line_subtotal_paise"] for l in lines)
        sum_cgst = sum(l["line_cgst_paise"] for l in lines)
        sum_sgst = sum(l["line_sgst_paise"] for l in lines)

        assert bill_row["subtotal_paise"] == sum_subtotal
        assert bill_row["cgst_paise"] == sum_cgst
        assert bill_row["sgst_paise"] == sum_sgst
        assert bill_row["total_paise"] == sum_subtotal + sum_cgst + sum_sgst

    def test_specific_textbook_bill(self):
        """
        Cross-check: hand-computed multi-slab bill.
        
        Sugar (loose): 2kg @ Rs.45/kg, 0% GST
          Taxable = 9000, CGST = 0, SGST = 0, Total = 9000
        
        Aashirvaad Atta: 1 @ Rs.225, 5% GST
          Taxable = 22500, CGST = (22500*250+5000)//10000 = 563, SGST = 563, Total = 23626
        
        Amul Butter: 1 @ Rs.62, 12% GST
          Taxable = 6200, CGST = (6200*600+5000)//10000 = 372, SGST = 372, Total = 6944
        
        Maggi: 4 @ Rs.14, 18% GST
          Taxable = round(1400*4) = 5600, CGST = (5600*900+5000)//10000 = 504, SGST = 504, Total = 6608
        
        Bill Total: 9000 + 23626 + 6944 + 6608 = 46178 paise = Rs.461.78
        """
        bill = open_draft_bill("owner1", "chat_textbook")
        update_draft_bill(bill["bill_id"], "ADD", "SUGAR_LOOSE", 2)
        update_draft_bill(bill["bill_id"], "ADD", "AASHIRVAAD_ATTA_5KG", 1)
        update_draft_bill(bill["bill_id"], "ADD", "AMUL_BUTTER_100G", 1)
        update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 4)

        result = finalize_bill(bill["bill_id"], "CASH", idempotency_key="textbook_test")

        # Verify against hand computation
        # Sugar: 0 tax
        # Atta: CGST = (22500*250+5000)//10000 = 563
        # Butter: CGST = (6200*600+5000)//10000 = 372
        # Maggi: CGST = (5600*900+5000)//10000 = 504
        expected_cgst = 0 + 563 + 372 + 504  # = 1439
        expected_sgst = expected_cgst
        expected_subtotal = 9000 + 22500 + 6200 + 5600  # = 43300
        expected_total = expected_subtotal + expected_cgst + expected_sgst

        assert result["subtotal_paise"] == expected_subtotal
        assert result["cgst_paise"] == expected_cgst
        assert result["sgst_paise"] == expected_sgst
        assert result["total_paise"] == expected_total
