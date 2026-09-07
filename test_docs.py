import sys
import os
import shutil
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.connection import init_db, get_connection
from src.documents.generators import generate_invoice_pdf, generate_analysis_deck
from datetime import datetime

init_db()
conn = get_connection()

# Find a finalized bill to test PDF generation
bill = conn.execute("SELECT bill_id, chat_id FROM bills WHERE status = 'FINALIZED' LIMIT 1").fetchone()

if bill:
    bill_id = bill["bill_id"]
    chat_id = bill["chat_id"]
    print(f"Testing PDF generation for bill: {bill_id}")
    res_pdf = generate_invoice_pdf(chat_id=chat_id, bill_id=bill_id)
    print("PDF Result:", res_pdf)
    
    if "file_path" in res_pdf and os.path.exists(res_pdf["file_path"]):
        dest = os.path.join(os.getcwd(), os.path.basename(res_pdf["file_path"]))
        shutil.copy(res_pdf["file_path"], dest)
        print(f"Copied PDF to: {dest}")
else:
    print("No finalized bill found. Cannot test PDF generation. Please finalize a bill first.")

# Test PPTX generation
today = datetime.now().strftime("%Y-%m-%d")
print(f"\nTesting PPTX generation for date: {today}")
res_pptx = generate_analysis_deck(target_date=today)
print("PPTX Result:", res_pptx)

if "file_path" in res_pptx and os.path.exists(res_pptx["file_path"]):
    dest = os.path.join(os.getcwd(), os.path.basename(res_pptx["file_path"]))
    shutil.copy(res_pptx["file_path"], dest)
    print(f"Copied PPTX to: {dest}")
