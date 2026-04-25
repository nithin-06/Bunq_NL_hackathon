"""
round_voice.py - Voice transcription + item matching for Round.
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
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_STOP = {
    "i", "me", "my", "had", "have", "the", "a", "an", "and", "with", "for",
    "to", "of", "please", "just", "got", "was", "were", "is", "it", "on",
    "in", "we", "our", "share", "bill", "also", "then", "plus",
}


def _tokens(text: str) -> list[str]:
    return [t for t in _normalize(text).split() if len(t) >= 3 and t not in _STOP]


def _item_keywords(item_name: str) -> set[str]:
    """
    Extract meaningful words from item name, stripping quantity prefixes.
    '2X CAESAR SALAD' -> {'caesar', 'salad'}
    'CHEESECAKE'      -> {'cheesecake'}
    """
    norm = _normalize(item_name)
    # Strip leading quantity like '2x ', 'x2 ', '2 '
    norm = re.sub(r"^\d+x?\s+|^x\d+\s+", "", norm)
    return {t for t in norm.split() if len(t) >= 3 and t not in _STOP}


def match_items(speech: str, receipt_items: list[dict]) -> dict:
    """
    Match speech/text against receipt items.
    Returns {"matched_items": [...], "total": float, "confidence": float}
    """
    if not speech or not receipt_items:
        return {"matched_items": [], "total": 0.0, "confidence": 0.0}

    speech_norm = _normalize(speech)
    speech_tok  = set(_tokens(speech))

    print(f"[MATCH] speech='{speech_norm}' tokens={speech_tok}")
    print(f"[MATCH] items={receipt_items}")

    matched = []  # (score, receipt_index, item_dict)

    for idx, item in enumerate(receipt_items):
        raw_name  = str(item.get("name", "")).strip()
        price     = float(item.get("price", 0) or 0)
        item_norm = _normalize(raw_name)
        item_kw   = _item_keywords(raw_name)
        # Quantity-stripped version of item name for matching
        stripped  = re.sub(r"^\d+x?\s+|^x\d+\s+", "", item_norm).strip()

        score = 0

        # 1. Full item name is substring of speech
        if item_norm and item_norm in speech_norm:
            score = 100

        # 2. Quantity-stripped name is substring of speech
        elif stripped and stripped in speech_norm:
            score = 98

        # 3. ALL item keywords present in speech tokens
        elif item_kw and item_kw.issubset(speech_tok):
            score = 90

        # 4. ANY keyword found in speech tokens
        elif item_kw:
            overlap = item_kw & speech_tok
            if overlap:
                score = int(75 + 15 * (len(overlap) / len(item_kw)))

        # 5. Any keyword appears as substring in speech (catches OCR variants)
        if score == 0 and item_kw:
            for kw in item_kw:
                if len(kw) >= 4 and kw in speech_norm:
                    score = 74
                    break

        # 6. rapidfuzz fallback
        if score == 0:
            try:
                from rapidfuzz import fuzz
                target = stripped if stripped else item_norm
                p = fuzz.partial_ratio(speech_norm, target)
                t = fuzz.token_set_ratio(speech_norm, target)
                if p >= 85 and t >= 65:
                    score = int((p + t) / 2)
                elif t >= 80:
                    score = int(t * 0.9)
            except ImportError:
                pass

        print(f"[MATCH]   '{raw_name}' (kw={item_kw}) -> score={score}")

        if score >= 74:
            matched.append((score, idx, {"name": raw_name, "price": round(price, 2)}))

    # Each receipt line claimed at most once
    seen_idx = set()
    deduped  = []
    for score, idx, item_dict in sorted(matched, key=lambda x: -x[0]):
        if idx not in seen_idx:
            seen_idx.add(idx)
            deduped.append((idx, item_dict))

    # Restore receipt order
    deduped.sort(key=lambda x: x[0])
    result_items = [d for _, d in deduped]

    total      = round(sum(i["price"] for i in result_items), 2)
    confidence = round(min(0.99, 0.65 + 0.1 * len(result_items)), 2) if result_items else 0.0

    print(f"[MATCH] => matched={[i['name'] for i in result_items]} total={total}")

    return {"matched_items": result_items, "total": total, "confidence": confidence}