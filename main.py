"""
main.py — AI Financial Assistant entry point.

Commands:
  python main.py demo                        Full scripted demo (no mic/image needed)
  python main.py invest "<text command>"     Process a typed investment command
  python main.py mic                         Record from microphone → invest
  python main.py receipt <image.jpg>         Scan a receipt image
  python main.py dashboard                   Generate + open HTML dashboard
  python main.py reset                       Reset CSV database to defaults
  python main.py balance                     Print current balances
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))


# ---------------------------------------------------------------------------
# Ollama health check — fail fast and clearly
# ---------------------------------------------------------------------------

def _check_ollama():
    import requests
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        if r.status_code == 200:
            return True
    except Exception:
        pass

    print("""
╔══════════════════════════════════════════════════════╗
║  Ollama is not running.                              ║
║                                                      ║
║  Fix:  open a new terminal and run:                  ║
║        ollama serve                                  ║
║                                                      ║
║  Then run your command again.                        ║
╚══════════════════════════════════════════════════════╝
""")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Shared session state
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
    from ocr import extract_text
    from parser import parse_receipt
    from classifier import classify_expense
    from insight import generate_insight
    import mock_bank

    print(f"\n{'='*52}")
    print(f"  RECEIPT SCAN: {image_path}")
    print(f"{'='*52}")

    print("\n[1/4] Running OCR...")
    raw_text = extract_text(image_path)
    print(f"  → {len(raw_text)} chars extracted")

    print("\n[DEBUG OCR OUTPUT]\n", raw_text)

    print("[2/4] Parsing receipt...")
    parsed = parse_receipt(raw_text)
    print(f"  → merchant: {parsed['merchant']}, total: €{parsed.get('total', 0):.2f}")
    print(f"  → items: {parsed['items']}")

    print("[3/4] Classifying expense (LLM)...")
    classification = classify_expense(parsed)
    category = classification["category"]
    print(f"  → {category} (confidence {classification['confidence']:.2f})")

    print("[4/4] Generating insight...")
    insight = generate_insight(parsed, category, SESSION_CONTEXT)
    SESSION_CONTEXT.weekly_spent[category] = (
        SESSION_CONTEXT.weekly_spent.get(category, 0.0) + (parsed.get("total") or 0.0)
    )

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
        "details": {"merchant": parsed.get("merchant", "Unknown"), "items": parsed.get("items", [])},
    }

    print(f"\n{'='*52}")
    print("  RESULT")
    print(f"{'='*52}")
    print(json.dumps(output, indent=2))
    return output


# ---------------------------------------------------------------------------
# Flow 2: Voice / text investment command
# ---------------------------------------------------------------------------

def handle_invest(command: str):
    from voice_invest import parse_investment_intent, execute_investment_command
    from intervention import evaluate

    print(f"\n{'='*52}")
    print(f"  VOICE COMMAND: '{command}'")
    print(f"{'='*52}")

    print("\n[1/3] Parsing intent (LLM)...")
    parsed_intent = parse_investment_intent(command)
    print(f"  → intent={parsed_intent['intent']}  amount={parsed_intent['amount']}  instrument={parsed_intent.get('instrument')}")

    if parsed_intent["intent"] in ("invest", "save") and parsed_intent["amount"]:
        print("\n[2/3] Running intervention check (LLM)...")
        intervention = evaluate(parsed_intent)
        level = intervention["level"]
        print(f"  → level: {level}")

        if level == "block":
            print(f"\n🚨 BLOCKED: {intervention['reason']}")
            print(f"   Suggestion: {intervention['suggestion']}")
            output = {
                "intent": parsed_intent["intent"],
                "action_taken": "blocked_by_intervention",
                "amount": parsed_intent["amount"],
                "instrument": parsed_intent.get("instrument"),
                "result": {},
                "message": f"Action blocked. {intervention['reason']} {intervention['suggestion']}",
                "risk": True,
                "risk_reason": intervention["reason"],
            }
            print(json.dumps(output, indent=2))
            return output

        if level == "warn":
            print(f"\n⚠️  WARNING: {intervention['reason']}")
            print(f"   Suggestion: {intervention['suggestion']}")
            try:
                confirm = input("\n   Proceed anyway? (yes / no): ").strip().lower()
            except EOFError:
                confirm = "no"
            if confirm not in ("yes", "y"):
                print("   Aborted.")
                output = {
                    "intent": parsed_intent["intent"],
                    "action_taken": "aborted_by_user",
                    "amount": parsed_intent["amount"],
                    "instrument": parsed_intent.get("instrument"),
                    "result": {},
                    "message": f"Action cancelled. {intervention['suggestion']}",
                    "risk": True,
                    "risk_reason": intervention["reason"],
                }
                print(json.dumps(output, indent=2))
                return output
            print("   Confirmed. Proceeding...")
    else:
        print("\n[2/3] No intervention needed.")

    print("\n[3/3] Executing...")
    result = execute_investment_command(command)

    print(f"\n{'='*52}")
    print("  RESULT")
    print(f"{'='*52}")
    print(json.dumps(result, indent=2))
    return result


# ---------------------------------------------------------------------------
# Flow 3: Microphone → transcribe → invest
# ---------------------------------------------------------------------------

def handle_mic():
    from voice_invest import record_from_mic, transcribe_audio

    print("\n[MIC] Starting microphone input...")
    duration = 6
    try:
        duration = int(input("Recording duration in seconds [6]: ").strip() or 6)
    except (ValueError, EOFError):
        pass

    audio = record_from_mic(duration_seconds=duration)
    transcript = transcribe_audio(audio)
    print(f"\n[WHISPER] Heard: '{transcript}'")

    if not transcript.strip():
        print("[ERROR] Nothing transcribed. Check your microphone and try again.")
        return

    handle_invest(transcript)


# ---------------------------------------------------------------------------
# Flow 4: Dashboard
# ---------------------------------------------------------------------------

def handle_dashboard():
    import dashboard as dash
    dash.main()


# ---------------------------------------------------------------------------
# Flow 5: Scripted demo (no image / mic needed)
# ---------------------------------------------------------------------------

def handle_demo():
    import mock_bank

    print("\n" + "="*60)
    print("  DEMO — AI Financial Assistant")
    print("="*60)

    bal = mock_bank.get_balance()
    print(f"\n📊 Starting: checking €{bal['checking']:.2f}  |  savings €{bal['savings']:.2f}  |  investments €{bal['investments']:.2f}")

    # Demo 1: normal investment
    print("\n" + "-"*60)
    print("DEMO 1: Normal investment command")
    handle_invest("Invest my 500 euro bonus in index funds")

    # Demo 2: risky investment → intervention
    print("\n" + "-"*60)
    print("DEMO 2: Risky command — should trigger intervention")
    handle_invest("Invest 3900 euros in bitcoin")

    # Demo 3: receipt (mocked — no image needed)
    print("\n" + "-"*60)
    print("DEMO 3: Simulated receipt scan (no image file needed)")
    _demo_mock_receipt()

    # Final state
    bal = mock_bank.get_balance()
    print("\n" + "="*60)
    print(f"📊 Final:    checking €{bal['checking']:.2f}  |  savings €{bal['savings']:.2f}  |  investments €{bal['investments']:.2f}")
    print("="*60)

    print("\n💡 Run  python main.py dashboard  to see the visual breakdown.")


def _demo_mock_receipt():
    """Simulate a full receipt scan without needing a real image or Tesseract."""
    from classifier import classify_expense
    from insight import generate_insight
    import mock_bank

    parsed = {
        "merchant": "Albert Heijn",
        "items": ["Halfvolle Melk", "Volkoren Brood", "Kaas Jong 500g"],
        "total": 8.66,
    }

    print(f"\n[MOCK RECEIPT] {parsed['merchant']} — €{parsed['total']:.2f}")
    print(f"  Items: {parsed['items']}")

    classification = classify_expense(parsed)
    category = classification["category"]
    print(f"  Classified: {category} ({classification['confidence']:.2f})")

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

USAGE = """\
Usage:
  python main.py demo                      Run full scripted demo
  python main.py invest "<text>"           Typed investment command
  python main.py mic                       Speak into microphone
  python main.py receipt <image.jpg>       Scan receipt image
  python main.py dashboard                 Generate + open HTML dashboard
  python main.py balance                   Print current account balances
  python main.py reset                     Reset CSV database to defaults

