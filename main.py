"""
main.py — Financial AI Assistant entry point.

Three flows, one program:

  1. Receipt scan      → python main.py receipt receipt.jpg
  2. Voice/text invest → python main.py invest "Invest my 500 euro bonus in ETFs"
  3. Demo all three    → python main.py demo

Architecture:
  Voice command  →  parse intent  →  [INTERVENTION CHECK]  →  execute  →  response
  Receipt image  →  OCR → parse  →  classify  →  insight   →  log      →  response
"""

import sys
import json

# ---------------------------------------------------------------------------
# Shared state — in a real app this would be a DB or session store
# ---------------------------------------------------------------------------
from insight import UserContext

SESSION_CONTEXT = UserContext(
    weekly_spent={
        "groceries": 55.0,
        "dining": 30.0,
        "travel": 0.0,
        "utilities": 0.0,
        "shopping": 20.0,
    }
)

# ---------------------------------------------------------------------------
# Flow 1: Receipt scan
# ---------------------------------------------------------------------------

def handle_receipt(image_path: str):
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))

    from ocr import extract_text
    from parser import parse_receipt
    from classifier import classify_expense
    from insight import generate_insight
    import mock_bank

    print(f"\n{'='*50}")
    print(f"  RECEIPT SCAN: {image_path}")
    print(f"{'='*50}")

    print("\n[1/4] Running OCR...")
    raw_text = extract_text(image_path)
    print(f"  → {len(raw_text)} chars extracted")

    print("[2/4] Parsing receipt...")
    parsed = parse_receipt(raw_text)
    print(f"  → merchant: {parsed['merchant']}, total: €{parsed.get('total', 0):.2f}")
    print(f"  → items: {parsed['items']}")

    print("[3/4] Classifying expense...")
    classification = classify_expense(parsed)
    category = classification["category"]
    print(f"  → category: {category} (confidence: {classification['confidence']:.2f})")

    print("[4/4] Generating insight...")
    insight = generate_insight(parsed, category, SESSION_CONTEXT)

    # Update session state
    SESSION_CONTEXT.weekly_spent[category] = (
        SESSION_CONTEXT.weekly_spent.get(category, 0.0) + (parsed.get("total") or 0.0)
    )

    # Log to mock bank
    mock_bank.log_expense(
        merchant=parsed.get("merchant", "Unknown"),
        amount=parsed.get("total") or 0.0,
        category=category,
    )

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

    print(f"\n{'='*50}")
    print("  RESULT")
    print(f"{'='*50}")
    print(json.dumps(output, indent=2))
    return output


# ---------------------------------------------------------------------------
# Flow 2: Voice / text investment command
# ---------------------------------------------------------------------------

def handle_invest(command: str):
    from voice_invest import parse_investment_intent, execute_investment_command
    from intervention import evaluate

    print(f"\n{'='*50}")
    print(f"  VOICE COMMAND: '{command}'")
    print(f"{'='*50}")

    # Parse intent first
    print("\n[1/3] Parsing intent...")
    parsed_intent = parse_investment_intent(command)
    print(f"  → {parsed_intent}")

    # Intervention check BEFORE executing
    if parsed_intent["intent"] in ("invest", "save") and parsed_intent["amount"]:
        print("\n[2/3] Running intervention check...")
        intervention = evaluate(parsed_intent)
        level = intervention["level"]
        print(f"  → level: {level}")

        if level == "block":
            print(f"\n🚨 BLOCKED: {intervention['reason']}")
            print(f"   Suggestion: {intervention['suggestion']}")
            return {
                "intent": parsed_intent["intent"],
                "action_taken": "blocked_by_intervention",
                "amount": parsed_intent["amount"],
                "instrument": parsed_intent.get("instrument"),
                "result": {},
                "message": f"Action blocked. {intervention['reason']} {intervention['suggestion']}",
                "risk": True,
                "risk_reason": intervention["reason"],
            }

        if level == "warn":
            print(f"\n⚠️  WARNING: {intervention['reason']}")
            print(f"   Suggestion: {intervention['suggestion']}")
            # In a real app: prompt the user to confirm. For demo we proceed.
            print("   [DEMO MODE: proceeding despite warning]")
    else:
        print("\n[2/3] No intervention needed for this intent.")

    # Execute
    print("\n[3/3] Executing command...")
    result = execute_investment_command(command)

    print(f"\n{'='*50}")
    print("  RESULT")
    print(f"{'='*50}")
    print(json.dumps(result, indent=2))
    return result


