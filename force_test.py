import sys
import os
import shutil
import uuid
from datetime import datetime
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.db.connection import init_db, get_connection
from src.documents.generators import generate_invoice_pdf, generate_analysis_deck

# Initialize DB
init_db()
conn = get_connection()

# 1. Force Inventory Update
sugar_sku = conn.execute("SELECT sku_id, name, sell_price_paise, gst_rate_bps, hsn_code FROM products WHERE LOWER(name) LIKE '%sugar%' LIMIT 1").fetchone()
maggi_sku = conn.execute("SELECT sku_id, name, sell_price_paise, gst_rate_bps, hsn_code FROM products WHERE LOWER(name) LIKE '%maggi%' LIMIT 1").fetchone()

if not sugar_sku or not maggi_sku:
    print("Could not find Sugar or Maggi in the database.")
    sys.exit(1)

print(f"Found SKUs - Sugar: {sugar_sku['sku_id']}, Maggi: {maggi_sku['sku_id']}")

sugar_qty_row = conn.execute("SELECT qty FROM stock_current WHERE sku_id = ?", (sugar_sku['sku_id'],)).fetchone()
sugar_current = sugar_qty_row["qty"] if sugar_qty_row else 0.0
conn.execute("INSERT INTO stock_ledger (sku_id, delta_qty, reason, balance_after, created_at, idempotency_key) VALUES (?, ?, ?, ?, ?, ?)",
             (sugar_sku['sku_id'], 100.0, 'Force test', sugar_current + 100.0, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(uuid.uuid4())))

maggi_qty_row = conn.execute("SELECT qty FROM stock_current WHERE sku_id = ?", (maggi_sku['sku_id'],)).fetchone()
maggi_current = maggi_qty_row["qty"] if maggi_qty_row else 0.0
conn.execute("INSERT INTO stock_ledger (sku_id, delta_qty, reason, balance_after, created_at, idempotency_key) VALUES (?, ?, ?, ?, ?, ?)",
             (maggi_sku['sku_id'], 100.0, 'Force test', maggi_current + 100.0, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(uuid.uuid4())))
print("Added 100 units to Sugar and Maggi inventory.")

# 2. Inject Finalized Bill
bill_id = str(uuid.uuid4())
chat_id = "test_chat_id_123"
today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
today_date = datetime.now().strftime("%Y-%m-%d")

sugar_qty = 2.0
maggi_qty = 6.0

sugar_subtotal = sugar_sku['sell_price_paise'] * sugar_qty
maggi_subtotal = maggi_sku['sell_price_paise'] * maggi_qty

sugar_gst = int(sugar_subtotal * sugar_sku['gst_rate_bps'] / 10000)
maggi_gst = int(maggi_subtotal * maggi_sku['gst_rate_bps'] / 10000)

total_subtotal = sugar_subtotal + maggi_subtotal
total_cgst = (sugar_gst + maggi_gst) // 2
total_sgst = (sugar_gst + maggi_gst) // 2
total_tax = total_cgst + total_sgst
total_paise = int(total_subtotal + total_tax)

conn.execute("""
    INSERT INTO bills (bill_id, owner_id, chat_id, customer_name, status, payment_mode, subtotal_paise, cgst_paise, sgst_paise, total_paise, created_at, finalized_at)
    VALUES (?, 'owner_1', ?, 'Test Customer', 'FINALIZED', 'UPI', ?, ?, ?, ?, ?, ?)
""", (bill_id, chat_id, total_subtotal, total_cgst, total_sgst, total_paise, today, today))

conn.execute("""
    INSERT INTO bill_lines (bill_id, sku_id, qty, unit_price_paise, gst_rate_bps, hsn_code, line_subtotal_paise, line_cgst_paise, line_sgst_paise)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (bill_id, sugar_sku['sku_id'], sugar_qty, sugar_sku['sell_price_paise'], sugar_sku['gst_rate_bps'], sugar_sku['hsn_code'], sugar_subtotal, sugar_gst // 2, sugar_gst // 2))

conn.execute("""
    INSERT INTO bill_lines (bill_id, sku_id, qty, unit_price_paise, gst_rate_bps, hsn_code, line_subtotal_paise, line_cgst_paise, line_sgst_paise)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (bill_id, maggi_sku['sku_id'], maggi_qty, maggi_sku['sell_price_paise'], maggi_sku['gst_rate_bps'], maggi_sku['hsn_code'], maggi_subtotal, maggi_gst // 2, maggi_gst // 2))

conn.commit()
print(f"Injected finalized bill {bill_id} for {today}")

# 3. Trigger Document Generators
print("\nGenerating Documents...")
res_pdf = generate_invoice_pdf(chat_id=chat_id, bill_id=bill_id)
res_pptx = generate_analysis_deck(target_date=today_date)

# 4. Output Verification
if "file_path" in res_pdf and os.path.exists(res_pdf["file_path"]):
    abs_pdf = os.path.abspath(res_pdf["file_path"])
    print(f"PDF Generated successfully: {abs_pdf}")
else:
    print(f"PDF Generation failed: {res_pdf}")

if "file_path" in res_pptx and os.path.exists(res_pptx["file_path"]):
    abs_pptx = os.path.abspath(res_pptx["file_path"])
    print(f"PPTX Generated successfully: {abs_pptx}")
else:
    print(f"PPTX Generation failed: {res_pptx}")
