"""Offline tests for the smoke-test tool loop. Uses a fake provider and a fake DB."""

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import scripts.smoke_test as smoke
from investigator.llm.base import LLMProvider, LLMResponse, ToolCall

TITLE = "checkout-api returning 502 errors"


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchone(self):
        return {"n": 8}


class FakeConn:
    def cursor(self):
        return FakeCursor()

    def close(self):
        pass


def fake_get_incident(conn, key):
    if key != "INC-1003":
        return {"error": f"No incident with key {key}"}
    return {"incident_key": "INC-1003", "title": TITLE}


class ScriptedProvider(LLMProvider):
    def __init__(self, turns):
        self.turns = list(turns)
        self.chat_calls = 0

    def check_model(self):
        pass

    def chat(self, system, messages, tools):
        self.chat_calls += 1
        return self.turns.pop(0) if self.turns else self.default()

    def default(self):
        return LLMResponse(text=None, tool_calls=[
            ToolCall("c", "get_incident", {"incident_key": "INC-1003"})])


def run_smoke(provider):
    out = io.StringIO()
    with mock.patch.object(smoke, "get_provider", return_value=provider), \
         mock.patch.object(smoke, "connect", return_value=FakeConn()), \
         mock.patch.object(smoke, "get_incident", side_effect=fake_get_incident), \
         redirect_stdout(out):
        rc = smoke.main()
    return rc, out.getvalue()


class SmokeLoopTests(unittest.TestCase):
    def test_happy_path_passes(self):
        provider = ScriptedProvider([
            LLMResponse(text=None, tool_calls=[
                ToolCall("c1", "get_incident", {"incident_key": "INC-1003"})]),
            LLMResponse(text=f"The title is {TITLE}."),
        ])
        rc, out = run_smoke(provider)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("[PASS]"), 4, out)

    def test_text_only_answer_fails_tool_check(self):
        # Saying the right title without calling the tool is not a pass.
        provider = ScriptedProvider([LLMResponse(text=f"The title is {TITLE}.")])
        rc, out = run_smoke(provider)
        self.assertEqual(rc, 1)
        self.assertIn("[FAIL] model called get_incident", out)

    def test_loop_stops_at_max_steps(self):
        provider = ScriptedProvider([])          # calls the tool forever
        rc, out = run_smoke(provider)
        self.assertEqual(provider.chat_calls, smoke.MAX_STEPS)
        self.assertEqual(rc, 1)

    def test_unknown_tool_gets_error_result(self):
        provider = ScriptedProvider([
            LLMResponse(text=None, tool_calls=[ToolCall("c1", "drop_tables", {})]),
            LLMResponse(text="ok"),
        ])
        rc, out = run_smoke(provider)
        self.assertEqual(rc, 1)
        self.assertIn("drop_tables", out)

    def test_model_unreachable_fails_cleanly(self):
        provider = ScriptedProvider([])
        provider.check_model = mock.Mock(side_effect=RuntimeError("model not found"))
        rc, out = run_smoke(provider)
        self.assertEqual(rc, 1)
        self.assertIn("[FAIL] model reachable", out)


if __name__ == "__main__":
    unittest.main()
