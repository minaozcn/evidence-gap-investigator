"""Provider-neutral types for talking to an LLM.

The investigation logic only uses the classes in this file. Each provider
(Gemini today, maybe Claude or OpenAI later) translates them to and from
its own SDK format, so switching providers never touches the agent code.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSpec:
    """A tool the model may call. `parameters` is a JSON Schema object."""
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    """The model asking us to run a tool."""
    id: str | None          # some providers use ids to match results to calls
    name: str
    args: dict[str, Any]


@dataclass
class ToolResult:
    """Our answer to one ToolCall."""
    call_id: str | None
    name: str
    content: dict[str, Any]


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The provider's own record of this model turn. It must be sent back
    # unchanged in the history (Gemini stores "thought signatures" in it).
    # Only the provider that created it reads it.
    raw: Any = None


# --- Conversation history -------------------------------------------------

@dataclass
class UserMessage:
    text: str


@dataclass
class AssistantMessage:
    response: LLMResponse


@dataclass
class ToolResultsMessage:
    results: list[ToolResult]


Message = UserMessage | AssistantMessage | ToolResultsMessage


class LLMProvider(ABC):
    @abstractmethod
    def check_model(self) -> None:
        """Raise an exception if the configured model cannot be used."""

    @abstractmethod
    def chat(self, system: str, messages: list[Message],
             tools: list[ToolSpec]) -> LLMResponse:
        """Send the whole conversation and return the model's next turn."""
