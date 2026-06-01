"""
Tests for agent registry with enhanced configuration.
Phase 5: Testing
"""
import pytest
import tempfile
import os
from pathlib import Path

from app.services.agent_registry import (
    AgentRegistry,
    AgentProfile,
    ToolConfig,
    ErrorHandlingConfig,
    MemoryConfig,
    ExecutionConfig,
    _parse_tools,
    _parse_error_handling,
    _parse_memory,
    _parse_execution,
)


class TestToolConfigParsing:
    """Tests for tool configuration parsing."""
    
    def test_parse_simple_tools(self):
        tools_data = ["read_file", "write_file", "grep"]
        tool_names, tool_configs = _parse_tools(tools_data)
        
        assert tool_names == ["read_file", "write_file", "grep"]
        assert tool_configs == {}
    
    def test_parse_enhanced_tools(self):
        tools_data = [
            {"name": "read_file", "timeout_ms": 5000, "max_retries": 2},
            {"name": "write_file", "timeout_ms": 10000, "fallback": "apply_diff"},
        ]
        
        tool_names, tool_configs = _parse_tools(tools_data)
        
        assert tool_names == ["read_file", "write_file"]
        assert "read_file" in tool_configs
        assert tool_configs["read_file"].timeout_ms == 5000
        assert tool_configs["read_file"].max_retries == 2
        assert tool_configs["write_file"].fallback == "apply_diff"
    
    def test_parse_mixed_tools(self):
        tools_data = [
            "grep",
            {"name": "read_file", "timeout_ms": 5000},
            "glob",
        ]
        
        tool_names, tool_configs = _parse_tools(tools_data)
        
        assert tool_names == ["grep", "read_file", "glob"]
        assert "read_file" in tool_configs
        assert "grep" not in tool_configs

    def test_parse_tools_allows_null_timeout_and_retries(self):
        tool_names, tool_configs = _parse_tools(
            [{"name": "read_file", "timeout_ms": None, "max_retries": None}]
        )

        assert tool_names == ["read_file"]
        assert tool_configs["read_file"].timeout_ms is None
        assert tool_configs["read_file"].max_retries is None


class TestErrorHandlingParsing:
    """Tests for error handling configuration parsing."""
    
    def test_parse_none(self):
        config = _parse_error_handling(None)
        
        assert isinstance(config, ErrorHandlingConfig)
        assert config.max_retries == 3
        assert config.retry_strategy == "exponential_backoff"
    
    def test_parse_full_config(self):
        data = {
            "max_retries": 5,
            "retry_strategy": "linear",
            "backoff_multiplier": 3.0,
            "initial_delay_ms": 200,
            "max_delay_ms": 20000,
            "fallback_agent": "repair_agent",
        }
        
        config = _parse_error_handling(data)
        
        assert config.max_retries == 5
        assert config.retry_strategy == "linear"
        assert config.backoff_multiplier == 3.0
        assert config.fallback_agent == "repair_agent"

    def test_parse_null_retries(self):
        config = _parse_error_handling({"max_retries": None})

        assert config.max_retries is None


class TestMemoryParsing:
    """Tests for memory configuration parsing."""
    
    def test_parse_none(self):
        config = _parse_memory(None)
        
        assert isinstance(config, MemoryConfig)
        assert config.max_messages == 20
        assert config.compression_enabled is True
    
    def test_parse_full_config(self):
        data = {
            "short_term": {
                "max_messages": 30,
                "max_tokens": 50000,
            },
            "context_compression": {
                "enabled": False,
                "threshold": 0.9,
            }
        }
        
        config = _parse_memory(data)
        
        assert config.max_messages == 30
        assert config.max_tokens == 50000
        assert config.compression_enabled is False
        assert config.compression_threshold == 0.9


class TestExecutionParsing:
    """Tests for execution configuration parsing."""
    
    def test_parse_none(self):
        config = _parse_execution(None)
        
        assert isinstance(config, ExecutionConfig)
        assert config.max_turns == 15
    
    def test_parse_full_config(self):
        data = {
            "max_turns": 20,
            "timeout_ms": 180000,
            "max_parallel_tools": 5,
        }
        
        config = _parse_execution(data)
        
        assert config.max_turns == 20
        assert config.timeout_ms == 180000
        assert config.max_parallel_tools == 5

    def test_parse_null_timeout(self):
        config = _parse_execution({"max_turns": 20, "timeout_ms": None, "max_parallel_tools": 5})

        assert config.max_turns == 20
        assert config.timeout_ms is None
        assert config.max_parallel_tools == 5


