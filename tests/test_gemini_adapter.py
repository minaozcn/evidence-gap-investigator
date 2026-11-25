"""Offline tests for the Gemini adapter. No network, no API quota used."""

import unittest
from unittest import mock

from google.genai import errors, types

from investigator.llm import gemini
from investigator.llm.base import (AssistantMessage, LLMResponse, ToolResult,
                                   ToolResultsMessage, ToolSpec, UserMessage)

TOOL = ToolSpec("get_incident", "Look up an incident.",
                {"type": "object", "properties": {"incident_key": {"type": "string"}},
                 "required": ["incident_key"]})


def make_response(*parts, finish_reason=None):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=list(parts)),
        finish_reason=finish_reason)])


def make_provider(generate):
    provider = gemini.GeminiProvider(api_key="test-key", model="gemini-3.8-flash")
    provider.client = mock.Mock()
    provider.client.models.generate_content.side_effect = generate
    return provider


def api_error(code):
    return errors.APIError(code, {"error": {"code": code, "message": "test", "status": "X"}})


class ResponseParsingTests(unittest.TestCase):
    def test_tool_call_is_parsed_with_id_and_args(self):
        p = make_provider(lambda **_: make_response(types.Part(
            function_call=types.FunctionCall(id="call-1", name="get_incident",
                                             args={"incident_key": "INC-1003"}))))
        r = p.chat("sys", [UserMessage("hi")], [TOOL])
        self.assertEqual(len(r.tool_calls), 1)
        self.assertEqual(r.tool_calls[0].id, "call-1")
        self.assertEqual(r.tool_calls[0].name, "get_incident")
        self.assertEqual(r.tool_calls[0].args, {"incident_key": "INC-1003"})

    def test_plain_text_is_not_a_tool_call(self):
        # A model that only *says* it will call a tool has not called it.
        p = make_provider(lambda **_: make_response(
            types.Part(text="I will call get_incident for INC-1003.")))
        r = p.chat("sys", [UserMessage("hi")], [TOOL])
        self.assertEqual(r.tool_calls, [])
        self.assertIn("get_incident", r.text)

    def test_thought_text_is_excluded_from_answer(self):
        p = make_provider(lambda **_: make_response(
            types.Part(text="secret reasoning", thought=True),
            types.Part(text="Final answer.")))
        r = p.chat("sys", [UserMessage("hi")], [])
        self.assertEqual(r.text, "Final answer.")

    def test_empty_turn_raises(self):
        p = make_provider(lambda **_: make_response(finish_reason="MAX_TOKENS"))
        with self.assertRaises(RuntimeError):
            p.chat("sys", [UserMessage("hi")], [])

    def test_no_candidates_raises(self):
        p = make_provider(lambda **_: types.GenerateContentResponse(candidates=[]))
        with self.assertRaises(RuntimeError):
            p.chat("sys", [UserMessage("hi")], [])


class HistoryTests(unittest.TestCase):
    def test_thought_signature_and_call_id_are_sent_back(self):
        model_turn = types.Content(role="model", parts=[types.Part(
            function_call=types.FunctionCall(id="call-1", name="get_incident",
                                             args={"incident_key": "INC-1003"}),
            thought_signature=b"signature-bytes")])
        history = [
            UserMessage("hi"),
            AssistantMessage(LLMResponse(text=None, raw=model_turn)),
            ToolResultsMessage([ToolResult("call-1", "get_incident", {"title": "t"})]),
        ]
        sent = {}

        def generate(**kwargs):
            sent.update(kwargs)
            return make_response(types.Part(text="done"))

        make_provider(generate).chat("sys", history, [TOOL])
        contents = sent["contents"]
        self.assertIs(contents[1], model_turn)                     # unchanged
        self.assertEqual(contents[1].parts[0].thought_signature, b"signature-bytes")
        fr = contents[2].parts[0].function_response
        self.assertEqual((fr.id, fr.name, fr.response), ("call-1", "get_incident", {"title": "t"}))

    def test_automatic_function_calling_is_disabled(self):
        sent = {}

        def generate(**kwargs):
            sent.update(kwargs)
            return make_response(types.Part(text="ok"))

        make_provider(generate).chat("sys", [UserMessage("hi")], [TOOL])
        self.assertTrue(sent["config"].automatic_function_calling.disable)


@mock.patch.object(gemini.time, "sleep")
class RetryTests(unittest.TestCase):
    def test_retries_are_bounded(self, sleep):
        calls = []

        def generate(**_):
            calls.append(1)
            raise api_error(429)

        with self.assertRaises(errors.APIError):
            make_provider(generate).chat("sys", [UserMessage("hi")], [])
        self.assertEqual(len(calls), 1 + len(gemini.RETRY_WAITS_SECONDS))

    def test_client_errors_are_not_retried(self, sleep):
        calls = []

        def generate(**_):
            calls.append(1)
            raise api_error(400)

        with self.assertRaises(errors.APIError):
            make_provider(generate).chat("sys", [UserMessage("hi")], [])
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    def test_recovers_after_one_rate_limit(self, sleep):
        outcomes = [api_error(429), make_response(types.Part(text="ok"))]

        def generate(**_):
            o = outcomes.pop(0)
            if isinstance(o, Exception):
                raise o
            return o

        r = make_provider(generate).chat("sys", [UserMessage("hi")], [])
        self.assertEqual(r.text, "ok")


if __name__ == "__main__":
    unittest.main()
