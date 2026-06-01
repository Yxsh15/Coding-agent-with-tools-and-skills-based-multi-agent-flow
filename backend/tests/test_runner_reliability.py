from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.agent_registry import AgentProfile, ErrorHandlingConfig, ExecutionConfig, MemoryConfig
from app.services.runner import _parse_validator_result, _validate_object_models, AgentRunner
import app.services.runner as runner_module
from app.services.tools import ToolBox


def _catalog_products() -> list[dict[str, object]]:
    return [
        {"id": f"prod-{index}", "categoryId": f"cat-{1 + ((index - 1) % 3)}"}
        for index in range(1, 7)
    ]


def test_parse_validator_result_accepts_strict_json() -> None:
    payload = _parse_validator_result(
        json.dumps(
            {
                "status": "INVALID",
                "summary": "Found a mismatch",
                "findings": [
                    {
                        "severity": "high",
                        "path": "pages/home.html",
                        "owner": "page_builder",
                        "issue": "Missing wiring",
                        "why": "The page is not interactive",
                        "fix": "Bind the button",
                    }
                ],
            }
        )
    )

    assert payload is not None
    assert payload["status"] == "INVALID"
    assert payload["findings"]


def test_parse_validator_result_rejects_unstructured_text() -> None:
    assert _parse_validator_result("VALID with some notes") is None
    assert _parse_validator_result('{"summary":"missing status","findings":[]}') is None


def test_validate_object_models_supports_lowercase_keys_and_correct_foreign_keys() -> None:
    object_models = {
        "product": {"mockData": _catalog_products()},
        "category": {
            "mockData": [
                {"id": "cat-1"},
                {"id": "cat-2"},
                {"id": "cat-3"},
            ]
        },
        "user": {
            "mockData": [
                {"id": "usr-admin", "role": "admin"},
                {"id": "usr-cust-1", "role": "customer"},
                {"id": "usr-cust-2", "role": "customer"},
            ]
        },
        "address": {"mockData": [{"id": "addr-1", "userId": "usr-cust-1"}]},
        "cartitem": {"mockData": [{"id": "cart-1", "productId": "prod-1"}]},
        "coupon": {"mockData": [{"id": "coupon-1", "expiryDate": "2099-12-31T00:00:00Z"}]},
        "order": {
            "mockData": [
                {
                    "id": "ord-1",
                    "userId": "usr-cust-1",
                    "items": [{"id": "item-1", "productId": "prod-1"}],
                },
                {
                    "id": "ord-2",
                    "userId": "usr-cust-2",
                    "items": [{"id": "item-2", "productId": "prod-2"}],
                },
            ]
        },
    }

    issues = _validate_object_models(object_models)

    assert not any("objects/CartItem.json references missing product ids" in issue for issue in issues)
    assert not any("objects/Order.json contains order items that reference missing product ids" in issue for issue in issues)


def test_validate_object_models_flags_invalid_nested_order_product_ids() -> None:
    object_models = {
        "product": {"mockData": _catalog_products()},
        "category": {
            "mockData": [
                {"id": "cat-1"},
                {"id": "cat-2"},
                {"id": "cat-3"},
            ]
        },
        "user": {
            "mockData": [
                {"id": "usr-admin", "role": "admin"},
                {"id": "usr-cust-1", "role": "customer"},
                {"id": "usr-cust-2", "role": "customer"},
            ]
        },
        "order": {
            "mockData": [
                {
                    "id": "ord-1",
                    "userId": "usr-cust-1",
                    "items": [{"id": "item-1", "productId": "prod-missing"}],
                },
                {
                    "id": "ord-2",
                    "userId": "usr-cust-2",
                    "items": [{"id": "item-2", "productId": "prod-2"}],
                },
            ]
        },
    }

    issues = _validate_object_models(object_models)

    assert any("objects/Order.json contains order items that reference missing product ids" in issue for issue in issues)


