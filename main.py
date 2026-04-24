"""
main.py — AI Financial Assistant — complete entry point.

MODES:
  python main.py demo                   Scripted demo (local CSV, no mic/image)
  python main.py invest "<text>"        Typed investment command
  python main.py mic                    Speak into mic → invest
  python main.py receipt <image>        Scan receipt image
  python main.py dashboard              Generate + open dashboard (local data)
  python main.py dashboard --live       Dashboard pulling real bunq data
  python main.py balance                Local CSV balances
  python main.py balance --live         Real bunq account balance
  python main.py reset                  Reset local CSV to defaults

  python main.py bunq setup             Run bunq auth setup (do once)
  python main.py bunq balance           Live bunq balance
  python main.py bunq topup             Request €500 from sugardaddy
  python main.py bunq transactions      Last 10 real transactions
  python main.py bunq pay               Test €0.10 payment

FLAGS:
  --live    Use real bunq API instead of local CSV mock
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

LIVE_MODE = "--live" in sys.argv


# ---------------------------------------------------------------------------
# Select bank backend based on --live flag
# ---------------------------------------------------------------------------

def _get_bank():
    if LIVE_MODE:
        try:
            import bunq_bridge
            print("[MODE] 🌐 LIVE — using real bunq sandbox API")
            return bunq_bridge
        except Exception as e:
            print(f"[MODE] ⚠️  bunq_bridge failed ({e}), falling back to mock")
    import mock_bank
    return mock_bank


# ---------------------------------------------------------------------------
# Ollama health check
# ---------------------------------------------------------------------------

def _check_ollama():
    import requests as _req
    try:
        r = _req.get("http://localhost:11434/api/tags", timeout=3)
        if r.status_code == 200:
            return True
    except Exception:
        pass
    print("""
