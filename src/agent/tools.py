"""
Groq Tool Registry — maps PRD §4.3 capabilities to OpenAI-compatible tool schemas.
"""

from src.tools.inventory import lookup_product, list_low_stock, receive_stock, create_product
from src.tools.billing import open_draft_bill, update_draft_bill, get_draft_bill_summary, finalize_bill, void_bill
from src.tools.khata import get_khata_balance, charge_khata, settle_khata, list_khata_customers
from src.tools.analytics import get_daily_close
from src.tools.preferences import set_preference, get_preference
from src.documents.generators import generate_invoice_pdf, generate_analysis_deck

# Registry mapping tool names to actual functions
TOOL_FUNCTIONS = {
    "lookup_product": lookup_product,
    "list_low_stock": list_low_stock,
    "receive_stock": receive_stock,
    "create_product": create_product,
    
    "open_draft_bill": open_draft_bill,
    "update_draft_bill": update_draft_bill,
    "get_draft_bill_summary": get_draft_bill_summary,
    "finalize_bill": finalize_bill,
    "void_bill": void_bill,
    
    "get_khata_balance": get_khata_balance,
    "charge_khata": charge_khata,
    "settle_khata": settle_khata,
    "list_khata_customers": list_khata_customers,
    
    "get_daily_close": get_daily_close,
    
    "set_preference": set_preference,
    "get_preference": get_preference,
    
    "generate_invoice_pdf": generate_invoice_pdf,
    "generate_analysis_deck": generate_analysis_deck,
}

