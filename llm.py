"""
llm.py — Shared Ollama client used by all modules.

Model assignments:
  - llama3.1:8b  → intent parsing + investment reasoning (needs instruction following)
  - llama3.1:8b  → risk/intervention reasoning (same)
  - llama3.1:8b  → receipt classification (simpler task, same model for consistency)

If you want to save RAM, swap FAST_MODEL to "llama3.2:3b" for classification only.
"""

import json
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"

# Use 8b for everything — you have it, it's better at JSON and reasoning than 3b
REASONING_MODEL = "llama3.1:8b"
FAST_MODEL = "llama3.1:8b"  # swap to llama3.2:3b if startup is slow


def query(prompt: str, model: str = REASONING_MODEL, temperature: float = 0.1) -> str:
    """
    Send a prompt to Ollama and return the raw response string.
    Raises RuntimeError if Ollama is not running.
    """
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature},
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            "\n[ERROR] Ollama is not running.\n"
            "Fix: open a terminal and run:  ollama serve\n"
            "Then try again."
        )


def query_json(prompt: str, model: str = REASONING_MODEL, temperature: float = 0.1) -> dict:
    """
    Same as query() but parses the response as JSON.
    Strips markdown fences if the model wraps its output.
    Raises RuntimeError if Ollama is down (never silently returns empty on connection failure).
    Returns empty dict only if the model's output genuinely can't be parsed as JSON.
    """
    raw = query(prompt, model=model, temperature=temperature)  # raises RuntimeError if Ollama is down

    # Strip ```json ... ``` fences
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            part = part.strip().lstrip("json").strip()
            try:
                return json.loads(part)
            except json.JSONDecodeError:
                continue

    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Last resort: find the first { ... } block
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end])
            except json.JSONDecodeError:
                pass

    return {}