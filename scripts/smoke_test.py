"""Smoke test: is the LLM provider wired up correctly?

Checks:
  1. The configured model is reachable with our API key.
  2. The database is reachable and seeded.
  3. Asked about INC-1003, the model calls get_incident(incident_key="INC-1003").
  4. The model's final answer contains the title stored in the database.

Run from the agent folder, with the virtual environment active:
    python -m scripts.smoke_test
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from investigator.db import connect  # noqa: E402  (needs .env loaded first)
from investigator.llm import get_provider  # noqa: E402
from investigator.llm.base import (AssistantMessage, ToolResult,  # noqa: E402
                                   ToolResultsMessage, UserMessage)
from investigator.redact import redact  # noqa: E402
from investigator.tools import GET_INCIDENT, get_incident  # noqa: E402

TARGET = "INC-1003"
MAX_STEPS = 3


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    return ok


def main() -> int:
    results = []

    provider = get_provider()
    try:
        provider.check_model()
        results.append(check("model reachable", True))
    except Exception as e:
        check("model reachable", False, redact(f"{type(e).__name__}: {e}"))
        return 1

    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM incidents")
        n = cur.fetchone()["n"]
    results.append(check("database seeded", n == 8, f"{n} incidents"))
    expected_title = get_incident(conn, TARGET)["title"]

    system = ("You answer questions about IT incidents. Always use the "
              "get_incident tool to look incidents up; never guess.")
    messages = [UserMessage(f"What is the title of incident {TARGET}?")]
    calls_seen = []
    final_text = None

    for step in range(1, MAX_STEPS + 1):
        response = provider.chat(system, messages, [GET_INCIDENT])
        messages.append(AssistantMessage(response))

        if not response.tool_calls:
            final_text = response.text
            break

        tool_results = []
        for call in response.tool_calls:
            print(f"  step {step}: model called {call.name}({call.args})")
            calls_seen.append(call)
            if call.name == "get_incident":
                content = get_incident(conn, call.args.get("incident_key", ""))
            else:
                content = {"error": f"Unknown tool {call.name}"}
            tool_results.append(ToolResult(call.id, call.name, content))
        messages.append(ToolResultsMessage(tool_results))

    conn.close()

    results.append(check(
        "model called get_incident with the right key",
        any(c.name == "get_incident" and c.args.get("incident_key") == TARGET
            for c in calls_seen),
        f"{len(calls_seen)} call(s)",
    ))
    results.append(check(
        "final answer contains the database title",
        bool(final_text) and expected_title.lower() in final_text.lower(),
        f"expected {expected_title!r}",
    ))
    print(f"\nModel's final answer:\n{final_text}")

    return 0 if all(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # Print a short, secret-free message instead of a raw traceback.
        print(f"[ERROR] {redact(f'{type(e).__name__}: {e}')}")
        sys.exit(2)
