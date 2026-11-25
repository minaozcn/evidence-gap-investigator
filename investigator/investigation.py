"""The investigation agent loop.

One "model turn" = one provider.chat() call. The loop is bounded:
  - at most MAX_MODEL_TURNS turns in total (repair turns included);
  - on the last turn only submit_investigation is offered;
  - at most MAX_TOOL_CALLS_PER_TURN tool calls are executed per turn and
    MAX_TOOL_CALLS in total; repeated identical calls are answered from a
    cache instead of hitting the database again;
  - one repair attempt after a rejected submission;
  - provider errors end the investigation (the provider itself retries
    rate limits a bounded number of times, see llm/gemini.py).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from .llm.base import (AssistantMessage, LLMProvider, ToolResult,
                       ToolResultsMessage, ToolSpec, UserMessage)
from .redact import redact
from .validation import (CATEGORIES, CONFIDENCES, STATUSES, VERDICTS,
                         ValidationContext, validate_submission)

MAX_MODEL_TURNS = 8
MAX_REPAIRS = 1
MAX_TOOL_CALLS_PER_TURN = 6
MAX_TOOL_CALLS = 20

_EVIDENCE_REF = {
    "type": "object",
    "properties": {
        "evidence_id": {"type": "string", "description": "An E- id from get_evidence"},
        "reason": {"type": "string"},
    },
    "required": ["evidence_id", "reason"],
}

SUBMIT_INVESTIGATION = ToolSpec(
    name="submit_investigation",
    description="Submit the final investigation result. Call exactly once, when done.",
    parameters={
        "type": "object",
        "properties": {
            "incident_key": {"type": "string"},
            "verdict": {"type": "string", "enum": list(VERDICTS)},
            "summary": {"type": "string"},
            "hypotheses": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "H1, H2, ..."},
                    "title": {"type": "string"},
                    "root_cause_category": {"type": "string", "enum": list(CATEGORIES)},
                    "confidence": {"type": "string", "enum": list(CONFIDENCES)},
                    "status": {"type": "string", "enum": list(STATUSES)},
                    "supporting_evidence": {"type": "array", "items": _EVIDENCE_REF},
                    "contradicting_evidence": {"type": "array", "items": _EVIDENCE_REF},
                    "related_history": {"type": "array", "items": {"type": "string"},
                                        "description": "Keys returned by find_similar_incidents"},
                },
                "required": ["id", "title", "root_cause_category", "confidence", "status",
                             "supporting_evidence", "contradicting_evidence",
                             "related_history"],
            }},
            "next_checks": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "why": {"type": "string"},
                    "hypotheses": {"type": "array", "items": {"type": "string"}},
                    "based_on_gaps": {"type": "array", "items": {"type": "string"},
                                      "description": "GAP- ids that motivate this check"},
                },
                "required": ["description", "why", "hypotheses", "based_on_gaps"],
            }},
        },
        "required": ["incident_key", "verdict", "summary", "hypotheses", "next_checks"],
    },
)

SYSTEM_PROMPT = """You investigate IT incidents using only the provided tools.

Process:
1. Call get_incident, get_evidence and find_similar_incidents for the target incident.
2. Form competing hypotheses and weigh the evidence for and against each.
3. Call submit_investigation exactly once with your result.

Rules:
- Cite only evidence ids (E-...) returned by get_evidence. Never invent ids.
- GAP-... ids are missing data. Never cite them as supporting or contradicting
  evidence; use them only in next_checks.based_on_gaps.
- A negative_finding can weaken a hypothesis but cannot rule it out. ruled_out
  requires contradicting evidence of kind 'observation' and confidence 'low'.
- Similar past incidents suggest hypotheses; they are not answers. Medium or
  high confidence needs supporting evidence from THIS incident.
- verdict 'conclusive' only if exactly one hypothesis is 'leading' with
  confidence 'high'. Otherwise use 'inconclusive', keep every confidence at
  'medium' or below, and list concrete next_checks.
