"""Run the investigation agent on one incident with the configured LLM.

THIS USES THE REAL LLM API. Each run makes at most 8 model turns
(MAX_MODEL_TURNS); with rate-limit retries the worst case is 8 x 4 HTTP
requests. Results are written to runs/ (git-ignored).

    python -m scripts.investigate INC-1003
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

AGENT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(AGENT_ROOT / ".env")

from investigator.db import connect  # noqa: E402
from investigator.investigation import run_investigation, save_run  # noqa: E402
from investigator.llm import get_provider  # noqa: E402
from investigator.redact import redact  # noqa: E402
from investigator.tools import execute_tool  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("incident_key")
    args = parser.parse_args()

    provider = get_provider()
    conn = connect()
    try:
        run = run_investigation(provider, lambda name, a: execute_tool(conn, name, a),
                                args.incident_key)
    finally:
        conn.close()
    run["provider"] = type(provider).__name__
    run["model"] = getattr(provider, "model", None)

    path = save_run(run, AGENT_ROOT / "runs")
    s = run["stats"]
    print(f"status: {run['status']}  (model turns {s['model_turns']}/{s['max_model_turns']}, "
          f"tool calls {s['tool_calls']}, rejected submissions {len(run['rejected_submissions'])})")
    if run["status"] == "completed":
        inv = run["investigation"]
        print(f"verdict: {inv['verdict']}")
        for h in inv["hypotheses"]:
            print(f"  {h['id']} [{h['status']}, {h['confidence']}] {h['title']}")
    else:
        print(f"failure: {run['failure']}")
    print(f"saved: {path.relative_to(AGENT_ROOT)}")
    return 0 if run["status"] == "completed" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[ERROR] {redact(f'{type(e).__name__}: {e}')}")
        sys.exit(2)
