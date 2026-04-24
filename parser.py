"""
parser.py — Convert OCR output into structured receipt data.

Two entry points:
  parse_receipt_paddle(lines, results, image_shape)
      → uses bounding box positions + rapidfuzz for best accuracy
      → called by pipeline.py when a real image is processed

  parse_receipt(raw_text)
      → text-only fallback, used by demo mode / tests with no image
"""

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Shared merchant list (merged from both original files)
# ---------------------------------------------------------------------------

KNOWN_MERCHANTS = [
    "albert heijn", "ah", "jumbo", "lidl", "aldi", "plus", "dirk",
    "spar", "action", "hema", "ikea", "mcdonalds", "mcdonald's",
    "starbucks", "subway", "ns", "translink",
    "kruidvat", "primark", "tesco", "blokker", "gamma", "mediamarkt",
    "coolblue", "decathlon", "zara", "h&m",
]

SKIP_PATTERNS = [
    r"^\s*$",
    r"btw|vat|tax",
    r"thank you|bedankt|welkom",
    r"kassabon|receipt|kwitantie",
    r"subtotal|sub-total",
    r"pin|debit|credit|cash|betaald|gepind",
    r"^[-=*_]{3,}$",
    r"datum|date|tijd|time",
    r"kasticket|ticket nr|nr\.",
    r"punten|points|kaart|loyal",   # loyalty noise from paddle script
]


def _is_noise(line: str) -> bool:
    line_lower = line.lower()
    return any(re.search(p, line_lower) for p in SKIP_PATTERNS)


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Merchant extraction
# ---------------------------------------------------------------------------

def _extract_merchant_fuzzy(lines: list[str]) -> str:
    """
    Use rapidfuzz for fuzzy merchant matching — handles OCR typos well.
    Falls back gracefully if rapidfuzz isn't installed.
    """
    try:
        from rapidfuzz import fuzz as _fuzz
    except ImportError:
        return _extract_merchant_simple(lines)

    candidates = []
    for i, line in enumerate(lines[:8]):
        clean = _normalize(line)
        if len(clean) < 3 or any(c.isdigit() for c in clean):
            continue
        candidates.append((clean, 100 - i * 10))

    best_match, best_score = None, 0
    for cand, pos_weight in candidates:
        for merchant in KNOWN_MERCHANTS:
            score = _fuzz.partial_ratio(cand, merchant)
            if merchant in cand:
                score += 30
            score += pos_weight
            if score > best_score:
                best_score = score
                best_match = merchant

    if best_score > 80 and best_match:
        return best_match.title()

    # Fallback: first non-empty non-digit line
    for line in lines[:5]:
        if line.strip() and not any(c.isdigit() for c in line):
            return line.strip().title()
    return "Unknown Merchant"


def _extract_merchant_simple(lines: list[str]) -> str:
    """Exact-match fallback used when rapidfuzz isn't available."""
    for line in lines[:5]:
        line_lower = line.lower().strip()
        for merchant in KNOWN_MERCHANTS:
            if merchant in line_lower:
                return line.strip().split("\n")[0].title()
    for line in lines[:5]:
        if line.strip():
            return line.strip().title()
    return "Unknown Merchant"


# ---------------------------------------------------------------------------
# Total + discount extraction (bounding-box aware)
# ---------------------------------------------------------------------------

def _clean_amount(text: str) -> Optional[float]:
    cleaned = re.sub(r"['\s]", ".", text.strip())
    cleaned = re.sub(r"[^\d.,-]", "", cleaned)
    cleaned = cleaned.replace(",", ".")
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Merchant — verbatim from test_ocr_paddle.py::extract_merchant()
# ---------------------------------------------------------------------------

def _extract_merchant_fuzzy(lines: list[str]) -> str:
    try:
        from rapidfuzz import fuzz as _fuzz
    except ImportError:
        return _extract_merchant_simple(lines)

    candidates = []
    for i, line in enumerate(lines[:8]):
        clean = _normalize(line)
        if len(clean) < 3:
            continue
        if any(char.isdigit() for char in clean):
            continue
        candidates.append((clean, 100 - i * 10))

    best_match = None
    best_score = 0

    for cand, pos_weight in candidates:
        for merchant in KNOWN_MERCHANTS:
            score = _fuzz.partial_ratio(cand, merchant)
            if merchant in cand:
                score += 30
            score += pos_weight
            if score > best_score:
                best_score = score
                best_match = merchant

    if best_score > 80 and best_match:
        return best_match.capitalize()

    # fallback: first non-empty non-digit line
    for line in lines[:5]:
        if line.strip() and not any(c.isdigit() for c in line):
            return line.strip().title()
    return "Unknown Merchant"


