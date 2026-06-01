from __future__ import annotations

import pytest

from app.services.runner import AgentRunner


class FakeAgentFS:
    def __init__(self, files: list[str], solution: str = "", logs: list[dict] | None = None) -> None:
        self._files = files
        self._solution = solution
        self._logs = logs

    def list_files(self, app_id: str) -> list[str]:
        return list(self._files)

    def read_file(self, app_id: str, path: str, truncate: bool = False) -> str:
        if path == "solution.md":
            if not self._solution:
                raise FileNotFoundError(path)
            return self._solution
        raise FileNotFoundError(path)

    def load_json(self, app_id: str, path: str):
        if path == ".internal/logs.json":
            if self._logs is None:
                raise FileNotFoundError(path)
            return self._logs
        raise FileNotFoundError(path)


def build_runner(agentfs: FakeAgentFS) -> AgentRunner:
    runner = AgentRunner.__new__(AgentRunner)
    runner.agentfs = agentfs
    return runner


def test_is_complex_request_from_prompt_and_solution() -> None:
    runner = build_runner(
        FakeAgentFS(
            files=["solution.md"],
            solution="## Page Map\n- home\n- catalog\n- checkout\n## Integration Plan",
        )
    )

    assert runner._is_complex_request(
        "Build a multi-page e-commerce app with catalog, cart, checkout, account, and admin views.",
        "app_test",
    )


def test_is_complex_request_skips_complex_rebuild_for_existing_app_follow_up() -> None:
    runner = build_runner(
        FakeAgentFS(
            files=[
                "solution.md",
                "index.html",
                "styles.css",
                "app.js",
                "objects/Product.json",
                "pages/home.html",
            ],
            solution="## Page Map\n- home\n- catalog\n- checkout\n## Integration Plan",
        )
    )

    assert not runner._is_complex_request(
        "Add dark mode and a button to toggle between dark and light mode.",
        "app_test",
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_enforce_complex_workflow_invokes_page_builder_when_missing() -> None:
    runner = build_runner(
        FakeAgentFS(
            files=["solution.md", "objects/Product.json", "objects/User.json"],
            solution="## Page Map\n- home\n- catalog\n- checkout\n## Integration Plan",
            logs=[{"agent": "object_builder", "message": "done"}],
        )
    )

    calls: list[tuple[str, str, list[str]]] = []
    messages: list[str] = []

    async def fake_invoke_agent(
        app_id: str,
        name: str,
        instructions: str,
        context_paths: list[str],
        emit,
    ) -> str:
        calls.append((name, instructions, context_paths))
        return "page artifacts created"

    async def fake_emit(event_type: str, payload: dict[str, str]) -> None:
        if event_type == "message":
            messages.append(payload.get("content", ""))

    runner.invoke_agent = fake_invoke_agent

    notes = await runner._enforce_complex_workflow(
        prompt="Build a multi-page commerce app with cart, checkout, account, and admin pages.",
        app_id="app_test",
        emit=fake_emit,
    )

    assert calls
    assert calls[0][0] == "page_builder"
    assert calls[0][2] == ["solution.md", "objects", "pages"]
    assert any("Complex workflow recovery" in message for message in messages)
    assert notes == ["Workflow recovery executed: page artifacts created"]


@pytest.mark.asyncio(loop_scope="function")
async def test_enforce_complex_workflow_skips_when_page_builder_already_ran() -> None:
    runner = build_runner(
        FakeAgentFS(
            files=["solution.md", "objects/Product.json", "pages/home.html"],
            solution="## Page Map\n- home\n- catalog\n## Integration Plan",
            logs=[
                {"agent": "object_builder", "message": "done"},
                {"agent": "page_builder", "message": "done"},
            ],
        )
    )

    async def fail_invoke_agent(*args, **kwargs) -> str:
        raise AssertionError("page_builder should not be invoked")

    async def fake_emit(event_type: str, payload: dict[str, str]) -> None:
        return None

    runner.invoke_agent = fail_invoke_agent

    notes = await runner._enforce_complex_workflow(
        prompt="Build a multi-page commerce app with cart and checkout.",
        app_id="app_test",
        emit=fake_emit,
    )

    assert notes == []
