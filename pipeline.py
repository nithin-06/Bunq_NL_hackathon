"""
pipeline.py — Full receipt intelligence pipeline.

Image → OCR → Parse → Classify → Insight → Final JSON
"""

import json
from typing import Optional
from ocr import extract_text
from parser import parse_receipt
from classifier import classify_expense
from insight import generate_insight, UserContext


def run_pipeline(
    image_path: str,
    user_context: Optional[UserContext] = None,
    verbose: bool = False,
) -> dict:
    """
    Full pipeline: receipt image → structured financial output.

    Args:
        image_path: Path to the receipt image.
        user_context: Optional budget/spending state for insight layer.
        verbose: Print intermediate steps if True.

    Returns:
        Final JSON matching the team contract:
        {
            "intent": "expense_tracking",
            "amount": float,
            "category": str,
            "message": str,
            "risk": bool,
            "risk_reason": str | None,
            "details": {
                "merchant": str,
                "items": list[str],
            }
        }
    """

    # --- Step 1: OCR ---
    if verbose:
        print(f"[1/4] Running OCR on: {image_path}")
    raw_text = extract_text(image_path)
    if verbose:
        print(f"  → {len(raw_text)} chars extracted")

    # --- Step 2: Parse ---
    if verbose:
        print("[2/4] Parsing receipt text...")
    parsed = parse_receipt(raw_text)
    if verbose:
        print(f"  → {parsed}")

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

    # Demo context: already spent €55 on groceries this week
    ctx = UserContext(weekly_spent={"groceries": 55.0})

    result = run_pipeline(image_path, user_context=ctx, verbose=verbose)
    print("\n=== FINAL OUTPUT ===")
    print(json.dumps(result, indent=2))