def _extract_merchant_simple(lines: list[str]) -> str:
    for line in lines[:5]:
        line_lower = line.lower().strip()
        for merchant in KNOWN_MERCHANTS:
            if merchant in line_lower:
                return line.strip().split("\n")[0].title()
    for line in lines[:5]:
        if line.strip():
            return line.strip().title()
    return "Unknown Merchant"


# ---------------------------------------------------------------------------
# Total + discount — verbatim from test_ocr_paddle.py::extract_values()
# ---------------------------------------------------------------------------

def _extract_values_paddle(results: list, image_shape: tuple) -> tuple[Optional[float], Optional[float]]:
    from rapidfuzz import fuzz

    height, width = image_shape[:2]

    totals = []
    discounts = []

    lines = [r[1][0].lower() for r in results]

    for i, line in enumerate(results):
        box, (text, score) = line

        if score < 0.5:
            continue

        context = " ".join(lines[max(0, i-2): i+3])

        # skip loyalty/points
        if any(word in context for word in ["punten", "points", "kaart", "loyal"]):
            continue

        matches = re.findall(r"-?\d+[.,]\d{2}|\b\d{1,4}\b", text)

        for m in matches:
            try:
                # prefer decimals strongly
                if re.fullmatch(r"\d{1,4}", m):
                    # reject plain integers unless VERY strong total context
                    if "tota" not in context:
                        continue
                    value = float(m)
                else:
                    value = float(m.replace(",", "."))

                if not (0.0 <= abs(value) <= 1000):
                    continue

                y_avg = sum([p[1] for p in box]) / 4
                x_avg = sum([p[0] for p in box]) / 4

                weight = 0
                if y_avg > height * 0.6:
                    weight += 40
                if x_avg > width * 0.5:
                    weight += 20

                # TOTAL
                if fuzz.partial_ratio(context, "totaal") > 65 or "tota" in context:
                    totals.append((value, weight + 300))
                    continue

                # DISCOUNT
                if fuzz.partial_ratio(context, "korting") > 70:
                    discounts.append((abs(value), weight + 200))
                    continue

            except Exception:
                pass

    def select_best(values):
        if not values:
            return None
        values.sort(key=lambda x: x[1], reverse=True)
        return values[0][0]

    total_paid = select_best(totals)
    discount   = select_best(discounts)

    # force 0 detection — verbatim from original
    for r in results:
        l = r[1][0]
        if "tota" in l.lower() and re.search(r"0+[.,]?0*", l):
            total_paid = 0.0

    if discount is not None and (total_paid is None or total_paid < discount):
        total_paid = 0.0

    return total_paid, discount


def _extract_total_text(lines: list[str]) -> Optional[float]:
    """Text-only total extraction — fallback when no bounding boxes available."""
    total = None
    for line in lines:
        if re.search(r"\btot(aal|al)?\b|\btotal\b|\bte betalen\b|\bamount due\b", line.lower()):
            match = re.search(r"(\d+[.,]\d{2})", line)
            if match:
                total = float(match.group(1).replace(",", "."))
    return total


def _extract_items(lines: list[str]) -> list[str]:
    items = []
    price_pattern = re.compile(r"\d+[.,]\d{2}")
    for line in lines:
        if _is_noise(line):
            continue
        if price_pattern.search(line):
            item_name = price_pattern.sub("", line).strip()
            item_name = re.sub(r"\s{2,}", " ", item_name).strip("-. \t")
            if item_name and len(item_name) > 1:
                items.append(item_name.title())
    return items


def _detect_currency(lines: list[str]) -> str:
    full = " ".join(lines)
    if "€" in full:
        return "€"
    if "£" in full:
        return "£"
    if "$" in full:
        return "$"
    return "€"


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def parse_receipt_paddle(lines: list[str], results: list, image_shape: tuple) -> dict:
    """
    Rich parser using PaddleOCR structured results.
    Called by pipeline.py when processing a real image.
    """
    merchant         = _extract_merchant_fuzzy(lines)
    currency         = _detect_currency(lines)
    total, discount  = _extract_values_paddle(results, image_shape)
    items            = _extract_items(lines)

    return {
        "merchant": merchant,
        "currency": currency,
        "items":    items,
        "total":    total,
        "discount": discount,
    }


def parse_receipt(raw_text: str) -> dict:
    """
    Text-only parser — used for demo/mock mode or Tesseract fallback.
    Keeps backward compatibility with pipeline.py's extract_text() path.
    """
    lines    = raw_text.splitlines()
    merchant = _extract_merchant_fuzzy(lines)
    total    = _extract_total_text(lines)
    items    = _extract_items(lines)

    return {
        "merchant": merchant,
        "items":    items,
        "total":    total,
        "discount": None,
    }


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

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