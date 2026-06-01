# System Improvement Plan

## Current State Assessment

This document maps the current system's gaps against proven agentic patterns (from suggestion.txt) and prioritizes improvements by impact.

---

## 1. The Core Loop Problem

### What suggestion.txt describes (the ideal)

```
while not done:
    observe_state()
    think()
    choose_tool()
    execute_tool()
    update_state()
```

A single persistent loop where error correction is just another iteration. The agent re-evaluates state every cycle using external feedback.

### What our system actually does

**File:** `backend/app/services/runner.py` lines 1129-1384

```
for step in range(max_turns):
    if timeout: break
    if compression_needed: compress()   # ← broken, now disabled
    message, tool_calls = model.generate()
    if text_only: break                 # ← exits immediately on text
    if tool_calls: execute_tools()      # ← no state evaluation after
    # NO observe_state() step
    # NO explicit "did my last action succeed?" check
```

**Key gaps vs ideal loop:**

| Ideal Loop Step | Our Implementation | Status |
|---|---|---|
| `observe_state()` | Missing entirely | The agent never explicitly checks workspace state between turns |
| `think()` | Implicit in LLM generation | Works, but no structured planning |
| `choose_tool()` | Implicit in LLM generation | Works |
| `execute_tool()` | `toolbox.execute()` (line 1294) | Works |
| `update_state()` | `model.add_tool_outputs()` (line 1323) | Only updates conversation history, not a structured state object |

**The fundamental issue:** Our loop is **open-loop** during execution. It generates, executes tools, and feeds results back — but never explicitly checks "did what I just did actually work?" The only closed-loop feedback happens when:
1. The LLM happens to read its tool output and self-correct (unreliable)
2. Post-loop validation catches structural issues (too late)
3. Repair pass re-runs the whole thing (expensive)

---

## 2. State Representation Gap

### What suggestion.txt describes

> The agent maintains structured state: file system snapshot, previous tool calls + outputs, terminal outputs, test results, lint/compiler errors, explicit task goal.

### What we have

**No structured state object exists.** The "state" is:
- Conversation history in `GeminiModel._managed_history` (llm.py line 349)
- Internal logs in `.internal/logs.json` (runner.py lines 1399-1417)
- Raw workspace files on disk

**What's missing:**
- No task goal tracking (the original prompt is in conversation history but not extracted as structured state)
- No "what files have I created/modified this session" tracker
- No test/lint results (no testing tools exist)
- No explicit "expected vs actual" comparison
- No progress tracker against the plan

**Impact:** The agent can't pattern-match "expected state vs actual state" because there is no explicit expected state to compare against.

---

## 3. Error Detection: Hard vs Soft Signals

### What suggestion.txt describes

**Hard signals (deterministic):** Exit codes != 0, test failures, linter errors, runtime exceptions
**Soft signals (LLM-evaluated):** Output doesn't match intent, missing functionality, logical inconsistency

### What we have

**Hard signals — partial:**
- `node --check app.js` for JS syntax (runner.py line 1007) — but only in post-validation, not during generation
- Exit codes from bash tool (tools.py line 501) — but bash removed from builders
- JSON parse validation on write_file for .json files (tools.py line 290)

**Soft signals — post-hoc only:**
- `_validate_generated_app()` runs AFTER generation completes (runner.py lines 954-1127)
- Checks: missing files, broken references, unwired IDs, missing CSS classes, route mismatches
- Validator agent runs grep-based checks but only when explicitly invoked

**What's missing:**
- No syntax checking during generation (agent writes JS, never validates it)
- No HTML validation tool
- No CSS validation tool
- No "does this page render" check
- No way for agent to run its own code and see the result
- Hard signals are only available post-completion, never mid-loop

**Concrete gap:** page_builder writes 10 HTML files. It has no way to check if any of them are valid HTML, if their IDs match what app.js expects, or if they reference existing CSS classes. It just writes and hopes.

---

## 4. Tool Quality Analysis

### Critical tool gaps

| Tool | Issue | Impact |
|---|---|---|
| `write_file` | Returns only "wrote {path}" — no byte count, line count, or verification | Agent can't confirm write succeeded or content is correct |
| `write_file` | No post-write verification | Silent corruption possible |
| `apply_diff` | No post-apply verification | Could silently produce wrong output |
| `read_file` | Silent truncation without clear signal | Agent may work with incomplete data |
| `grep` | Substring match only, no regex, no context lines | Limited search capability |
| `invoke_agent` | No structured success/failure signal | Orchestrator can't reliably know if sub-agent succeeded |

### Missing tools entirely

| Tool | Purpose | Why Needed |
|---|---|---|
| `validate_html` | Check HTML syntax | Catch malformed HTML during generation |
| `validate_js` | Run `node --check` | Catch JS errors during generation, not just post-validation |
| `validate_css` | Check CSS syntax | Catch CSS errors |
| `check_references` | Verify IDs, classes, routes match across files | The #1 failure mode — mismatched IDs between HTML and JS |
| `run_preview` | Headless render check | Confirm app actually loads |
| `verify_write` | Read-back after write, return diff of expected vs actual | Close the write verification gap |

---

