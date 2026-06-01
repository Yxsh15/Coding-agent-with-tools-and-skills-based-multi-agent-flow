"""
Tests for observability module: metrics, logging, tracing.
Phase 5: Testing
"""
import pytest
import json
import time
from app.services.observability import (
    CorrelationContext,
    MetricsCollector,
    AgentMetrics,
    Tracer,
    StructuredLogFormatter,
    get_metrics,
    get_tracer,
)


class TestCorrelationContext:
    """Tests for correlation context management."""
    
    def test_get_creates_correlation_id(self):
        CorrelationContext.clear()
        
        cid = CorrelationContext.get_correlation_id()
        assert cid is not None
        assert len(cid) == 16  # hex UUID format
    
    def test_set_correlation_id(self):
        CorrelationContext.set_correlation_id("test-id-123")
        assert CorrelationContext.get_correlation_id() == "test-id-123"
    
    def test_session_id(self):
        CorrelationContext.set_session_id("session-123")
        assert CorrelationContext.get_session_id() == "session-123"
    
    def test_agent_name(self):
        CorrelationContext.set_agent_name("orchestrator")
        assert CorrelationContext.get_agent_name() == "orchestrator"
    
    def test_clear(self):
        CorrelationContext.set_session_id("session")
        CorrelationContext.clear()
        assert CorrelationContext.get_session_id() is None


class TestMetricsCollector:
    """Tests for metrics collection."""
    
    def test_increment_counter(self):
        collector = MetricsCollector()
        
        collector.increment_counter("test_counter")
        collector.increment_counter("test_counter")
        collector.increment_counter("test_counter", value=5)
        
        assert collector.get_counter("test_counter") == 7
    
    def test_counter_with_labels(self):
        collector = MetricsCollector()
        
        collector.increment_counter("requests", labels={"method": "GET"})
        collector.increment_counter("requests", labels={"method": "POST"})
        collector.increment_counter("requests", labels={"method": "GET"})
        
        assert collector.get_counter("requests", labels={"method": "GET"}) == 2
        assert collector.get_counter("requests", labels={"method": "POST"}) == 1
    
    def test_set_gauge(self):
        collector = MetricsCollector()
        
        collector.set_gauge("temperature", 72.5)
        assert collector.get_gauge("temperature") == 72.5
        
        collector.set_gauge("temperature", 75.0)
        assert collector.get_gauge("temperature") == 75.0
    
    def test_observe_histogram(self):
        collector = MetricsCollector()
        
        for value in [10, 20, 30, 40, 50]:
            collector.observe_histogram("latency", value)
        
        stats = collector.get_histogram_stats("latency")
        
        assert stats["count"] == 5
        assert stats["sum"] == 150
        assert stats["min"] == 10
        assert stats["max"] == 50
        assert stats["avg"] == 30
    
    def test_get_all_metrics(self):
        collector = MetricsCollector()
        
        collector.increment_counter("requests")
        collector.set_gauge("connections", 10)
        collector.observe_histogram("latency", 100)
        
        all_metrics = collector.get_all_metrics()
        
        assert "counters" in all_metrics
        assert "gauges" in all_metrics
        assert "histograms" in all_metrics
    
    def test_reset(self):
        collector = MetricsCollector()
        
        collector.increment_counter("test")
        collector.reset()
        
        assert collector.get_counter("test") == 0


class TestAgentMetrics:
    """Tests for agent-specific metrics."""
    
    def test_record_agent_lifecycle(self):
        collector = MetricsCollector()
        metrics = AgentMetrics(collector)
        
        metrics.record_agent_started("orchestrator")
        metrics.record_agent_finished("orchestrator", 1500.0, success=True)
        
        # Check counters
        started = collector.get_counter(
            "agent_executions_total",
            labels={"agent": "orchestrator", "status": "started"}
        )
        assert started == 1
        
        success = collector.get_counter(
            "agent_executions_total",
            labels={"agent": "orchestrator", "status": "success"}
        )
        assert success == 1
    
    def test_record_tool_invocation(self):
        collector = MetricsCollector()
        metrics = AgentMetrics(collector)
        
        metrics.record_tool_invocation("read_file", "orchestrator", True, 50.0)
        
        invocations = collector.get_counter(
            "tool_invocations_total",
            labels={"tool": "read_file", "agent": "orchestrator", "status": "success"}
        )
        assert invocations == 1
    
    def test_record_error(self):
        collector = MetricsCollector()
        metrics = AgentMetrics(collector)
        
        metrics.record_error("ValidationError", agent_name="validator")
        
        errors = collector.get_counter(
            "errors_total",
            labels={"type": "ValidationError", "agent": "validator"}
        )
        assert errors == 1


class TestTracer:
    """Tests for distributed tracing."""
    
    def test_start_span(self):
        tracer = Tracer("test-service")
        
        with tracer.start_span("test-operation") as span:
            span.set_attribute("key", "value")
        
        assert span.end_time is not None
        assert span.status == "ok"
        assert span.attributes["key"] == "value"
    
    def test_nested_spans(self):
        tracer = Tracer("test-service")
        
        with tracer.start_span("parent") as parent:
            with tracer.start_span("child") as child:
                pass
        
        # Child should have parent's span as parent
        assert child.parent_span_id == parent.span_id
        assert child.trace_id == parent.trace_id
    
    def test_span_error_handling(self):
        tracer = Tracer("test-service")
        
        try:
            with tracer.start_span("failing-op") as span:
                raise ValueError("test error")
        except ValueError:
            pass
        
        assert span.status == "error"
    
    def test_get_recent_spans(self):
        tracer = Tracer("test-service")
        
        with tracer.start_span("op1"):
            pass
        with tracer.start_span("op2"):
            pass
        
        spans = tracer.get_recent_spans(limit=10)
        assert len(spans) >= 2


class TestStructuredLogFormatter:
    """Tests for structured log formatting."""
    
    def test_formats_as_json(self):
        import logging
        
        formatter = StructuredLogFormatter()
        
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        
        formatted = formatter.format(record)
        parsed = json.loads(formatted)
        
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "Test message"
        assert "timestamp" in parsed
        assert "correlation_id" in parsed


class TestGlobalInstances:
    """Tests for global instance management."""
    
    def test_get_metrics_singleton(self):
        m1 = get_metrics()
        m2 = get_metrics()
        assert m1 is m2
    
    def test_get_tracer_singleton(self):
        t1 = get_tracer()
        t2 = get_tracer()
        assert t1 is t2
