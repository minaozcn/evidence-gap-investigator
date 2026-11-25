"""Gemini implementation of LLMProvider. The only file that imports google-genai."""

import time

from google import genai
from google.genai import errors, types

from .base import (AssistantMessage, LLMProvider, LLMResponse, Message,
                   ToolCall, ToolResultsMessage, ToolSpec, UserMessage)

# Free-tier limits are low (roughly 10 requests/minute), so a 429 is
# expected now and then. Retry a few times, waiting longer each time.
RETRY_WAITS_SECONDS = [10, 30, 60]


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str, thinking_level: str = "low"):
        self.client = genai.Client(api_key=api_key)
        self.model = model
        # "minimal" is rejected by gemini-3.8-flash, so "low" is the floor.
        self.thinking_level = thinking_level

    def check_model(self) -> None:
        self.client.models.get(model=self.model)

    def chat(self, system: str, messages: list[Message],
             tools: list[ToolSpec]) -> LLMResponse:
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name=t.name,
                    description=t.description,
                    parameters_json_schema=t.parameters,
                ) for t in tools
            ])] if tools else None,
            # We run the tools ourselves in our own loop.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_level=self.thinking_level),
        )
        contents = [self._to_content(m) for m in messages]
        response = self._generate_with_retry(contents, config)
        return self._from_response(response)

    # --- helpers -------------------------------------------------------------

    def _generate_with_retry(self, contents, config):
        for attempt, wait in enumerate([0] + RETRY_WAITS_SECONDS):
            if wait:
                print(f"[gemini] rate limited or server error, retrying in {wait}s "
                      f"(attempt {attempt + 1})")
                time.sleep(wait)
            try:
                return self.client.models.generate_content(
                    model=self.model, contents=contents, config=config)
            except errors.APIError as e:
                retryable = e.code == 429 or (e.code is not None and e.code >= 500)
                if not retryable or attempt == len(RETRY_WAITS_SECONDS):
                    raise

    @staticmethod
    def _to_content(message: Message) -> types.Content:
        if isinstance(message, UserMessage):
            return types.Content(role="user", parts=[types.Part(text=message.text)])
        if isinstance(message, AssistantMessage):
            # Send Gemini's own content back untouched (keeps thought signatures).
            return message.response.raw
        if isinstance(message, ToolResultsMessage):
            return types.Content(role="user", parts=[
                types.Part(function_response=types.FunctionResponse(
                    id=r.call_id, name=r.name, response=r.content))
                for r in message.results
            ])
        raise TypeError(f"Unknown message type: {type(message).__name__}")

    @staticmethod
    def _from_response(response: types.GenerateContentResponse) -> LLMResponse:
        candidate = response.candidates[0] if response.candidates else None
        if candidate is None or candidate.content is None:
            reason = candidate.finish_reason if candidate else "no candidates"
            raise RuntimeError(f"Gemini returned no content (finish_reason={reason})")

        parts = candidate.content.parts or []
        if not parts:
            # e.g. MAX_TOKENS or SAFETY: neither an answer nor a tool call.
            raise RuntimeError(
                f"Gemini returned an empty turn (finish_reason={candidate.finish_reason})")
        text = "".join(p.text for p in parts if p.text and not p.thought) or None
        tool_calls = [
            ToolCall(id=p.function_call.id, name=p.function_call.name,
                     args=dict(p.function_call.args or {}))
            for p in parts if p.function_call
        ]
        return LLMResponse(text=text, tool_calls=tool_calls, raw=candidate.content)