## 5. Feedback Loop Quality

### What suggestion.txt describes

> Closed-loop grounding: Write code → Run tool (compiler/test) → Observe failure → Replan → Patch → Repeat.
> Equivalent to gradient descent over program correctness using environment feedback.

### What we have

**The feedback loop is open during generation, closed only at the end:**

```
[Generation Phase — OPEN LOOP]
  orchestrator → invoke object_builder → objects written (no verification)
  orchestrator → invoke validator → JSON response (sometimes ignored)
  orchestrator → invoke page_builder → pages written (no verification)
  orchestrator → write index.html, styles.css, app.js (no verification)

[Post-Generation — CLOSED LOOP]
  _validate_generated_app() → finds issues
  if issues: _build_repair_prompt() → re-run orchestrator
  if still issues: report to user
```

**The problem:** By the time validation runs, the agent has already used all its turns and context. The repair pass is a cold-start with a new prompt — it doesn't have the original reasoning chain.

### What it should look like

```
[Generation Phase — CLOSED LOOP]
  for each file written:
    validate(file)                    # ← immediate feedback
    if issues: fix in same turn       # ← no context switch
  for each specialist:
    invoke → validate result → retry if needed  # ← before moving on
  before finishing:
    cross-validate all files together  # ← catch integration issues
```

---

## 6. Planning Layer

### What suggestion.txt describes

> Better systems include an explicit plan. After each step: Is step complete? If no → refine. If yes → proceed.

### What we have

**The orchestrator has a decision gate (SIMPLE vs COMPLEX) but no explicit plan execution tracker.**

- Decision gate: runner.py lines 442-449 — classifies requests
- Complex workflow: runner.py lines 458-470 — 8-step sequence
- No step-completion tracking — orchestrator can skip steps or do them out of order
- No plan persistence — if agent restarts mid-task, plan is lost
- solution.md serves as an implicit plan but is not machine-parseable

