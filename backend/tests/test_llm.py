from __future__ import annotations

import pytest
from types import SimpleNamespace

from google.genai import types

import app.services.llm as llm_module
from app.services.llm import GeminiModel, LLMConfig


class _FakeChat:
    def __init__(self, response) -> None:
        self._response = response
        self._curated_history = []

    def send_message(self, pending_message):
        return self._response


class _FakeChats:
    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.created_histories = []

    def create(self, **kwargs):
        self.created_histories.append(list(kwargs.get("history", [])))
        return _FakeChat(self._responses.pop(0))


class _FakeModels:
    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses) -> None:
        self.chats = _FakeChats(responses)
        self.models = _FakeModels(responses)


def test_generate_raises_when_candidate_content_has_none_parts(monkeypatch) -> None:
    """None parts (e.g. thinking tokens consumed all output budget) raises RuntimeError."""
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(role="model", parts=None),
                finish_reason="UNKNOWN",
            )
        ]
    )
    monkeypatch.setattr(llm_module, "get_genai_client", lambda: _FakeClient([response]))

    model = GeminiModel(
        prompt="Build an app.",
        app_id="app_test",
        system_prompt="You are helpful.",
        tools=[],
        config=LLMConfig(model="gemini-3-pro-preview"),
    )

    with pytest.raises(RuntimeError, match="no usable parts"):
        model.generate()


def test_generate_raises_on_malformed_function_call_with_content(monkeypatch) -> None:
    """MALFORMED_FUNCTION_CALL finish_reason raises even when content is present."""
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    role="model",
                    parts=[SimpleNamespace(function_call=None, text=None, thought_signature=b"sig")],
                ),
                finish_reason="FinishReason.MALFORMED_FUNCTION_CALL",
            )
        ]
    )
    monkeypatch.setattr(llm_module, "get_genai_client", lambda: _FakeClient([response]))

    model = GeminiModel(
        prompt="Build an app.",
        app_id="app_test",
        system_prompt="You are helpful.",
        tools=["write_file"],
        config=LLMConfig(model="gemini-3-pro-preview"),
    )

    with pytest.raises(RuntimeError, match="malformed function call"):
        model.generate()


def test_generate_raises_on_malformed_function_call_without_content(monkeypatch) -> None:
    """MALFORMED_FUNCTION_CALL with no content still raises correct error (not 'no usable content')."""
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=None,
                finish_reason="FinishReason.MALFORMED_FUNCTION_CALL",
            )
        ]
    )
    monkeypatch.setattr(llm_module, "get_genai_client", lambda: _FakeClient([response]))

    model = GeminiModel(
        prompt="Build an app.",
        app_id="app_test",
        system_prompt="You are helpful.",
        tools=["write_file"],
        config=LLMConfig(model="gemini-3-pro-preview"),
    )

    with pytest.raises(RuntimeError, match="malformed function call"):
        model.generate()


def test_generate_does_not_append_history_on_malformed_function_call(monkeypatch) -> None:
    """History must stay clean when MALFORMED_FUNCTION_CALL is detected — no history corruption."""
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    role="model",
                    parts=[SimpleNamespace(function_call=None, text=None, thought_signature=b"sig")],
                ),
                finish_reason="FinishReason.MALFORMED_FUNCTION_CALL",
            )
        ]
    )
    monkeypatch.setattr(llm_module, "get_genai_client", lambda: _FakeClient([response]))

    model = GeminiModel(
        prompt="Build an app.",
        app_id="app_test",
        system_prompt="You are helpful.",
        tools=["write_file"],
        config=LLMConfig(model="gemini-3-pro-preview"),
    )

    with pytest.raises(RuntimeError):
        model.generate()

    # History must be empty — the malformed response must not have been appended.
    assert model._managed_history == []


def test_generate_raises_on_thought_only_response(monkeypatch) -> None:
    """Parts containing only thought signatures (no text, no function calls) raise RuntimeError."""
    thought_only_part = SimpleNamespace(
        function_call=None,
        text=None,
        thought_signature=b"reasoning-bytes",
    )
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(role="model", parts=[thought_only_part]),
                finish_reason="STOP",
            )
        ]
    )
    monkeypatch.setattr(llm_module, "get_genai_client", lambda: _FakeClient([response]))

    model = GeminiModel(
        prompt="Build an app.",
        app_id="app_test",
        system_prompt="You are helpful.",
        tools=[],
        config=LLMConfig(model="gemini-3-pro-preview"),
    )

    with pytest.raises(RuntimeError, match="only internal reasoning"):
        model.generate()