# ── OpenAI/Groq-compatible tool schemas ──
GROQ_TOOLS = [
    # ── INVENTORY ──
    {
        "type": "function",
        "function": {
            "name": "lookup_product",
            "description": "Search the product catalog by name or SKU ID to check prices, stock, and GST rates. Do this before adding items to bills or receiving stock if you are unsure of the exact SKU_ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Product name or SKU (e.g. 'maggi', 'ATTA_LOOSE')"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_low_stock",
            "description": "List all products that have fallen at or below their reorder level.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "receive_stock",
            "description": "Add new inventory to the stock ledger. Optionally update cost price or MRP in the catalog.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sku_id": {"type": "string", "description": "Exact SKU ID of the product"},
                    "qty": {"type": "number", "description": "Quantity received (positive number)"},
                    "cost_price_paise": {"type": "integer", "description": "Optional updated cost price in paise (Rs * 100)"},
                    "mrp_paise": {"type": "integer", "description": "Optional updated MRP in paise (Rs * 100)"},
                    "idempotency_key": {"type": "string", "description": "A unique key for this specific action to prevent double-processing. MUST be provided."}
                },
                "required": ["sku_id", "qty", "idempotency_key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_product",
            "description": "Onboard a new product into the catalog. Returns the newly generated sku_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the product"},
                    "unit": {"type": "string", "description": "Unit of measurement (e.g. 'packet', 'kg', 'bottle')"},
                    "is_loose": {"type": "boolean", "description": "True if sold in fractional quantities (like loose sugar)"},
                    "hsn_code": {"type": "string", "description": "GST HSN code (4, 6, or 8 digits)"},
                    "gst_rate_bps": {"type": "integer", "description": "GST rate in basis points (e.g., 18% = 1800, 5% = 500)"},
                    "cost_price_paise": {"type": "integer", "description": "Cost price in paise (Rs * 100)"},
                    "mrp_paise": {"type": "integer", "description": "MRP in paise (Rs * 100)"},
                    "sell_price_paise": {"type": "integer", "description": "Selling price in paise (Rs * 100)"},
                    "reorder_level_qty": {"type": "number", "description": "Quantity threshold to trigger low stock alerts"}
                },
                "required": ["name", "unit", "is_loose", "hsn_code", "gst_rate_bps", "cost_price_paise", "mrp_paise", "sell_price_paise", "reorder_level_qty"]
            }
        }
    },

    # ── BILLING ──
    {
        "type": "function",
        "function": {
            "name": "open_draft_bill",
            "description": "Start a new bill for the current customer or get the ID of the existing open draft.",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner_id": {"type": "string", "description": "The shop owner ID"},
                    "chat_id": {"type": "string", "description": "The Telegram chat ID"},
                    "customer_name": {"type": "string", "description": "Optional name of the customer"}
                },
                "required": ["owner_id", "chat_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_draft_bill",
            "description": "Add, modify, or remove an item on a draft bill. Never decrements stock. Returns the updated cart.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bill_id": {"type": "string", "description": "The DRAFT bill ID"},
                    "op": {"type": "string", "description": "Operation: 'ADD' (increase qty), 'SET_QTY' (replace qty), or 'REMOVE' (delete line)"},
                    "sku_id": {"type": "string", "description": "Exact SKU ID of the product"},
                    "qty": {"type": "number", "description": "Quantity to add/set (default 1.0 for ADD)"}
                },
                "required": ["bill_id", "op", "sku_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_draft_bill_summary",
            "description": "View the current contents, subtotal, and GST amounts for a draft bill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bill_id": {"type": "string"}
                },
                "required": ["bill_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_bill",
            "description": "Finalize a draft bill, decrement inventory, compute final GST, and optionally charge to Khata.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bill_id": {"type": "string", "description": "The DRAFT bill ID to finalize"},
                    "payment_mode": {"type": "string", "description": "'CASH', 'UPI', 'CARD', or 'KHATA'"},
                    "payment_ref": {"type": "string", "description": "Optional UPI/Card transaction reference"},
                    "khata_customer_id": {"type": "string", "description": "If payment_mode is KHATA, provide the customer ID to charge"},
                    "idempotency_key": {"type": "string", "description": "Unique key to prevent double-charging. MUST be provided."},
                    "confirm_below_cost": {"type": "boolean", "description": "Set to true if you are intentionally finalizing a bill where an item is sold below cost (after previously getting a below_cost warning)."}
                },
                "required": ["bill_id", "payment_mode", "idempotency_key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "void_bill",
            "description": "Cancel a finalized bill and restore inventory to the stock ledger. Cannot delete draft bills.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bill_id": {"type": "string", "description": "The FINALIZED bill ID"},
                    "reason": {"type": "string", "description": "Reason for voiding (e.g. 'Customer returned', 'Mistake')"}
                },
                "required": ["bill_id", "reason"]
            }
        }
    },

    # ── KHATA ──
    {
        "type": "function",
        "function": {
            "name": "get_khata_balance",
            "description": "Check the outstanding credit balance of a customer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id_or_name": {"type": "string", "description": "Customer ID or partial name"}
                },
                "required": ["customer_id_or_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "charge_khata",
            "description": "Add a debt to a customer's khata account (they owe you money). Do not use this for settling bills, use finalize_bill with mode=KHATA instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id_or_name": {"type": "string", "description": "Customer ID or partial name"},
                    "amount_paise": {"type": "integer", "description": "Amount to charge in paise (Rs * 100)"},
                    "ref_bill_id": {"type": "string", "description": "Optional related bill ID"},
                    "idempotency_key": {"type": "string", "description": "Unique key to prevent double-charging. MUST be provided."},
                    "confirm_new": {"type": "boolean", "description": "Set to true to explicitly create a new account if the customer does not exist."}
                },
                "required": ["customer_id_or_name", "amount_paise", "idempotency_key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "settle_khata",
            "description": "Record a payment from a customer towards their khata debt (reduces what they owe).",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id_or_name": {"type": "string", "description": "Customer ID or partial name"},
                    "amount_paise": {"type": "integer", "description": "Amount paid in paise (Rs * 100)"},
                    "idempotency_key": {"type": "string", "description": "Unique key to prevent double-processing. MUST be provided."}
                },
                "required": ["customer_id_or_name", "amount_paise", "idempotency_key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_khata_customers",
            "description": "List all customers with khata accounts and their current balances.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },

    # ── ANALYTICS ──
    {
        "type": "function",
        "function": {
            "name": "get_daily_close",
            "description": "Get revenue, GST totals, payment splits, and top items for a specific date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_date": {"type": "string", "description": "ISO date string YYYY-MM-DD. Defaults to today."}
                }
            }
        }
    },

    # ── PREFERENCES ──
    {
        "type": "function",
        "function": {
            "name": "set_preference",
            "description": "Save a preference for the shop owner (e.g., 'default_payment_mode'='UPI'). Persists across sessions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner_id": {"type": "string"},
                    "key": {"type": "string"},
                    "value": {"type": "string"}
                },
                "required": ["owner_id", "key", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_preference",
            "description": "Retrieve a specific preference for the shop owner.",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner_id": {"type": "string"},
                    "key": {"type": "string"}
                },
                "required": ["owner_id", "key"]
            }
        }
    },

    # ── DOCUMENTS ──
    {
        "type": "function",
        "function": {
            "name": "generate_invoice_pdf",
            "description": "Generate an A4 PDF tax invoice for a FINALIZED bill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bill_id": {"type": "string", "description": "The FINALIZED bill ID (optional)"},
                    "chat_id": {"type": "string", "description": "The Telegram chat ID"}
                },
                "required": ["chat_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_analysis_deck",
            "description": "Generate a PowerPoint (.pptx) deck analyzing the daily close.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_date": {"type": "string", "description": "ISO date string YYYY-MM-DD"}
                },
                "required": ["target_date"]
            }
        }
    }
]


def execute_tool(name: str, args: dict) -> dict:
    """Execute a tool by name with the given args."""
    if name not in TOOL_FUNCTIONS:
        return {"error": f"Unknown tool '{name}'"}
    try:
        # Convert floats back to ints for paise fields if the LLM sent floats
        for k, v in args.items():
            if k.endswith("_paise") or k.endswith("_bps"):
                if isinstance(v, float):
                    args[k] = int(v)
                    
        return TOOL_FUNCTIONS[name](**args)
    except Exception as e:
        return {"error": "tool_execution_failed", "message": str(e)}