class FakeAgentFS:
    def __init__(self) -> None:
        self.files = {
            "solution.md": "# solution",
            "objects/product.json": "{}",
            "pages/home.html": "<div></div>",
        }

    def read_file(self, app_id: str, relative_path: str, *args, **kwargs) -> str:
        if relative_path not in self.files:
            raise FileNotFoundError(relative_path)
        return self.files[relative_path]

    def search(self, app_id: str, query: str, path_prefix: str | None = None) -> list[dict]:
        return [
            {"path": "solution.md", "line": 1, "snippet": "solution"},
            {"path": "pages/home.html", "line": 1, "snippet": "home"},
        ]

    def glob(self, app_id: str, pattern: str, path_prefix: str | None = None) -> list[str]:
        return ["solution.md", "objects/product.json", "pages/home.html"]


def _specialist_profile(name: str, model_name: str = "gemini-3-pro-preview", fallback_models: list[str] | None = None) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="specialist",
        model_provider="google",
        model_name=model_name,
        temperature=0.1,
        max_output_tokens=1024,
        top_p=0.9,
        top_k=40,
        tools=[],
        skills=[],
        fallback_models=list(fallback_models or []),
        error_handling=ErrorHandlingConfig(),
        memory=MemoryConfig(),
        execution=ExecutionConfig(timeout_ms=120000, max_turns=4, max_parallel_tools=1),
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_toolbox_enforces_context_paths_for_read_scopes() -> None:
    toolbox = ToolBox(FakeAgentFS(), lambda *args, **kwargs: None, lambda *args, **kwargs: None)
    toolbox.set_path_constraints("app_test", "validator", ["solution.md", "objects"])

    with pytest.raises(ValueError):
        await toolbox.read_file("app_test", "validator", {"path": "pages/home.html"}, lambda *args, **kwargs: None)

    grep_matches = await toolbox.grep("app_test", "validator", {"query": "x"}, lambda *args, **kwargs: None)
    glob_matches = await toolbox.glob("app_test", "validator", {"pattern": "*"}, lambda *args, **kwargs: None)

    assert all(match["path"] != "pages/home.html" for match in grep_matches)
    assert "pages/home.html" not in glob_matches


@pytest.mark.asyncio(loop_scope="function")
async def test_invoke_agent_enforces_structured_validator_output() -> None:
    runner = AgentRunner.__new__(AgentRunner)
    runner.registry = SimpleNamespace(
        load_profile=lambda name: SimpleNamespace(name=name),
        root=Path("/tmp/agents"),
    )
    runner.toolbox = SimpleNamespace(
        set_path_constraints=lambda *args, **kwargs: None,
        clear_path_constraints=lambda *args, **kwargs: None,
    )

    async def fake_run_loop(**kwargs):
        return '{"status":"VALID","summary":"ok","findings":[]}'

    runner._run_loop = fake_run_loop

    result = await AgentRunner.invoke_agent(
        runner,
        app_id="app_test",
        name="validator",
        instructions="Validate",
        context_paths=["solution.md"],
        emit=lambda *args, **kwargs: None,
    )

    assert json.loads(result)["status"] == "VALID"


@pytest.mark.asyncio(loop_scope="function")
async def test_invoke_agent_rejects_unstructured_validator_output() -> None:
    runner = AgentRunner.__new__(AgentRunner)
    runner.registry = SimpleNamespace(
        load_profile=lambda name: SimpleNamespace(name=name),
        root=Path("/tmp/agents"),
    )
    runner.toolbox = SimpleNamespace(
        set_path_constraints=lambda *args, **kwargs: None,
        clear_path_constraints=lambda *args, **kwargs: None,
    )

    async def fake_run_loop(**kwargs):
        return "validator hit max_turns=8"

    runner._run_loop = fake_run_loop

    with pytest.raises(ValueError):
        await AgentRunner.invoke_agent(
            runner,
            app_id="app_test",
            name="validator",
            instructions="Validate",
            context_paths=["solution.md"],
            emit=lambda *args, **kwargs: None,
        )


