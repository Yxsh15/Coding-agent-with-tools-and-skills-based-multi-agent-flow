"""
Resilience module: Error handling, retry policies, and circuit breakers.
Phase 1.1: Error Handling & Recovery
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar
from functools import wraps

logger = logging.getLogger("app.services.resilience")
T = TypeVar("T")


class ErrorCategory(Enum):
    """Classification of errors for retry decisions."""
    TRANSIENT = "transient"      # Network issues, timeouts - retry
    RATE_LIMIT = "rate_limit"    # Rate limiting - retry with backoff
    VALIDATION = "validation"    # Invalid input - don't retry
    PERMANENT = "permanent"      # API errors, auth failures - don't retry
    UNKNOWN = "unknown"          # Unknown error - conservative retry


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    max_retries: int = 3
    initial_delay_ms: int = 100
    max_delay_ms: int = 10000
    backoff_multiplier: float = 2.0
    jitter: bool = True
    
    def get_delay(self, attempt: int) -> float:
        """Calculate delay for a given attempt number."""
        delay_ms = min(
            self.initial_delay_ms * (self.backoff_multiplier ** attempt),
            self.max_delay_ms
        )
        if self.jitter:
            delay_ms = delay_ms * (0.5 + random.random())
        return delay_ms / 1000  # Return in seconds


@dataclass
class ErrorHandlingConfig:
    """Configuration for error handling."""
    max_retries: int = 3
    retry_strategy: str = "exponential_backoff"
    backoff_multiplier: float = 2.0
    initial_delay_ms: int = 100
    max_delay_ms: int = 10000
    fallback_agent: str | None = None
    
    def to_retry_config(self) -> RetryConfig:
        return RetryConfig(
            max_retries=self.max_retries,
            initial_delay_ms=self.initial_delay_ms,
            max_delay_ms=self.max_delay_ms,
            backoff_multiplier=self.backoff_multiplier,
            jitter=True,
        )


# Transient error indicators
TRANSIENT_ERROR_INDICATORS = {
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

TRANSIENT_MESSAGE_PATTERNS = (
    "connection reset by peer",
    "connection aborted",
    "connection refused",
    "connection terminated",
    "broken pipe",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "service unavailable",
    "too many requests",
    "rate limit",
)


def classify_error(exc: BaseException) -> ErrorCategory:
    """Classify an exception into an error category."""
    error_name = type(exc).__name__
    error_message = str(exc).lower()
    
    # Check for rate limiting
    if "rate limit" in error_message or "429" in error_message:
        return ErrorCategory.RATE_LIMIT
    
    # Check for transient errors by type
    if error_name in TRANSIENT_ERROR_INDICATORS:
        return ErrorCategory.TRANSIENT
    
    # Check for transient errors by message
    for pattern in TRANSIENT_MESSAGE_PATTERNS:
        if pattern in error_message:
            return ErrorCategory.TRANSIENT
    
    # Check for validation errors
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return ErrorCategory.VALIDATION
    
    # Check for auth/permission errors
    if "unauthorized" in error_message or "forbidden" in error_message or "401" in error_message or "403" in error_message:
        return ErrorCategory.PERMANENT
    
    return ErrorCategory.UNKNOWN


def should_retry(exc: BaseException, attempt: int, config: RetryConfig) -> bool:
    """Determine if an error should be retried."""
    if attempt >= config.max_retries:
        return False
    
    category = classify_error(exc)
    return category in (ErrorCategory.TRANSIENT, ErrorCategory.RATE_LIMIT, ErrorCategory.UNKNOWN)


class CircuitBreaker:
    """Simple circuit breaker pattern implementation."""
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_requests: int = 1,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_requests = half_open_requests
        
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._state = "closed"  # closed, open, half-open
        self._half_open_count = 0
        
    @property
    def state(self) -> str:
        return self._state
    
    def is_open(self) -> bool:
        """Check if circuit is open (blocking requests)."""
        if self._state == "open":
            if self._last_failure_time and time.time() - self._last_failure_time >= self.recovery_timeout:
                self._state = "half-open"
                self._half_open_count = 0
                return False
            return True
        return False
    
    def record_success(self) -> None:
        """Record a successful request."""
        if self._state == "half-open":
            self._half_open_count += 1
            if self._half_open_count >= self.half_open_requests:
                self._state = "closed"
                self._failure_count = 0
        elif self._state == "closed":
            self._failure_count = 0
    
    def record_failure(self) -> None:
        """Record a failed request."""
        self._failure_count += 1
        self._last_failure_time = time.time()
        
        if self._state == "half-open":
            self._state = "open"
        elif self._failure_count >= self.failure_threshold:
            self._state = "open"


class RetryExecutor:
    """Executes operations with retry logic."""
    
    def __init__(self, config: RetryConfig, circuit_breaker: CircuitBreaker | None = None):
        self.config = config
        self.circuit_breaker = circuit_breaker
    
    async def execute_async(
        self,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute an async function with retry logic."""
        if self.circuit_breaker and self.circuit_breaker.is_open():
            raise RuntimeError("Circuit breaker is open")
        
        last_error: BaseException | None = None
        
        for attempt in range(self.config.max_retries + 1):
            try:
                result = await func(*args, **kwargs)
                if self.circuit_breaker:
                    self.circuit_breaker.record_success()
                return result
            
            except Exception as exc:
                last_error = exc
                if self.circuit_breaker:
                    self.circuit_breaker.record_failure()
                
                if not should_retry(exc, attempt, self.config):
                    raise
                
                delay = self.config.get_delay(attempt)
                logger.warning(
                    "Retry attempt %d/%d after %.2fs: %s",
                    attempt + 1,
                    self.config.max_retries,
                    delay,
                    str(exc),
                )
                await asyncio.sleep(delay)
        
        if last_error:
            raise last_error
        raise RuntimeError("Retry executor completed without result or error")
    
    def execute_sync(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute a sync function with retry logic."""
        if self.circuit_breaker and self.circuit_breaker.is_open():
            raise RuntimeError("Circuit breaker is open")
        
        last_error: BaseException | None = None
        
        for attempt in range(self.config.max_retries + 1):
            try:
                result = func(*args, **kwargs)
                if self.circuit_breaker:
                    self.circuit_breaker.record_success()
                return result
            
            except Exception as exc:
                last_error = exc
                if self.circuit_breaker:
                    self.circuit_breaker.record_failure()
                
                if not should_retry(exc, attempt, self.config):
                    raise
                
                delay = self.config.get_delay(attempt)
                logger.warning(
                    "Retry attempt %d/%d after %.2fs: %s",
                    attempt + 1,
                    self.config.max_retries,
                    delay,
                    str(exc),
                )
                time.sleep(delay)
        
        if last_error:
            raise last_error
        raise RuntimeError("Retry executor completed without result or error")


def with_retry(config: RetryConfig | None = None):
    """Decorator for adding retry logic to async functions."""
    if config is None:
        config = RetryConfig()
    
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            executor = RetryExecutor(config)
            return await executor.execute_async(func, *args, **kwargs)
        return wrapper
    return decorator


# Tool-specific retry policies
TOOL_RETRY_POLICIES: dict[str, RetryConfig] = {
    "read_file": RetryConfig(max_retries=1, initial_delay_ms=100),
    "write_file": RetryConfig(max_retries=2, initial_delay_ms=200),
    "apply_diff": RetryConfig(max_retries=2, initial_delay_ms=100),
    "grep": RetryConfig(max_retries=1, initial_delay_ms=100),
    "glob": RetryConfig(max_retries=1, initial_delay_ms=100),
    "bash": RetryConfig(max_retries=0),  # No retries for bash - side effects
    "todos": RetryConfig(max_retries=1, initial_delay_ms=100),
    "web_search": RetryConfig(max_retries=2, initial_delay_ms=500),
    "web_fetch": RetryConfig(max_retries=2, initial_delay_ms=500),
    "invoke_agent": RetryConfig(max_retries=1, initial_delay_ms=1000),
}

# Tool fallback chains
TOOL_FALLBACKS: dict[str, list[str]] = {
    "apply_diff": ["write_file"],
    "grep": ["read_file"],
}


def get_tool_retry_policy(tool_name: str) -> RetryConfig:
    """Get the retry policy for a specific tool."""
    return TOOL_RETRY_POLICIES.get(tool_name, RetryConfig(max_retries=1))


def get_tool_fallbacks(tool_name: str) -> list[str]:
    """Get fallback tools for a specific tool."""
    return TOOL_FALLBACKS.get(tool_name, [])
