"""
Artifact Generation Tests.

Tests the PDF and PPTX document generation tools.
"""

import os
from datetime import datetime
from src.db.connection import get_connection
from src.tools.billing import open_draft_bill, update_draft_bill, finalize_bill
from src.documents.generators import generate_invoice_pdf, generate_analysis_deck

def test_generate_invoice_pdf_and_analysis_deck(fresh_db):
    """
    Test PDF invoice and PPTX analysis deck generation.
    1. Seed a finalized bill with mixed GST slabs.
    2. Verify PDF generation.
    3. Verify PPTX generation.
    4. Cleanup generated files.
    """
    # 1. Setup: Create a bill with mixed GST items
    # ATTA_LOOSE (0%), AMUL_BUTTER_100G (12%), MAGGI_70G (18%)
    bill = open_draft_bill("qa_owner", "chat_doc")
    
    update_draft_bill(bill["bill_id"], "ADD", "ATTA_LOOSE", 2.0)
    update_draft_bill(bill["bill_id"], "ADD", "AMUL_BUTTER_100G", 1)
    update_draft_bill(bill["bill_id"], "ADD", "MAGGI_70G", 5)
    
    # Need to manipulate stock so we don't hit oversell
    conn = get_connection()
    conn.execute("DELETE FROM stock_ledger")
    conn.commit()
    from src.tools.inventory import receive_stock
    receive_stock("ATTA_LOOSE", 100, idempotency_key="qa_doc_atta")
    receive_stock("AMUL_BUTTER_100G", 100, idempotency_key="qa_doc_butter")
    receive_stock("MAGGI_70G", 100, idempotency_key="qa_doc_maggi")
    
    fin_res = finalize_bill(bill["bill_id"], "CASH", idempotency_key="qa_doc_fin")
    print("\nFIN_RES:", fin_res, "\n")
    assert fin_res.get("status") == "FINALIZED"
    
    # 2. PDF Verification
    pdf_res = generate_invoice_pdf(bill["bill_id"])
    assert "error" not in pdf_res
    pdf_path = pdf_res["file_path"]
    
    assert os.path.exists(pdf_path), f"PDF file not found at {pdf_path}"
    assert os.path.getsize(pdf_path) > 0, "PDF file is empty (0 bytes)"
    assert pdf_path.endswith(".pdf")
    
    # 3. PPTX Verification
    today = datetime.now().strftime("%Y-%m-%d")
    # Our DB records the finalized bill timestamp in UTC, let's just use the timestamp substring from DB
    target_date = conn.execute("SELECT finalized_at FROM bills WHERE bill_id = ?", (bill["bill_id"],)).fetchone()["finalized_at"][:10]
    
    pptx_res = generate_analysis_deck(target_date)
    assert "error" not in pptx_res
    pptx_path = pptx_res["file_path"]
    
    assert os.path.exists(pptx_path), f"PPTX file not found at {pptx_path}"
    assert os.path.getsize(pptx_path) > 0, "PPTX file is empty (0 bytes)"
    assert pptx_path.endswith(".pptx")
    
    # 4. Teardown
    os.remove(pdf_path)
    os.remove(pptx_path)
    
    assert not os.path.exists(pdf_path)
    assert not os.path.exists(pptx_path)
