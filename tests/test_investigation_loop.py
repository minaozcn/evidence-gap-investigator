"""Offline tests for the investigation loop (fake provider, fake tools, no DB, no API)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from investigator.investigation import (MAX_MODEL_TURNS, MAX_TOOL_CALLS,
                                        MAX_TOOL_CALLS_PER_TURN,
                                        run_investigation, save_run)
from investigator.llm.base import LLMResponse
from tests.fakes import (ScriptedProvider, call, latest_results, ref, submit,
                         tool_turn)

KEY = "INC-9"
TOOL_OUTPUT = {
    "get_incident": {"incident_key": KEY, "title": "t"},
    "get_evidence": {
        "incident_key": KEY,
        "evidence": [{"evidence_id": "E-1", "kind": "observation", "content": "errors from pod a"},
                     {"evidence_id": "E-2", "kind": "negative_finding", "content": "no deploys"}],
        "data_gaps": [{"gap_id": "GAP-3", "kind": "data_gap", "content": "logs missing"}],
    },
    "find_similar_incidents": {"target_incident_key": KEY,
                               "similar_incidents": [{"incident_key": "INC-5"}]},
}


class FakeTools:
    def __init__(self):
        self.calls = []

    def __call__(self, name, args):
        self.calls.append((name, args))
        return TOOL_OUTPUT.get(name, {"error": f"Unknown tool {name}"})


def gather():
    return tool_turn(call("get_incident", incident_key=KEY),
                     call("get_evidence", incident_key=KEY),
                     call("find_similar_incidents", incident_key=KEY))


def good_submission(**changes):
    s = {"incident_key": KEY, "verdict": "inconclusive", "summary": "unclear",
         "hypotheses": [{"id": "H1", "title": "bad pod", "root_cause_category": "infrastructure",
                         "confidence": "medium", "status": "plausible",
                         "supporting_evidence": [ref("E-1")], "contradicting_evidence": [],
                         "related_history": ["INC-5"]}],
         "next_checks": [{"description": "get logs", "why": "missing", "hypotheses": ["H1"],
                          "based_on_gaps": ["GAP-3"]}]}
    s.update(changes)
    return s


def bad_submission():
    s = good_submission()
    s["hypotheses"][0]["supporting_evidence"] = [ref("GAP-3")]
    return s


def run(script):
    provider, tools = ScriptedProvider(script), FakeTools()
    return run_investigation(provider, tools, KEY), provider, tools


class LoopTests(unittest.TestCase):
    def test_happy_path(self):
        r, p, t = run(lambda turn, m, tl: gather() if turn == 1 else submit(good_submission()))
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["investigation"]["verdict"], "inconclusive")
        self.assertEqual(p.turns, 2)
        self.assertEqual(len(t.calls), 3)
        self.assertEqual(r["rejected_submissions"], [])

    def test_one_repair_then_success(self):
        script = {1: gather(), 2: submit(bad_submission()), 3: submit(good_submission())}
        seen = {}

        def play(turn, messages, tools):
            seen[turn] = latest_results(messages)
            return script[turn]
        r, p, _ = run(play)
        # Before the repair turn, the model was shown the violations.
        self.assertIn("violations", seen[3]["submit_investigation"])
        self.assertEqual(r["status"], "completed")
        self.assertEqual(len(r["rejected_submissions"]), 1)
        self.assertTrue(any("GAP-3 is missing data" in v
                            for v in r["rejected_submissions"][0]["violations"]))
        self.assertEqual(r["investigation"], good_submission())
        self.assertEqual(p.turns, 3)

    def test_invalid_twice_fails_and_stores_no_investigation(self):
        script = {1: gather(), 2: submit(bad_submission()), 3: submit(bad_submission())}
        r, p, _ = run(lambda turn, m, tl: script[turn])
        self.assertEqual(r["status"], "failed")
        self.assertIsNone(r["investigation"])
        self.assertEqual(r["failure"]["reason"], "validation_failed")
        self.assertTrue(r["failure"]["violations"])
        self.assertEqual(len(r["rejected_submissions"]), 2)
        self.assertEqual(p.turns, 3)

    def test_turn_limit_and_cached_duplicates(self):
        r, p, t = run(lambda turn, m, tl: tool_turn(call("get_incident", incident_key=KEY)))
        self.assertEqual(p.turns, MAX_MODEL_TURNS)
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["failure"]["reason"], "turn_limit")
        self.assertEqual(len(t.calls), 1)                      # rest served from cache
        self.assertEqual(r["stats"]["cached_tool_calls"], MAX_MODEL_TURNS - 2)
        self.assertEqual(p.offered_tools[-1], ["submit_investigation"])
        self.assertEqual(len(p.offered_tools[0]), 4)

    def test_text_only_answers_hit_the_limit(self):
        r, p, _ = run(lambda turn, m, tl: LLMResponse(text="I think it is DNS."))
        self.assertEqual(p.turns, MAX_MODEL_TURNS)
        self.assertEqual(r["failure"]["reason"], "turn_limit")

    def test_per_turn_tool_cap(self):
        many = tool_turn(*[call("get_incident", incident_key=f"INC-{i}") for i in range(10)])
        r, _, t = run(lambda turn, m, tl: many if turn == 1 else submit(good_submission()))
        self.assertEqual(len(t.calls), MAX_TOOL_CALLS_PER_TURN)
        self.assertEqual(r["stats"]["refused_tool_calls"], 10 - MAX_TOOL_CALLS_PER_TURN)

    def test_total_tool_cap(self):
        def script(turn, m, tl):
            return tool_turn(*[call("get_incident", incident_key=f"INC-{turn}-{i}")
                               for i in range(MAX_TOOL_CALLS_PER_TURN)])
        r, p, t = run(script)
        self.assertEqual(len(t.calls), MAX_TOOL_CALLS)
        self.assertEqual(p.turns, MAX_MODEL_TURNS)

    def test_model_error_is_redacted(self):
        def boom(turn, m, tl):
            raise RuntimeError("bad key sk-SECRET-123")
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "sk-SECRET-123"}):
            r, p, _ = run(boom)
        self.assertEqual(r["failure"]["reason"], "model_error")
        self.assertNotIn("sk-SECRET-123", json.dumps(r))
        self.assertEqual(p.turns, 1)

    def test_tool_error_fails_cleanly(self):
        provider = ScriptedProvider(lambda turn, m, tl: gather())

        def broken(name, args):
            raise ConnectionError("db down")
        r = run_investigation(provider, broken, KEY)
        self.assertEqual(r["failure"]["reason"], "tool_error")
        self.assertEqual(provider.turns, 1)

    def test_history_requires_find_similar_in_this_run(self):
        def script(turn, m, tl):
            if turn == 1:
                return tool_turn(call("get_evidence", incident_key=KEY))
            return submit(good_submission())
        r, _, _ = run(script)
        self.assertEqual(r["status"], "failed")
        self.assertTrue(any("INC-5 was not returned by find_similar_incidents" in v
                            for v in r["failure"]["violations"]))

    def test_invalid_submission_on_last_turn_fails(self):
        def script(turn, m, tl):
            if turn < MAX_MODEL_TURNS:
                return gather()
            return submit(bad_submission())
        r, p, _ = run(script)
        self.assertEqual(p.turns, MAX_MODEL_TURNS)
        self.assertEqual(r["status"], "failed")
        self.assertIsNone(r["investigation"])

    def test_second_submit_in_same_turn_is_ignored(self):
        two = LLMResponse(text=None, tool_calls=[
            submit(good_submission()).tool_calls[0], submit(bad_submission()).tool_calls[0]])
        r, _, _ = run(lambda turn, m, tl: gather() if turn == 1 else two)
        self.assertEqual(r["status"], "completed")


class SaveRunTests(unittest.TestCase):
    def test_no_secrets_and_no_overwrite(self):
        secrets = {"GEMINI_API_KEY": "key-ABC-999", "MYSQL_PASSWORD": "pw-777"}
        run_record = {"incident_key": KEY, "started_at": "2026-10-17T12:00:00Z",
                      "status": "failed",
                      "failure": {"message": "auth failed for key-ABC-999 / pw-777"}}
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, secrets):
            p1 = save_run(run_record, d)
            p2 = save_run(run_record, d)
            self.assertNotEqual(p1, p2)
            for p in (p1, p2):
                text = Path(p).read_text(encoding="utf-8")
                self.assertNotIn("key-ABC-999", text)
                self.assertNotIn("pw-777", text)
                json.loads(text)


if __name__ == "__main__":
    unittest.main()
