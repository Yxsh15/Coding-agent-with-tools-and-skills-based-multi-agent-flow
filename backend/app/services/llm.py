"""Google GenAI SDK integration for agent LLM calls."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from google import genai
from google.genai import types


logger = logging.getLogger("app.services.llm")
TRANSIENT_TRANSPORT_ERROR_NAMES = {
    "ConnectError",
    "ConnectTimeout",
    "ConnectionError",
    "ProtocolError",
    "ReadError",
    "ReadTimeout",
    "RemoteProtocolError",
    "TimeoutException",
    "WriteError",
    "WriteTimeout",
}
# HTTP status codes that are transient and should be retried
TRANSIENT_HTTP_STATUS_CODES = {429, 500, 503, 529}
MAX_TRANSPORT_RETRIES = 5


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        next_exc = current.__cause__ or current.__context__
        current = next_exc if isinstance(next_exc, BaseException) else None
    return chain


def _is_transient_transport_error(exc: BaseException) -> bool:
    for error in _exception_chain(exc):
        if type(error).__name__ in TRANSIENT_TRANSPORT_ERROR_NAMES:
            return True
        # Handle google.genai API errors by status code (503 = overloaded, 429 = rate limit, 500 = server error)
        status_code = getattr(error, "status_code", None) or getattr(error, "code", None)
        if isinstance(status_code, int) and status_code in TRANSIENT_HTTP_STATUS_CODES:
            return True
        message = str(error).lower()
        if any(
            snippet in message
            for snippet in (
                "connection reset by peer",
                "connection aborted",
                "connection refused",
                "connection terminated",
                "broken pipe",
                "timed out",
                "timeout",
                "temporarily unavailable",
                "high demand",
                "rate limit",
                "overloaded",
                "unavailable",
                "try again",
                "resource exhausted",
            )
        ):
            return True
    return False


def _to_log_json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=True)


def _mapping_keys(value: Any) -> list[str]:
    if value is None:
        return []
    try:
        return sorted(str(key) for key in dict(value).keys())
    except Exception:
        return []


def _summarize_part(part: Any) -> dict[str, Any]:
    function_call = getattr(part, "function_call", None)
    function_response = getattr(part, "function_response", None)
    text = getattr(part, "text", None)
    thought_signature = getattr(part, "thought_signature", None)

    summary: dict[str, Any] = {
        "has_thought_signature": thought_signature is not None,
    }
    if thought_signature is not None:
        try:
            summary["thought_signature_len"] = len(thought_signature)
        except TypeError:
            summary["thought_signature_len"] = "unknown"

    if function_call is not None:
        summary["type"] = "function_call"
        summary["name"] = getattr(function_call, "name", None)
        summary["arg_keys"] = _mapping_keys(getattr(function_call, "args", None))
        return summary

    if function_response is not None:
        summary["type"] = "function_response"
        summary["name"] = getattr(function_response, "name", None)
        summary["response_keys"] = _mapping_keys(getattr(function_response, "response", None))
        return summary

    if text:
        summary["type"] = "text"
        summary["text_len"] = len(text)
        if os.environ.get("LLM_LOG_INCLUDE_TEXT", "0") == "1":
            summary["text_preview"] = text[:200]
        return summary

    summary["type"] = "unknown"
    return summary


def _summarize_content(content: Any) -> dict[str, Any]:
    parts = getattr(content, "parts", None) or []
    return {
        "role": getattr(content, "role", None),
        "parts": [_summarize_part(part) for part in parts],
    }


def _summarize_history(messages: list[Any]) -> list[dict[str, Any]]:
    return [_summarize_content(message) for message in messages]


def _missing_signature_function_calls(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for index, content in enumerate(history):
        for part_index, part in enumerate(content["parts"]):
            if part.get("type") == "function_call" and not part.get("has_thought_signature"):
                missing.append(
                    {
                        "content_index": index,
                        "part_index": part_index,
                        "role": content.get("role"),
                        "name": part.get("name"),
                    }
                )
    return missing


def _sdk_version() -> str:
    try:
        return version("google-genai")
    except PackageNotFoundError:
        return "unknown"


@dataclass
class LLMConfig:
    """Configuration for LLM calls."""
    model: str = "gemini-3-pro-preview"
    temperature: float = 0.7
    max_output_tokens: int = 8192
    top_p: float = 0.95
    top_k: int = 40
    thinking_budget: int | None = None  # cap thinking tokens; None = model default


def get_genai_client() -> genai.Client:
    """Get Google GenAI client with API key from environment."""
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError(
            "GOOGLE_API_KEY or GEMINI_API_KEY environment variable must be set. "
            "Get your API key from https://aistudio.google.com/apikey"
        )
    return genai.Client(api_key=api_key)


def build_tools_schema(tools: list[str]) -> list[types.Tool]:
    """Build Gemini function declarations from tool names."""
    tool_definitions = {
        "read_file": types.FunctionDeclaration(
            name="read_file",
            description="Read the contents of a file from the workspace.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "path": types.Schema(type=types.Type.STRING, description="Relative path to the file to read"),
                    "start": types.Schema(type=types.Type.INTEGER, description="Start line (optional)"),
                    "end": types.Schema(type=types.Type.INTEGER, description="End line (optional)"),
                },
                required=["path"],
            ),
        ),
        "write_file": types.FunctionDeclaration(
            name="write_file",
            description="Write content to a file in the workspace. Creates directories as needed.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "path": types.Schema(type=types.Type.STRING, description="Relative path to the file"),
                    "content": types.Schema(type=types.Type.STRING, description="Content to write to the file"),
                },
                required=["path", "content"],
            ),
        ),
        "apply_diff": types.FunctionDeclaration(
            name="apply_diff",
            description=(
                "Apply a targeted unified diff to modify an existing file without rewriting the whole file. "
                "Use it for localized edits in one logical area or a few nearby regions, not for broad rewrites."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "path": types.Schema(type=types.Type.STRING, description="Path to the file to patch"),
                    "diff": types.Schema(type=types.Type.STRING, description="Unified diff content"),
                },
                required=["path", "diff"],
            ),
        ),
        "grep": types.FunctionDeclaration(
            name="grep",
            description=(
                "Search for a pattern in files. Use this to compare routes, IDs, CSS classes, object keys, and event bindings across the workspace."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(type=types.Type.STRING, description="Search pattern"),
                    "path": types.Schema(type=types.Type.STRING, description="Path to search in (optional)"),
                },
                required=["query"],
            ),
        ),
        "glob": types.FunctionDeclaration(
            name="glob",
            description="List files matching a glob pattern so you can inventory the relevant workspace files before validating or patching.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "pattern": types.Schema(type=types.Type.STRING, description="Glob pattern (e.g., '**/*.json')"),
                    "path": types.Schema(type=types.Type.STRING, description="Base path (optional)"),
                },
                required=["pattern"],
            ),
        ),
        "bash": types.FunctionDeclaration(
            name="bash",
            description="Execute a bash command. Only safe commands are allowed.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "command": types.Schema(type=types.Type.STRING, description="Command to execute"),
                },
                required=["command"],
            ),
        ),
        "todos": types.FunctionDeclaration(
            name="todos",
            description="Manage a todo list for tracking progress.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "action": types.Schema(
                        type=types.Type.STRING,
                        description="Action: 'replace', 'mark_done', or 'mark_in_progress'",
                    ),
                    "items": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.OBJECT),
                        description="Todo items (for 'replace' action)",
                    ),
                    "id": types.Schema(type=types.Type.INTEGER, description="Todo ID (for mark actions)"),
                },
                required=["action"],
            ),
        ),
        "web_search": types.FunctionDeclaration(
            name="web_search",
            description="Search the web for information.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(type=types.Type.STRING, description="Search query"),
                },
                required=["query"],
            ),
        ),
        "web_fetch": types.FunctionDeclaration(
            name="web_fetch",
            description="Fetch content from a URL.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "url": types.Schema(type=types.Type.STRING, description="URL to fetch"),
                },
                required=["url"],
            ),
        ),
        "invoke_agent": types.FunctionDeclaration(
            name="invoke_agent",
            description=(
                "Invoke a specialist sub-agent for a narrow task. "
                "The orchestrator should decide whether the request is simple or complex; "
                "for complex work, delegate object modeling, validation, and page building deliberately."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "agent": types.Schema(type=types.Type.STRING, description="Name of the agent to invoke"),
                    "instructions": types.Schema(
                        type=types.Type.STRING,
                        description="Concrete instructions for the agent, including deliverables and constraints",
                    ),
                    "context_paths": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING),
                        description="Paths the agent should inspect before working",
                    ),
                },
                required=["agent", "instructions"],
            ),
        ),
        "validate_workspace": types.FunctionDeclaration(
            name="validate_workspace",
            description=(
                "Run structural validation on the entire workspace. Checks: missing required files, "
                "broken asset references, JS syntax errors, DOM IDs referenced in app.js but missing from HTML, "
                "unwired interactive elements, CSS class gaps, and partial tab implementations. "
                "Call this AFTER writing app.js and styles.css to catch integration issues before finishing."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={},
            ),
        ),
        "validate_syntax": types.FunctionDeclaration(
            name="validate_syntax",
            description=(
                "Check syntax of a single file. For .js runs node --check, for .json validates JSON parse, "
                "for .html checks basic structure, for .css checks brace balance. "
                "Call this after writing a file to catch errors immediately."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "path": types.Schema(type=types.Type.STRING, description="Relative path to the file to validate"),
                },
                required=["path"],
            ),
        ),
    }

    function_declarations = []
    for tool_name in tools:
        if tool_name in tool_definitions:
            function_declarations.append(tool_definitions[tool_name])

    if function_declarations:
        return [types.Tool(function_declarations=function_declarations)]
    return []


class GeminiModel:
    """Gemini LLM wrapper for agent execution."""

    def __init__(
        self,
        prompt: str,
        app_id: str,
        system_prompt: str,
        tools: list[str],
        config: LLMConfig,
    ) -> None:
        self.prompt = prompt
        self.app_id = app_id
        self.system_prompt = system_prompt
        self.tools = tools
        self.config = config
        self.client = get_genai_client()
        self.pending_message: str | list[types.Part] | None = None
        self._managed_history: list[types.Content] = []
        self._initialized = False
        self._turn_index = 0
        self._last_response_summary: dict[str, Any] | None = None
        self._compression_context = ""
        self._sdk_version = _sdk_version()
        part_fields = getattr(types.Part, "model_fields", {})
        self._supports_thought_signatures = "thought_signature" in part_fields

    def _build_generate_config(self) -> types.GenerateContentConfig:
        system_instruction = self.system_prompt
        if self._compression_context:
            system_instruction = f"{system_instruction}\n\n{self._compression_context}"
        thinking_config = (
            types.ThinkingConfig(thinking_budget=self.config.thinking_budget)
            if self.config.thinking_budget is not None
            else None
        )
        return types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=self.config.temperature,
            max_output_tokens=self.config.max_output_tokens,
            top_p=self.config.top_p,
            top_k=self.config.top_k,
            tools=build_tools_schema(self.tools) or None,
            thinking_config=thinking_config,
        )

    def _reset_chat_from_history(self) -> None:
        logger.warning(
            "Retrying Gemini request after transient transport failure app_id=%s model=%s history_len=%s",
            self.app_id,
            self.config.model,
            len(self._history()),
        )

    def _rebuild_chat(self) -> None:
        return None

    def _pending_user_content(self) -> types.UserContent:
        if self.pending_message is None:
            raise RuntimeError("Gemini pending message missing while building local history.")
        if isinstance(self.pending_message, str):
            return types.UserContent(parts=[types.Part.from_text(text=self.pending_message)])
        return types.UserContent(parts=list(self.pending_message))

    def _init_conversation(self) -> None:
        """Initialize conversation with system prompt and user message."""
        if self._initialized:
            return
        self.pending_message = self.prompt
        self._initialized = True

        logger.info(
            "Initialized Gemini chat app_id=%s model=%s sdk_version=%s supports_thought_signatures=%s",
            self.app_id,
            self.config.model,
            self._sdk_version,
            self._supports_thought_signatures,
        )
        if self.config.model.startswith("gemini-3") and not self._supports_thought_signatures:
            logger.warning(
                "Installed google-genai sdk appears too old for Gemini 3 thought signatures app_id=%s sdk_version=%s",
                self.app_id,
                self._sdk_version,
            )
            raise RuntimeError(
                "Installed google-genai SDK does not support Gemini 3 thought signatures. "
                f"Current version: {self._sdk_version}. Upgrade the backend environment to "
                "google-genai==1.68.0 and reinstall backend requirements."
            )

    def _history(self) -> list[Any]:
        return list(self._managed_history)

    def compress_history(self, recent_history: list[Any], compression_context: str) -> None:
        """Rebuild the chat with compacted history and a persisted compression summary."""
        self._compression_context = compression_context.strip()
        self._managed_history = list(recent_history)
        logger.info(
            "Compressed Gemini chat history app_id=%s model=%s recent_history_len=%s summary_len=%s",
            self.app_id,
            self.config.model,
            len(self._managed_history),
            len(self._compression_context),
        )

    def _pending_message_summary(self) -> dict[str, Any] | None:
        if self.pending_message is None:
            return None
        if isinstance(self.pending_message, str):
            return {
                "role": "user",
                "parts": [{"type": "text", "text_len": len(self.pending_message), "has_thought_signature": False}],
            }
        return {
            "role": "user",
            "parts": [_summarize_part(part) for part in self.pending_message],
        }

    def generate(self) -> tuple[str | None, list[dict[str, Any]] | None]:
        """
        Generate the next response from the model.
        Returns (message, None) for text response or (None, tool_calls) for tool calls.
        """
        self._init_conversation()
        self._turn_index += 1
        if self.pending_message is None:
            raise RuntimeError("Gemini generate() called without a pending user or tool message.")
        pending_content = self._pending_user_content()

        history_summary = _summarize_history(self._history())
        pending_summary = self._pending_message_summary()
        logger.debug(
            "Gemini request app_id=%s turn=%s model=%s tools=%s history=%s pending=%s",
            self.app_id,
            self._turn_index,
            self.config.model,
            self.tools,
            _to_log_json(history_summary),
            _to_log_json(pending_summary),
        )
        missing_signatures = _missing_signature_function_calls(history_summary)
        if missing_signatures:
            logger.warning(
                "Gemini history contains function calls without thought signatures app_id=%s turn=%s missing=%s",
                self.app_id,
                self._turn_index,
                _to_log_json(missing_signatures),
            )

        # Save pending_message before the API call so MALFORMED_FUNCTION_CALL can restore it
        saved_pending_message = self.pending_message

        response = None
        for attempt in range(1, MAX_TRANSPORT_RETRIES + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.config.model,
                    contents=self._history() + [pending_content],
                    config=self._build_generate_config(),
                )
                self.pending_message = None
                break
            except Exception as exc:
                is_transient = _is_transient_transport_error(exc)
                if is_transient and attempt < MAX_TRANSPORT_RETRIES:
                    backoff_seconds = min(float(2 ** (attempt - 1)), 32.0)
                    logger.warning(
                        "Gemini transient transport failure app_id=%s turn=%s attempt=%s/%s model=%s error=%s retry_in=%ss",
                        self.app_id,
                        self._turn_index,
                        attempt,
                        MAX_TRANSPORT_RETRIES,
                        self.config.model,
                        repr(exc),
                        backoff_seconds,
                    )
                    self._reset_chat_from_history()
                    time.sleep(backoff_seconds)
                    continue

                logger.exception(
                    "Gemini request failed app_id=%s turn=%s model=%s sdk_version=%s attempts=%s history=%s pending=%s last_response=%s",
                    self.app_id,
                    self._turn_index,
                    self.config.model,
                    self._sdk_version,
                    attempt,
                    _to_log_json(history_summary),
                    _to_log_json(pending_summary),
                    _to_log_json(self._last_response_summary),
                )
                if is_transient:
                    raise RuntimeError(
                        "Transient network error while talking to Gemini after "
                        f"{attempt} attempts: {exc}"
                    ) from exc
                raise

        if response is None:
            raise RuntimeError("Gemini request did not produce a response after retry handling.")

        # Get the response
        candidate = response.candidates[0] if response.candidates else None
        finish_reason = str(getattr(candidate, "finish_reason", "UNKNOWN")) if candidate else "NO_CANDIDATE"

        if not candidate or not candidate.content:
            logger.warning(
                "Gemini returned empty candidate app_id=%s turn=%s finish_reason=%s",
                self.app_id,
                self._turn_index,
                finish_reason,
            )
            if "MALFORMED_FUNCTION_CALL" in finish_reason:
                self.pending_message = saved_pending_message
                raise RuntimeError(
                    f"Gemini generated a malformed function call (finish_reason={finish_reason}). "
                    "The model produced invalid tool-call syntax. This is usually transient — the runner will retry."
                )
            if "MAX_TOKENS" in finish_reason:
                raise RuntimeError(
                    f"Response cut off: model hit max_output_tokens limit (finish_reason={finish_reason}). "
                    "Increase max_output_tokens in the agent config or break the task into smaller steps."
                )
            if "SAFETY" in finish_reason:
                raise RuntimeError(
                    f"Response blocked by safety filters (finish_reason={finish_reason}). "
                    "Rephrase the request or check content policy."
                )
            raise RuntimeError(
                f"Gemini returned no usable content (finish_reason={finish_reason}). "
                "This may be a transient model error — the runner will retry."
            )

        # Check finish_reason BEFORE appending to history so a malformed/bad response
        # never corrupts the conversation history for subsequent turns.
        if "MALFORMED_FUNCTION_CALL" in finish_reason:
            logger.warning(
                "Gemini returned MALFORMED_FUNCTION_CALL app_id=%s turn=%s finish_reason=%s",
                self.app_id,
                self._turn_index,
                finish_reason,
            )
            self.pending_message = saved_pending_message
            raise RuntimeError(
                f"Gemini generated a malformed function call (finish_reason={finish_reason}). "
                "The model produced invalid tool-call syntax. This is usually transient — the runner will retry."
            )

        candidate_summary = _summarize_content(candidate.content)
        self._last_response_summary = candidate_summary
        logger.debug(
            "Gemini response app_id=%s turn=%s finish_reason=%s summary=%s",
            self.app_id,
            self._turn_index,
            finish_reason,
            _to_log_json(candidate_summary),
        )

        parts = list(getattr(candidate.content, "parts", None) or [])
        if not parts:
            logger.warning(
                "Gemini returned candidate content without iterable parts app_id=%s turn=%s finish_reason=%s summary=%s",
                self.app_id,
                self._turn_index,
                finish_reason,
                _to_log_json(candidate_summary),
            )
            raise RuntimeError(
                f"Gemini returned candidate with no usable parts (finish_reason={finish_reason}). "
                "Thinking tokens may have consumed all of max_output_tokens. "
                "Increase max_output_tokens or lower thinking_budget in the agent config."
            )

        # Check for function calls
        tool_calls: list[dict[str, Any]] = []
        for part in parts:
            if part.function_call:
                fc = part.function_call
                tool_calls.append(
                    {
                        "tool": fc.name,
                        "payload": dict(fc.args) if fc.args else {},
                    }
                )

        # Extract text response
        text_parts = [part.text for part in parts if part.text]

        if not tool_calls and not text_parts:
            # All parts were thought-only (no text, no function calls) — thinking exhausted the token budget
            logger.warning(
                "Gemini response had only thought parts, no text or tool calls app_id=%s turn=%s finish_reason=%s",
                self.app_id,
                self._turn_index,
                finish_reason,
            )
            raise RuntimeError(
                f"Gemini response contained only internal reasoning with no output (finish_reason={finish_reason}). "
                "The thinking budget likely consumed all max_output_tokens. "
                "Increase max_output_tokens or lower thinking_budget in the agent config."
            )

        # Only append to history once we have a valid, usable response
        self._managed_history.append(pending_content)
        self._managed_history.append(candidate.content)

        if tool_calls:
            return None, tool_calls

        return " ".join(text_parts), None

    def add_tool_outputs(self, outputs: list[dict[str, Any]]) -> None:
        """Add tool outputs to the conversation in a single user turn, preserving call order and IDs."""
        parts: list[types.Part] = []
        output_summary: list[dict[str, Any]] = []
        for output in outputs:
            response_payload = output["response"]
            output_summary.append(
                {
                    "tool": output["tool"],
                    "response_keys": sorted(str(key) for key in response_payload.keys()),
                    "has_error": "error" in response_payload,
                }
            )
            parts.append(
                types.Part.from_function_response(
                    name=output["tool"],
                    response=response_payload,
                )
            )
        self.pending_message = parts
        logger.debug(
            "Gemini tool outputs queued app_id=%s turn=%s outputs=%s appended=%s history=%s",
            self.app_id,
            self._turn_index,
            _to_log_json(output_summary),
            _to_log_json({"role": "user", "parts": [_summarize_part(part) for part in parts]}),
            _to_log_json(_summarize_history(self._history())),
        )
