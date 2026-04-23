"""
classifier.py — Classify a parsed receipt using a local LLM via Ollama.
No API key needed. Runs fully offline.
"""

import json
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.1:8b"  # change to llama3.1:8b if you have more RAM

VALID_CATEGORIES = ["groceries", "dining", "travel", "utilities", "shopping"]

PROMPT_TEMPLATE = """You are a financial expense classifier.
Given receipt data, classify the expense into exactly one of these categories:
groceries, dining, travel, utilities, shopping

Receipt data:
Merchant: {merchant}
Items: {items}
Total: €{total:.2f}

Respond ONLY with a valid JSON object, no explanation, no markdown:
{{"category": "groceries", "confidence": 0.95}}"""


def classify_expense(parsed_data: dict) -> dict:
    """
    Classify a parsed receipt using a local Ollama model.

    Args:
        parsed_data: Output from parser.parse_receipt()

    Returns:
        {"category": str, "confidence": float}
    """
    prompt = PROMPT_TEMPLATE.format(
        merchant=parsed_data.get("merchant", "Unknown"),
        items=", ".join(parsed_data.get("items", [])) or "N/A",
        total=parsed_data.get("total") or 0.0,
    )

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,  # low temp = more deterministic output
                },
            },
            timeout=30,
        )
        response.raise_for_status()
        raw = response.json().get("response", "").strip()

        # Strip markdown fences if model adds them anyway
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()

        result = json.loads(raw)

        if result.get("category") not in VALID_CATEGORIES:
            result["category"] = "shopping"
        result["confidence"] = float(result.get("confidence", 0.8))
        return result

    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            "Ollama is not running. Start it with: ollama serve"
        )
    except (json.JSONDecodeError, KeyError):
        # Fallback: try to find a category word anywhere in the raw response
        raw_lower = raw.lower() if "raw" in dir() else ""
        for cat in VALID_CATEGORIES:
            if cat in raw_lower:
                return {"category": cat, "confidence": 0.6}
        return {"category": "shopping", "confidence": 0.5}


# --- quick test ---
if __name__ == "__main__":
    sample = {
        "merchant": "Albert Heijn",
        "items": ["Halfvolle Melk", "Volkoren Brood", "Sinaasappels"],
        "total": 8.66,
    }
    result = classify_expense(sample)
    print(result)