@pytest.mark.asyncio(loop_scope="function")
async def test_invoke_agent_retries_no_response_with_fallback_model() -> None:
    runner = AgentRunner.__new__(AgentRunner)
    runner.registry = SimpleNamespace(
        load_profile=lambda name: _specialist_profile("page_builder", fallback_models=["fallback-model"]),
        root=Path("/tmp/agents"),
    )
    runner.toolbox = SimpleNamespace(
        set_path_constraints=lambda *args, **kwargs: None,
        clear_path_constraints=lambda *args, **kwargs: None,
    )

    models_tried: list[str] = []
    messages: list[str] = []

    def fake_artifact_issues(app_id: str, agent_name: str) -> list[str]:
        return ["artifacts incomplete"] if len(models_tried) < 4 else []

    runner._specialist_artifact_issues = fake_artifact_issues

    async def fake_run_loop(**kwargs):
        models_tried.append(kwargs["profile_override"].model_name)
        if len(models_tried) <= 3:
            return "No response generated."
        return "page artifacts created"

    async def fake_emit(event_type: str, payload: dict[str, str]) -> None:
        if event_type == "message":
            messages.append(payload.get("content", ""))

    runner._run_loop = fake_run_loop

    result = await AgentRunner.invoke_agent(
        runner,
        app_id="app_test",
        name="page_builder",
        instructions="Build pages.",
        context_paths=["solution.md", "objects", "pages"],
        emit=fake_emit,
    )

    assert result == "page artifacts created"
    assert models_tried == [
        "gemini-3-pro-preview",
        "gemini-3-pro-preview",
        "gemini-3-pro-preview",
        "fallback-model",
    ]
    assert any("Retrying the same model (2/3)." in message for message in messages)
    assert any("Retrying with fallback model fallback-model." in message for message in messages)


@pytest.mark.asyncio(loop_scope="function")
async def test_invoke_agent_retries_same_model_before_failing() -> None:
    runner = AgentRunner.__new__(AgentRunner)
    runner.registry = SimpleNamespace(
        load_profile=lambda name: _specialist_profile("object_builder"),
        root=Path("/tmp/agents"),
    )
    runner.toolbox = SimpleNamespace(
        set_path_constraints=lambda *args, **kwargs: None,
        clear_path_constraints=lambda *args, **kwargs: None,
    )
    runner._specialist_artifact_issues = lambda app_id, agent_name: ["artifacts incomplete"]

    attempts: list[str] = []

    async def fake_run_loop(**kwargs):
        attempts.append(kwargs["profile_override"].model_name)
        return "No response generated."

    runner._run_loop = fake_run_loop
    
    async def fake_emit(*args, **kwargs):
        return None

    with pytest.raises(ValueError, match="after 3 attempts: gemini-3-pro-preview x3"):
        await AgentRunner.invoke_agent(
            runner,
            app_id="app_test",
            name="object_builder",
            instructions="Build objects.",
            context_paths=["solution.md", "objects"],
            emit=fake_emit,
        )

    assert attempts == [
        "gemini-3-pro-preview",
        "gemini-3-pro-preview",
        "gemini-3-pro-preview",
    ]


@pytest.mark.asyncio(loop_scope="function")
async def test_invoke_agent_accepts_owned_artifacts_after_no_response() -> None:
    runner = AgentRunner.__new__(AgentRunner)
    runner.registry = SimpleNamespace(
        load_profile=lambda name: _specialist_profile("object_builder"),
        root=Path("/tmp/agents"),
    )
    runner.toolbox = SimpleNamespace(
        set_path_constraints=lambda *args, **kwargs: None,
        clear_path_constraints=lambda *args, **kwargs: None,
    )
    runner._specialist_artifact_issues = lambda app_id, agent_name: []

    async def fake_run_loop(**kwargs):
        return "No response generated."

    messages: list[str] = []

    async def fake_emit(event_type: str, payload: dict[str, str]) -> None:
        if event_type == "message":
            messages.append(payload.get("content", ""))

    runner._run_loop = fake_run_loop

    result = await AgentRunner.invoke_agent(
        runner,
        app_id="app_test",
        name="object_builder",
        instructions="Build objects.",
        context_paths=["solution.md", "objects"],
        emit=fake_emit,
    )

    assert result == "object_builder completed owned artifact generation."
    assert any("produced owned artifacts" in message for message in messages)


