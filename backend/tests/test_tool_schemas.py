"""
Tests for tool schema validation.
Phase 5: Testing
"""
import pytest
from app.services.tool_schemas import (
    ToolSchemaValidator,
    ToolConstraints,
    validate_tool_input,
    validate_tool_output,
    TOOL_SCHEMAS,
)


class TestToolInputValidation:
    """Tests for tool input validation."""
    
    def test_read_file_valid(self):
        errors = validate_tool_input("read_file", {"path": "test.js"})
        assert errors == []
    
    def test_read_file_missing_path(self):
        errors = validate_tool_input("read_file", {})
        assert "Missing required field: path" in errors
    
    def test_read_file_invalid_path_traversal(self):
        validator = ToolSchemaValidator()
        errors = validator.validate_input("read_file", {"path": "../../../etc/passwd"})
        assert any("traversal" in e.lower() for e in errors)
    
    def test_write_file_valid(self):
        errors = validate_tool_input("write_file", {
            "path": "test.js",
            "content": "console.log('hello');"
        })
        assert errors == []
    
    def test_write_file_missing_content(self):
        errors = validate_tool_input("write_file", {"path": "test.js"})
        assert "Missing required field: content" in errors
    
    def test_bash_valid(self):
        errors = validate_tool_input("bash", {"command": "ls -la"})
        assert errors == []
    
    def test_invoke_agent_valid(self):
        errors = validate_tool_input("invoke_agent", {
            "agent": "object_builder",
            "instructions": "Create user object"
        })
        assert errors == []
    
    def test_invoke_agent_invalid_agent(self):
        validator = ToolSchemaValidator()
        errors = validator.validate_input("invoke_agent", {
            "agent": "unknown_agent",
            "instructions": "Do something"
        })
        assert any("not in allowed list" in e for e in errors)


class TestToolOutputValidation:
    """Tests for tool output validation."""
    
    def test_read_file_output_valid(self):
        errors = validate_tool_output("read_file", "file contents here")
        assert errors == []
    
    def test_read_file_output_invalid(self):
        errors = validate_tool_output("read_file", {"content": "wrong type"})
        assert len(errors) > 0
    
    def test_glob_output_valid(self):
        errors = validate_tool_output("glob", ["file1.js", "file2.js"])
        assert errors == []
    
    def test_bash_output_valid(self):
        errors = validate_tool_output("bash", {
            "exit_code": 0,
            "stdout": "output",
            "stderr": ""
        })
        assert errors == []


class TestToolConstraints:
    """Tests for tool constraints."""
    
    def test_default_constraints(self):
        constraints = ToolConstraints()
        assert constraints.max_file_size_bytes == 1_000_000
        assert constraints.timeout_ms == 30000
        assert ".json" in constraints.allowed_extensions
    
    def test_file_size_validation(self):
        validator = ToolSchemaValidator(ToolConstraints(max_file_size_bytes=100))
        
        # Content within limit
        errors = validator.validate_input("write_file", {
            "path": "test.txt",
            "content": "x" * 50
        })
        assert not any("size" in e.lower() for e in errors)
        
        # Content exceeds limit
        errors = validator.validate_input("write_file", {
            "path": "test.txt",
            "content": "x" * 200
        })
        assert any("size" in e.lower() for e in errors)


class TestBashValidation:
    """Tests for bash command validation."""
    
    def test_dangerous_patterns_blocked(self):
        validator = ToolSchemaValidator()
        
        dangerous_commands = [
            "find . -exec rm {} \\;",  # -exec pattern
            "python -c 'import os'",     # inline python
            "echo $(whoami)",            # command substitution
            "cat file | sh",             # pipe to shell
        ]
        
        for cmd in dangerous_commands:
            errors = validator.validate_input("bash", {"command": cmd})
            assert len(errors) > 0, f"Expected {cmd} to be blocked"


class TestSchemaCompleteness:
    """Tests for schema completeness."""
    
    def test_all_tools_have_schemas(self):
        expected_tools = [
            "read_file", "write_file", "apply_diff", "grep", "glob",
            "bash", "todos", "web_search", "web_fetch", "invoke_agent"
        ]
        
        for tool in expected_tools:
            assert tool in TOOL_SCHEMAS, f"Missing schema for {tool}"
    
    def test_schemas_have_required_properties(self):
        for tool_name, schema in TOOL_SCHEMAS.items():
            assert "type" in schema
            assert "properties" in schema
            assert "required" in schema