# ---------------------------------------------------------------------------
# Flow 3: Full demo (no real image/voice needed)
# ---------------------------------------------------------------------------

def handle_demo():
    from voice_invest import execute_investment_command
    from intervention import evaluate
    import mock_bank

    print("\n" + "="*60)
    print("  DEMO: AI Financial Assistant")
    print("="*60)

    # Show initial balance
    balances = mock_bank.get_balance()
    print(f"\n📊 Starting balance: €{balances['checking']:.2f} checking | €{balances['savings']:.2f} savings")

    # --- Demo 1: Normal investment ---
    print("\n" + "-"*60)
    print("DEMO 1: Normal voice investment command")
    print("-"*60)
    handle_invest("Invest my 500 euro bonus in index funds")

    # --- Demo 2: Risky investment (intervention triggers) ---
    print("\n" + "-"*60)
    print("DEMO 2: Risky command — intervention should fire")
    print("-"*60)
    handle_invest("Invest 3900 euros in bitcoin")

    # --- Demo 3: Simulated receipt (no OCR needed) ---
    print("\n" + "-"*60)
    print("DEMO 3: Simulated receipt scan (mocked, no image needed)")
    print("-"*60)
    _demo_receipt_without_image()

    # Final balance
    balances = mock_bank.get_balance()
    print("\n" + "="*60)
    print(f"📊 Final balance: €{balances['checking']:.2f} checking | €{balances['savings']:.2f} savings")
    print("="*60)


def _demo_receipt_without_image():
    """Simulate a receipt scan without needing a real image or Tesseract."""
    from classifier import classify_expense
    from insight import generate_insight
    import mock_bank

    # Simulated parsed receipt (as if OCR + parser already ran)
    parsed = {
        "merchant": "Albert Heijn",
        "items": ["Halfvolle Melk", "Volkoren Brood", "Kaas Jong 500g"],
        "total": 8.66,
    }

    print(f"\n[MOCK RECEIPT] {parsed['merchant']} — €{parsed['total']:.2f}")
    print(f"  Items: {parsed['items']}")

    classification = classify_expense(parsed)
    category = classification["category"]
    print(f"  Classified as: {category} (confidence: {classification['confidence']:.2f})")

    insight = generate_insight(parsed, category, SESSION_CONTEXT)
    mock_bank.log_expense(parsed["merchant"], parsed["total"], category)

    output = {
        "intent": "expense_tracking",
        "amount": parsed["total"],
        "category": category,
        "message": insight["message"],
        "risk": insight["risk"],
    }
    print(json.dumps(output, indent=2))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

USAGE = """
Usage:
  python main.py demo                          # Run full demo (no image/mic needed)
  python main.py invest "<command>"            # Process a voice/text investment command
  python main.py receipt <image_path>          # Scan a receipt image

Examples:
  python main.py demo
  python main.py invest "Invest my 500 euro bonus in ETFs"
  python main.py invest "Save 200 euros from my bonus"
  python main.py invest "Invest 4000 euros in crypto"   # triggers intervention
  python main.py receipt receipt.jpg
"""

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(1)

    mode = sys.argv[1].lower()

    if mode == "demo":
        handle_demo()

    elif mode == "invest":
        if len(sys.argv) < 3:
            print("Error: provide a command string.\nExample: python main.py invest \"Invest 500 euros in ETFs\"")
            sys.exit(1)
        command = " ".join(sys.argv[2:])
        handle_invest(command)

    elif mode == "receipt":
        if len(sys.argv) < 3:
            print("Error: provide an image path.\nExample: python main.py receipt receipt.jpg")
            sys.exit(1)
        handle_receipt(sys.argv[2])

    else:
        print(f"Unknown mode: {mode}")
        print(USAGE)
        sys.exit(1)