@pytest.mark.asyncio(loop_scope="function")
async def test_invoke_agent_rejects_bundle_only_object_artifacts() -> None:
    class BundleOnlyAgentFS:
        def list_files(self, app_id: str) -> list[str]:
            return ["solution.md", "objects/models.json"]

        def read_file(self, app_id: str, path: str, truncate: bool = False) -> str:
            if path == "solution.md":
                return "# solution"
            raise FileNotFoundError(path)

    runner = AgentRunner.__new__(AgentRunner)
    runner.agentfs = BundleOnlyAgentFS()
    runner.registry = SimpleNamespace(
        load_profile=lambda name: _specialist_profile("object_builder"),
        root=Path("/tmp/agents"),
    )
    runner.toolbox = SimpleNamespace(
        set_path_constraints=lambda *args, **kwargs: None,
        clear_path_constraints=lambda *args, **kwargs: None,
    )

    async def fake_run_loop(**kwargs):
        return "objects created"

    runner._run_loop = fake_run_loop

    with pytest.raises(ValueError, match="objects/models.json"):
        await AgentRunner.invoke_agent(
            runner,
            app_id="app_test",
            name="object_builder",
            instructions="Build objects.",
            context_paths=["solution.md", "objects"],
            emit=lambda *args, **kwargs: None,
        )


def test_validate_generated_app_flags_bundle_only_objects_and_missing_declared_pages(monkeypatch) -> None:
    monkeypatch.setattr(runner_module.shutil, "which", lambda name: None)

    class ValidationAgentFS:
        def __init__(self) -> None:
            self.files = {
                "solution.md": "\n".join(
                    [
                        "## Pages (`pages/`)",
                        "* **home.html**: Home",
                        "* **trips.html**: Trips",
                        "* **admin.html**: Admin",
                    ]
                ),
                "index.html": '<!DOCTYPE html><html><head><link rel="stylesheet" href="styles.css"></head><body><main id="app-root"></main><script src="app.js"></script></body></html>',
                "styles.css": ".home-shell { display: block; }",
                "app.js": 'if (document.readyState !== "loading") { window.__appReady = true; }',
                "pages/home.html": '<section class="home-shell">Home</section>',
                "objects/models.json": "{}",
            }

        def list_files(self, app_id: str) -> list[str]:
            return list(self.files)

        def read_file(self, app_id: str, relative_path: str, *args, **kwargs) -> str:
            if relative_path not in self.files:
                raise FileNotFoundError(relative_path)
            return self.files[relative_path]

        def load_json(self, app_id: str, relative_path: str):
            return json.loads(self.read_file(app_id, relative_path))

    runner = AgentRunner.__new__(AgentRunner)
    runner.agentfs = ValidationAgentFS()

    issues = AgentRunner._validate_generated_app(runner, "app_test")

    assert any("bundled objects/models.json" in issue for issue in issues)
    assert any("solution.md declares pages that were not generated" in issue for issue in issues)