╔══════════════════════════════════════════════════════╗
║  Ollama is not running.                              ║
║  Fix:  open a new terminal and run:  ollama serve    ║
╚══════════════════════════════════════════════════════╝
""")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Shared session context for insight layer
# ---------------------------------------------------------------------------

from insight import UserContext

SESSION_CONTEXT = UserContext(
    weekly_spent={
        "groceries": 55.0,
        "dining":    30.0,
        "travel":     0.0,
        "utilities":  0.0,
        "shopping":  20.0,
    }
)


# ---------------------------------------------------------------------------
# Flow 1: Receipt scan
# ---------------------------------------------------------------------------

def handle_receipt(image_path: str):
    from ocr import run_ocr_full
    from parser import parse_receipt_paddle
    from classifier import classify_expense
    from insight import generate_insight

    bank = _get_bank()

    print(f"\n{'='*54}")
    print(f"  RECEIPT SCAN: {image_path}")
    print(f"{'='*54}")

    print("\n[1/4] Running PaddleOCR...")
    lines, results, image_shape = run_ocr_full(image_path)
    print(f"  → {len(lines)} lines extracted")

    print("[2/4] Parsing receipt...")
    parsed = parse_receipt_paddle(lines, results, image_shape)
    total_display = f"€{parsed['total']:.2f}" if parsed.get("total") is not None else "not found"
    print(f"  → merchant: {parsed['merchant']}, total: {total_display}")
    if parsed.get("discount"):
        print(f"  → discount: €{parsed['discount']:.2f}")
    print(f"  → items: {parsed['items']}")

    print("[3/4] Classifying (LLM)...")
    classification = classify_expense(parsed)
    category = classification["category"]
    print(f"  → {category} ({classification['confidence']:.2f})")

    print("[4/4] Generating insight + logging expense...")
    insight = generate_insight(parsed, category, SESSION_CONTEXT)
    SESSION_CONTEXT.weekly_spent[category] = (
        SESSION_CONTEXT.weekly_spent.get(category, 0.0) + (parsed.get("total") or 0.0)
    )

    bank.log_expense(
        merchant=parsed.get("merchant", "Unknown"),
        amount=parsed.get("total") or 0.0,
        category=category,
    )

    output = {
        "intent":     "expense_tracking",
        "amount":     parsed.get("total") or 0.0,
        "category":   category,
        "message":    insight["message"],
        "risk":       insight["risk"],
        "risk_reason": insight["risk_reason"],
        "details": {
            "merchant": parsed.get("merchant", "Unknown"),
            "items":    parsed.get("items", []),
            "currency": parsed.get("currency", "€"),
        },
    }

    print(f"\n{'='*54}")
    print("  RESULT")
    print(f"{'='*54}")
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return output


# ---------------------------------------------------------------------------
# Flow 2: Voice / text investment command
# ---------------------------------------------------------------------------

def handle_invest(command: str = None):
    """
    If command is None (or --live with no text), record from mic.
    In live mode, all actions go through bunq_bridge.
    """
    from voice_invest import parse_investment_intent, record_from_mic, transcribe_audio
    from intervention import evaluate

    bank = _get_bank()

    # --- Mic input: always in live mode, or when no command given ---
    if command is None or LIVE_MODE:
        if command is None:
            print("\n[MIC] No text given — recording from microphone...")
        else:
            print(f"\n[MIC] --live mode: recording voice instead of using text arg")
        duration = 6
        try:
            duration = int(input("Recording duration in seconds [6]: ").strip() or 6)
        except (ValueError, EOFError):
            pass
        audio = record_from_mic(duration_seconds=duration)
        command = transcribe_audio(audio)
        print(f"\n[WHISPER] Heard: '{command}'")
        if not command.strip():
            print("[ERROR] Nothing transcribed. Check your microphone.")
            return

    print(f"\n{'='*54}")
    print(f"  VOICE COMMAND: '{command}'")
    print(f"{'='*54}")

    print("\n[1/3] Parsing intent (LLM)...")
    parsed_intent = parse_investment_intent(command, bank=bank)
    intent     = parsed_intent["intent"]
    amount     = parsed_intent["amount"]
    instrument = parsed_intent.get("instrument", "ETF_SP500")
    recipient_email = parsed_intent.get("recipient_email", "sugardaddy@bunq.com")
    recipient_name  = parsed_intent.get("recipient_name", "Sugar Daddy")

    print(f"  → intent={intent}  amount={amount}  instrument={instrument}")
    if intent == "send":
        print(f"  → recipient={recipient_name} ({recipient_email})")

    # --- Send intent: direct payment, skip investment intervention ---
    if intent == "send":
        if not amount:
            print("[ERROR] Couldn't determine amount to send.")
            return
        print(f"\n[2/3] Send intent — no investment intervention needed.")
        print(f"\n[3/3] Executing payment of €{amount:.2f} to {recipient_name}...")
        if LIVE_MODE:
            import bunq_client as bc
            state = bc._load_state()
            private_pem, _ = bc._generate_or_load_keys()
            if not state.get("session_token"):
                state = bc.create_session(private_pem, state)
            accounts = bc.get_accounts(state)
            acc_id = accounts[0]["id"]
            result = bc.make_payment(
                state, private_pem, acc_id,
                amount=str(amount),
                description=f"Voice payment to {recipient_name}",
                recipient_email=recipient_email,
                recipient_name=recipient_name,
            )
            output = {
                "intent": "send",
                "action_taken": "payment_executed_bunq",
                "amount": amount,
                "recipient": recipient_name,
                "recipient_email": recipient_email,
                "result": result,
                "message": f"✅ Sent €{amount:.2f} to {recipient_name} via bunq.",
                "risk": False, "risk_reason": None,
            }
        else:
            result = bank.transfer_to_savings(amount)  # simulate in mock mode
            output = {
                "intent": "send",
                "action_taken": "payment_simulated",
                "amount": amount,
                "recipient": recipient_name,
                "message": f"[MOCK] Simulated sending €{amount:.2f} to {recipient_name}.",
                "risk": False, "risk_reason": None,
            }
        print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
        return output

    # --- Invest / Save: run intervention check ---
    if intent in ("invest", "save") and amount:
        print("\n[2/3] Running intervention check (LLM)...")
        intervention = evaluate(parsed_intent)
        level = intervention["level"]
        print(f"  → level: {level}")

        if level == "block":
            print(f"\n🚨 BLOCKED: {intervention['reason']}")
            print(f"   Suggestion: {intervention['suggestion']}")
            output = {
                "intent":       intent,
                "action_taken": "blocked_by_intervention",
                "amount":       amount,
                "instrument":   instrument,
                "result":       {},
                "message":      f"Action blocked. {intervention['reason']} {intervention['suggestion']}",
                "risk":         True,
                "risk_reason":  intervention["reason"],
            }
            print(json.dumps(output, indent=2, ensure_ascii=False))
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
                    "intent":       intent,
                    "action_taken": "aborted_by_user",
                    "amount":       amount,
                    "instrument":   instrument,
                    "result":       {},
                    "message":      f"Action cancelled. {intervention['suggestion']}",
                    "risk":         True,
                    "risk_reason":  intervention["reason"],
                }
                print(json.dumps(output, indent=2, ensure_ascii=False))
                return output
            print("   Confirmed. Proceeding...")
    else:
        print("\n[2/3] No intervention needed.")

    print("\n[3/3] Executing...")
    if intent == "invest" and amount:
        result = bank.invest(amount, instrument)
    elif intent == "save" and amount:
        result = bank.transfer_to_savings(amount)
    elif intent == "check_balance":
        bal = bank.get_balance()
        result = {
            "intent": "check_balance",
            "action_taken": "balance_check",
            "result": bal,
            "message": f"Checking: €{bal['checking']:.2f} | Savings: €{bal['savings']:.2f} | Investments: €{bal['investments']:.2f}",
            "risk": False, "risk_reason": None,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return result
    else:
        print(f"[VOICE] Unknown intent '{intent}' — nothing executed.")
        return {"intent": intent, "action_taken": "none", "message": "Couldn't understand that command."}

    print(f"\n{'='*54}")
    print("  RESULT")
    print(f"{'='*54}")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
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
        print("[ERROR] Nothing transcribed. Check mic and try again.")
        return

    handle_invest(transcript)


# ---------------------------------------------------------------------------
# Flow 4: Dashboard
# ---------------------------------------------------------------------------

def handle_dashboard():
    import dashboard as dash
    dash.main(live=LIVE_MODE)


# ---------------------------------------------------------------------------
# Flow 5: Scripted demo
# ---------------------------------------------------------------------------

def handle_demo():
    bank = _get_bank()

    print("\n" + "="*60)
    print("  DEMO — AI Financial Assistant")
    print("="*60)

    bal = bank.get_balance()
    print(f"\n📊 Starting: checking €{bal['checking']:.2f}  |  savings €{bal['savings']:.2f}  |  investments €{bal['investments']:.2f}")

    print("\n" + "-"*60)
    print("DEMO 1: Normal investment command")
    handle_invest("Invest my 500 euro bonus in index funds")

    print("\n" + "-"*60)
    print("DEMO 2: Risky command — should trigger intervention")
    handle_invest("Invest 3900 euros in bitcoin")

    print("\n" + "-"*60)
    print("DEMO 3: Simulated receipt scan (no image needed)")
    _demo_mock_receipt()

    bal = bank.get_balance()
    print("\n" + "="*60)
    print(f"📊 Final: checking €{bal['checking']:.2f}  |  savings €{bal['savings']:.2f}  |  investments €{bal['investments']:.2f}")
    print("="*60)
    print("\n💡 Run  python main.py dashboard  to see the visual breakdown.")


def _demo_mock_receipt():
    from classifier import classify_expense
    from insight import generate_insight
    bank = _get_bank()

    parsed = {
        "merchant": "Albert Heijn",
        "items":    ["Halfvolle Melk", "Volkoren Brood", "Kaas Jong 500g"],
        "total":    8.66,
    }
    print(f"\n[MOCK RECEIPT] {parsed['merchant']} — €{parsed['total']:.2f}")

    classification = classify_expense(parsed)
    category = classification["category"]
    print(f"  Classified: {category} ({classification['confidence']:.2f})")

    insight = generate_insight(parsed, category, SESSION_CONTEXT)
    bank.log_expense(parsed["merchant"], parsed["total"], category)

    print(json.dumps({
        "intent":   "expense_tracking",
        "amount":   parsed["total"],
        "category": category,
        "message":  insight["message"],
        "risk":     insight["risk"],
    }, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# bunq-specific subcommands  (python main.py bunq <cmd>)
# ---------------------------------------------------------------------------

def handle_bunq(subcmd: str):
    import bunq_client as bc

    if subcmd == "setup":
        print("\n[BUNQ] Running full setup (installation → device → session)...")
        state, private_pem = bc.full_setup()
        print("\n✅ Setup complete. You can now use --live mode.")
        print("   Run: python main.py bunq topup   to get sandbox money")

    elif subcmd == "balance":
        state = bc._load_state()
        if not state.get("session_token"):
            print("[ERROR] Run: python main.py bunq setup first")
            sys.exit(1)
        accounts = bc.get_accounts(state)
        print(f"\n{'='*54}")
        print("  BUNQ ACCOUNTS")
        print(f"{'='*54}")
        for acc in accounts:
            print(f"  ID:      {acc['id']}")
            print(f"  Name:    {acc['name']}")
            print(f"  IBAN:    {acc['iban']}")
            print(f"  Email:   {acc['email']}")
            print(f"  Balance: {acc['currency']} {acc['balance']}")
            print()

    elif subcmd == "topup":
        state = bc._load_state()
        private_pem, _ = bc._generate_or_load_keys()
        accounts = bc.get_accounts(state)
        acc_id = accounts[0]["id"]
        result = bc.request_money_from_sugardaddy(state, private_pem, acc_id, "500.00")
        print("\n✅ Requested €500 from sugardaddy")
        print("   Check your sandbox app — it should appear shortly.")

    elif subcmd == "transactions":
        state = bc._load_state()
        accounts = bc.get_accounts(state)
        acc_id = accounts[0]["id"]
        payments = bc.get_payments(state, acc_id, limit=10)
        print(f"\n{'='*54}")
        print("  LAST 10 BUNQ TRANSACTIONS")
        print(f"{'='*54}")
        for p in payments:
            sign = "-" if float(p["amount"]) < 0 else "+"
            print(f"  {p['created'][:10]}  {p['currency']} {sign}{abs(float(p['amount'])):>8.2f}  {p['counterparty']:<20}  {p['description'][:30]}")

    elif subcmd == "pay":
        # Test payment: €0.10 to sugardaddy
        state = bc._load_state()
        private_pem, _ = bc._generate_or_load_keys()
        if not state.get("session_token"):
            state = bc.create_session(private_pem, state)
        accounts = bc.get_accounts(state)
        acc_id = accounts[0]["id"]
        result = bc.make_payment(
            state, private_pem, acc_id,
            amount="0.10",
            description="Test payment from AI assistant"
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif subcmd == "session":
        # Refresh session (call when getting 401 errors)
        private_pem, _ = bc._generate_or_load_keys()
        state = bc._load_state()
        state = bc.create_session(private_pem, state)
        print("✅ Session refreshed.")

    else:
        print(f"Unknown bunq subcommand: {subcmd}")
        print("Available: setup | balance | topup | transactions | pay | session")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

USAGE = """\
AI Financial Assistant

