"""
Observability module: Structured logging, metrics, and tracing.
Phase 5: Observability & Testing
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Generator
from collections import defaultdict
from threading import Lock

logger = logging.getLogger("app.services.observability")


# ============================================================================
# Correlation & Context
# ============================================================================

class CorrelationContext:
    """Thread-local correlation context for request tracing."""
    
    _local_storage: dict[str, str] = {}
    
    @classmethod
    def get_correlation_id(cls) -> str:
        """Get or create correlation ID for current request."""
        if "correlation_id" not in cls._local_storage:
            cls._local_storage["correlation_id"] = uuid.uuid4().hex[:16]
        return cls._local_storage["correlation_id"]
    
    @classmethod
    def set_correlation_id(cls, correlation_id: str) -> None:
        """Set correlation ID for current request."""
        cls._local_storage["correlation_id"] = correlation_id
    
    @classmethod
    def get_session_id(cls) -> str | None:
        """Get session ID if set."""
        return cls._local_storage.get("session_id")
    
    @classmethod
    def set_session_id(cls, session_id: str) -> None:
        """Set session ID for current request."""
        cls._local_storage["session_id"] = session_id
    
    @classmethod
    def get_agent_name(cls) -> str | None:
        """Get current agent name if set."""
        return cls._local_storage.get("agent_name")
    
    @classmethod
    def set_agent_name(cls, agent_name: str) -> None:
        """Set current agent name."""
        cls._local_storage["agent_name"] = agent_name
    
    @classmethod
    def clear(cls) -> None:
        """Clear all context."""
        cls._local_storage.clear()
    
    @classmethod
    def get_all(cls) -> dict[str, str]:
        """Get all context values."""
        return dict(cls._local_storage)


# ============================================================================
# Structured Logging
# ============================================================================

class StructuredLogFormatter(logging.Formatter):
    """JSON formatter for structured logging."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": CorrelationContext.get_correlation_id(),
        }
        
        # Add optional context
        session_id = CorrelationContext.get_session_id()
        if session_id:
            log_data["session_id"] = session_id
        
        agent_name = CorrelationContext.get_agent_name()
        if agent_name:
            log_data["agent"] = agent_name
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)
        
        return json.dumps(log_data, default=str)