@pytest.mark.asyncio(loop_scope="function")
async def test_toolbox_aligns_invoke_agent_timeout_with_subagent_budget() -> None:
    async def fake_task_runner(*args, **kwargs) -> str:
        await asyncio.sleep(0.1)
        return "done"

    toolbox = ToolBox(
        FakeAgentFS(),
        fake_task_runner,
        lambda *args, **kwargs: None,
        registry=SimpleNamespace(
            load_profile=lambda name: SimpleNamespace(
                execution=SimpleNamespace(timeout_ms=100),
                fallback_models=["fallback-model"],
                model_name="gemini-3-pro-preview",
            )
        ),
    )

    caller_profile = SimpleNamespace(
        name="orchestrator",
        tools=["invoke_agent"],
        get_tool_config=lambda tool_name: SimpleNamespace(timeout_ms=50),
    )

    async def fake_emit(*args, **kwargs):
        return None

    result = await toolbox.execute(
        "app_test",
        caller_profile,
        "orchestrator",
        "invoke_agent",
        {
            "agent": "page_builder",
            "instructions": "Build pages.",
            "context_paths": ["solution.md", "objects", "pages"],
        },
        fake_emit,
    )

    assert result == "done"


@pytest.mark.asyncio(loop_scope="function")
async def test_toolbox_disables_invoke_agent_timeout_when_subagent_timeout_is_null() -> None:
    async def fake_task_runner(*args, **kwargs) -> str:
        await asyncio.sleep(0.1)
        return "done"

    toolbox = ToolBox(
        FakeAgentFS(),
        fake_task_runner,
        lambda *args, **kwargs: None,
        registry=SimpleNamespace(
            load_profile=lambda name: SimpleNamespace(
                execution=SimpleNamespace(timeout_ms=None),
                fallback_models=[],
                model_name="gemini-3-pro-preview",
            )
        ),
    )

    caller_profile = SimpleNamespace(
        name="orchestrator",
        tools=["invoke_agent"],
        get_tool_config=lambda tool_name: SimpleNamespace(timeout_ms=50),
    )

    async def fake_emit(*args, **kwargs):
        return None

    result = await toolbox.execute(
        "app_test",
        caller_profile,
        "orchestrator",
        "invoke_agent",
        {
            "agent": "page_builder",
            "instructions": "Build pages.",
            "context_paths": ["solution.md", "objects", "pages"],
        },
        fake_emit,
    )

    assert result == "done"


@pytest.mark.asyncio(loop_scope="function")
async def test_run_loop_applies_actual_context_compression(monkeypatch) -> None:
    class FakeModel:
        instances: list["FakeModel"] = []

        def __init__(self, **kwargs) -> None:
            self.history = [{"role": "user", "parts": [{"text": f"msg-{index}"}]} for index in range(12)]
            self.compressed_history = None
            self.compression_context = None
            FakeModel.instances.append(self)

        def _history(self):
            return list(self.history)

        def compress_history(self, recent_history, compression_context):
            self.compressed_history = list(recent_history)
            self.compression_context = compression_context
            self.history = list(recent_history)

        def generate(self):
            return "done", None

    monkeypatch.setattr(runner_module, "GeminiModel", FakeModel)

    runner = AgentRunner.__new__(AgentRunner)
    runner.registry = SimpleNamespace(
        root=Path("/tmp/agents"),
        load_profile=lambda name: SimpleNamespace(
            name=name,
            role="orchestrator",
            model_name="fake-model",
            temperature=0.1,
            max_output_tokens=1024,
            top_p=0.9,
            top_k=40,
            thinking_budget=None,
            tools=[],
            skills=[],
            memory=SimpleNamespace(max_tokens=50000, max_messages=2, compression_threshold=0.8, compression_enabled=True),
            execution=SimpleNamespace(timeout_ms=120000, max_turns=3, max_parallel_tools=1),
        ),
        load_skill_bundle=lambda skills: "",
    )
    runner.agentfs = SimpleNamespace(list_files=lambda app_id: ["index.html", "styles.css", "app.js"])
    runner.config = SimpleNamespace(agent=SimpleNamespace(max_turns=3))
    runner.metrics = SimpleNamespace(
        record_agent_started=lambda *args, **kwargs: None,
        record_agent_finished=lambda *args, **kwargs: None,
    )
    runner._context_managers = {}
    runner._append_log = lambda *args, **kwargs: None

    async def fake_emit(*args, **kwargs):
        return None

    final_message = await AgentRunner._run_loop(
        runner,
        prompt="Add dark mode toggle.",
        app_id="app_test",
        agent_name="orchestrator",
        emit=fake_emit,
    )

    assert final_message == "done"
    assert FakeModel.instances
    assert FakeModel.instances[0].compressed_history is not None
    assert len(FakeModel.instances[0].compressed_history) == 10
    assert "Previous Context (Compressed)" in FakeModel.instances[0].compression_context