def test_generate_does_not_append_history_on_thought_only_response(monkeypatch) -> None:
    """History must stay clean when a thought-only response is detected."""
    thought_only_part = SimpleNamespace(function_call=None, text=None, thought_signature=b"sig")
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(role="model", parts=[thought_only_part]),
                finish_reason="STOP",
            )
        ]
    )
    monkeypatch.setattr(llm_module, "get_genai_client", lambda: _FakeClient([response]))

    model = GeminiModel(
        prompt="Build an app.",
        app_id="app_test",
        system_prompt="You are helpful.",
        tools=[],
        config=LLMConfig(model="gemini-3-pro-preview"),
    )

    with pytest.raises(RuntimeError):
        model.generate()

    assert model._managed_history == []


def test_generate_appends_history_only_after_valid_text_response(monkeypatch) -> None:
    """History is only appended when a valid text response is returned."""
    text_response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=types.ModelContent(parts=[types.Part.from_text(text="Done.")]),
                finish_reason="STOP",
            )
        ]
    )
    monkeypatch.setattr(llm_module, "get_genai_client", lambda: _FakeClient([text_response]))

    model = GeminiModel(
        prompt="Build an app.",
        app_id="app_test",
        system_prompt="You are helpful.",
        tools=[],
        config=LLMConfig(model="gemini-3-pro-preview"),
    )

    message, tool_calls = model.generate()

    assert message == "Done."
    assert tool_calls is None
    # Both the user turn and the model turn must be in history after success.
    assert len(model._managed_history) == 2
    assert getattr(model._managed_history[0], "role", None) == "user"
    assert getattr(model._managed_history[1], "role", None) == "model"


def test_build_generate_config_includes_thinking_config_when_budget_set(monkeypatch) -> None:
    """ThinkingConfig is passed to GenerateContentConfig when thinking_budget is set."""
    monkeypatch.setattr(llm_module, "get_genai_client", lambda: SimpleNamespace(models=SimpleNamespace()))

    model = GeminiModel(
        prompt="Build an app.",
        app_id="app_test",
        system_prompt="You are helpful.",
        tools=[],
        config=LLMConfig(model="gemini-3-pro-preview", thinking_budget=4000),
    )

    config = model._build_generate_config()

    assert config.thinking_config is not None
    assert config.thinking_config.thinking_budget == 4000


def test_build_generate_config_omits_thinking_config_when_none(monkeypatch) -> None:
    """ThinkingConfig is absent when thinking_budget is None (model uses its own default)."""
    monkeypatch.setattr(llm_module, "get_genai_client", lambda: SimpleNamespace(models=SimpleNamespace()))

    model = GeminiModel(
        prompt="Build an app.",
        app_id="app_test",
        system_prompt="You are helpful.",
        tools=[],
        config=LLMConfig(model="gemini-3-pro-preview", thinking_budget=None),
    )

    config = model._build_generate_config()

    assert config.thinking_config is None


def test_generate_rebuilds_chat_from_local_history_for_tool_turns(monkeypatch) -> None:
    tool_call_response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=types.ModelContent(
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name="read_file",
                                args={"path": "solution.md"},
                            ),
                            thought_signature=b"sig-1",
                        )
                    ]
                )
            )
        ]
    )
    text_response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=types.ModelContent(parts=[types.Part.from_text(text="done")])
            )
        ]
    )
    fake_client = _FakeClient([tool_call_response, text_response])
    monkeypatch.setattr(llm_module, "get_genai_client", lambda: fake_client)

    model = GeminiModel(
        prompt="Build an app.",
        app_id="app_test",
        system_prompt="You are helpful.",
        tools=["read_file"],
        config=LLMConfig(model="gemini-3-pro-preview"),
    )

    message, tool_calls = model.generate()

    assert message is None
    assert tool_calls == [{"tool": "read_file", "payload": {"path": "solution.md"}}]

    model.add_tool_outputs([
        {"tool": "read_file", "response": {"result": "# solution"}},
    ])

    message, tool_calls = model.generate()

    assert message == "done"
    assert tool_calls is None
    assert len(fake_client.models.calls) == 2
    second_history = fake_client.models.calls[-1]["contents"][:-1]
    assert len(second_history) == 2
    assert getattr(second_history[0], "role", None) == "user"
    assert getattr(second_history[1], "role", None) == "model"

