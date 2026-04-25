"""
round_voice.py - Voice transcription + deterministic item matching for Round.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000
_whisper_model = None

sys.path.insert(0, str(Path(__file__).parent.parent / "receipt_module"))


def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        try:
            import whisper
            print("[VOICE] Loading Whisper model...")
            _whisper_model = whisper.load_model("base")
            print("[VOICE] Ready.")
        except ImportError as e:
            raise RuntimeError(
                "openai-whisper not installed.\n"
                "Fix: pip install openai-whisper sounddevice scipy"
            ) from e
    return _whisper_model


def record_audio(duration_seconds: int = 6) -> np.ndarray:
    try:
        import sounddevice as sd
    except ImportError as e:
        raise RuntimeError("sounddevice not installed.\nFix: pip install sounddevice scipy") from e

    print(f"[VOICE] Recording {duration_seconds}s...")
    audio = sd.rec(
        int(duration_seconds * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
    )
    sd.wait()
    print("[VOICE] Done.")
    return audio.flatten()


def transcribe(audio: np.ndarray) -> str:
    model = get_whisper()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        import scipy.io.wavfile as wavfile
        wavfile.write(tmp_path, SAMPLE_RATE, (audio * 32767).astype(np.int16))
        result = model.transcribe(tmp_path, language="en", fp16=False)
        return result["text"].strip()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def transcribe_bytes(audio_bytes: bytes) -> str:
    model = get_whisper()
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        result = model.transcribe(tmp_path, fp16=False)
        return result["text"].strip()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    stop = {
        "i", "me", "my", "had", "have", "the", "a", "an", "and", "with", "for",
        "to", "of", "please", "just", "got", "was", "were", "is", "it", "on",
        "in", "we", "our", "share", "bill",
    }
    return [t for t in _normalize(text).split() if len(t) > 2 and t not in stop]


def _category_keywords(item_name: str) -> set[str]:
    lowered = _normalize(item_name)
    keywords = set()
    if any(word in lowered for word in ["wine", "beer", "cola", "coke", "sprite", "juice", "water", "tea", "coffee", "latte", "drink", "cocktail"]):
        keywords.add("drinks")
    if any(word in lowered for word in ["cake", "cheesecake", "dessert", "ice cream", "tiramisu", "brownie"]):
        keywords.add("dessert")
    if any(word in lowered for word in ["burger", "pizza", "pasta", "salad", "fries", "steak", "sandwich", "meal"]):
        keywords.add("food")
        keywords.add("main")
    return keywords


def _item_score(speech_norm: str, speech_tokens: set[str], item_name: str) -> tuple[int, str]:
    item_norm = _normalize(item_name)
    item_tokens = set(_tokens(item_name))
    if not item_norm:
        return 0, "empty"

    if item_norm in speech_norm:
        return 100, "exact phrase"

    meaningful_overlap = speech_tokens & item_tokens
    if meaningful_overlap:
        score = 70 + 10 * len(meaningful_overlap)
        return min(score, 95), "token overlap"

    try:
        from rapidfuzz import fuzz
    except ImportError:
        return 0, "no match"

    partial = fuzz.partial_ratio(speech_norm, item_norm)
    token_set = fuzz.token_set_ratio(speech_norm, item_norm)

    # Require strong similarity unless there is actual token overlap.
    if partial >= 92 and token_set >= 70:
        return int((partial + token_set) / 2), "high fuzzy"

    return 0, "no match"


def match_items(speech: str, receipt_items: list[dict]) -> dict:
    speech_norm = _normalize(speech)
    speech_tokens = set(_tokens(speech))

    if not speech_norm or not receipt_items:
        return {"matched_items": [], "total": 0.0, "confidence": 0.0}

    matches = []
    category_requested = set()
    for token in speech_tokens:
        if token in {"drink", "drinks", "beverage", "beverages"}:
            category_requested.add("drinks")
        if token in {"dessert", "desserts", "sweet", "sweets"}:
            category_requested.add("dessert")
        if token in {"food", "foods", "main", "mains"}:
            category_requested.add("food")

    for item in receipt_items:
        name = str(item.get("name", "")).strip()
        price = float(item.get("price", 0) or 0)
        score, reason = _item_score(speech_norm, speech_tokens, name)

        if not score and category_requested:
            item_categories = _category_keywords(name)
            if item_categories & category_requested:
                score = 74
                reason = "category"

        if score >= 74:
            matches.append({
                "name": name,
                "price": round(price, 2),
                "_score": score,
                "_reason": reason,
            })

    # Deduplicate and keep stable ordering.
    deduped = []
    seen = set()
    for item in sorted(matches, key=lambda x: (-x["_score"], x["name"])):
        key = (_normalize(item["name"]), item["price"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"name": item["name"], "price": item["price"]})

    total = round(sum(item["price"] for item in deduped), 2)
    confidence = 0.0
    if deduped:
        confidence = round(min(0.99, 0.65 + 0.1 * len(deduped)), 2)

    return {
        "matched_items": deduped,
        "total": total,
        "confidence": confidence,
    }