@pytest.mark.asyncio(loop_scope="function")
async def test_run_loop_streams_final_summary_when_hitting_max_turns(monkeypatch) -> None:
    class FakeModel:
        def __init__(self, **kwargs) -> None:
            self.history = []

        def _history(self):
            return list(self.history)

        def generate(self):
            return None, [{"tool": "read_file", "payload": {"path": "index.html"}}]

        def add_tool_outputs(self, outputs):
            return None

    monkeypatch.setattr(runner_module, "GeminiModel", FakeModel)

    runner = AgentRunner.__new__(AgentRunner)
    runner.registry = SimpleNamespace(
        root=Path("/tmp/agents"),
        load_profile=lambda name: SimpleNamespace(
            name=name,
            role="orchestrator",
            model_name="fake-model",
            temperature=0.1,
            max_output_tokens=1024,
            top_p=0.9,
            top_k=40,
            thinking_budget=None,
            tools=["read_file"],
            skills=[],
            memory=SimpleNamespace(max_tokens=50000, max_messages=30, compression_threshold=0.8, compression_enabled=False),
            execution=SimpleNamespace(timeout_ms=120000, max_turns=1, max_parallel_tools=1),
        ),
        load_skill_bundle=lambda skills: "",
    )
    runner.agentfs = SimpleNamespace(list_files=lambda app_id: ["index.html", "styles.css", "app.js"])
    async def fake_execute(*args, **kwargs):
        return "<html></html>"

    runner.toolbox = SimpleNamespace(execute=fake_execute)
    runner.config = SimpleNamespace(agent=SimpleNamespace(max_turns=1))
    runner.metrics = SimpleNamespace(
        record_agent_started=lambda *args, **kwargs: None,
        record_agent_finished=lambda *args, **kwargs: None,
    )
    runner._context_managers = {}
    runner._append_log = lambda *args, **kwargs: None

    events: list[tuple[str, dict]] = []

    async def fake_emit(event_type, payload):
        events.append((event_type, payload))

    final_message = await AgentRunner._run_loop(
        runner,
        prompt="Build something large.",
        app_id="app_test",
        agent_name="orchestrator",
        emit=fake_emit,
    )

    assert final_message == "orchestrator hit max_turns=1"
    assert any(
        event_type == "message" and payload.get("content") == "orchestrator hit max_turns=1"
        for event_type, payload in events
    )


# ---------------------------------------------------------------------------
# Helpers shared by the _run_loop edge-case tests below
# ---------------------------------------------------------------------------

def _make_run_loop_runner(monkeypatch, fake_model_cls, *, max_turns: int = 20, tools: list[str] | None = None, thinking_budget: int | None = None):
    """Build a minimal AgentRunner stub wired with fake_model_cls for _run_loop tests."""
    tools = tools or ["read_file", "grep", "glob"]
    monkeypatch.setattr(runner_module, "GeminiModel", fake_model_cls)

    runner = AgentRunner.__new__(AgentRunner)
    runner.registry = SimpleNamespace(
        root=Path("/tmp/agents"),
        load_profile=lambda name: SimpleNamespace(
            name=name,
            role="orchestrator",
            model_name="fake-model",
            temperature=0.1,
            max_output_tokens=1024,
            top_p=0.9,
            top_k=40,
            tools=tools,
            skills=[],
            thinking_budget=thinking_budget,
            memory=SimpleNamespace(max_tokens=50000, max_messages=30, compression_threshold=0.8, compression_enabled=False),
            execution=SimpleNamespace(timeout_ms=None, max_turns=max_turns, max_parallel_tools=3),
        ),
        load_skill_bundle=lambda skills: "",
    )
    runner.agentfs = SimpleNamespace(list_files=lambda app_id: ["index.html", "styles.css", "app.js"])
    runner.toolbox = SimpleNamespace(execute=AsyncMock(return_value="<html></html>"))
    runner.config = SimpleNamespace(agent=SimpleNamespace(max_turns=max_turns))
    runner.metrics = SimpleNamespace(
        record_agent_started=lambda *a, **kw: None,
        record_agent_finished=lambda *a, **kw: None,
    )
    runner._context_managers = {}
    runner._append_log = lambda *a, **kw: None
    return runner


