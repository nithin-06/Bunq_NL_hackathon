"""
parser.py - Convert OCR output into structured receipt data.
"""

from __future__ import annotations

import re
from typing import Optional


KNOWN_MERCHANTS = [
    "albert heijn", "ah", "jumbo", "lidl", "aldi", "plus", "dirk",
    "spar", "action", "hema", "ikea", "mcdonalds", "mcdonald's",
    "starbucks", "subway", "ns", "translink", "kruidvat", "primark",
    "tesco", "blokker", "gamma", "mediamarkt", "coolblue", "decathlon",
    "zara", "h&m",
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
    r"punten|points|kaart|loyal",
]

ITEM_SKIP_PATTERNS = [
    r"\bsubtotaal\b|\bsubtotal\b",
    r"\btotaal\b|\btotal\b|\bte betalen\b|\bamount due\b",
    r"\bkorting\b|\bdiscount\b",
    r"\bvat\b|\bbtw\b|\btax\b",
    r"\bchange\b|\bcash\b|\bpin\b|\bdebit\b|\bcredit\b",
]

AMOUNT_RE = re.compile(r"(-?\d+[.,]\d{2})(?!.*\d+[.,]\d{2})")


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _titleish(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" -:;,.").title()


def _is_noise(line: str) -> bool:
    line_lower = line.lower()
    return any(re.search(p, line_lower) for p in SKIP_PATTERNS)


def _clean_amount(text: str) -> Optional[float]:
    cleaned = re.sub(r"[^\d,.\-]", "", text.strip()).replace(",", ".")
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _candidate_header_lines(lines: list[str]) -> list[str]:
    headers = []
    for line in lines[:8]:
        raw = re.sub(r"\s+", " ", line).strip()
        clean = _normalize(raw)
        if not clean or _is_noise(raw):
            continue
        if re.search(r"\d+[.,]\d{2}", raw):
            continue
        if sum(ch.isdigit() for ch in raw) > 2:
            continue
        if len(clean) < 3:
            continue
        headers.append(raw)
    return headers


def _extract_merchant_fuzzy(lines: list[str]) -> str:
    headers = _candidate_header_lines(lines)
    if not headers:
        return "Unknown Merchant"

    first_header = headers[0]
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return _titleish(first_header)

    normalized_first = _normalize(first_header)
    best_match = None
    best_score = 0
    for merchant in KNOWN_MERCHANTS:
        score = fuzz.ratio(normalized_first, merchant)
        if merchant in normalized_first:
            score += 8
        if score > best_score:
            best_score = score
            best_match = merchant

    # Only trust fuzzy known-merchant matches when they are very strong.
    if best_match and best_score >= 93:
        return _titleish(best_match)

    return _titleish(first_header)


def _extract_values_paddle(results: list, image_shape: tuple) -> tuple[Optional[float], Optional[float]]:
    try:
        from rapidfuzz import fuzz
    except ImportError:
        fuzz = None

    height, width = image_shape[:2]
    totals = []
    discounts = []
    lines = [r[1][0].lower() for r in results]

    for i, line in enumerate(results):
        box, (text, score) = line
        if score < 0.5:
            continue

        context = " ".join(lines[max(0, i - 2): i + 3])
        if any(word in context for word in ["punten", "points", "kaart", "loyal"]):
            continue

        matches = re.findall(r"-?\d+[.,]\d{2}|\b\d{1,4}\b", text)
        for match in matches:
            try:
                if re.fullmatch(r"\d{1,4}", match):
                    if "tota" not in context and "total" not in context:
                        continue
                    value = float(match)
                else:
                    value = float(match.replace(",", "."))
                if not (0.0 <= abs(value) <= 1000):
                    continue

                y_avg = sum(p[1] for p in box) / 4
                x_avg = sum(p[0] for p in box) / 4

                weight = 0
                if y_avg > height * 0.6:
                    weight += 40
                if x_avg > width * 0.5:
                    weight += 20

                totalish = "tota" in context or "total" in context
                discountish = "korting" in context or "discount" in context
                if fuzz:
                    totalish = totalish or fuzz.partial_ratio(context, "totaal") > 65
                    discountish = discountish or fuzz.partial_ratio(context, "korting") > 70

                if totalish:
                    totals.append((value, weight + 300))
                    continue
                if discountish:
                    discounts.append((abs(value), weight + 200))
                    continue
            except Exception:
                pass

    def _best(values):
        if not values:
            return None
        values.sort(key=lambda x: x[1], reverse=True)
        return values[0][0]

    total_paid = _best(totals)
    discount = _best(discounts)

    for r in results:
        text = r[1][0]
        if "tota" in text.lower() and re.search(r"0+[.,]?0*", text):
            total_paid = 0.0

    if discount is not None and (total_paid is None or total_paid < discount):
        total_paid = 0.0

    return total_paid, discount


