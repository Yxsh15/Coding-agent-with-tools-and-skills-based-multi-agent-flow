from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from dataclasses import dataclass
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - fallback for zero-install exploration
    yaml = None


ROOT_DIR = Path(__file__).resolve().parents[2]


@dataclass
class AgentConfig:
    name: str
    max_turns: int
    app_root: str
    agents_root: str


@dataclass
class ModelConfig:
    provider: str
    name: str
    temperature: float
    max_output_tokens: int = 8192
    top_p: float = 0.95
    top_k: int = 40


@dataclass
class UIConfig:
    stream_keepalive_ms: int = 400


@dataclass
class AgentFSConfig:
    root: str
    max_read_chars: int = 6000


@dataclass
class AppConfig:
    agent: AgentConfig
    model: ModelConfig
    tools: list[str]
    ui: UIConfig
    agentfs: AgentFSConfig


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    config_path = ROOT_DIR / "config.yaml"
    text = config_path.read_text()
    data: dict[str, Any]
    if yaml is not None:
        data = yaml.safe_load(text)
    else:
        data = _parse_minimal_yaml(text)
    return AppConfig(
        agent=AgentConfig(**data["agent"]),
        model=ModelConfig(**data["model"]),
        tools=list(data["tools"]),
        ui=UIConfig(**data["ui"]),
        agentfs=AgentFSConfig(**data["agentfs"]),
    )


def get_root_dir() -> Path:
    return ROOT_DIR


def _parse_minimal_yaml(text: str) -> dict[str, Any]:
    lines = [line.rstrip("\n") for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]

    def parse_scalar(value: str) -> Any:
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            return value[1:-1]
        if value.startswith("'") and value.endswith("'"):
            return value[1:-1]
        if value in {"true", "false"}:
            return value == "true"
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            return value

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        container: dict[str, Any] | list[Any] | None = None
        while index < len(lines):
            line = lines[index]
            current_indent = len(line) - len(line.lstrip(" "))
            if current_indent < indent:
                break
            if current_indent > indent:
                raise ValueError(f"Invalid indentation in config.yaml near: {line}")

            stripped = line.strip()
            if stripped.startswith("- "):
                if container is None:
                    container = []
                if not isinstance(container, list):
                    raise ValueError("Mixed YAML structures are not supported")
                item_text = stripped[2:]
                container.append(parse_scalar(item_text))
                index += 1
                continue

            key, _, value = stripped.partition(":")
            if container is None:
                container = {}
            if not isinstance(container, dict):
                raise ValueError("Mixed YAML structures are not supported")

            if value.strip():
                container[key] = parse_scalar(value.strip())
                index += 1
                continue

            index += 1
            nested, index = parse_block(index, indent + 2)
            container[key] = nested

        return container or {}, index

    parsed, _ = parse_block(0, 0)
    if not isinstance(parsed, dict):
        raise ValueError("Top-level YAML must be a mapping")
    return parsed