class AsyncMock:
    """Minimal async callable stub."""
    def __init__(self, return_value=None):
        self._return_value = return_value

    async def __call__(self, *args, **kwargs):
        return self._return_value


# ---------------------------------------------------------------------------
# Empty response → must surface as "LLM error:"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="function")
async def test_run_loop_empty_response_treated_as_llm_error(monkeypatch) -> None:
    """(None, None) from generate() must produce a final_message starting with 'LLM error:'."""

    class EmptyResponseModel:
        def __init__(self, **kwargs):
            pass

        def _history(self):
            return []

        def generate(self):
            # Simulates unexpected (None, None) — e.g. an SDK edge case after our RuntimeError guards
            return None, None

        def add_tool_outputs(self, outputs):
            pass

    runner = _make_run_loop_runner(monkeypatch, EmptyResponseModel)

    events: list[tuple[str, dict]] = []

    async def fake_emit(event_type, payload):
        events.append((event_type, payload))

    final_message = await AgentRunner._run_loop(
        runner,
        prompt="Build something.",
        app_id="app_test",
        agent_name="orchestrator",
        emit=fake_emit,
    )

    assert final_message.startswith("LLM error:"), f"Expected 'LLM error:' prefix, got: {final_message!r}"
    # Must also be streamed so the UI shows it
    assert any(
        event_type == "message" and payload.get("content", "").startswith("LLM error:")
        for event_type, payload in events
    )


