"""
voice_invest.py — Hear a command, parse intent, execute investment.

Flow:
  Text command (from voice STT or typed)
  → LLM extracts: intent, amount, instrument
  → Risk guard checks the decision
  → If safe: mock_bank.invest() executes it
  → Structured response returned

No real STT here — plug in Whisper output as a string.
Whisper integration note at bottom of file.
"""

import json
from llm import query_json, REASONING_MODEL
import mock_bank

# ---------------------------------------------------------------------------
# Intent extraction
# ---------------------------------------------------------------------------

INTENT_PROMPT = """You are a financial assistant parsing a voice command.

Extract the user's financial intent from this command:
"{command}"

Current account balance: €{balance:.2f}

Respond ONLY with JSON, no explanation:
{{
  "intent": "invest" | "save" | "check_balance" | "unknown",
  "amount": <float or null>,
  "instrument": "ETF_SP500" | "ETF_BONDS" | "CRYPTO_BTC" | null,
  "raw_instrument_mention": "<what the user said, e.g. 'index fund', 'crypto', 'bonds'>"
}}

Rules:
- If user says "invest my bonus", "put X into", "buy ETF" → intent = invest
- If user says "save", "put aside" → intent = save
- If no amount mentioned, amount = null
- Map instrument mentions: "index fund/ETF/stocks" → ETF_SP500, "bonds/fixed" → ETF_BONDS, "crypto/bitcoin/BTC" → CRYPTO_BTC
- If instrument unclear, default to ETF_SP500"""


def parse_investment_intent(command: str) -> dict:
    """
    Use LLM to extract structured intent from a natural language command.
    """
    balance = mock_bank.get_balance()["checking"]
    prompt = INTENT_PROMPT.format(command=command, balance=balance)
    result = query_json(prompt, model=REASONING_MODEL)

    # Defaults if LLM returns partial data
    result.setdefault("intent", "unknown")
    result.setdefault("amount", None)
    result.setdefault("instrument", "ETF_SP500")
    result.setdefault("raw_instrument_mention", "")

    return result


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def execute_investment_command(command: str) -> dict:
    """
    Full pipeline: text command → parse → execute → response.

    Args:
        command: Natural language string, e.g. "Invest my €500 bonus in ETFs"

    Returns:
        {
            "intent": str,
            "action_taken": str,
            "amount": float | None,
            "instrument": str | None,
            "result": dict,         ← bank transaction or error
            "message": str,         ← human-readable summary
            "risk": bool,
            "risk_reason": str | None
        }
    """
    print(f"\n[VOICE] Command received: '{command}'")

    # Step 1: Parse intent
    parsed = parse_investment_intent(command)
    intent = parsed["intent"]
    amount = parsed["amount"]
    instrument = parsed["instrument"] or "ETF_SP500"

    print(f"[VOICE] Parsed intent={intent}, amount={amount}, instrument={instrument}")

    # Step 2: Handle unknown or unsupported intent
    if intent == "check_balance":
        balances = mock_bank.get_balance()
        return {
            "intent": "check_balance",
            "action_taken": "balance_check",
            "amount": None,
            "instrument": None,
            "result": balances,
            "message": (
                f"Your current balance: €{balances['checking']:.2f} checking, "
                f"€{balances['savings']:.2f} savings, "
                f"€{balances['investments']:.2f} in investments."
            ),
            "risk": False,
            "risk_reason": None,
        }

    if intent == "unknown" or amount is None:
        return {
            "intent": intent,
            "action_taken": "none",
            "amount": amount,
            "instrument": None,
            "result": {},
            "message": "I couldn't understand that command. Try: 'Invest €500 in ETFs' or 'Save my €200 bonus'.",
            "risk": False,
            "risk_reason": None,
        }

    if intent == "save":
        result = mock_bank.transfer_to_savings(amount)
        if result["success"]:
            tx = result["transaction"]
            return {
                "intent": "save",
                "action_taken": "savings_transfer",
                "amount": amount,
                "instrument": None,
                "result": result,
                "message": f"Moved €{amount:.2f} to your savings account. New savings balance: €{tx['new_savings']:.2f}.",
                "risk": False,
                "risk_reason": None,
            }
        else:
            return {
                "intent": "save",
                "action_taken": "failed",
                "amount": amount,
                "instrument": None,
                "result": result,
                "message": f"Couldn't complete transfer: {result['reason']}",
                "risk": False,
                "risk_reason": None,
            }

    if intent == "invest":
        result = mock_bank.invest(amount, instrument)
        if result["success"]:
            tx = result["transaction"]
            return {
                "intent": "invest",
                "action_taken": "investment_executed",
                "amount": amount,
                "instrument": instrument,
                "result": result,
                "message": (
                    f"Invested €{amount:.2f} in {instrument}. "
                    f"Purchased {tx['units_purchased']} units @ €{tx['price_per_unit']:.2f}/unit. "
                    f"Remaining balance: €{tx['new_balance']:.2f}."
                ),
                "risk": False,
                "risk_reason": None,
            }
        else:
            return {
                "intent": "invest",
                "action_taken": "failed",
                "amount": amount,
                "instrument": instrument,
                "result": result,
                "message": f"Investment failed: {result['reason']}",
                "risk": False,
                "risk_reason": None,
            }

    return {
        "intent": intent,
        "action_taken": "none",
        "amount": amount,
        "instrument": None,
        "result": {},
        "message": "Command not handled.",
        "risk": False,
        "risk_reason": None,
    }


# ---------------------------------------------------------------------------
# Whisper integration note
# ---------------------------------------------------------------------------
# To add real voice input, install: pip install openai-whisper
# Then:
#   import whisper
#   model = whisper.load_model("base")          # or "small", "medium"
#   result = model.transcribe("audio.mp3")
#   command = result["text"]
#   execute_investment_command(command)
#
# For microphone input add: pip install sounddevice scipy
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import sys
    command = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Invest my 500 euro bonus in index funds"
    result = execute_investment_command(command)
    print("\n=== RESULT ===")
    print(json.dumps(result, indent=2))