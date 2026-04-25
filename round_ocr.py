"""
round_ocr.py - Receipt scanning for Round.

Uses Claude vision (Anthropic API) as primary OCR — fast, accurate, no
PaddleOCR setup required.  PaddleOCR is kept as an optional fallback.
"""

import base64
import imghdr
import json
import os
import re
import sys
import tempfile
from pathlib import Path


def _scan_with_claude(image_bytes: bytes) -> dict:
    import anthropic
    client = anthropic.Anthropic()

    b64  = base64.standard_b64encode(image_bytes).decode("utf-8")
    kind = imghdr.what(None, h=image_bytes)
    mime = "image/jpeg" if kind in ("jpeg", "jpg") else "image/png"

    prompt = """You are a receipt parser. Extract all line items and the total from this receipt image.

Return ONLY valid JSON in exactly this format, no markdown, no explanation:
{
  "restaurant": "Restaurant Name",
  "items": [
    {"name": "Item Name", "price": 12.50},
    {"name": "Another Item", "price": 6.00}
  ],
  "total": 51.30,
  "currency": "EUR"
}

Rules:
- Include every food/drink line item with its price as a number
- If an item has a quantity prefix like "2X" keep it in the name exactly as printed
- Do not include subtotal, tax, tip, or service charge as items
- total should be the final amount charged
- currency: use EUR if unclear, USD if you see $
- Always return valid JSON even if the image is unclear"""

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )

    raw = response.content[0].text.strip()
    print(f"[OCR-CLAUDE] raw: {raw[:300]}")

    raw = re.sub(r"^```json\s*|^```\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    parsed = json.loads(raw)

    items = []
    for item in parsed.get("items", []):
        name  = str(item.get("name", "")).strip()
        price = float(item.get("price", 0) or 0)
        if name:
            items.append({"name": name, "price": round(price, 2)})

    result = {
        "restaurant": str(parsed.get("restaurant", "Restaurant")),
        "items":      items,
        "total":      float(parsed.get("total") or 0),
        "currency":   str(parsed.get("currency", "EUR")),
        "discount":   None,
    }
    print(f"[OCR-CLAUDE] result: {result}")
    return result


def _scan_with_paddle(image_path: str) -> dict:
    sys.path.insert(0, str(Path(__file__).parent.parent / "receipt_module"))
    from ocr import run_ocr_full
    from parser import parse_receipt_paddle

    lines, results, image_shape = run_ocr_full(image_path)
    parsed = parse_receipt_paddle(lines, results, image_shape)

    items = []
    for item in parsed.get("items", []):
        if isinstance(item, dict):
            items.append({"name": str(item.get("name", item)), "price": float(item.get("price", 0))})
        elif isinstance(item, str):
            items.append({"name": item, "price": 0.0})

    return {
        "restaurant": parsed.get("merchant", "Restaurant"),
        "items":      items,
        "total":      float(parsed.get("total") or 0),
        "currency":   parsed.get("currency", "EUR"),
        "discount":   parsed.get("discount"),
    }


def _detect_image_suffix(image_bytes: bytes, fallback: str = ".jpg") -> str:
    kind = imghdr.what(None, h=image_bytes)
    if kind == "png":   return ".png"
    if kind in {"jpeg", "jpg"}: return ".jpg"
    return fallback


def scan_receipt_bytes(image_bytes: bytes, suffix: str = ".jpg") -> dict:
    # Try Claude vision first
    try:
        result = _scan_with_claude(image_bytes)
        if result.get("items"):
            return result
        print("[OCR] Claude returned no items, trying PaddleOCR")
    except Exception as e:
        print(f"[OCR] Claude vision failed: {e} — trying PaddleOCR")

    # Fallback to PaddleOCR
    try:
        import cv2, numpy as np
        safe_suffix = _detect_image_suffix(image_bytes, fallback=suffix or ".jpg")
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        with tempfile.NamedTemporaryFile(suffix=safe_suffix, delete=False) as tmp:
            tmp_path = tmp.name
        try:
            cv2.imwrite(tmp_path, img)
            return _scan_with_paddle(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    except Exception as e2:
        raise RuntimeError(
            f"All OCR methods failed. Set ANTHROPIC_API_KEY env var. Last error: {e2}"
        )


def scan_receipt(image_path: str) -> dict:
    return scan_receipt_bytes(Path(image_path).read_bytes())