class TestAgentProfile:
    """Tests for AgentProfile."""
    
    def test_get_tool_config_exists(self):
        profile = AgentProfile(
            name="test",
            role="specialist",
            model_provider="google",
            model_name="gemini-pro",
            temperature=0.5,
            max_output_tokens=8192,
            top_p=0.95,
            top_k=40,
            tools=["read_file"],
            skills=["core"],
            tool_configs={
                "read_file": ToolConfig(name="read_file", timeout_ms=5000)
            },
        )
        
        config = profile.get_tool_config("read_file")
        assert config.timeout_ms == 5000
    
    def test_get_tool_config_default(self):
        profile = AgentProfile(
            name="test",
            role="specialist",
            model_provider="google",
            model_name="gemini-pro",
            temperature=0.5,
            max_output_tokens=8192,
            top_p=0.95,
            top_k=40,
            tools=["write_file"],
            skills=["core"],
        )
        
        config = profile.get_tool_config("write_file")
        assert config.name == "write_file"
        assert config.timeout_ms == 30000  # default


class TestAgentRegistryIntegration:
    """Integration tests for AgentRegistry."""
    
    def test_load_profile_with_enhanced_config(self, tmp_path):
        """Test loading a profile with enhanced configuration."""
        # Create a temporary config file
        config_yaml = """
name: test_agent
role: specialist
model:
  provider: google
  name: gemini-pro
  temperature: 0.5
  max_output_tokens: 8192
  top_p: 0.95
  top_k: 40
  fallback_models:
    - gemini-pro-1.5
tools:
  - name: read_file
    timeout_ms: 5000
  - write_file
skills:
  - core
error_handling:
  max_retries: 2
memory:
  short_term:
    max_messages: 25
execution:
  max_turns: 10
"""
        
        # Write to temp file
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        config_file = agent_dir / "config.yaml"
        config_file.write_text(config_yaml)
        
        # Note: This test requires mocking the registry's root path
        # For now, just verify the parsing functions work correctly
        
        from app.services.agent_registry import _parse_tools, _parse_error_handling
        
        import yaml
        data = yaml.safe_load(config_yaml)
        
        tool_names, tool_configs = _parse_tools(data["tools"])
        assert "read_file" in tool_names
        assert "write_file" in tool_names
        assert tool_configs["read_file"].timeout_ms == 5000
        
        error_config = _parse_error_handling(data["error_handling"])
        assert error_config.max_retries == 2

    def test_load_real_orchestrator_profile_uses_agent_config(self):
        registry = AgentRegistry()

        profile = registry.load_profile("orchestrator")

        assert profile.execution.max_turns == 40
        assert profile.execution.timeout_ms == 3000000  # 50 min — set to handle sequential sub-agent chains
        assert profile.execution.max_parallel_tools == 3
        assert profile.get_tool_config("invoke_agent").timeout_ms == 210000


class TestThinkingBudgetParsing:
    """Tests for thinking_budget field on AgentProfile."""

    def test_thinking_budget_parsed_when_present_in_yaml(self, tmp_path):
        config_yaml = """
name: test_agent
role: specialist
model:
  provider: google
  name: gemini-3-pro-preview
  temperature: 0.5
  max_output_tokens: 16384
  thinking_budget: 8000
  top_p: 0.95
  top_k: 40
  fallback_models: []
tools:
  - read_file
skills:
  - core
"""
        import yaml
        data = yaml.safe_load(config_yaml)
        raw = data["model"].get("thinking_budget")
        thinking_budget = int(raw) if raw is not None else None

        assert thinking_budget == 8000

    def test_thinking_budget_defaults_to_none_when_absent(self, tmp_path):
        config_yaml = """
name: test_agent
role: specialist
model:
  provider: google
  name: gemini-3-pro-preview
  temperature: 0.5
  max_output_tokens: 8192
  top_p: 0.95
  top_k: 40
  fallback_models: []
tools:
  - read_file
skills:
  - core
"""
        import yaml
        data = yaml.safe_load(config_yaml)
        raw = data["model"].get("thinking_budget")
        thinking_budget = int(raw) if raw is not None else None

        assert thinking_budget is None

    def test_load_real_agent_profiles_have_expected_thinking_budgets(self):
        """All four live agent configs must have thinking_budget set at the values we configured."""
        registry = AgentRegistry()

        orchestrator = registry.load_profile("orchestrator")
        validator = registry.load_profile("validator")
        object_builder = registry.load_profile("object_builder")
        page_builder = registry.load_profile("page_builder")

        assert orchestrator.thinking_budget == 4000
        assert validator.thinking_budget == 4000
        assert object_builder.thinking_budget == 8000
        assert page_builder.thinking_budget == 8000

    def test_load_real_specialist_profiles_have_correct_max_output_tokens(self):
        """Specialists must have max_output_tokens reverted to 16384 (not the broken 32768)."""
        registry = AgentRegistry()

        object_builder = registry.load_profile("object_builder")
        page_builder = registry.load_profile("page_builder")

        assert object_builder.max_output_tokens == 16384
        assert page_builder.max_output_tokens == 16384

    def test_thinking_budget_field_defaults_to_none_on_agent_profile(self):
        """AgentProfile.thinking_budget defaults to None when not supplied."""
        profile = AgentProfile(
            name="test",
            role="specialist",
            model_provider="google",
            model_name="gemini-pro",
            temperature=0.5,
            max_output_tokens=8192,
            top_p=0.95,
            top_k=40,
            tools=[],
            skills=[],
        )

        assert profile.thinking_budget is None
