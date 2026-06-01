"""
Tool Schema Validation for validating tool inputs and outputs.
Phase 1.3: Tool Schema Validation
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("app.services.tool_schemas")


@dataclass
class ToolConstraints:
    """Constraints for tool execution."""
    max_file_size_bytes: int = 1_000_000  # 1MB
    max_output_tokens: int = 5000
    timeout_ms: int = 30000
    max_retries: int = 2
    allowed_extensions: list[str] = field(default_factory=lambda: [
        ".html", ".css", ".js", ".json", ".md", ".txt", ".yaml", ".yml"
    ])


# JSON Schema definitions for each tool
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_file": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file to read",
                "pattern": r"^[a-zA-Z0-9_./-]+$",
                "maxLength": 256,
            },
            "start": {
                "type": "integer",
                "description": "Start line (optional, 1-indexed)",
                "minimum": 1,
            },
            "end": {
                "type": "integer",
                "description": "End line (optional, 1-indexed)",
                "minimum": 1,
            },
            "summary": {
                "type": "boolean",
                "description": "Return summary instead of full content",
                "default": False,
            },
        },
        "required": ["path"],
    },
    
    "write_file": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file",
                "pattern": r"^[a-zA-Z0-9_./-]+$",
                "maxLength": 256,
            },
            "content": {
                "type": "string",
                "description": "Content to write to the file",
                "maxLength": 1_000_000,  # 1MB
            },
        },
        "required": ["path", "content"],
    },
    
    "apply_diff": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to patch",
                "pattern": r"^[a-zA-Z0-9_./-]+$",
                "maxLength": 256,
            },
            "diff": {
                "type": "string",
                "description": "Unified diff content with @@ hunk headers",
                "maxLength": 500_000,
            },
        },
        "required": ["path", "diff"],
    },
    
    "grep": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search pattern",
                "maxLength": 500,
            },
            "path": {
                "type": "string",
                "description": "Path to search in (optional)",
                "pattern": r"^[a-zA-Z0-9_./-]*$",
                "maxLength": 256,
            },
        },
        "required": ["query"],
    },
    
    "glob": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern (e.g., '**/*.json')",
                "maxLength": 256,
            },
            "path": {
                "type": "string",
                "description": "Base path (optional)",
                "pattern": r"^[a-zA-Z0-9_./-]*$",
                "maxLength": 256,
            },
        },
        "required": ["pattern"],
    },
    
    "bash": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Command to execute",
                "maxLength": 1000,
            },
        },
        "required": ["command"],
    },
    
    "todos": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: 'replace', 'mark_done', or 'mark_in_progress'",
                "enum": ["replace", "mark_done", "mark_in_progress"],
            },
            "items": {
                "type": "array",
                "description": "Todo items (for 'replace' action)",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "title": {"type": "string", "maxLength": 500},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "done"],
                        },
                    },
                    "required": ["id", "title", "status"],
                },
            },
            "id": {
                "type": "integer",
                "description": "Todo ID (for mark actions)",
            },
        },
        "required": ["action"],
    },
    
    "web_search": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
                "maxLength": 500,
            },
        },
        "required": ["query"],
    },
    
    "web_fetch": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to fetch",
                "maxLength": 2048,
            },
        },
        "required": ["url"],
    },
    
    "invoke_agent": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "Name of the agent to invoke",
                "enum": ["object_builder", "page_builder", "validator"],
            },
            "instructions": {
                "type": "string",
                "description": "Instructions for the agent",
                "maxLength": 10000,
            },
            "context_paths": {
                "type": "array",
                "description": "Paths the agent should inspect",
                "items": {"type": "string", "maxLength": 256},
            },
        },
        "required": ["agent", "instructions"],
    },

    "validate_workspace": {
        "type": "object",
        "properties": {},
        "required": [],
    },

    "validate_syntax": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file to validate",
                "pattern": r"^[a-zA-Z0-9_./-]+$",
                "maxLength": 256,
            },
        },
        "required": ["path"],
    },
}


# Output schemas for validation
TOOL_OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_file": {"type": "string"},
    "write_file": {"type": "string"},  # Success message
    "apply_diff": {"type": "string"},  # Success message
    "grep": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "line": {"type": "integer"},
                "snippet": {"type": "string"},
            },
        },
    },
    "glob": {"type": "array", "items": {"type": "string"}},
    "bash": {
        "type": "object",
        "properties": {
            "exit_code": {"type": "integer"},
            "stdout": {"type": "string"},
            "stderr": {"type": "string"},
        },
    },
    "todos": {"type": "array"},
    "web_search": {"type": "array"},
    "web_fetch": {"type": "object"},
    "invoke_agent": {"type": "string"},
    "validate_workspace": {"type": "object"},
    "validate_syntax": {"type": "object"},
}


class ValidationError(Exception):
    """Raised when tool input or output validation fails."""
    
    def __init__(self, tool_name: str, message: str, details: dict[str, Any] | None = None):
        self.tool_name = tool_name
        self.message = message
        self.details = details or {}
        super().__init__(f"{tool_name}: {message}")


class ToolSchemaValidator:
    """Validates tool inputs and outputs against schemas."""
    
    def __init__(self, constraints: ToolConstraints | None = None):
        self.constraints = constraints or ToolConstraints()
        self._path_pattern = re.compile(r"^[a-zA-Z0-9_./-]+$")
    
    def validate_input(self, tool_name: str, payload: dict[str, Any]) -> list[str]:
        """
        Validate tool input against schema.
        Returns list of validation errors (empty if valid).
        """
        errors: list[str] = []
        schema = TOOL_SCHEMAS.get(tool_name)
        
        if not schema:
            logger.warning("No schema defined for tool: %s", tool_name)
            return errors
        
        # Check required fields
        required = schema.get("required", [])
        for field in required:
            if field not in payload:
                errors.append(f"Missing required field: {field}")
        
        # Validate each property
        properties = schema.get("properties", {})
        for field, value in payload.items():
            if field not in properties:
                continue  # Allow extra fields
            
            prop_schema = properties[field]
            field_errors = self._validate_property(field, value, prop_schema)
            errors.extend(field_errors)
        
        # Tool-specific validation
        tool_errors = self._validate_tool_specific(tool_name, payload)
        errors.extend(tool_errors)
        
        if errors:
            logger.warning(
                "Tool input validation failed tool=%s errors=%s",
                tool_name,
                errors,
            )
        
        return errors
    
    def _validate_property(self, field: str, value: Any, schema: dict[str, Any]) -> list[str]:
        """Validate a single property against its schema."""
        errors: list[str] = []
        expected_type = schema.get("type")
        
        # Type validation
        if expected_type == "string" and not isinstance(value, str):
            errors.append(f"{field} must be a string")
        elif expected_type == "integer" and not isinstance(value, int):
            errors.append(f"{field} must be an integer")
        elif expected_type == "boolean" and not isinstance(value, bool):
            errors.append(f"{field} must be a boolean")
        elif expected_type == "array" and not isinstance(value, list):
            errors.append(f"{field} must be an array")
        elif expected_type == "object" and not isinstance(value, dict):
            errors.append(f"{field} must be an object")
        
        # String validations
        if isinstance(value, str):
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                errors.append(f"{field} exceeds max length of {schema['maxLength']}")
            
            if "pattern" in schema:
                pattern = re.compile(schema["pattern"])
                if not pattern.match(value):
                    errors.append(f"{field} does not match required pattern")
            
            if "enum" in schema and value not in schema["enum"]:
                errors.append(f"{field} must be one of: {schema['enum']}")
        
        # Number validations
        if isinstance(value, (int, float)):
            if "minimum" in schema and value < schema["minimum"]:
                errors.append(f"{field} must be at least {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                errors.append(f"{field} must be at most {schema['maximum']}")
        
        return errors
    
    def _validate_tool_specific(self, tool_name: str, payload: dict[str, Any]) -> list[str]:
        """Tool-specific validation rules."""
        errors: list[str] = []
        
        if tool_name in ("read_file", "write_file", "apply_diff"):
            path = payload.get("path", "")
            
            # Check for path traversal
            if ".." in path:
                errors.append("Path traversal not allowed")
            
            # Check for absolute paths
            if path.startswith("/"):
                errors.append("Absolute paths not allowed")
            
            # Check extension for write operations
            if tool_name == "write_file":
                ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
                if ext and ext not in self.constraints.allowed_extensions:
                    errors.append(f"File extension {ext} not allowed")
                
                content = payload.get("content", "")
                if len(content.encode("utf-8")) > self.constraints.max_file_size_bytes:
                    errors.append(f"Content exceeds max size of {self.constraints.max_file_size_bytes} bytes")
        
        elif tool_name == "bash":
            command = payload.get("command", "")
            
            # Check for dangerous patterns
            dangerous_patterns = [
                r"rm\s+-rf",
                r"rm\s+-r\s+/",
                r">\s*/dev/",
                r"dd\s+",
                r"mkfs",
                r"chmod\s+777",
                r"wget\s+.*\|\s*sh",
                r"curl\s+.*\|\s*sh",
                r"-exec\s+",           # find -exec can run arbitrary commands
                r"python\s+-c",        # inline Python code execution
                r"\$\(",               # command substitution
                r"\|\s*sh\b",          # piping to shell
                r"\|\s*bash\b",        # piping to bash
                r"eval\s+",            # eval command
                r"source\s+",          # source command
            ]
            for pattern in dangerous_patterns:
                if re.search(pattern, command):
                    errors.append(f"Dangerous command pattern detected")
                    break
        
        elif tool_name == "invoke_agent":
            agent = payload.get("agent", "")
            allowed_agents = ["object_builder", "page_builder", "validator"]
            if agent not in allowed_agents:
                errors.append(f"Agent '{agent}' not in allowed list: {allowed_agents}")
        
        return errors
    
    def validate_output(self, tool_name: str, output: Any) -> list[str]:
        """
        Validate tool output against expected schema.
        Returns list of validation errors (empty if valid).
        """
        errors: list[str] = []
        schema = TOOL_OUTPUT_SCHEMAS.get(tool_name)
        
        if not schema:
            return errors
        
        expected_type = schema.get("type")
        
        if expected_type == "string" and not isinstance(output, str):
            errors.append(f"Expected string output, got {type(output).__name__}")
        elif expected_type == "array" and not isinstance(output, list):
            errors.append(f"Expected array output, got {type(output).__name__}")
        elif expected_type == "object" and not isinstance(output, dict):
            errors.append(f"Expected object output, got {type(output).__name__}")
        
        return errors
    
    def sanitize_input(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Sanitize tool input by applying constraints."""
        sanitized = dict(payload)
        schema = TOOL_SCHEMAS.get(tool_name, {})
        properties = schema.get("properties", {})
        
        for field, value in sanitized.items():
            if field not in properties:
                continue
            
            prop_schema = properties[field]
            
            # Truncate strings that exceed maxLength
            if isinstance(value, str) and "maxLength" in prop_schema:
                if len(value) > prop_schema["maxLength"]:
                    sanitized[field] = value[:prop_schema["maxLength"]]
                    logger.warning(
                        "Truncated %s.%s from %d to %d chars",
                        tool_name,
                        field,
                        len(value),
                        prop_schema["maxLength"],
                    )
        
        return sanitized


# Global validator instance
_validator: ToolSchemaValidator | None = None


def get_tool_validator() -> ToolSchemaValidator:
    """Get or create the global tool validator."""
    global _validator
    if _validator is None:
        _validator = ToolSchemaValidator()
    return _validator


def validate_tool_input(tool_name: str, payload: dict[str, Any]) -> list[str]:
    """Convenience function to validate tool input."""
    return get_tool_validator().validate_input(tool_name, payload)


def validate_tool_output(tool_name: str, output: Any) -> list[str]:
    """Convenience function to validate tool output."""
    return get_tool_validator().validate_output(tool_name, output)
