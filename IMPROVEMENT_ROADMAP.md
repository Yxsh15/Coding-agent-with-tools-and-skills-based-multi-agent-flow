# Agentic System Improvement Roadmap

> **Current Production Readiness: ~35-40%**  
> **Target: 80%+ for production deployment**

---

## Phase 1: Critical Foundations (Week 1-2)
*Goal: Fix blocking issues that prevent production use*

### 1.1 Error Handling & Recovery
- [ ] Add `error_handling` section to all agent configs:
  ```yaml
  error_handling:
    max_retries: 3
    retry_strategy: exponential_backoff
    backoff_multiplier: 2
    initial_delay_ms: 100
    max_delay_ms: 10000
    fallback_agent: repair_agent
  ```
- [ ] Implement `RetryPolicy` class in `backend/app/services/runner.py`
- [ ] Add transient vs permanent error classification
- [ ] Implement per-tool retry policies in `tools.py`

### 1.2 Context Window Management
- [ ] Create `ContextWindowManager` class:
  ```python
  class ContextWindowManager:
      def __init__(self, max_tokens=100_000):
          self.max_tokens = max_tokens
      
      def should_compress(self, history) -> bool:
          return self.estimate_tokens(history) > self.max_tokens * 0.8
      
      def compress_history(self, history) -> list:
          # Summarize old turns, keep recent 10
  ```
- [ ] Integrate into `_run_loop()` in runner.py
- [ ] Add token counting utility
- [ ] Test with long sessions (50+ turns)

### 1.3 Tool Schema Validation
- [ ] Create `schemas/` directory for tool JSON schemas
- [ ] Add input validation before tool execution:
  ```python
  def validate_tool_input(tool_name: str, payload: dict) -> bool:
      schema = load_schema(tool_name)
      return jsonschema.validate(payload, schema)
  ```
- [ ] Add output validation after tool execution
- [ ] Add constraints (max file size, allowed paths, timeouts)

### 1.4 Bash Tool Security Hardening
- [ ] Replace command prefix whitelist with full command+args whitelist
- [ ] Add execution timeout (30s default)
- [ ] Block dangerous argument patterns (`-exec`, `-c`, eval, etc.)
- [ ] Add audit logging for all bash executions


---

## Phase 2: Agentic Loop Improvements (Week 3-4)
*Goal: Implement ReAct-style reasoning and better orchestration*

### 2.1 Visible Reasoning (ReAct Pattern)
- [ ] Modify `llm.py` to expose thinking:
  ```python
  def generate_with_reasoning(self):
      response = self.chat.send_message(...)
      return {
          "type": "thinking|acting|done",
          "reasoning": extract_thought(response),
          "tool_calls": extract_tools(response),
      }
  ```
- [ ] Add `reasoning` event type to streaming
- [ ] Update frontend to display reasoning steps
- [ ] Add reasoning to trace logs

### 2.2 Multi-Step Planning
- [ ] Add planning phase before execution:
  ```python
  async def create_execution_plan(prompt, tools):
      plan = await model.plan(prompt)
      return [Step(tool=t, args=a) for t, a in plan]
  ```
- [ ] Implement plan validation
- [ ] Add re-planning on failure
- [ ] Track plan progress in session

### 2.3 Parallel Tool Execution
- [ ] Identify independent tool calls
- [ ] Execute independent tools with `asyncio.gather()`
- [ ] Add `execution.tool_concurrency_rules` to configs:
  ```yaml
  execution:
    max_parallel_tools: 3
    concurrent_allowed: [read_file, grep, glob]
    sequential_required: [write_file, apply_diff]
  ```

### 2.4 Tool Fallback Chains
- [ ] Define fallback mappings:
  ```python
  TOOL_FALLBACKS = {
      "apply_diff": ["write_file"],
      "grep": ["read_file"],
  }
  ```
- [ ] Implement automatic fallback on failure
- [ ] Log fallback usage for analysis

---

## Phase 3: Skills & Instructions (Week 5-6)
*Goal: Comprehensive instruction coverage*

### 3.1 Create Missing Critical Skills

#### `skills/error_handling.md`
```markdown
# Error Handling Skill

## When Operations Fail:
1. Log full error context (tool, args, error message)
2. Classify: transient (retry) vs permanent (abort/fallback)
3. For transient: retry with exponential backoff
4. For permanent: try fallback tool or report to user

## Tool-Specific Recovery:
- read_file fails → Check path exists, verify permissions
- apply_diff fails → Read latest file, use write_file instead
- grep fails → Use simpler pattern or glob first
- bash fails → Log stderr, suggest manual fix

## JavaScript Error Patterns:
- Wrap async in try/catch
- Validate DOM elements before manipulation
- Check localStorage availability
```

#### `skills/security.md`
```markdown
# Security Skill

## Input Validation:
- Sanitize user input before DOM insertion
- Use textContent, NOT innerHTML for user data
- Validate type/length of all inputs

## Data Storage:
- localStorage for non-sensitive data only
- Never store credentials in frontend
- Clear sensitive data on logout

## XSS Prevention:
✗ BAD: element.innerHTML = userInput
✓ GOOD: element.textContent = userInput
```

#### `skills/testing_qa.md`
```markdown
# Testing & QA Skill

## Unit Tests:
- Test CRUD: create, read, update, delete
- Test validation: invalid input rejected
- Test edge cases: empty arrays, null, duplicates

## Integration Tests:
- Cross-page navigation works
- State persists on reload
- Forms submit correctly

## QA Checklist:
□ No console errors on load
□ All buttons/links functional
□ Forms validate input
□ Mobile layout works
```

