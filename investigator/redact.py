"""Remove secret values from text before it is printed or logged."""

import os

SECRET_ENV_VARS = ("GEMINI_API_KEY", "MYSQL_PASSWORD")


def redact(text: str) -> str:
    for name in SECRET_ENV_VARS:
        value = os.environ.get(name)
        if value:
            text = text.replace(value, f"<{name} redacted>")
    return text
