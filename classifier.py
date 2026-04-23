"""
classifier.py — Classify a parsed receipt using a local LLM via Ollama.
No API key needed. Runs fully offline.
"""

from llm import query_json, FAST_MODEL

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

    result = query_json(prompt, model=FAST_MODEL)

    if result.get("category") not in VALID_CATEGORIES:
        result["category"] = "shopping"
    result["confidence"] = float(result.get("confidence", 0.8))
    return result


# --- quick test ---
if __name__ == "__main__":
    import json
    sample = {
        "merchant": "Albert Heijn",
        "items": ["Halfvolle Melk", "Volkoren Brood", "Sinaasappels"],
        "total": 8.66,
    }
    result = classify_expense(sample)
    print(json.dumps(result, indent=2))