def _extract_total_text(lines: list[str]) -> Optional[float]:
    total = None
    for line in lines:
        if re.search(r"\btot(aal|al)?\b|\btotal\b|\bte betalen\b|\bamount due\b", line.lower()):
            match = re.search(r"(\d+[.,]\d{2})", line)
            if match:
                total = float(match.group(1).replace(",", "."))
    return total


def _extract_items_with_prices(lines: list[str]) -> list[dict]:
    items = []
    seen = set()

    for line in lines:
        raw = re.sub(r"\s+", " ", line).strip()
        lower = raw.lower()
        if _is_noise(raw):
            continue
        if any(re.search(p, lower) for p in ITEM_SKIP_PATTERNS):
            continue

        amount_match = AMOUNT_RE.search(raw)
        if not amount_match:
            continue

        price = _clean_amount(amount_match.group(1))
        if price is None or price < 0 or price > 250:
            continue

        name = raw[:amount_match.start()].strip(" -:;,.xX\t")
        name = re.sub(r"\s{2,}", " ", name)
        name = re.sub(r"^\d+\s*[xX]\s*", "", name)
        if len(_normalize(name)) < 2:
            continue
        if any(ch.isdigit() for ch in name) and len(_normalize(name)) < 5:
            continue

        normalized_key = (_normalize(name), round(price, 2))
        if normalized_key in seen:
            continue
        seen.add(normalized_key)
        items.append({"name": _titleish(name), "price": round(price, 2)})

    return items


def _extract_items_from_results(results: list, image_shape: tuple) -> list[dict]:
    rows = []
    seen = set()
    height, width = image_shape[:2]

    for line in results:
        try:
            box, (text, score) = line
        except Exception:
            continue
        if score < 0.45:
            continue

        raw = re.sub(r"\s+", " ", str(text)).strip()
        lower = raw.lower()
        if _is_noise(raw):
            continue
        if any(re.search(p, lower) for p in ITEM_SKIP_PATTERNS):
            continue

        y_avg = sum(p[1] for p in box) / 4
        x_avg = sum(p[0] for p in box) / 4
        if y_avg < height * 0.10:
            continue
        if y_avg > height * 0.92:
            continue

        rows.append({
            "text": raw,
            "lower": lower,
            "x": x_avg,
            "y": y_avg,
            "amount": _clean_amount(raw) if AMOUNT_RE.fullmatch(raw.replace(" ", "")) or re.fullmatch(r"-?\d+[.,]\d{2}", raw) else None,
        })

    price_rows = [r for r in rows if r["amount"] is not None and 0 <= r["amount"] <= 250]
    text_rows = [r for r in rows if r["amount"] is None]

    items = []
    for price_row in price_rows:
        if price_row["x"] < width * 0.45:
            continue

        candidates = []
        for text_row in text_rows:
            if text_row["x"] > price_row["x"]:
                continue
            if abs(text_row["y"] - price_row["y"]) > max(14, height * 0.018):
                continue
            if len(_normalize(text_row["text"])) < 2:
                continue
            candidates.append(text_row)

        if not candidates:
            continue

        candidates.sort(key=lambda r: (abs(r["y"] - price_row["y"]), r["x"]))
        name = candidates[0]["text"].strip(" -:;,.")
        price = round(float(price_row["amount"]), 2)
        if price <= 0:
            continue

        key = (_normalize(name), price)
        if key in seen:
            continue
        seen.add(key)
        items.append({"name": _titleish(name), "price": price})

    return items


def _detect_currency(lines: list[str]) -> str:
    full = " ".join(lines)
    if "€" in full or "â‚¬" in full:
        return "EUR"
    if "£" in full or "Â£" in full:
        return "GBP"
    if "$" in full:
        return "USD"
    return "EUR"


def parse_receipt_paddle(lines: list[str], results: list, image_shape: tuple) -> dict:
    merchant = _extract_merchant_fuzzy(lines)
    currency = _detect_currency(lines)
    total, discount = _extract_values_paddle(results, image_shape)
    items = _extract_items_from_results(results, image_shape)

    if not items:
        items = _extract_items_with_prices(lines)

    if total is None:
        total = _extract_total_text(lines)

    return {
        "merchant": merchant,
        "currency": currency,
        "items": items,
        "total": total,
        "discount": discount,
    }


def parse_receipt(raw_text: str) -> dict:
    lines = raw_text.splitlines()
    merchant = _extract_merchant_fuzzy(lines)
    total = _extract_total_text(lines)
    items = _extract_items_with_prices(lines)
    return {
        "merchant": merchant,
        "items": items,
        "total": total,
        "discount": None,
    }