#### `skills/accessibility.md`
```markdown
# Accessibility Skill (WCAG 2.1 AA)

## HTML:
- <button> not <div onclick>
- Every <input> has <label for>
- Every <img> has alt text

## Keyboard:
- All interactive elements Tab-reachable
- Escape closes modals
- Logical tab order

## Color:
- 4.5:1 contrast for text
- Never color alone for meaning
```

### 3.2 Skill Composition System
- [ ] Add skill dependencies:
  ```yaml
  skills:
    - name: app_builder
      depends_on: [core, error_handling]
  ```
- [ ] Add conditional skill loading based on task type
- [ ] Add skill versioning

---

## Phase 4: Agent Configuration (Week 7-8)
*Goal: Production-grade agent configs*

### 4.1 Enhanced Config Schema
```yaml
# agents/orchestrator/config.yaml
name: orchestrator
role: orchestrator

model:
  provider: google
  name: gemini-3-pro-preview
  temperature: 0.7
  max_output_tokens: 8192
  fallback_models:
    - gemini-2-pro
    - gemini-pro
  function_calling:
    mode: auto
    strict_mode: true

tools:
  - name: read_file
    timeout_ms: 5000
    max_retries: 2
  - name: write_file
    timeout_ms: 10000
    max_retries: 1
  - name: apply_diff
    timeout_ms: 5000
    max_retries: 2
    fallback: write_file

error_handling:
  max_retries: 3
  retry_strategy: exponential_backoff
  fallback_agent: repair_agent

memory:
  short_term:
    max_messages: 20
    max_tokens: 32000
  context_compression:
    enabled: true
    threshold: 0.8

execution:
  max_turns: 15
  timeout_ms: 120000
  max_parallel_tools: 3

observability:
  log_level: INFO
  trace_sampling: 0.1
```

### 4.2 Memory Configuration
- [ ] Add short-term memory (sliding window)
- [ ] Add context compression settings
- [ ] Add long-term memory (optional RAG)

### 4.3 Execution Constraints
- [ ] Add per-agent timeout
- [ ] Add token budget per session
- [ ] Add rate limiting config

---

## Phase 5: Observability & Testing (Week 9-10)
*Goal: Production-grade monitoring and test coverage*

### 5.1 Structured Logging
- [ ] Switch to JSON logging format
- [ ] Add correlation IDs to all requests
- [ ] Add structured context (agent, tool, session_id)
- [ ] Implement log levels by component

### 5.2 Metrics Collection
- [ ] Add Prometheus metrics:
  - `agent_execution_duration_seconds`
  - `tool_invocations_total`
  - `tool_failures_total`
  - `tokens_used_total`
  - `active_sessions_count`
- [ ] Add `/metrics` endpoint
- [ ] Create Grafana dashboards

### 5.3 Distributed Tracing
- [ ] Add OpenTelemetry instrumentation
- [ ] Trace agent loops end-to-end
- [ ] Add span attributes for tools
- [ ] Export to Jaeger/DataDog

### 5.4 Test Coverage
- [ ] Unit tests for all tools (target: 80%)
- [ ] Unit tests for agent registry
- [ ] Integration tests for agent loops
- [ ] E2E tests for common workflows
- [ ] Load tests (100 concurrent sessions)

---

## Phase 6: Production Hardening (Week 11-12)
*Goal: Ready for production deployment*

### 6.1 Security
- [ ] Add request validation at API boundary
- [ ] Implement rate limiting (per IP, per session)
- [ ] Add CORS configuration (not `*`)
- [ ] Add API authentication middleware
- [ ] Security audit for prompt injection

### 6.2 Resilience
- [ ] Implement circuit breaker for LLM calls
- [ ] Add graceful degradation
- [ ] Add dead-letter queue for failed operations
- [ ] Implement health checks


---

## Progress Tracking

| Phase | Status | Expected Completion | Actual |
|-------|--------|---------------------|--------|
| Phase 1: Critical Foundations | ✅ Completed | Week 2 | 31 Mar 2026 |
| Phase 2: Agentic Loop | 🔴 Not Started | Week 4 | - |
| Phase 3: Skills & Instructions | ✅ Completed | Week 6 | 31 Mar 2026 |
| Phase 4: Agent Configuration | ✅ Completed | Week 8 | 31 Mar 2026 |
| Phase 5: Observability & Testing | ✅ Completed | Week 10 | 31 Mar 2026 |
| Phase 6: Production Hardening | 🔴 Not Started | Week 12 | - |

---

## Quick Wins (Can Do Today)

1. **Add error_handling.md skill** - 30 min
2. **Add tool timeout to bash** - 15 min  
3. **Add basic tool output validation** - 1 hr
4. **Fix CORS to not be `*`** - 5 min
5. **Add correlation ID to logs** - 30 min

---

## Reference: Production System Comparison

| Feature | Current | Target | Claude | Copilot |
|---------|---------|--------|--------|---------|
| Visible reasoning | ❌ | ✅ | ✅ | ✅ |
| Context compression | ❌ | ✅ | ✅ | ✅ |
| Per-tool retries | ❌ | ✅ | ✅ | ✅ |
| Tool fallbacks | ❌ | ✅ | ✅ | ✅ |
| Parallel tools | ❌ | ✅ | ✅ | ✅ |
| Memory tiers | ❌ | ✅ | ✅ | ✅ |
| Structured errors | ❌ | ✅ | ✅ | ✅ |
| Observability | ❌ | ✅ | ✅ | ✅ |
| Test coverage | 5% | 80% | 90%+ | 90%+ |
| Security hardening | ❌ | ✅ | ✅ | ✅ |

---

*Last Updated: 31 March 2026*
