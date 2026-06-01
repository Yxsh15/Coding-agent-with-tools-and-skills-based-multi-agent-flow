from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from app.config import get_config, get_root_dir

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None


@dataclass
class ToolConfig:
    """Configuration for a single tool."""
    name: str
    timeout_ms: int | None = 30000
    max_retries: int | None = 1
    fallback: str | None = None


@dataclass
class ErrorHandlingConfig:
    """Configuration for error handling."""
    max_retries: int | None = 3
    retry_strategy: str = "exponential_backoff"
    backoff_multiplier: float = 2.0
    initial_delay_ms: int = 100
    max_delay_ms: int = 10000
    fallback_agent: str | None = None


@dataclass
class MemoryConfig:
    """Configuration for memory management."""
    max_messages: int = 20
    max_tokens: int = 32000
    compression_enabled: bool = True
    compression_threshold: float = 0.8


@dataclass
class ExecutionConfig:
    """Configuration for execution constraints."""
    max_turns: int = 15
    timeout_ms: int | None = 120000
    max_parallel_tools: int = 3


@dataclass
class AgentProfile:
    name: str
    role: str
    model_provider: str
    model_name: str
    temperature: float
    max_output_tokens: int
    top_p: float
    top_k: int
    tools: list[str]
    skills: list[str]
    thinking_budget: int | None = None
    # Enhanced config fields
    tool_configs: dict[str, ToolConfig] = field(default_factory=dict)
    fallback_models: list[str] = field(default_factory=list)
    error_handling: ErrorHandlingConfig = field(default_factory=ErrorHandlingConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    
    def get_tool_config(self, tool_name: str) -> ToolConfig:
        """Get configuration for a specific tool."""
        return self.tool_configs.get(tool_name, ToolConfig(name=tool_name))


def _parse_minimal_yaml(text: str) -> dict[str, Any]:
    from app.config import _parse_minimal_yaml as parse_yaml

    return parse_yaml(text)


def _parse_optional_int(value: Any, default: int) -> int | None:
    if value is None:
        return None
    return int(value) if value != "" else default


def _parse_tools(tools_data: list[Any]) -> tuple[list[str], dict[str, ToolConfig]]:
    """Parse tools configuration, supporting both simple and enhanced formats."""
    tool_names: list[str] = []
    tool_configs: dict[str, ToolConfig] = {}
    
    for tool in tools_data:
        if isinstance(tool, str):
            # Simple format: just tool name
            tool_names.append(tool)
        elif isinstance(tool, dict):
            # Enhanced format: tool with config
            name = tool.get("name", "")
            if name:
                tool_names.append(name)
                tool_configs[name] = ToolConfig(
                    name=name,
                    timeout_ms=_parse_optional_int(tool["timeout_ms"], 30000) if "timeout_ms" in tool else 30000,
                    max_retries=_parse_optional_int(tool["max_retries"], 1) if "max_retries" in tool else 1,
                    fallback=tool.get("fallback"),
                )
    
    return tool_names, tool_configs


def _parse_error_handling(data: dict[str, Any] | None) -> ErrorHandlingConfig:
    """Parse error handling configuration."""
    if not data:
        return ErrorHandlingConfig()
    
    return ErrorHandlingConfig(
        max_retries=_parse_optional_int(data["max_retries"], 3) if "max_retries" in data else 3,
        retry_strategy=str(data.get("retry_strategy", "exponential_backoff")),
        backoff_multiplier=float(data.get("backoff_multiplier", 2.0)),
        initial_delay_ms=int(data.get("initial_delay_ms", 100)),
        max_delay_ms=int(data.get("max_delay_ms", 10000)),
        fallback_agent=data.get("fallback_agent"),
    )


def _parse_memory(data: dict[str, Any] | None) -> MemoryConfig:
    """Parse memory configuration."""
    if not data:
        return MemoryConfig()
    
    short_term = data.get("short_term", {})
    compression = data.get("context_compression", {})
    
    return MemoryConfig(
        max_messages=int(short_term.get("max_messages", 20)),
        max_tokens=int(short_term.get("max_tokens", 32000)),
        compression_enabled=bool(compression.get("enabled", True)),
        compression_threshold=float(compression.get("threshold", 0.8)),
    )


def _parse_execution(data: dict[str, Any] | None) -> ExecutionConfig:
    """Parse execution configuration."""
    if not data:
        return ExecutionConfig()
    
    return ExecutionConfig(
        max_turns=int(data.get("max_turns", 15)),
        timeout_ms=_parse_optional_int(data["timeout_ms"], 120000) if "timeout_ms" in data else 120000,
        max_parallel_tools=int(data.get("max_parallel_tools", 3)),
    )


class AgentRegistry:
    def __init__(self) -> None:
        self.config = get_config()
        self.root = (get_root_dir() / self.config.agent.agents_root).resolve()
        self.skill_root = (get_root_dir() / "skills").resolve()

    def load_profile(self, name: str) -> AgentProfile:
        config_path = self.root / name / "config.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"Missing agent config: {config_path}")
        text = config_path.read_text()
        data = yaml.safe_load(text) if yaml is not None else _parse_minimal_yaml(text)
        model_name = os.environ.get("GEMINI_MODEL") or data["model"]["name"]
        
        # Parse tools (support both simple and enhanced format)
        tools_data = data.get("tools", [])
        tool_names, tool_configs = _parse_tools(tools_data)
        
        # Parse enhanced configurations
        error_handling = _parse_error_handling(data.get("error_handling"))
        memory = _parse_memory(data.get("memory"))
        execution = _parse_execution(data.get("execution"))
        fallback_models = data.get("model", {}).get("fallback_models", [])
        
        raw_thinking_budget = data["model"].get("thinking_budget")
        thinking_budget = int(raw_thinking_budget) if raw_thinking_budget is not None else None

        return AgentProfile(
            name=data["name"],
            role=data["role"],
            model_provider=data["model"]["provider"],
            model_name=model_name,
            temperature=float(data["model"]["temperature"]),
            max_output_tokens=int(data["model"].get("max_output_tokens", 8192)),
            top_p=float(data["model"].get("top_p", 0.95)),
            top_k=int(data["model"].get("top_k", 40)),
            tools=tool_names,
            skills=list(data.get("skills", [])),
            thinking_budget=thinking_budget,
            tool_configs=tool_configs,
            fallback_models=fallback_models,
            error_handling=error_handling,
            memory=memory,
            execution=execution,
        )

    def load_skill_bundle(self, skill_names: list[str]) -> str:
        parts: list[str] = []
        for skill_name in skill_names:
            skill_path = self.skill_root / f"{skill_name}.md"
            if skill_path.exists():
                parts.append(skill_path.read_text())
        return "\n\n".join(parts)
