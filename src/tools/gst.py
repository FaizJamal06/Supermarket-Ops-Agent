"""
GST calculation helpers — PRD §4.4.2.

All monetary values are integer paise. Tax rates are basis points (bps).
Round-half-up at the line level using the formula:
    cgst = (taxable_amount * (gst_rate_bps / 2) + 5000) // 10000
    sgst = cgst  (symmetric intra-state split)
"""


def compute_line_tax(unit_price_paise: int, qty: float, gst_rate_bps: int) -> dict:
    """
    Compute per-line GST amounts using round-half-up in integer paise.

    Args:
        unit_price_paise: price per unit in paise (integer)
        qty: quantity (can be fractional for loose items)
        gst_rate_bps: GST rate in basis points (e.g. 500 = 5%, 1800 = 18%)

    Returns:
        dict with keys:
            line_subtotal_paise: taxable amount (unit_price * qty)
            line_cgst_paise: CGST amount (round-half-up)
            line_sgst_paise: SGST amount (= CGST, symmetric split)
            line_total_paise: subtotal + cgst + sgst
    """
    # Taxable amount: for fractional qty (loose items), we need to handle carefully
    # Convert to integer paise: round the taxable amount itself
    taxable = round(unit_price_paise * qty)

    if gst_rate_bps == 0:
        return {
            "line_subtotal_paise": taxable,
            "line_cgst_paise": 0,
            "line_sgst_paise": 0,
            "line_total_paise": taxable,
        }

    # PRD §4.4.2 formula: round-half-up at basis-point precision
    # CGST rate = gst_rate_bps / 2 (intra-state split)
    half_rate_bps = gst_rate_bps // 2  # integer division, GST rates are always even in bps

    # cgst = (taxable * half_rate_bps + 5000) // 10000
    cgst = (taxable * half_rate_bps + 5000) // 10000
    sgst = cgst  # Symmetric intra-state split

    return {
        "line_subtotal_paise": taxable,
        "line_cgst_paise": cgst,
        "line_sgst_paise": sgst,
        "line_total_paise": taxable + cgst + sgst,
    }


def compute_bill_totals(lines: list[dict]) -> dict:
    """
    Compute bill-level totals from already-rounded line amounts.

    Per PRD §4.4.2: bill totals are the SUM of already-rounded line amounts,
    NOT a re-rounding of the subtotal. This matches real GST invoice reconciliation.

    Args:
        lines: list of dicts, each with line_subtotal_paise, line_cgst_paise, line_sgst_paise

    Returns:
        dict with subtotal_paise, cgst_paise, sgst_paise, total_paise
    """
    subtotal = sum(l["line_subtotal_paise"] for l in lines)
    cgst = sum(l["line_cgst_paise"] for l in lines)
    sgst = sum(l["line_sgst_paise"] for l in lines)

    return {
        "subtotal_paise": subtotal,
        "cgst_paise": cgst,
        "sgst_paise": sgst,
        "total_paise": subtotal + cgst + sgst,
    }