Examples:
  python main.py invest "Invest my 500 euro bonus in ETFs"
  python main.py invest "Save 200 euros"
  python main.py invest "Invest 3900 euros in crypto"   ← intervention fires
  python main.py mic
  python main.py receipt receipt.jpg
  python main.py dashboard
"""

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(0)

    mode = sys.argv[1].lower()

    # Commands that need Ollama
    if mode in ("demo", "invest", "mic", "receipt"):
        _check_ollama()

    if mode == "demo":
        handle_demo()

    elif mode == "invest":
        if len(sys.argv) < 3:
            print("Error: provide a command string.\nExample: python main.py invest \"Invest 500 euros in ETFs\"")
            sys.exit(1)
        handle_invest(" ".join(sys.argv[2:]))

    elif mode == "mic":
        handle_mic()

    elif mode == "receipt":
        if len(sys.argv) < 3:
            print("Error: provide an image path.\nExample: python main.py receipt receipt.jpg")
            sys.exit(1)
        handle_receipt(sys.argv[2])

    elif mode == "dashboard":
        handle_dashboard()

    elif mode == "balance":
        import mock_bank
        bal = mock_bank.get_balance()
        print(f"\nChecking:    €{bal['checking']:.2f}")
        print(f"Savings:     €{bal['savings']:.2f}")
        print(f"Investments: €{bal['investments']:.2f}")
        print(f"Net worth:   €{bal['checking']+bal['savings']+bal['investments']:.2f}")

    elif mode == "reset":
        import mock_bank
        mock_bank.reset_to_defaults()

    else:
        print(f"Unknown command: {mode}\n")
        print(USAGE)
        sys.exit(1)