"""
classifier.py — Use an LLM to classify a parsed receipt into an expense category.
"""

import json
import os
import anthropic

VALID_CATEGORIES = ["groceries", "dining", "travel", "utilities", "shopping"]

SYSTEM_PROMPT = """You are a financial expense classifier.
Given structured receipt data, classify the expense into exactly one of these categories:
groceries, dining, travel, utilities, shopping

Respond ONLY with a valid JSON object like:
{"category": "groceries", "confidence": 0.95}

No explanation. No markdown. Just JSON."""


def classify_expense(parsed_data: dict) -> dict:
    """
    Classify a parsed receipt using Claude.

    Args:
        parsed_data: Output from parser.parse_receipt()
            {"merchant": str, "items": list, "total": float}

    Returns:
        {"category": str, "confidence": float}
    """
    client = anthropic.Anthropic()

    user_message = f"""Receipt data:
Merchant: {parsed_data.get('merchant', 'Unknown')}
Items: {', '.join(parsed_data.get('items', [])) or 'N/A'}
Total: €{parsed_data.get('total', 0.0):.2f}

Classify this expense."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=100,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text.strip()

    try:
        result = json.loads(raw)
        # Validate category
        if result.get("category") not in VALID_CATEGORIES:
            result["category"] = "shopping"  # safe fallback
        result["confidence"] = float(result.get("confidence", 0.8))
        return result
    except (json.JSONDecodeError, KeyError):
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