**What's missing:**
- Explicit plan with checkable milestones
- Step-completion verification before proceeding
- Plan revision when steps fail
- Plan visibility to sub-agents (they don't know the overall plan)

---

## 7. Self-Correction Patterns

### What suggestion.txt describes

> What looks like "it realized the bug and fixed it" is actually: Observed stack trace → Recognized pattern → Retrieved fix pattern → Applied patch → Verified via tools.

### What we have

- **No self-verification loop.** Agent writes a file and moves on.
- **No "read back what I wrote" pattern.** System prompt says "read before editing" but never "read after writing."
- **Repair pass is the only correction mechanism** — and it's a cold restart, not an in-loop correction.
- **Validator agent is invoked but its output isn't always acted on.** Orchestrator receives validator JSON but may not format it as actionable instructions for specialists.

### Concrete failure mode from the logs

```
1. page_builder writes pages/home.html with id="trip-list"
2. page_builder writes pages/trips.html with id="trip-grid"
3. orchestrator writes app.js referencing getElementById("trip-list")
4. app.js references "trip-grid" which doesn't exist in home.html
5. Post-validation catches this — but only after all files are written
6. Repair pass tries to fix but may not have enough context
```

If page_builder had verified its IDs against app.js expectations mid-loop, this would never happen.

---

## 8. Resilience Infrastructure — Built But Unused

### What exists but isn't connected

| Component | File | Status |
|---|---|---|
| Circuit Breaker | resilience.py lines 136-187 | Defined, **never instantiated** |
| RetryExecutor | resilience.py lines 189-276 | Defined, **never used in runner** |
| Error Classification | resilience.py lines 98-124 | Defined, **not used for LLM errors** |
| Tool Retry Policies | resilience.py lines 294-305 | Defined, **not applied in tool execution** |
| Metrics Endpoint | main.py lines 457-504 | **BROKEN** — calls undefined `get_metrics_collector()` |

The resilience module is well-written but completely disconnected from the actual execution path. The runner has its own ad-hoc retry logic (MALFORMED_FC_RETRY_LIMIT in runner.py line 1245) that doesn't use any of the resilience infrastructure.

---

## 9. Observability Gaps

| What's Tracked | What's Missing |
|---|---|
| Agent execution count & duration | Agent success rate (% of runs producing valid apps) |
| Tool invocation count & duration | Tool failure rate by type |
| Token usage per agent | Cost per generation |
| Active sessions | Generation quality score |
| Trace spans (in-memory, 1000 max) | External trace export (Jaeger, etc.) |
| | Per-file validation pass/fail rate |
| | User satisfaction / retry rate |
| | Agent stall/crash rate |

---

## Prioritized Improvements

### Priority 1: Close the Feedback Loop (Highest Impact)

**Problem:** Agent writes files and never checks them. Errors accumulate until post-validation.

**Solution: Add a `validate_workspace` tool**

This is one tool that performs the checks currently done in `_validate_generated_app()` (runner.py lines 954-1127) but is callable BY the agent mid-generation. The agent can run it after writing key files to catch issues immediately.

**Implementation:**
- Extract validation logic from `_validate_generated_app()` into a tool
- Add to orchestrator's and page_builder's tool lists
- Add to system prompt: "After writing app.js, run validate_workspace to check for issues"
- Returns structured JSON: `{issues: [{file, issue, fix_hint}]}`

**Why this is #1:** This single change converts the open-loop generation into a closed-loop one. The agent can self-correct mid-run instead of relying on post-hoc repair passes.

### Priority 2: Add `validate_syntax` Tool

**Problem:** No syntax checking during generation. JS/HTML/CSS errors caught only in post-validation (and only JS).

**Solution:**
- `validate_syntax(path)` — runs appropriate checker based on extension:
  - `.js` → `node --check`
  - `.html` → basic tag matching / well-formedness check
  - `.css` → basic syntax check
  - `.json` → JSON.parse
- Returns errors with line numbers
- Available to all builder agents

**Why this is #2:** Catches the most common generation errors (syntax) at write time instead of after all files are done.

### Priority 3: Improve `write_file` Feedback

**Problem:** Returns only "wrote {path}". Agent has no verification.

**Solution:**
- Return `{path, bytes_written, line_count, checksum}` after write
- Optionally run syntax validation automatically on write
- For `.json` files: validate and return parse status

**Why this is #3:** Low effort, high value. Every tool call gives the agent more signal.

### Priority 4: Add `check_references` Tool

**Problem:** The #1 failure mode is mismatched IDs/classes between HTML and JS/CSS.

**Solution:**
- `check_references()` — runs the cross-file checks from `_validate_generated_app()`:
  - DOM IDs referenced in JS exist in HTML
  - CSS classes used in HTML exist in CSS
  - Route targets in links match route handlers in JS
  - Asset references (src, href) point to existing files
- Returns structured mismatch list
- Available to orchestrator

**Why this is #4:** Directly addresses the most common validation failure. Combined with Priority 1, this gives the agent a complete self-check capability.

### Priority 5: Fix invoke_agent Result Handling

**Problem:** When invoke_agent returns, orchestrator gets raw text. No structured signal about success/failure.

**Solution:**
- Return `{status: "success"|"error"|"partial", message, artifacts_created: [...], issues: [...]}`
- For page_builder: include list of pages created vs expected
- For object_builder: include list of objects created vs expected
- For validator: already returns JSON, but parse and validate it properly

**Why this is #5:** Orchestrator can make better decisions about whether to retry or proceed.

### Priority 6: Connect Resilience Module

**Problem:** Circuit breakers, retry executors, and error classification exist but aren't used.

**Solution:**
- Use `RetryExecutor` in tool execution path (tools.py `execute()` method)
- Apply tool-specific retry policies from resilience.py
- Add circuit breaker for LLM API calls (prevent hammering on sustained failures)
- Fix broken metrics endpoint (main.py — `get_metrics_collector()` → `get_metrics()`)

### Priority 7: Add Progress Streaming

**Problem:** Agents in pure tool-call mode emit nothing to chat. User sees silence then error.

**Solution:**
- In the runner loop, after every N tool calls (e.g., every 3), emit a progress event:
  ```python
  if step % 3 == 0 and tool_calls:
      await emit("progress", {"agent": agent_name, "step": step, "last_tool": tool_name})
  ```
- Frontend can show "page_builder: writing pages/trips.html (step 7/16)"
- On completion, emit a synthetic summary if agent only returned tool calls

### Priority 8: Structured State Object

**Problem:** No explicit state tracking. Agent's "state" is just conversation history.

**Solution:**
- Create `AgentState` dataclass tracking:
  - `files_created: list[str]`
  - `files_modified: list[str]`
  - `validation_issues: list[str]`
  - `plan_steps_completed: list[str]`
  - `current_step: str`
- Update after each tool execution
- Include in system prompt context each turn
- Persist to `.internal/state.json`

---

## What NOT to Add (Overcomplexity Traps)

| Suggestion | Why Skip It |
|---|---|
| Separate planning agent | Orchestrator's decision gate + solution.md is sufficient for this project's scope |
| Full test runner tool | No tests to run — the app is static HTML/CSS/JS |
| External trace export | Not needed until multi-user or production deployment |
| Token budget optimizer | Gemini 3 Pro has 1M context, not a constraint |
| Rollback mechanism | Git-style rollbacks add complexity; repair pass is simpler |
| Agent memory across sessions | Each generation is independent; no cross-session learning needed |

---

## Implementation Order

```
Phase 1 (Close the loop):
  1. validate_workspace tool      ← biggest bang for buck
  2. validate_syntax tool          ← catch errors early
  3. Better write_file feedback    ← low effort

Phase 2 (Better coordination):
  4. check_references tool         ← fix #1 failure mode
  5. Structured invoke_agent results
  6. Progress streaming

Phase 3 (Harden):
  7. Connect resilience module
  8. Structured state object
  9. Fix broken metrics endpoint
```

---

## Summary

The core issue is not that the tools are "basic" — it's that the **feedback loop is open during generation**. The agent writes files and never checks them. Validation only happens after the agent is done, when it's too late for efficient correction.

The suggestion.txt is right: this should be a closed-loop system where every action is verified before moving to the next. The top 4 priorities above convert the system from open-loop to closed-loop without requiring architectural rewrites.
