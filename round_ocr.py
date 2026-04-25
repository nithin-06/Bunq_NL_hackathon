"""
round_ocr.py - Receipt scanning for Round.
Wraps the existing OCR + parser pipeline into a clean interface.
"""

import imghdr
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "receipt_module"))

from ocr import run_ocr_full
from parser import parse_receipt_paddle


def _detect_image_suffix(image_bytes: bytes, fallback: str = ".jpg") -> str:
    kind = imghdr.what(None, h=image_bytes)
    if kind == "png":
        return ".png"
    if kind in {"jpeg", "jpg"}:
        return ".jpg"
    return fallback


def _decode_image_bytes(image_bytes: bytes):
    array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("Uploaded file is not a readable PNG or JPEG image.")
    return image


def scan_receipt(image_path: str) -> dict:
    """
    Scan a receipt image and return structured data.
    """
    lines, results, image_shape = run_ocr_full(image_path)
    parsed = parse_receipt_paddle(lines, results, image_shape)

    items = []
    for item in parsed.get("items", []):
        if isinstance(item, dict):
            items.append(
                {
                    "name": str(item.get("name", item)),
                    "price": float(item.get("price", 0)),
                }
            )
        elif isinstance(item, str):
            items.append({"name": item, "price": 0.0})

    total = float(parsed.get("total") or 0)

    return {
        "restaurant": parsed.get("merchant", "Restaurant"),
        "items": items,
        "total": total,
        "currency": parsed.get("currency", "EUR"),
        "discount": parsed.get("discount"),
    }


def scan_receipt_bytes(image_bytes: bytes, suffix: str = ".jpg") -> dict:
    """Scan receipt from raw bytes uploaded via FastAPI."""
    safe_suffix = _detect_image_suffix(image_bytes, fallback=suffix or ".jpg")
    image = _decode_image_bytes(image_bytes)

    with tempfile.NamedTemporaryFile(suffix=safe_suffix, delete=False) as tmp:
        tmp_path = tmp.name

    try:
        if not cv2.imwrite(tmp_path, image):
            raise RuntimeError("Failed to write uploaded image for OCR.")
        return scan_receipt(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