LOCAL (mock CSV):
  python main.py demo                      Full scripted demo
  python main.py invest "<text>"           Typed investment command
  python main.py mic                       Speak into mic
  python main.py receipt <image.jpg>       Scan receipt
  python main.py dashboard                 Local dashboard
  python main.py balance                   Local balances
  python main.py reset                     Reset local CSV

LIVE (real bunq sandbox):
  python main.py bunq setup                Register with bunq (run once)
  python main.py bunq balance              Real account balance
  python main.py bunq topup                Get €500 sandbox money
  python main.py bunq transactions         Real transaction history
  python main.py bunq pay                  Test €0.10 payment
  python main.py bunq session              Refresh session if 401 errors

  python main.py invest "<text>" --live    Execute against real bunq
  python main.py receipt img.jpg --live    Log expense to real bunq
  python main.py dashboard --live          Dashboard with real bunq data
"""

if __name__ == "__main__":
    # Strip --live from argv so it doesn't confuse subcommand parsing
    args = [a for a in sys.argv[1:] if a != "--live"]

    if not args:
        print(USAGE)
        sys.exit(0)

    mode = args[0].lower()

    # Commands that need Ollama
    if mode in ("demo", "invest", "mic", "receipt"):
        _check_ollama()

    if mode == "demo":
        handle_demo()

    elif mode == "invest":
        if LIVE_MODE:
            # Live mode always uses mic — ignore any text argument
            handle_invest(command=None)
        else:
            if len(args) < 2:
                print("Error: provide a command string.\nExample: python main.py invest \"Invest 500 euros in ETFs\"")
                sys.exit(1)
            handle_invest(" ".join(args[1:]))

    elif mode == "mic":
        handle_mic()

    elif mode == "receipt":
        if len(args) < 2:
            print("Error: provide an image path.")
            sys.exit(1)
        handle_receipt(args[1])

    elif mode == "dashboard":
        handle_dashboard()

    elif mode == "balance":
        bank = _get_bank()
        bal = bank.get_balance()
        print(f"\n  Checking:    €{bal['checking']:.2f}")
        print(f"  Savings:     €{bal['savings']:.2f}")
        print(f"  Investments: €{bal['investments']:.2f}")
        print(f"  Net worth:   €{bal['checking']+bal['savings']+bal['investments']:.2f}")
        if bal.get("iban"):
            print(f"  IBAN:        {bal['iban']}")

    elif mode == "reset":
        import mock_bank
        mock_bank.reset_to_defaults()

    elif mode == "bunq":
        subcmd = args[1] if len(args) > 1 else "balance"
        handle_bunq(subcmd)

    else:
        print(f"Unknown command: {mode}\n")
        print(USAGE)
        sys.exit(1)