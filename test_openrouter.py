"""One-off connectivity test for OpenRouter.

Run from the project root after `uv pip install -e .` and filling in .env:

    python test_openrouter.py

Delete (or keep gitignored) after confirming the connection works.
"""

from __future__ import annotations

from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from shared._openrouter import get_openrouter_client  # noqa: E402 — must load .env first
from shared.config import ConfigError  # noqa: E402

# OpenRouter published pricing for anthropic/claude-haiku-4.5 ($/million tokens).
# Check https://openrouter.ai/anthropic/claude-haiku-4.5 for the latest rates.
_PRICE_INPUT_PER_M: float = 0.80
_PRICE_OUTPUT_PER_M: float = 4.00


def main() -> None:
    try:
        client = get_openrouter_client()
    except ConfigError as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1)

    model = "anthropic/claude-haiku-4.5"
    prompt = 'Reply with exactly one sentence: "Hello, OpenRouter is working."'

    print(f"Model  : {model}")
    print(f"Prompt : {prompt}")
    print()

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=64,
    )

    answer = resp.choices[0].message.content or ""
    usage = resp.usage

    input_tok = getattr(usage, "prompt_tokens", 0) or 0
    output_tok = getattr(usage, "completion_tokens", 0) or 0
    cost_usd = (input_tok * _PRICE_INPUT_PER_M + output_tok * _PRICE_OUTPUT_PER_M) / 1_000_000

    print(f"Answer : {answer.strip()}")
    print()
    print(f"Tokens : {input_tok} in + {output_tok} out = {input_tok + output_tok} total")
    print(f"Cost   : ~${cost_usd:.6f}  (< $0.001 ✓)" if cost_usd < 0.001 else
          f"Cost   : ~${cost_usd:.6f}")
    print()
    print("OpenRouter connection OK.")


if __name__ == "__main__":
    main()
