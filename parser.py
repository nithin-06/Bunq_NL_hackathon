"""
parser.py — Convert messy OCR text into structured receipt data.
"""

import re
from typing import Optional


# Common Dutch/EU supermarket names (extend as needed)
KNOWN_MERCHANTS = [
    "albert heijn", "ah", "jumbo", "lidl", "aldi", "plus", "dirk",
    "spar", "action", "hema", "ikea", "mcdonalds", "mcdonald's",
    "starbucks", "subway", "ns", "translink"
]

# Lines to ignore — they're noise, not items
SKIP_PATTERNS = [
    r"^\s*$",                        # empty lines
    r"btw|vat|tax",                  # tax lines
    r"thank you|bedankt|welkom",     # pleasantries
    r"kassabon|receipt|kwitantie",   # receipt headers
    r"subtotal|sub-total",
    r"pin|debit|credit|cash|betaald|gepind",
    r"^[-=*_]{3,}$",                 # divider lines
    r"datum|date|tijd|time",
    r"kasticket|ticket nr|nr\.",
]


def _is_noise(line: str) -> bool:
    line_lower = line.lower()
    for pattern in SKIP_PATTERNS:
        if re.search(pattern, line_lower):
            return True
    return False


def _extract_merchant(lines: list[str]) -> str:
    """Check first few lines for a known merchant name."""
    for line in lines[:5]:
        line_lower = line.lower().strip()
        for merchant in KNOWN_MERCHANTS:
            if merchant in line_lower:
                # Return a clean title-cased version
                return line.strip().split("\n")[0].title()
    # Fallback: just return the first non-empty line
    for line in lines[:5]:
        if line.strip():
            return line.strip().title()
    return "Unknown Merchant"


def _extract_total(lines: list[str]) -> Optional[float]:
    """
    Look for a TOTAAL / TOTAL line and extract the amount.
    Tries the last occurrence (most receipts show subtotals before the real total).
    """
    total = None
    for line in lines:
        line_lower = line.lower()
        if re.search(r"\btot(aal|al)?\b|\btotal\b|\bte betalen\b|\bamount due\b", line_lower):
            # Find a price pattern: digits, optional comma/dot, digits
            match = re.search(r"(\d+[.,]\d{2})", line)
            if match:
                amount_str = match.group(1).replace(",", ".")
                total = float(amount_str)
    return total


def _extract_items(lines: list[str], total_approx: Optional[float]) -> list[str]:
    """
    Pull out item lines — lines that look like they have a product + price.
    """
    items = []
    price_pattern = re.compile(r"\d+[.,]\d{2}")

    for line in lines:
        if _is_noise(line):
            continue
        # Item lines usually have a price somewhere on the same line
        if price_pattern.search(line):
            # Strip the price out to get just the item name
            item_name = price_pattern.sub("", line).strip()
            item_name = re.sub(r"\s{2,}", " ", item_name)  # collapse spaces
            item_name = item_name.strip("-. \t")
            if item_name and len(item_name) > 1:
                items.append(item_name.title())

    return items


def parse_receipt(raw_text: str) -> dict:
    """
    Parse raw OCR text into structured receipt data.

    Args:
        raw_text: Output from ocr.extract_text()

    Returns:
        {
            "merchant": str,
            "items": list[str],
            "total": float | None
        }
    """
    lines = raw_text.splitlines()

    merchant = _extract_merchant(lines)
    total = _extract_total(lines)
    items = _extract_items(lines, total)

    return {
        "merchant": merchant,
        "items": items,
        "total": total,
    }


# --- quick test ---
if __name__ == "__main__":
    sample = """
    ALBERT HEIJN
    Kalverstraat 100, Amsterdam

    Halfvolle Melk         0.89
    Volkoren Brood         1.29
    Sinaasappels 6st       2.49
    Kaas Jong 500g         3.99

    Subtotaal              8.66
    BTW 9%                 0.78
    TOTAAL                 8.66

    Bedankt voor uw bezoek
    """
    import json
    result = parse_receipt(sample)
    print(json.dumps(result, indent=2))