# ---------------------------------------------------------------------------
# Repair-loop stall → must surface as "LLM error:"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="function")
async def test_run_loop_repair_stall_treated_as_llm_error(monkeypatch) -> None:
    """Repair run with 7 consecutive read-only turns must produce 'LLM error:' and be streamed."""

    call_count = {"n": 0}

    class ReadOnlyModel:
        def __init__(self, **kwargs):
            pass

        def _history(self):
            return []

        def generate(self):
            call_count["n"] += 1
            return None, [{"tool": "read_file", "payload": {"path": f"file{call_count['n']}.js"}}]

        def add_tool_outputs(self, outputs):
            pass

    runner = _make_run_loop_runner(monkeypatch, ReadOnlyModel, max_turns=20, tools=["read_file", "grep", "glob"])

    events: list[tuple[str, dict]] = []

    async def fake_emit(event_type, payload):
        events.append((event_type, payload))

    # is_repair_run=True when prompt starts with "Repair the existing generated app"
    final_message = await AgentRunner._run_loop(
        runner,
        prompt="Repair the existing generated app: fix broken wiring.",
        app_id="app_test",
        agent_name="orchestrator",
        emit=fake_emit,
    )

    assert final_message.startswith("LLM error:"), f"Expected 'LLM error:' prefix, got: {final_message!r}"
    assert "stalled" in final_message.lower()
    assert any(
        event_type == "message" and "stalled" in payload.get("content", "").lower()
        for event_type, payload in events
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_run_loop_repair_stall_threshold_is_six_not_four(monkeypatch) -> None:
    """5 consecutive read-only turns in a repair run must NOT trigger the stall guard (threshold is 6)."""

    turns = {"n": 0}
    WRITE_ON_TURN = 6  # 5 read-only turns, then a write on turn 6 → no stall

    class MixedModel:
        def __init__(self, **kwargs):
            pass

        def _history(self):
            return []

        def generate(self):
            turns["n"] += 1
            if turns["n"] == WRITE_ON_TURN:
                return None, [{"tool": "write_file", "payload": {"path": "app.js", "content": "fixed"}}]
            if turns["n"] > WRITE_ON_TURN:
                return "Fixed.", None
            return None, [{"tool": "read_file", "payload": {"path": f"f{turns['n']}.js"}}]

        def add_tool_outputs(self, outputs):
            pass

    runner = _make_run_loop_runner(monkeypatch, MixedModel, max_turns=20, tools=["read_file", "write_file"])

    async def fake_emit(*a, **kw):
        pass

    final_message = await AgentRunner._run_loop(
        runner,
        prompt="Repair the existing generated app: fix broken wiring.",
        app_id="app_test",
        agent_name="orchestrator",
        emit=fake_emit,
    )

    # Should finish with the text response, not a stall error
    assert final_message == "Fixed.", f"Unexpected message: {final_message!r}"


@pytest.mark.asyncio(loop_scope="function")
async def test_run_loop_non_repair_read_only_never_stalls(monkeypatch) -> None:
    """The read-only stall guard must NOT fire for non-repair runs regardless of how many read turns happen."""

    turns = {"n": 0}

    class AlwaysReadModel:
        def __init__(self, **kwargs):
            pass

        def _history(self):
            return []

        def generate(self):
            turns["n"] += 1
            if turns["n"] >= 10:
                return "Done.", None
            return None, [{"tool": "read_file", "payload": {"path": f"f{turns['n']}.js"}}]

        def add_tool_outputs(self, outputs):
            pass

    runner = _make_run_loop_runner(monkeypatch, AlwaysReadModel, max_turns=15, tools=["read_file"])

    async def fake_emit(*a, **kw):
        pass

    final_message = await AgentRunner._run_loop(
        runner,
        prompt="Build a new app from scratch.",  # NOT a repair prompt
        app_id="app_test",
        agent_name="orchestrator",
        emit=fake_emit,
    )

    assert final_message == "Done.", f"Non-repair run stalled unexpectedly: {final_message!r}"


# ---------------------------------------------------------------------------
# thinking_budget wired through to LLMConfig
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="function")
async def test_run_loop_passes_thinking_budget_to_llm_config(monkeypatch) -> None:
    """thinking_budget from the agent profile must reach the LLMConfig that GeminiModel receives."""

    captured_configs: list = []

    class CapturingModel:
        def __init__(self, **kwargs):
            captured_configs.append(kwargs.get("config"))

        def _history(self):
            return []

        def generate(self):
            return "Done.", None

        def add_tool_outputs(self, outputs):
            pass

    runner = _make_run_loop_runner(monkeypatch, CapturingModel, thinking_budget=4000)

    async def fake_emit(*a, **kw):
        pass

    await AgentRunner._run_loop(
        runner,
        prompt="Build an app.",
        app_id="app_test",
        agent_name="orchestrator",
        emit=fake_emit,
    )

    assert captured_configs, "GeminiModel was never instantiated"
    assert captured_configs[0].thinking_budget == 4000


@pytest.mark.asyncio(loop_scope="function")
async def test_run_loop_thinking_budget_none_when_not_configured(monkeypatch) -> None:
    """When thinking_budget is None in the profile, LLMConfig.thinking_budget must also be None."""

    captured_configs: list = []

    class CapturingModel:
        def __init__(self, **kwargs):
            captured_configs.append(kwargs.get("config"))

        def _history(self):
            return []

        def generate(self):
            return "Done.", None

        def add_tool_outputs(self, outputs):
            pass

    runner = _make_run_loop_runner(monkeypatch, CapturingModel, thinking_budget=None)

    async def fake_emit(*a, **kw):
        pass

    await AgentRunner._run_loop(
        runner,
        prompt="Build an app.",
        app_id="app_test",
        agent_name="orchestrator",
        emit=fake_emit,
    )

    assert captured_configs[0].thinking_budget is None
