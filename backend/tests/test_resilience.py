"""
Tests for resilience module: error handling, retry policies, circuit breakers.
Phase 5: Testing
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.services.resilience import (
    ErrorCategory,
    RetryConfig,
    ErrorHandlingConfig,
    classify_error,
    should_retry,
    CircuitBreaker,
    RetryExecutor,
    get_tool_retry_policy,
    get_tool_fallbacks,
)


class TestErrorClassification:
    """Tests for error classification."""
    
    def test_classifies_connection_errors_as_transient(self):
        class ConnectionError(Exception):
            pass
        
        exc = ConnectionError("Connection refused")
        assert classify_error(exc) == ErrorCategory.TRANSIENT
    
    def test_classifies_timeout_as_transient(self):
        exc = Exception("Request timed out")
        assert classify_error(exc) == ErrorCategory.TRANSIENT
    
    def test_classifies_rate_limit_as_rate_limit(self):
        exc = Exception("Rate limit exceeded (429)")
        assert classify_error(exc) == ErrorCategory.RATE_LIMIT
    
    def test_classifies_value_error_as_validation(self):
        exc = ValueError("Invalid input")
        assert classify_error(exc) == ErrorCategory.VALIDATION
    
    def test_classifies_auth_error_as_permanent(self):
        exc = Exception("401 Unauthorized")
        assert classify_error(exc) == ErrorCategory.PERMANENT
    
    def test_classifies_unknown_error(self):
        exc = Exception("Something weird happened")
        assert classify_error(exc) == ErrorCategory.UNKNOWN


class TestRetryConfig:
    """Tests for retry configuration."""
    
    def test_default_config(self):
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.initial_delay_ms == 100
        assert config.backoff_multiplier == 2.0
    
    def test_get_delay_exponential(self):
        config = RetryConfig(
            initial_delay_ms=100,
            backoff_multiplier=2.0,
            jitter=False,
        )
        
        # First attempt: 100ms
        assert config.get_delay(0) == 0.1
        # Second attempt: 200ms
        assert config.get_delay(1) == 0.2
        # Third attempt: 400ms
        assert config.get_delay(2) == 0.4
    
    def test_get_delay_respects_max(self):
        config = RetryConfig(
            initial_delay_ms=1000,
            max_delay_ms=5000,
            backoff_multiplier=10.0,
            jitter=False,
        )
        
        # Would be 10000ms but capped at 5000ms
        assert config.get_delay(1) == 5.0


class TestShouldRetry:
    """Tests for retry decision logic."""
    
    def test_no_retry_when_max_reached(self):
        config = RetryConfig(max_retries=3)
        exc = Exception("timeout")
        
        assert should_retry(exc, 3, config) is False
    
    def test_retry_on_transient_error(self):
        config = RetryConfig(max_retries=3)
        exc = Exception("Connection reset by peer")
        
        assert should_retry(exc, 0, config) is True
        assert should_retry(exc, 2, config) is True
    
    def test_no_retry_on_validation_error(self):
        config = RetryConfig(max_retries=3)
        exc = ValueError("Invalid input")
        
        assert should_retry(exc, 0, config) is False


class TestCircuitBreaker:
    """Tests for circuit breaker pattern."""
    
    def test_starts_closed(self):
        cb = CircuitBreaker()
        assert cb.state == "closed"
        assert cb.is_open() is False
    
    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        
        cb.record_failure()
        assert cb.state == "closed"
        
        cb.record_failure()
        assert cb.state == "closed"
        
        cb.record_failure()
        assert cb.state == "open"
        assert cb.is_open() is True
    
    def test_success_resets_counter(self):
        cb = CircuitBreaker(failure_threshold=3)
        
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        
        # Counter reset, need 3 more failures to open
        cb.record_failure()
        assert cb.state == "closed"


class TestToolPolicies:
    """Tests for tool-specific retry policies."""
    
    def test_get_tool_policy(self):
        policy = get_tool_retry_policy("read_file")
        assert policy.max_retries == 1  # read_file has 1 retry by default
        
        policy = get_tool_retry_policy("bash")
        assert policy.max_retries == 0  # No retries for bash
    
    def test_get_fallbacks(self):
        fallbacks = get_tool_fallbacks("apply_diff")
        assert "write_file" in fallbacks
        
        fallbacks = get_tool_fallbacks("read_file")
        assert fallbacks == []


@pytest.mark.asyncio(loop_scope="class")
class TestRetryExecutor:
    """Tests for retry executor."""
    
    async def test_succeeds_on_first_try(self):
        config = RetryConfig(max_retries=3)
        executor = RetryExecutor(config)
        
        async_func = AsyncMock(return_value="success")
        result = await executor.execute_async(async_func)
        
        assert result == "success"
        assert async_func.call_count == 1
    
    async def test_retries_on_transient_error(self):
        config = RetryConfig(max_retries=3, initial_delay_ms=1)
        executor = RetryExecutor(config)
        
        call_count = 0
        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Connection reset")
            return "success"
        
        result = await executor.execute_async(flaky_func)
        assert result == "success"
        assert call_count == 3
    
    async def test_fails_after_max_retries(self):
        config = RetryConfig(max_retries=2, initial_delay_ms=1)
        executor = RetryExecutor(config)
        
        async def always_fails():
            raise Exception("Connection timeout")
        
        with pytest.raises(Exception, match="Connection timeout"):
            await executor.execute_async(always_fails)