def configure_structured_logging(
    level: str = "INFO",
    json_output: bool = True,
) -> logging.Logger:
    """Configure structured JSON logging."""
    app_logger = logging.getLogger("app")
    
    if getattr(app_logger, "_structured_configured", False):
        return app_logger
    
    log_level = getattr(logging, level.upper(), logging.INFO)
    app_logger.setLevel(log_level)
    
    # Clear existing handlers
    app_logger.handlers.clear()
    
    # Create handler
    handler = logging.StreamHandler()
    handler.setLevel(log_level)
    
    if json_output:
        handler.setFormatter(StructuredLogFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            fmt="%(asctime)s %(levelname)s [%(correlation_id)s] %(name)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
    
    app_logger.addHandler(handler)
    app_logger.propagate = False
    app_logger._structured_configured = True
    
    return app_logger


class StructuredLogger:
    """Logger with structured context support."""
    
    def __init__(self, name: str):
        self._logger = logging.getLogger(name)
    
    def _log(self, level: int, message: str, **kwargs: Any) -> None:
        record = self._logger.makeRecord(
            self._logger.name,
            level,
            "",
            0,
            message,
            (),
            None,
        )
        record.extra_fields = kwargs
        self._logger.handle(record)
    
    def debug(self, message: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, message, **kwargs)
    
    def info(self, message: str, **kwargs: Any) -> None:
        self._log(logging.INFO, message, **kwargs)
    
    def warning(self, message: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, message, **kwargs)
    
    def error(self, message: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, message, **kwargs)
    
    def exception(self, message: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, message, **kwargs)


# ============================================================================
# Metrics Collection
# ============================================================================

@dataclass
class MetricValue:
    """A single metric measurement."""
    name: str
    value: float
    timestamp: float = field(default_factory=time.time)
    labels: dict[str, str] = field(default_factory=dict)


class MetricsCollector:
    """Simple in-memory metrics collector."""
    
    def __init__(self):
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()
    
    def increment_counter(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """Increment a counter metric."""
        key = self._make_key(name, labels)
        with self._lock:
            self._counters[key] += value
    
    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Set a gauge metric."""
        key = self._make_key(name, labels)
        with self._lock:
            self._gauges[key] = value
    
    def observe_histogram(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Observe a value for a histogram metric."""
        key = self._make_key(name, labels)
        with self._lock:
            self._histograms[key].append(value)
            # Keep only last 1000 observations
            if len(self._histograms[key]) > 1000:
                self._histograms[key] = self._histograms[key][-1000:]
    
    def _make_key(self, name: str, labels: dict[str, str] | None) -> str:
        """Create a unique key for a metric with labels."""
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"
    
    def get_counter(self, name: str, labels: dict[str, str] | None = None) -> float:
        """Get current counter value."""
        key = self._make_key(name, labels)
        return self._counters.get(key, 0.0)
    
    def get_gauge(self, name: str, labels: dict[str, str] | None = None) -> float | None:
        """Get current gauge value."""
        key = self._make_key(name, labels)
        return self._gauges.get(key)
    
    def get_histogram_stats(self, name: str, labels: dict[str, str] | None = None) -> dict[str, float]:
        """Get histogram statistics."""
        key = self._make_key(name, labels)
        values = self._histograms.get(key, [])
        
        if not values:
            return {"count": 0}
        
        sorted_values = sorted(values)
        count = len(values)
        
        return {
            "count": count,
            "sum": sum(values),
            "min": sorted_values[0],
            "max": sorted_values[-1],
            "avg": sum(values) / count,
            "p50": sorted_values[int(count * 0.5)],
            "p95": sorted_values[int(count * 0.95)] if count >= 20 else sorted_values[-1],
            "p99": sorted_values[int(count * 0.99)] if count >= 100 else sorted_values[-1],
        }
    
    def get_all_metrics(self) -> dict[str, Any]:
        """Get all metrics as a dictionary."""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {
                    name: self.get_histogram_stats(name)
                    for name in self._histograms.keys()
                },
            }
    
    def reset(self) -> None:
        """Reset all metrics."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


# Global metrics collector
_metrics: MetricsCollector | None = None


def get_metrics() -> MetricsCollector:
    """Get or create the global metrics collector."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics


# ============================================================================
# Predefined Metrics
# ============================================================================

class AgentMetrics:
    """Helper for recording agent-related metrics."""
    
    def __init__(self, collector: MetricsCollector | None = None):
        self._collector = collector or get_metrics()
    
    def record_agent_started(self, agent_name: str) -> None:
        """Record agent execution started."""
        self._collector.increment_counter(
            "agent_executions_total",
            labels={"agent": agent_name, "status": "started"},
        )
    
    def record_agent_finished(self, agent_name: str, duration_ms: float, success: bool = True) -> None:
        """Record agent execution finished."""
        status = "success" if success else "error"
        self._collector.increment_counter(
            "agent_executions_total",
            labels={"agent": agent_name, "status": status},
        )
        self._collector.observe_histogram(
            "agent_execution_duration_ms",
            duration_ms,
            labels={"agent": agent_name},
        )
    
    def record_tool_invocation(self, tool_name: str, agent_name: str, success: bool, duration_ms: float) -> None:
        """Record tool invocation."""
        status = "success" if success else "error"
        self._collector.increment_counter(
            "tool_invocations_total",
            labels={"tool": tool_name, "agent": agent_name, "status": status},
        )
        self._collector.observe_histogram(
            "tool_execution_duration_ms",
            duration_ms,
            labels={"tool": tool_name},
        )
    
    def record_tokens_used(self, agent_name: str, input_tokens: int, output_tokens: int) -> None:
        """Record token usage."""
        self._collector.increment_counter(
            "tokens_used_total",
            value=input_tokens,
            labels={"agent": agent_name, "type": "input"},
        )
        self._collector.increment_counter(
            "tokens_used_total",
            value=output_tokens,
            labels={"agent": agent_name, "type": "output"},
        )
    
    def record_active_sessions(self, count: int) -> None:
        """Record current active session count."""
        self._collector.set_gauge("active_sessions", count)
    
    def record_error(self, error_type: str, agent_name: str | None = None) -> None:
        """Record an error occurrence."""
        labels = {"type": error_type}
        if agent_name:
            labels["agent"] = agent_name
        self._collector.increment_counter("errors_total", labels=labels)


# ============================================================================
# Tracing
# ============================================================================

@dataclass
class Span:
    """A trace span representing an operation."""
    trace_id: str
    span_id: str
    name: str
    start_time: float
    end_time: float | None = None
    parent_span_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ok"
    
    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """Add an event to the span."""
        self.events.append({
            "name": name,
            "timestamp": time.time(),
            "attributes": attributes or {},
        })
    
    def set_attribute(self, key: str, value: Any) -> None:
        """Set a span attribute."""
        self.attributes[key] = value
    
    def set_status(self, status: str, message: str | None = None) -> None:
        """Set span status."""
        self.status = status
        if message:
            self.attributes["status_message"] = message
    
    def finish(self) -> None:
        """Finish the span."""
        self.end_time = time.time()
    
    @property
    def duration_ms(self) -> float | None:
        """Get span duration in milliseconds."""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000
    
    def to_dict(self) -> dict[str, Any]:
        """Convert span to dictionary."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "name": self.name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "parent_span_id": self.parent_span_id,
            "attributes": self.attributes,
            "events": self.events,
            "status": self.status,
        }


class Tracer:
    """Simple tracer for distributed tracing."""
    
    def __init__(self, service_name: str = "agent-service"):
        self.service_name = service_name
        self._spans: list[Span] = []
        self._current_trace_id: str | None = None
        self._current_span_id: str | None = None
        self._lock = Lock()
    
    @contextmanager
    def start_span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> Generator[Span, None, None]:
        """Start a new span."""
        # Generate IDs
        if self._current_trace_id is None:
            trace_id = uuid.uuid4().hex
        else:
            trace_id = self._current_trace_id
        
        span_id = uuid.uuid4().hex[:16]
        parent_span_id = self._current_span_id
        
        # Create span
        span = Span(
            trace_id=trace_id,
            span_id=span_id,
            name=name,
            start_time=time.time(),
            parent_span_id=parent_span_id,
            attributes=attributes or {},
        )
        span.set_attribute("service.name", self.service_name)
        
        # Set as current
        old_trace_id = self._current_trace_id
        old_span_id = self._current_span_id
        self._current_trace_id = trace_id
        self._current_span_id = span_id
        
        try:
            yield span
            span.set_status("ok")
        except Exception as e:
            span.set_status("error", str(e))
            raise
        finally:
            span.finish()
            with self._lock:
                self._spans.append(span)
                # Keep only last 1000 spans
                if len(self._spans) > 1000:
                    self._spans = self._spans[-1000:]
            
            # Restore parent context
            self._current_trace_id = old_trace_id
            self._current_span_id = old_span_id
    
    def get_recent_spans(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent spans."""
        with self._lock:
            return [span.to_dict() for span in self._spans[-limit:]]
    
    def get_trace(self, trace_id: str) -> list[dict[str, Any]]:
        """Get all spans for a trace."""
        with self._lock:
            return [
                span.to_dict()
                for span in self._spans
                if span.trace_id == trace_id
            ]


# Global tracer
_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    """Get or create the global tracer."""
    global _tracer
    if _tracer is None:
        _tracer = Tracer()
    return _tracer


# ============================================================================
# Convenience Functions
# ============================================================================

def log_agent_event(
    event: str,
    agent_name: str,
    **kwargs: Any,
) -> None:
    """Log an agent event with structured data."""
    log = StructuredLogger("app.services.agent")
    log.info(
        f"Agent event: {event}",
        event=event,
        agent=agent_name,
        **kwargs,
    )


def log_tool_event(
    event: str,
    tool_name: str,
    agent_name: str,
    **kwargs: Any,
) -> None:
    """Log a tool event with structured data."""
    log = StructuredLogger("app.services.tool")
    log.info(
        f"Tool event: {event}",
        event=event,
        tool=tool_name,
        agent=agent_name,
        **kwargs,
    )
