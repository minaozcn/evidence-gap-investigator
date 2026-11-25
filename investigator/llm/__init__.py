"""Pick the LLM provider named in the environment (.env)."""

import os

from .base import LLMProvider


def get_provider() -> LLMProvider:
    name = os.environ.get("LLM_PROVIDER", "gemini").lower()

    if name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider(
            api_key=os.environ["GEMINI_API_KEY"],
            model=os.environ["LLM_MODEL"],
            thinking_level=os.environ.get("LLM_THINKING_LEVEL", "low"),
        )

    raise ValueError(f"Unknown LLM_PROVIDER: {name!r}")
