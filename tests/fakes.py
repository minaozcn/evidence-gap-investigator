"""Shared fakes for investigation tests: a scripted LLM provider (no API)."""

from investigator.llm.base import (LLMProvider, LLMResponse, ToolCall,
                                   ToolResultsMessage)


class ScriptedProvider(LLMProvider):
    """Plays a fixed script. script(turn, messages, tools) -> LLMResponse."""

    model = "scripted-fake"

    def __init__(self, script):
        self.script = script
        self.turns = 0
        self.offered_tools = []

    def check_model(self):
        pass

    def chat(self, system, messages, tools):
        self.turns += 1
        self.offered_tools.append([t.name for t in tools])
        return self.script(self.turns, messages, tools)


def call(name, **args):
    return ToolCall(id=f"{name}-{len(args)}", name=name, args=args)


def tool_turn(*calls):
    return LLMResponse(text=None, tool_calls=list(calls))


def submit(submission):
    return LLMResponse(text=None, tool_calls=[
        ToolCall(id="submit", name="submit_investigation", args=submission)])


def gather_first_tools(key):
    return tool_turn(call("get_incident", incident_key=key),
                     call("get_evidence", incident_key=key),
                     call("find_similar_incidents", incident_key=key))


def latest_results(messages) -> dict:
    """Map tool name -> latest result content seen in the conversation."""
    out = {}
    for m in messages:
        if isinstance(m, ToolResultsMessage):
            for r in m.results:
                out[r.name] = r.content
    return out


def evidence_id(results, text) -> str:
    """Find the real E- id of the evidence whose content contains `text`."""
    for e in results["get_evidence"]["evidence"]:
        if text in e["content"]:
            return e["evidence_id"]
    raise LookupError(text)


def gap_id(results, source_name) -> str:
    for g in results["get_evidence"]["data_gaps"]:
        if g["source_name"] == source_name:
            return g["gap_id"]
    raise LookupError(source_name)


def ref(eid, reason="see evidence"):
    return {"evidence_id": eid, "reason": reason}
