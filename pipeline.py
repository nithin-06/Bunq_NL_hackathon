"""
pipeline.py — Full receipt intelligence pipeline.

Image → OCR (PaddleOCR) → Parse → Classify → Insight → Final JSON
"""

import json
from typing import Optional

from ocr import run_ocr_full
from parser import parse_receipt_paddle
from classifier import classify_expense
from insight import generate_insight, UserContext


def run_pipeline(
    image_path: str,
    user_context: Optional[UserContext] = None,
    verbose: bool = False,
) -> dict:
    """
    Full pipeline: receipt image → structured financial output.
    """

    # --- Step 1: OCR (PaddleOCR, multi-variant) ---
    if verbose:
        print(f"[1/4] Running PaddleOCR on: {image_path}")
    lines, results, image_shape = run_ocr_full(image_path)
    if verbose:
        print(f"  → {len(lines)} lines extracted")

    # --- Step 2: Parse (bounding-box aware) ---
    if verbose:
        print("[2/4] Parsing receipt...")
    parsed = parse_receipt_paddle(lines, results, image_shape)
    if verbose:
        print(f"  → merchant: {parsed['merchant']}, total: {parsed.get('total')}, discount: {parsed.get('discount')}")
        print(f"  → items: {parsed['items']}")

    # --- Step 3: Classify ---
    if verbose:
        print("[3/4] Classifying expense via LLM...")
    classification = classify_expense(parsed)
    category = classification["category"]
    confidence = classification["confidence"]
    if verbose:
        print(f"  → category={category}, confidence={confidence:.2f}")

    # --- Step 4: Insight + Risk ---
    if verbose:
        print("[4/4] Generating insight...")
    insight = generate_insight(parsed, category, user_context)
    if verbose:
        print(f"  → {insight['message']}")

    # --- Final output (team contract) ---
    output = {
        "intent": "expense_tracking",
        "amount": parsed.get("total") or 0.0,
        "category": category,
        "message": insight["message"],
        "risk": insight["risk"],
        "risk_reason": insight["risk_reason"],
        "details": {
            "merchant": parsed.get("merchant", "Unknown"),
            "items": parsed.get("items", []),
            "discount": parsed.get("discount"),
        },
    }

    return output


# --- CLI entry point ---
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python pipeline.py <receipt_image_path> [--verbose]")
        sys.exit(1)

    image_path = sys.argv[1]
    verbose = "--verbose" in sys.argv

    ctx = UserContext(weekly_spent={"groceries": 55.0})
    result = run_pipeline(image_path, user_context=ctx, verbose=verbose)
    print("\n=== FINAL OUTPUT ===")
    print(json.dumps(result, indent=2))