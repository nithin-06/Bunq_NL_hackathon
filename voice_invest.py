"""
voice_invest.py — Mic → Whisper STT → LLM intent parse → execute investment.

Mic recording uses sounddevice (cross-platform, no extra system deps).
Transcription uses openai-whisper running fully locally (no API key).

Install once:
    pip install openai-whisper sounddevice scipy
    # On Linux also: sudo apt install ffmpeg
    # On Mac: brew install ffmpeg
"""

import json
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

from llm import query_json, REASONING_MODEL
import mock_bank

# ---------------------------------------------------------------------------
# Whisper model — loaded once, reused
# ---------------------------------------------------------------------------

_whisper_model = None

def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        try:
            import whisper
            print("[MIC] Loading Whisper model (base)... ", end="", flush=True)
            _whisper_model = whisper.load_model("base")
            print("ready.")
        except ImportError:
            raise RuntimeError(
                "\n[ERROR] openai-whisper not installed.\n"
                "Fix: pip install openai-whisper sounddevice scipy\n"
                "     On Linux also: sudo apt install ffmpeg\n"
                "     On Mac: brew install ffmpeg"
            )
    return _whisper_model


# ---------------------------------------------------------------------------
# Microphone recording
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16_000  # Whisper expects 16kHz

def record_from_mic(duration_seconds: int = 5) -> np.ndarray:
    """
    Record audio from the default system microphone.

    Args:
        duration_seconds: How long to record (user should finish speaking by then).

    Returns:
        numpy array of float32 audio samples at 16kHz.
    """
    try:
        import sounddevice as sd
    except ImportError:
        raise RuntimeError(
            "\n[ERROR] sounddevice not installed.\n"
            "Fix: pip install sounddevice scipy"
        )

    print(f"\n🎤 Recording for {duration_seconds}s — speak now...")
    audio = sd.rec(
        int(duration_seconds * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
    )
    sd.wait()  # blocks until recording is done
    print("✅ Recording complete.")
    return audio.flatten()


def transcribe_audio(audio: np.ndarray) -> str:
    """Run Whisper on a numpy audio array and return transcript string."""
    model = _get_whisper()

    # Write to a temp WAV file — Whisper's numpy API is less reliable across versions
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        import scipy.io.wavfile as wavfile
        # Whisper needs int16 PCM
        wavfile.write(tmp_path, SAMPLE_RATE, (audio * 32767).astype(np.int16))
        result = model.transcribe(tmp_path, language="en", fp16=False)
        transcript = result["text"].strip()
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return transcript


# ---------------------------------------------------------------------------
# Intent extraction
# ---------------------------------------------------------------------------

INTENT_PROMPT = """You are a financial assistant parsing a spoken command.

Extract the user's financial intent from this command:
"{command}"

Current account balance: €{balance:.2f}

Respond ONLY with JSON, no explanation:
{{
  "intent": "invest" | "save" | "check_balance" | "unknown",
  "amount": <float or null>,
  "instrument": "ETF_SP500" | "ETF_BONDS" | "CRYPTO_BTC" | null,
  "raw_instrument_mention": "<what the user said>"
}}

Mapping rules:
- "index fund", "ETF", "stocks", "S&P" → ETF_SP500
- "bonds", "fixed income", "safe" → ETF_BONDS
- "crypto", "bitcoin", "BTC", "coin" → CRYPTO_BTC
- If instrument not mentioned → ETF_SP500 (default)
- "save", "put aside", "set aside" → intent = save
- "invest", "buy", "put into" → intent = invest
- "balance", "how much" → intent = check_balance"""


def parse_investment_intent(command: str) -> dict:
    balance = mock_bank.get_balance()["checking"]
    prompt = INTENT_PROMPT.format(command=command, balance=balance)
    result = query_json(prompt, model=REASONING_MODEL)

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
    Full pipeline: text command → parse → execute → response dict.
    """
    print(f"\n[VOICE] Command: '{command}'")

    parsed = parse_investment_intent(command)
    intent = parsed["intent"]
    amount = parsed["amount"]
    instrument = parsed.get("instrument") or "ETF_SP500"

    print(f"[VOICE] intent={intent}  amount={amount}  instrument={instrument}")

    if intent == "check_balance":
        bal = mock_bank.get_balance()
        return {
            "intent": "check_balance",
            "action_taken": "balance_check",
            "amount": None, "instrument": None,
            "result": bal,
            "message": (
                f"Checking: €{bal['checking']:.2f} | "
                f"Savings: €{bal['savings']:.2f} | "
                f"Investments: €{bal['investments']:.2f}"
            ),
            "risk": False, "risk_reason": None,
        }

    if intent == "unknown" or amount is None:
        return {
            "intent": intent, "action_taken": "none",
            "amount": amount, "instrument": None, "result": {},
            "message": "Couldn't understand that. Try: 'Invest €500 in ETFs' or 'Save my €200 bonus'.",
            "risk": False, "risk_reason": None,
        }

    if intent == "save":
        result = mock_bank.transfer_to_savings(amount)
        if result["success"]:
            tx = result["transaction"]
            return {
                "intent": "save", "action_taken": "savings_transfer",
                "amount": amount, "instrument": None, "result": result,
                "message": f"Moved €{amount:.2f} to savings. New savings: €{tx['balance_after']:.2f}.",
                "risk": False, "risk_reason": None,
            }
        return {
            "intent": "save", "action_taken": "failed",
            "amount": amount, "instrument": None, "result": result,
            "message": f"Transfer failed: {result['reason']}",
            "risk": False, "risk_reason": None,
        }

    if intent == "invest":
        result = mock_bank.invest(amount, instrument)
        if result["success"]:
            tx = result["transaction"]
            return {
                "intent": "invest", "action_taken": "investment_executed",
                "amount": amount, "instrument": instrument, "result": result,
                "message": (
                    f"Invested €{amount:.2f} in {instrument}. "
                    f"Bought {tx['units_purchased']} units @ €{tx['price_per_unit']:.2f}. "
                    f"Balance: €{tx['balance_after']:.2f}."
                ),
                "risk": False, "risk_reason": None,
            }
        return {
            "intent": "invest", "action_taken": "failed",
            "amount": amount, "instrument": instrument, "result": result,
            "message": f"Investment failed: {result['reason']}",
            "risk": False, "risk_reason": None,
        }

    return {
        "intent": intent, "action_taken": "none",
        "amount": amount, "instrument": None, "result": {},
        "message": "Command not handled.", "risk": False, "risk_reason": None,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Text mode: python voice_invest.py "Invest 500 euros in ETFs"
        command = " ".join(sys.argv[1:])
    else:
        # Mic mode: python voice_invest.py
        audio = record_from_mic(duration_seconds=6)
        command = transcribe_audio(audio)
        print(f"[WHISPER] Heard: '{command}'")

    result = execute_investment_command(command)
    print("\n=== RESULT ===")
    print(json.dumps(result, indent=2))