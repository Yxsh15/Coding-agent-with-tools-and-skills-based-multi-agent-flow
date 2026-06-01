"""
Context Window Management for handling token limits and history compression.
Phase 1.2: Context Window Management
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("app.services.context")


def _message_parts(message: Any) -> list[Any]:
    if isinstance(message, dict):
        return list(message.get("parts", []))
    return list(getattr(message, "parts", []) or [])


def _part_has_function_call(part: Any) -> bool:
    if isinstance(part, dict):
        return "function_call" in part and part.get("function_call") is not None
    return getattr(part, "function_call", None) is not None


def _part_has_function_response(part: Any) -> bool:
    if isinstance(part, dict):
        return "function_response" in part and part.get("function_response") is not None
    return getattr(part, "function_response", None) is not None


def _message_has_function_call(message: Any) -> bool:
    return any(_part_has_function_call(part) for part in _message_parts(message))


def _message_has_function_response(message: Any) -> bool:
    return any(_part_has_function_response(part) for part in _message_parts(message))


@dataclass
class ContextConfig:
    """Configuration for context window management."""
    max_tokens: int = 100_000
    compression_threshold: float = 0.8  # Compress when 80% full
    max_messages: int = 50
    keep_recent_messages: int = 10
    preserve_system_prompt: bool = True
    

@dataclass
class ContextStats:
    """Statistics about the current context."""
    estimated_tokens: int = 0
    message_count: int = 0
    compressed_count: int = 0
    is_compressed: bool = False


class ContextWindowManager:
    """
    Manages the context window for LLM conversations.
    Handles token counting, history compression, and context windowing.
    """
    
    # Rough estimate: ~4 characters per token for English text
    CHARS_PER_TOKEN = 4
    
    def __init__(self, config: ContextConfig | None = None):
        self.config = config or ContextConfig()
        self._compression_summaries: list[dict[str, Any]] = []
        
    def estimate_tokens(self, content: str | dict | list) -> int:
        """Estimate token count from content."""
        if isinstance(content, str):
            return len(content) // self.CHARS_PER_TOKEN
        elif isinstance(content, (dict, list)):
            return len(json.dumps(content, default=str)) // self.CHARS_PER_TOKEN
        return 0
    
    def estimate_message_tokens(self, message: dict[str, Any]) -> int:
        """Estimate tokens for a single message."""
        tokens = 0
        
        # Role overhead
        tokens += 4
        
        # Content - either string or parts
        content = message.get("content")
        if content:
            tokens += self.estimate_tokens(content)
        
        parts = message.get("parts", [])
        for part in parts:
            if isinstance(part, dict):
                if "text" in part:
                    tokens += self.estimate_tokens(part["text"])
                elif "function_call" in part:
                    tokens += self.estimate_tokens(part["function_call"])
                elif "function_response" in part:
                    tokens += self.estimate_tokens(part["function_response"])
                else:
                    tokens += self.estimate_tokens(part)
            else:
                # Handle Gemini Part objects
                text = getattr(part, "text", None)
                if text:
                    tokens += self.estimate_tokens(text)
                fc = getattr(part, "function_call", None)
                if fc:
                    tokens += 50  # Overhead for function call structure
                    args = getattr(fc, "args", {})
                    if args:
                        tokens += self.estimate_tokens(dict(args))
                fr = getattr(part, "function_response", None)
                if fr:
                    tokens += 50  # Overhead
                    response = getattr(fr, "response", {})
                    if response:
                        tokens += self.estimate_tokens(dict(response))
        
        return tokens
    
    def estimate_history_tokens(self, history: list[Any]) -> int:
        """Estimate total tokens in message history."""
        total = 0
        for message in history:
            if isinstance(message, dict):
                total += self.estimate_message_tokens(message)
            else:
                # Gemini Content object
                content_dict = {
                    "role": getattr(message, "role", "user"),
                    "parts": list(getattr(message, "parts", [])),
                }
                total += self.estimate_message_tokens(content_dict)
        return total
    
    def should_compress(self, history: list[Any]) -> bool:
        """Check if history should be compressed."""
        tokens = self.estimate_history_tokens(history)
        threshold = self.config.max_tokens * self.config.compression_threshold
        
        if tokens >= threshold:
            logger.info(
                "Context compression needed: %d tokens >= %.0f threshold",
                tokens,
                threshold,
            )
            return True
        
        if len(history) > self.config.max_messages:
            logger.info(
                "Context compression needed: %d messages > %d max",
                len(history),
                self.config.max_messages,
            )
            return True
        
        return False
    
    def get_stats(self, history: list[Any]) -> ContextStats:
        """Get statistics about the current context."""
        return ContextStats(
            estimated_tokens=self.estimate_history_tokens(history),
            message_count=len(history),
            compressed_count=len(self._compression_summaries),
            is_compressed=len(self._compression_summaries) > 0,
        )
    
    def extract_key_artifacts(self, history: list[Any]) -> list[str]:
        """Extract key artifacts mentioned in history."""
        artifacts = set()
        
        for message in history:
            parts = []
            if isinstance(message, dict):
                parts = message.get("parts", [])
            else:
                parts = list(getattr(message, "parts", []))
            
            for part in parts:
                # Check function calls for file operations
                fc = getattr(part, "function_call", None) if not isinstance(part, dict) else part.get("function_call")
                if fc:
                    if isinstance(fc, dict):
                        name = fc.get("name", "")
                        args = fc.get("args", {})
                    else:
                        name = getattr(fc, "name", "")
                        args = dict(getattr(fc, "args", {})) if getattr(fc, "args", None) else {}
                    
                    if name in ("write_file", "apply_diff", "read_file"):
                        path = args.get("path", "")
                        if path:
                            artifacts.add(path)
        
        return sorted(artifacts)
    
    def extract_key_decisions(self, history: list[Any]) -> list[str]:
        """Extract key decisions from history messages."""
        decisions = []
        
        for message in history:
            role = message.get("role") if isinstance(message, dict) else getattr(message, "role", None)
            
            if role != "model":
                continue
            
            parts = message.get("parts", []) if isinstance(message, dict) else list(getattr(message, "parts", []))
            
            for part in parts:
                text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
                if text:
                    # Extract decision-like statements
                    for line in text.split("\n"):
                        line = line.strip()
                        if any(
                            indicator in line.lower()
                            for indicator in ("decided", "choosing", "will", "created", "updated", "fixed")
                        ):
                            if len(line) < 200:
                                decisions.append(line)
        
        # Keep only most recent decisions
        return decisions[-10:]
    
    def create_compression_summary(self, history: list[Any], cutoff: int) -> dict[str, Any]:
        """Create a summary of compressed turns."""
        compressed_turns = history[:cutoff]
        
        summary = {
            "type": "context_compression_summary",
            "turns_compressed": len(compressed_turns),
            "artifacts_created": self.extract_key_artifacts(compressed_turns),
            "key_decisions": self.extract_key_decisions(compressed_turns),
            "timestamp": "auto",
        }
        
        return summary
    
    def compress_history(self, history: list[Any]) -> list[Any]:
        """
        Compress the history to fit within token limits.
        Returns a new history with old turns summarized.
        """
        if len(history) <= self.config.keep_recent_messages:
            return history
        
        # Determine cutoff point
        cutoff = len(history) - self.config.keep_recent_messages
        while cutoff > 0 and _message_has_function_response(history[cutoff]):
            if _message_has_function_call(history[cutoff - 1]):
                cutoff -= 1
                continue
            logger.warning(
                "Dropping orphaned function response at compression boundary cutoff=%s history_len=%s",
                cutoff,
                len(history),
            )
            cutoff += 1
            if cutoff >= len(history):
                return history[-self.config.keep_recent_messages :]
        old_turns = history[:cutoff]
        recent_turns = history[cutoff:]
        
        # Create summary
        summary = self.create_compression_summary(history, cutoff)
        self._compression_summaries.append(summary)
        
        logger.info(
            "Compressed %d turns, keeping %d recent. Artifacts: %s",
            len(old_turns),
            len(recent_turns),
            summary["artifacts_created"],
        )
        
        # Return recent turns only (summary is stored separately)
        return recent_turns
    
    def get_compression_context(self) -> str:
        """Get context from compression summaries for inclusion in prompts."""
        if not self._compression_summaries:
            return ""
        
        context_parts = ["## Previous Context (Compressed)"]
        
        for i, summary in enumerate(self._compression_summaries):
            context_parts.append(f"\n### Compression {i + 1}")
            context_parts.append(f"Turns compressed: {summary['turns_compressed']}")
            
            if summary["artifacts_created"]:
                context_parts.append(f"Files created/modified: {', '.join(summary['artifacts_created'])}")
            
            if summary["key_decisions"]:
                context_parts.append("Key decisions:")
                for decision in summary["key_decisions"][:5]:
                    context_parts.append(f"  - {decision}")
        
        return "\n".join(context_parts)
    
    def truncate_tool_output(self, output: Any, max_tokens: int = 5000) -> Any:
        """Truncate tool output to fit within token budget."""
        if isinstance(output, str):
            max_chars = max_tokens * self.CHARS_PER_TOKEN
            if len(output) > max_chars:
                truncated = output[:max_chars]
                return f"{truncated}\n\n[... truncated {len(output) - max_chars} characters ...]"
            return output
        
        elif isinstance(output, list):
            # For lists, truncate items
            serialized = json.dumps(output, default=str)
            if self.estimate_tokens(serialized) > max_tokens:
                # Keep first 10 items and last 3
                if len(output) > 13:
                    truncated = output[:10] + [{"_truncated": f"... {len(output) - 13} items omitted ..."}] + output[-3:]
                    return truncated
            return output
        
        elif isinstance(output, dict):
            serialized = json.dumps(output, default=str)
            if self.estimate_tokens(serialized) > max_tokens:
                # Try to truncate string values
                truncated = {}
                for key, value in output.items():
                    if isinstance(value, str) and len(value) > 1000:
                        truncated[key] = value[:1000] + f"... [truncated {len(value) - 1000} chars]"
                    else:
                        truncated[key] = value
                return truncated
            return output
        
        return output
    
    def reset(self) -> None:
        """Reset the context manager state."""
        self._compression_summaries.clear()


# Global instance for convenience
_default_context_manager: ContextWindowManager | None = None


def get_context_manager() -> ContextWindowManager:
    """Get or create the default context manager."""
    global _default_context_manager
    if _default_context_manager is None:
        _default_context_manager = ContextWindowManager()
    return _default_context_manager
