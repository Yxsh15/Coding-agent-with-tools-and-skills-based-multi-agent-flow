POC objective: replicate **behavioral primitives**, not full system. Strip everything to the minimum that proves:

* agent can **self-drive via tools**
* agent can **edit via diffs**
* agent can **manage state via filesystem**
* agent can **delegate via sub-agents**
* system can **visualize execution (chat + tool trace)**

---

# POC: AgentFS App Builder (Minimal Claude-Code Style)

## Scope Boundary

Build only:

* solution → objects → pages (skip workflows, automation, roles)
* JSON + simple UI schema (no compilers)
* single workspace per app

Everything else is noise.

---

# System Architecture (POC)

## Core Loop (non-negotiable)

```python
while True:
    response = llm(messages, tools)

    if response.tool_calls:
        for call in response.tool_calls:
            result = execute_tool(call)
            messages.append(tool_result(result))
    else:
        break
```

No phases. No executor branching. No explore.

---

# Required Components

## 1. AgentFS (must stay)

Minimal version:

```
/workspace/{app}/
├── solution.md
├── objects/
│   └── order.json
├── pages/
│   └── order_list.json
└── .internal/
    ├── todos.json
    └── logs.json
```

### Implement only:

* `list_files(path)`
* `read_file(path)`
* `write_file(path)`
* `apply_diff(path, diff)`  ← critical
* `search(query)`

No SQLite required for POC. Use local FS.

---

## 2. Tool Layer (Claude-style)

### Mandatory tools

#### 1. Bash (restricted)

* allow:

  * python execution
  * file ops
* block:

  * network
  * system-level commands

#### 2. Edit (Unified Diff)

Input:

```
--- a/file.json
+++ b/file.json
@@
- "status": "pending"
+ "status": "active"
```

Backend:

* apply patch
* validate JSON
* reject if broken

---

#### 3. Read (token safe)

```python
read_file(path, start=None, end=None)
```

Add:

* auto truncate (2–4k tokens)
* optional summary mode

---

#### 4. Grep / Search

```python
search(query, path=None)
```

Return:

* file
* line snippet

No embeddings. Pure string search.

---

#### 5. Todos (stateful planning)

Stored in:

```
.internal/todos.json
```

Structure:

```json
[
  {"id": 1, "task": "Create objects", "status": "pending"},
  {"id": 2, "task": "Create pages", "status": "done"}
]
```

No backend enforcement. Model updates it.

---

#### 6. Tasks (Sub-agents)

```python
run_task(name, instructions, context_paths)
```

* new loop
* restricted tools
* returns final output only

Use cases:

* generate one page
* analyze file
* validate

---

#### 7. Skills (mandatory)

Folder:

```
skills/
├── core.md
├── app_builder.md
├── json_rules.md
```

Loaded into system prompt.

No dynamic composition engine. Just concatenate.

---

## 3. YAML Config (mandatory but minimal)

```yaml
agent:
  name: app_builder
  max_turns: 20

model:
  name: gpt-5.3
  temperature: 0.2

tools:
  - read_file
  - write_file
  - apply_diff
  - search
  - bash
  - todos
  - run_task

agentfs:
  root: ./workspace
```

---

## 4. System Prompt (critical)

Single prompt:

* workspace structure
* tool rules
* diff format rules
* ordering hint

Example core:

```
You are building an application inside a filesystem.

Rules:
- Never rewrite full files unless creating new ones
- Use apply_diff for edits
- Always validate JSON after edits
- Use search before reading multiple files
- Maintain todos.json
- Prefer small changes over large rewrites

Order:
1. Read solution.md
2. Create objects
3. Create pages
4. Validate
```

---

# Agent Flow (POC)

## Input

User provides:

```
solution.md
```

## Loop behavior

Model does:

1. `read_file(solution.md)`
2. `write_file(objects/order.json)`
3. `read_file(objects/order.json)`
4. `apply_diff(...)`
5. `search("order")`
6. `run_task("generate_page", ...)`
7. `write_file(pages/order_list.json)`
8. updates todos
9. stops

---

# Agentic Chat UI (Required)

## Display layers

### 1. Messages

* user
* assistant

### 2. Tool Calls (timeline)

```
[12:01:02] read_file("/solution.md") → 120ms
[12:01:04] write_file("/objects/order.json") → 80ms
[12:01:07] apply_diff("/objects/order.json") → 95ms
```

### 3. Sub-agent trace

```
Main Agent → run_task(generate_page)

  Sub-agent:
    read_file(...)
    write_file(...)
    done
```

### 4. Duration per step

Track:

* start_time
* end_time

---

## UI Stack

* React
* SSE or websocket stream
* simple vertical timeline

---

# Implementation Plan

## Phase 1 — Core Loop (1–2 days)

* implement while loop
* integrate tool calling
* log tool events

---

## Phase 2 — AgentFS (1–2 days)

* file-based storage
* path isolation per app
* basic CRUD tools

---

## Phase 3 — Diff Engine (2 days)

* parse unified diff
* apply safely
* JSON validation

---

## Phase 4 — Tooling (2–3 days)

* search
* todos
* run_task (sub-agent loop)

---

## Phase 5 — Skills + Prompt (1 day)

* static skill loading
* system prompt assembly

---

## Phase 6 — UI (2–3 days)

* chat
* tool timeline
* sub-agent nesting

---

## Phase 7 — Demo Scenario (1 day)

Test with:

“Supply Chain Disruption Monitoring”

Validate:

* objects created
* pages created
* edits via diff
* no full rewrites

---

# What This POC Must Prove

1. Model can navigate filesystem without explore phase
2. Model prefers diff over rewrite
3. Sub-agents reduce context pressure
4. Workspace replaces conversation history
5. Tool loop is stable over 10–20 turns

---

# What NOT to Build

* no PDP pipeline
* no validators beyond JSON
* no SQLite
* no multi-agent orchestration layer
* no compilers

---

# Model Choice for POC

Use: **Gemini 3.1 pro**

Reason:

* strongest tool loop stability
* best diff adherence
* lowest failure rate in long loops

---

# Failure Signals (kill criteria)

* model rewrites full files repeatedly
* model loops without convergence
* diff application breaks frequently
* excessive file reads (>20 per task)

---

# Success Signal

Agent completes:

* full app skeleton
* with <15 tool calls
* using diff edits
* without predefined phases

End state: validated minimal replica of Claude-style agent behavior, directly portable into main system.