- Do not overstate certainty."""

LAST_TURN_PROMPT = ("This is your last turn. Call submit_investigation now with the "
                    "best result the evidence supports.")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _update_context(ctx: ValidationContext, tool: str, result: dict):
    if "error" in result:
        return
    if tool == "get_evidence":
        for e in result.get("evidence", []):
            ctx.evidence_kinds[e["evidence_id"]] = e["kind"]
        for g in result.get("data_gaps", []):
            ctx.gap_ids.add(g["gap_id"])
    elif tool == "find_similar_incidents":
        for m in result.get("similar_incidents", []):
            ctx.similar_keys.add(m["incident_key"])


def run_investigation(provider: LLMProvider, execute_tool, incident_key: str,
                      max_turns: int = MAX_MODEL_TURNS) -> dict:
    """Investigate one incident.

    execute_tool(name, args) -> dict runs a read-only tool (tools.execute_tool
    bound to a DB connection in production; a fake in tests).
    Returns a run record. A submission is stored under "investigation" only
    if it passed validation.
    """
    from .tools import TOOL_SPECS   # local import keeps this module DB-free

    ctx = ValidationContext(incident_key=incident_key)
    run = {
        "incident_key": incident_key,
        "started_at": _now(),
        "status": None,
        "investigation": None,
        "rejected_submissions": [],
        "failure": None,
        "tool_log": [],
        "stats": {"model_turns": 0, "max_model_turns": max_turns, "tool_calls": 0,
                  "cached_tool_calls": 0, "refused_tool_calls": 0},
    }
    cache: dict[str, dict] = {}
    messages = [UserMessage(f"Investigate incident {incident_key}.")]
    all_tools = TOOL_SPECS + [SUBMIT_INVESTIGATION]

    def finish(status, failure=None):
        run["status"] = status
        run["failure"] = failure
        run["finished_at"] = _now()
        return run

    for turn in range(1, max_turns + 1):
        last_turn = turn == max_turns
        tools = [SUBMIT_INVESTIGATION] if last_turn else all_tools
        if last_turn:
            messages.append(UserMessage(LAST_TURN_PROMPT))

        run["stats"]["model_turns"] = turn
        try:
            response = provider.chat(SYSTEM_PROMPT, messages, tools)
        except Exception as e:
            return finish("failed", {"reason": "model_error",
                                     "message": redact(f"{type(e).__name__}: {e}")})
        messages.append(AssistantMessage(response))

        if not response.tool_calls:
            messages.append(UserMessage(
                "Use the tools, then call submit_investigation. Plain text is not a result."))
            continue

        results = []
        submission = None
        for n, call in enumerate(response.tool_calls):
            log = {"turn": turn, "tool": call.name, "args": call.args}
            run["tool_log"].append(log)

            if call.name == SUBMIT_INVESTIGATION.name:
                if submission is None:
                    submission = call
                else:
                    results.append(ToolResult(call.id, call.name, {
                        "error": "Only one submit_investigation per turn; extra call ignored."}))
                continue
            if last_turn or n >= MAX_TOOL_CALLS_PER_TURN or \
                    run["stats"]["tool_calls"] >= MAX_TOOL_CALLS:
                run["stats"]["refused_tool_calls"] += 1
                log["refused"] = True
                content = {"error": "Tool call limit reached. Call submit_investigation."}
            else:
                cache_key = json.dumps([call.name, call.args], sort_keys=True, default=str)
                if cache_key in cache:
                    run["stats"]["cached_tool_calls"] += 1
                    log["cached"] = True
                    content = {**cache[cache_key],
                               "note": "Duplicate call; same result as before."}
                else:
                    run["stats"]["tool_calls"] += 1
                    try:
                        content = execute_tool(call.name, call.args)
                    except Exception as e:
                        return finish("failed", {"reason": "tool_error", "tool": call.name,
                                                 "message": redact(f"{type(e).__name__}: {e}")})
                    cache[cache_key] = content
                    _update_context(ctx, call.name, content)
                if "error" in content:
                    log["error"] = content["error"]
            results.append(ToolResult(call.id, call.name, content))

        if submission is not None:
            violations = validate_submission(submission.args, ctx)
            if not violations:
                run["investigation"] = submission.args
                return finish("completed")

            attempt = len(run["rejected_submissions"]) + 1
            run["rejected_submissions"].append({
                "attempt": attempt,
                "violations": violations,
                "submission_REJECTED_NOT_VALID": submission.args,
            })
            if attempt > MAX_REPAIRS:
                return finish("failed", {"reason": "validation_failed",
                                         "violations": violations})
            results.append(ToolResult(submission.id, submission.name, {
                "error": "Submission rejected. Fix every violation and call "
                         "submit_investigation again.",
                "violations": violations,
            }))

        messages.append(ToolResultsMessage(results))

    return finish("failed", {"reason": "turn_limit",
                             "message": f"No valid submission within {max_turns} model turns."})


def save_run(run: dict, directory) -> Path:
    """Write a run record as JSON. Secrets are redacted as a final safeguard."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = run["started_at"].replace(":", "").replace("-", "")
    path = directory / f"{stamp}_{run['incident_key']}.json"
    n = 2
    while path.exists():                      # never overwrite an earlier run
        path = directory / f"{stamp}_{run['incident_key']}_{n}.json"
        n += 1
    text = redact(json.dumps(run, indent=2, ensure_ascii=False, default=str))
    path.write_text(text, encoding="utf-8")
    return path
