# Multi-Agent System Architecture

> **Complete technical documentation of the AI-powered web application builder**  
> Generated: March 2026

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture Diagram](#architecture-diagram)
3. [Multi-Agent Orchestration](#multi-agent-orchestration)
4. [Agent Delegation Pattern](#agent-delegation-pattern-important)
5. [Agent Implementations](#agent-implementations)
6. [Tools System](#tools-system)
7. [Skills System](#skills-system)
8. [LLM Integration](#llm-integration)
9. [Validation & Repair Mechanisms](#validation--repair-mechanisms)
10. [Streaming & Event System](#streaming--event-system)
11. [Session & Storage Management](#session--storage-management)
12. [Data Flow Diagrams](#data-flow-diagrams)
13. [Configuration Reference](#configuration-reference)
14. [Where Agents Write Output](#where-agents-write-output)
15. [Complete Repair Flow](#complete-repair-flow)

---

## System Overview

This is a **multi-agent AI system** that generates functional web applications from natural language descriptions. The system uses Google Gemini as the LLM backbone and implements an orchestrator pattern where a central agent coordinates specialist sub-agents.

### Key Characteristics

| Aspect | Implementation |
|--------|----------------|
| **Architecture Pattern** | Single orchestrator with specialist sub-agents |
| **LLM Provider** | Google Gemini (gemini-3-pro-preview) |
| **Communication** | Server-Sent Events (SSE) streaming |
| **Storage** | SQLite (sessions) + Virtual Filesystem (AgentFS) |
| **Validation** | Automated 7-check validation with one-shot repair |
| **Frontend** | React + Vite |
| **Backend** | FastAPI (Python) |

### Core Components

```
┌─────────────────────────────────────────────────────────────────────┐
│                         FRONTEND (React + Vite)                     │
│   ChatPanel │ WorkspacePreview │ RunTimeline │ SessionSidebar       │
└─────────────────────────────┬───────────────────────────────────────┘
                              │ SSE Stream
┌─────────────────────────────▼───────────────────────────────────────┐
│                          FASTAPI BACKEND                            │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────────┐ │
│  │    main.py   │  │ AgentRunner  │  │      Session Store         │ │
│  │  (Endpoints) │──│ (Execution)  │──│   (SQLite Persistence)     │ │
│  └──────────────┘  └──────┬───────┘  └────────────────────────────┘ │
│                           │                                         │
│  ┌────────────────────────▼─────────────────────────────────────┐   │
│  │                    AGENT LAYER                                │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐           │   │
│  │  │ Orchestrator│──│Page Builder│──│  Validator  │           │   │
│  │  │   Agent     │  │   Agent     │  │   Agent     │           │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘           │   │
│  │         │                                                     │   │
│  │  ┌──────▼──────┐  ┌─────────────┐  ┌─────────────┐           │   │
│  │  │Object Builder│ │   Skills    │  │   ToolBox   │           │   │
│  │  │   Agent     │  │   Bundle    │  │  (11 tools) │           │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘           │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                           │                                         │
│  ┌────────────────────────▼─────────────────────────────────────┐   │
│  │                      AgentFS                                  │   │
│  │     Virtual Filesystem per App (workspace/app_{id}/)          │   │
│  └───────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Architecture Diagram

### Request Flow

```
User Prompt
    │
    ▼
┌───────────────────────────────────────────────────────────────────┐
│ GET /api/runs/stream?prompt={prompt}&app_id={app_id}              │
└───────────────────────────────────────────────────────────────────┘
    │
    ▼
┌───────────────────────────────────────────────────────────────────┐
│ AgentRunner.run_stream()                                          │
│   • Creates event queue (asyncio.Queue)                           │
│   • Spawns background worker task                                 │
│   • Yields SSE events to client                                   │
└───────────────────────────────────────────────────────────────────┘
    │
    ▼
┌───────────────────────────────────────────────────────────────────┐
│ AgentRunner._run_loop(agent_name="orchestrator")                  │
│   1. Load agent profile from agents/orchestrator/config.yaml      │
│   2. Load skill bundle (core.md + app_builder.md)                 │
│   3. Build system prompt                                          │
│   4. Create GeminiModel instance                                  │
│   5. Execute agentic loop (up to max_turns)                       │
│   6. Return final message                                         │
└───────────────────────────────────────────────────────────────────┘
    │
    ▼
┌───────────────────────────────────────────────────────────────────┐
│ Validation & Repair                                               │
│   • Run 7 validation checks                                       │
│   • If issues found → one repair pass                             │
│   • Emit final workspace snapshot                                 │
└───────────────────────────────────────────────────────────────────┘
    │
    ▼
┌───────────────────────────────────────────────────────────────────┐
│ SSE Stream → Browser                                              │
│   Events: agent_started, message_chunk, tool_started, etc.        │
└───────────────────────────────────────────────────────────────────┘
```

---

## Multi-Agent Orchestration

### Orchestration Pattern

The system uses a **hub-and-spoke orchestration pattern**:

```
                    ┌─────────────────┐
                    │   User Prompt   │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   Orchestrator   │ ◄─── Main coordinator
                    │     Agent        │      (temperature: 0.7)
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
    ┌────▼────┐        ┌─────▼─────┐       ┌────▼────┐
    │ Object  │        │   Page    │       │Validator│
    │ Builder │        │  Builder  │       │ Agent   │
    │ (0.5)   │        │  (0.5)    │       │ (0.3)   │
    └─────────┘        └───────────┘       └─────────┘
```

### How Agents Are Coordinated

1. **Initial Request**: User sends a prompt to the orchestrator
2. **Planning Phase**: Orchestrator writes `solution.md` documenting architecture
3. **Execution Phase**: Orchestrator creates files or invokes specialists
4. **Sub-agent Delegation**: Via `invoke_agent` tool:
   ```python
   tool_call = {
       "tool": "invoke_agent",
       "payload": {
           "agent": "page_builder",
           "instructions": "Create the dashboard page",
           "context_paths": ["pages/dashboard.html"]  # Optional scope
       }
   }
   ```
5. **Sub-agent Execution**: Specialist runs isolated `_run_loop()` with its own config
6. **Result Aggregation**: Sub-agent result returned to orchestrator
7. **Validation**: After orchestrator finishes, automated validation runs
8. **Repair**: If issues found, one repair pass is executed

---

## Agent Delegation Pattern (IMPORTANT)

### LLM-Guided Complexity Decision

**This system does NOT use a separate backend route for complex apps.**

Instead, the **orchestrator LLM decides** whether the request is simple or complex from the prompt plus the current workspace state.

```
✅ simple request → orchestrator may build directly
✅ complex request → orchestrator must follow the staged specialist workflow
```

### How It Actually Works

```
┌─────────────────────────────────────────────────────────────────────┐
│                    USER REQUEST                                      │
│              "Build a task manager app"                             │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR STARTS                               │
│                                                                       │
│  System prompt requires an explicit decision gate:                   │
│  "Decide simple vs complex before substantial implementation work"   │
│                                                                       │
│  Available tools include: invoke_agent                               │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    LLM DECIDES (not hardcoded)                       │
│                                                                       │
│  Option A: Simple app                                                │
│  ├── Write solution.md                                               │
│  ├── Write index.html                                                │
│  ├── Write styles.css                                                │
│  ├── Write app.js                                                    │
│  └── Done (NO sub-agents invoked)                                    │
│                                                                       │
│  Option B: Complex app needing specialists                           │
│  ├── Write solution.md                                               │
│  ├── invoke_agent(object_builder, "define objects/") ────────┐ │    │
│  │   └── object_builder creates object artifacts             │ │    │
│  ├── invoke_agent(validator, "review solution + objects") ───┼─┘    │
│  ├── apply fixes using diffs or specialist repairs           │        │
│  ├── invoke_agent(page_builder, "build pages/") ─────────────┐       │
│  │   └── page_builder creates page artifacts                │       │
│  ├── Write remaining root files                             │       │
│  ├── invoke_agent(validator, "review integrated app") ──────┘       │
│  └── Done                                                          │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    VALIDATION (always runs)                          │
│                                                                       │
│  7 automated checks → issues found?                                  │
│  ├── YES: Orchestrator runs repair pass (not validator agent)       │
│  └── NO: Success                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Points

| Aspect | How It Works |
|--------|--------------|
| **Delegation decision** | LLM decides at runtime whether the request is simple or complex |
| **Who can delegate** | Only orchestrator (has `invoke_agent` tool) |
| **Sub-agent recursion** | Sub-agents CANNOT invoke other agents |
| **Validation agent** | Invoked by orchestrator during complex flows; remains read-only |
| **Repair agent** | Uses orchestrator again, not a separate agent |

### Why This Design?

1. **Simple tasks don't need overhead**: A "hello world" app skips sub-agents entirely
2. **LLM can reason about needs**: A calculator stays direct, while a multi-page e-commerce build triggers staged delegation.
3. **Complex flows still get structure**: Once the orchestrator decides "complex", object modeling and validation are no longer optional.
4. **Flexibility**: New agent types can be added without adding a new request route

### Tool Availability Per Agent

```
┌────────────────────┬──────────────────────────────────────────────────┐
│      Agent         │  invoke_agent tool?                              │
├────────────────────┼──────────────────────────────────────────────────┤
│  orchestrator      │  ✅ YES - can delegate to any specialist        │
│  object_builder    │  ❌ NO  - isolated execution, returns to caller │
│  page_builder      │  ❌ NO  - isolated execution, returns to caller │
│  validator         │  ❌ NO  - read-only checks, returns to caller   │
└────────────────────┴──────────────────────────────────────────────────┘
```

### Example: When Orchestrator Delegates

The LLM might emit this tool call:

```json
{
  "tool": "invoke_agent",
  "payload": {
    "agent": "page_builder",
    "instructions": "Create a responsive dashboard page with charts section, stats cards, and navigation sidebar",
    "context_paths": ["solution.md", "styles.css"]
  }
}
```

**What happens:**
1. `tools.py` receives the call
2. Calls `runner.invoke_agent()` 
3. Loads `page_builder` profile (lower temperature, fewer tools)
4. Runs `_run_loop()` with isolated prompt
5. Page builder creates files, returns summary
6. Orchestrator continues with result

### Typical Execution Patterns

**Pattern 1: Simple App (no delegation)**
```
orchestrator
├── write_file(solution.md)
├── write_file(index.html)
├── write_file(styles.css)
├── write_file(app.js)
└── done
```

**Pattern 2: Complex App (mandatory staged delegation after LLM classifies complexity)**
```
orchestrator
├── write_file(solution.md)
├── invoke_agent(object_builder) → creates objects/**
├── invoke_agent(validator) → reviews solution + objects
├── apply_diff / specialist repair
├── invoke_agent(page_builder) → creates pages/**
├── write_file(index.html)
├── write_file(styles.css)
├── write_file(app.js)
├── invoke_agent(validator) → reviews integrated app
└── done
```

**Pattern 3: With Validation Repair**
```
orchestrator
├── [creates files]
├── done (has issues)
│
[VALIDATION RUNS - code, not agent]
├── Issues found: missing readyState
│
orchestrator (REPAIR)  ← Same agent, repair prompt
├── read_file(app.js)
├── write_file(app.js)  ← fixed
└── done
│
[RE-VALIDATION]
└── Passed ✓
```

### Agent Hierarchy

| Agent | Role | Temperature | Can Invoke Others |
|-------|------|-------------|-------------------|
| **Orchestrator** | Main coordinator | 0.7 | Yes |
| **Object Builder** | Domain object generation and repair | 0.5 | No |
| **Page Builder** | Page artifact generation and repair | 0.5 | No |
| **Validator** | Read-only validation | 0.3 | No |

---

## Agent Implementations

### Agent Profile Structure

Each agent is configured via a YAML file in `agents/{name}/config.yaml`:

```yaml
# agents/orchestrator/config.yaml
name: orchestrator
role: orchestrator  # Special flag for prompt customization
model:
  provider: google
  name: gemini-3-pro-preview
  temperature: 0.7
  max_output_tokens: 8192
  top_p: 0.95
  top_k: 40
tools:
  - read_file
  - write_file
  - apply_diff
  - grep
  - glob
  - bash
  - todos
  - web_search
  - web_fetch
  - invoke_agent  # Key: enables sub-agent coordination
skills:
  - core
  - app_builder
```

### Agent Configuration Comparison

| Agent | Temperature | Tools | Skills |
|-------|-------------|-------|--------|
| **Orchestrator** | 0.7 | All 11 tools | core, app_builder |
| **Object Builder** | 0.5 | 8 tools (no web, no invoke) | core, json_rules, app_builder |
| **Page Builder** | 0.5 | 6 tools (page-focused file ops + diffs) | core, app_builder |
| **Validator** | 0.3 | 5 tools (read-only focus) | core, json_rules |

### Agent Loading Process

```python
# backend/app/services/agent_registry.py

class AgentRegistry:
    def load_profile(self, name: str) -> AgentProfile:
        """Load agent configuration from YAML"""
        config_path = Path(f"agents/{name}/config.yaml")
        config = yaml.safe_load(config_path.read_text())
        return AgentProfile(
            name=config["name"],
            role=config["role"],
            model_name=config["model"]["name"],
            temperature=config["model"]["temperature"],
            # ... other fields
        )
    
    def load_skill_bundle(self, skills: list[str]) -> str:
        """Concatenate skill markdown files"""
        bundle_parts = []
        for skill_name in skills:
            skill_path = Path(f"skills/{skill_name}.md")
            bundle_parts.append(skill_path.read_text())
        return "\n\n".join(bundle_parts)
```

---

## Tools System

### Overview

The ToolBox class provides 11 tools that agents can invoke during execution. Each tool is:
- Whitelisted per agent via config.yaml
- Emits start/finish events for real-time tracking
- Returns structured results to the LLM

### Tool Categories

#### File Operations

| Tool | Parameters | Description |
|------|------------|-------------|
| `read_file` | `path`, `start?`, `end?`, `summary?` | Read workspace files with optional line ranges |
| `write_file` | `path`, `content` | Create/overwrite files (validates JSON) |
| `apply_diff` | `path`, `diff` | Apply unified diff patches |

#### Search & Discovery

| Tool | Parameters | Description |
|------|------------|-------------|
| `grep` | `query`, `path?` | Search patterns in files |
| `glob` | `pattern`, `path?` | List files matching glob patterns |

#### Execution

| Tool | Parameters | Description |
|------|------------|-------------|
| `bash` | `command` | Execute whitelisted bash commands |

**Allowed bash commands**: `python3`, `python`, `ls`, `pwd`, `echo`, `cat`, `mkdir`, `find`, `head`

**Blocked**: `curl`, `wget`, any HTTP URLs

#### Task Management

| Tool | Parameters | Description |
|------|------------|-------------|
| `todos` | `action`, `items?`, `id?` | Manage task list in `.internal/todos.json` |

Actions: `replace`, `mark_done`, `mark_in_progress`

#### Web Integration

| Tool | Parameters | Description |
|------|------------|-------------|
| `web_search` | `query` | Search local knowledge base |
| `web_fetch` | `url` | Fetch document from knowledge base |

#### Agent Coordination

| Tool | Parameters | Description |
|------|------------|-------------|
| `invoke_agent` | `agent`, `instructions`, `context_paths?` | Call a specialist sub-agent |

### Tool Execution Flow

```python
# backend/app/services/tools.py

async def execute(self, app_id, profile, agent_name, tool_name, payload, emit):
    # 1. Validate tool is enabled for this agent
    if tool_name not in profile.tools:
        raise ValueError(f"Tool '{tool_name}' not enabled for '{profile.name}'")
    
    # 2. Emit start event
    await emit("tool_started", {
        "agent": agent_name,
        "tool": tool_name,
        "input": payload,
        "started_at": time.perf_counter()
    })
    
    # 3. Execute tool handler
    result = await getattr(self, tool_name)(app_id, agent_name, payload, emit)
    
    # 4. Emit finish event
    await emit("tool_finished", {
        "agent": agent_name,
        "tool": tool_name,
        "output": result,
        "duration_ms": elapsed_ms
    })
    
    return result
```

### Diff Failure Escalation

The system tracks failed `apply_diff` attempts and progressively escalates:

```python
# First failure
"{error}. Read the latest version of {path} and use write_file to rewrite the full file."

# Second+ failure  
"{error}. apply_diff has failed {count} times for {path}. 
 Stop retrying the diff and rewrite the file with write_file instead."
```

---

## Skills System

### What Are Skills?

Skills are **markdown instruction files** that shape agent behavior. They are:
- Loaded at runtime from `/skills/` directory
- Concatenated into a "skill bundle"
- Injected into the system prompt

### Available Skills

#### core.md - Foundation Principles

```markdown
# Core Skill

You are an app-building coding agent operating inside a filesystem workspace. 
You generate REAL, WORKING CODE.

## Principles:
- Use a single tool-driven loop
- ALWAYS generate complete, functional code - never stubs or placeholders
- Write clean, well-commented code
- Update todos as you make progress
- Keep artifacts inspectable by humans
- When modifying existing files, prefer unified diffs
- Delegate narrow tasks to sub-agents when they can work with less context

## Code Quality Standards:
- Use modern JavaScript (ES6+)
- Use semantic HTML5
- Use CSS custom properties and modern layout techniques
- Add meaningful comments
- Handle edge cases and errors
- Make UI responsive and accessible
```

#### app_builder.md - Web Application Workflow

```markdown
# App Builder Skill

You transform a product idea into a fully working web application.

## Workflow:
1. Write `solution.md` documenting the app architecture
2. Create `index.html` with semantic HTML structure
3. Create `styles.css` with modern, responsive styling
4. Create `app.js` with full JavaScript functionality
5. Add any additional pages as needed
6. Test the logic and ensure consistency

## Code Generation Requirements:

### HTML (index.html):
- Use semantic HTML5 elements
- Include proper meta tags and viewport
- Link CSS and JS correctly

### CSS (styles.css):
- Use CSS custom properties
- Implement responsive design
- Use CSS Grid and/or Flexbox

### JavaScript (app.js):
- Use modern ES6+ syntax
- Implement CRUD operations
- Add event listeners
```

#### json_rules.md - JSON Handling Standards

Defines JSON file handling: 2-space indentation, stable keys, validation requirements.

### How Skills Are Loaded

```python
def load_skill_bundle(skills: list[str]) -> str:
    """
    Load and concatenate skill files into a single instruction bundle.
    
    Example:
        skills = ["core", "app_builder"]
        Returns: content of core.md + "\n\n" + content of app_builder.md
    """
    bundle_parts = []
    for skill_name in skills:
        skill_path = Path(f"skills/{skill_name}.md")
        bundle_parts.append(skill_path.read_text())
    return "\n\n".join(bundle_parts)
```

### Skills Assignment Per Agent

| Agent | Skills | Purpose |
|-------|--------|---------|
| Orchestrator | core, app_builder | Full app generation |
| Object Builder | core, json_rules, app_builder | JSON-focused with structure |
| Page Builder | core, app_builder | Page generation |
| Validator | core, json_rules | Validation focus |

---

## LLM Integration

### GeminiModel Class

The LLM integration is handled by the `GeminiModel` class in `backend/app/services/llm.py`:

```python
class GeminiModel:
    def __init__(self, prompt, app_id, system_prompt, tools, config):
        self.client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        self.prompt = prompt
        self.system_prompt = system_prompt
        self.tools = tools
        self.config = config
        self.chat = None  # Initialized on first generate()
        
    def _init_conversation(self):
        """Create Gemini chat session with config"""
        config = GenerateContentConfig(
            system_instruction=self.system_prompt,
            temperature=self.config.temperature,
            max_output_tokens=self.config.max_output_tokens,
            tools=build_tools_schema(self.tools),
        )
        self.chat = self.client.chats.create(
            model=self.config.model,
            config=config,
            history=[]
        )
        
    def generate(self) -> tuple[str | None, list[dict] | None]:
        """
        Send message to Gemini and parse response.
        
        Returns:
            - (message, None) for text responses
            - (None, tool_calls) for function call requests
        """
        if not self.chat:
            self._init_conversation()
            
        response = self.chat.send_message(self.pending_message)
        
        # Parse response parts
        tool_calls = [
            {"tool": fc.name, "payload": dict(fc.args)}
            for fc in response.function_calls
        ]
        
        if tool_calls:
            return (None, tool_calls)
        return (response.text, None)
        
    def add_tool_outputs(self, outputs: list[dict]):
        """Queue tool results for next generate() call"""
        parts = [
            Part.from_function_response(
                name=output["tool"],
                response=output["response"]
            )
            for output in outputs
        ]
        self.pending_message = parts
```

### Tool Schema Generation

```python
def build_tools_schema(tools: list[str]) -> list[types.Tool]:
    """Convert tool names to Gemini function declarations"""
    declarations = []
    for tool_name in tools:
        schema = TOOL_SCHEMAS.get(tool_name)
        if schema:
            declarations.append(types.Tool(
                function_declarations=[schema]
            ))
    return declarations
```

### LLM Configuration

```python
@dataclass
class LLMConfig:
    model: str = "gemini-3-pro-preview"
    temperature: float = 0.7        # Creativity (0.3-0.7 per agent)
    max_output_tokens: int = 8192   # Response length limit
    top_p: float = 0.95             # Nucleus sampling
    top_k: int = 40                 # Top-k sampling
```

---

## Validation & Repair Mechanisms

### Validation Pipeline

After the orchestrator completes, the system runs **7 automated validation checks**:

```python
def _validate_generated_app(self, app_id: str) -> list[str]:
    issues = []
    files = set(self.agentfs.list_files(app_id))
    
    # 1. Required files check
    for required in ("index.html", "styles.css", "app.js"):
        if required not in files:
            issues.append(f"Missing required file: {required}")
    
    # 2. Asset reference check
    html = self.agentfs.read_file(app_id, "index.html")
    for match in LOCAL_ASSET_PATTERN.finditer(html):
        asset_path = match.group(1)
        if asset_path not in files:
            issues.append(f"Missing local asset: {asset_path}")
    
    # 3. Script loading check
    if "<script" not in html.lower():
        issues.append("index.html does not load any script")
    
    # 4. Interactive wiring check
    js = self.agentfs.read_file(app_id, "app.js")
    if has_interactive_html(html):
        if not has_event_binding(js):
            issues.append("app.js does not bind interactive behavior")
    
    # 5. DOMContentLoaded robustness check
    if "DOMContentLoaded" in js and "readyState" not in js:
        issues.append("Missing document.readyState fallback")
    
    # 6. JavaScript syntax check
    result = subprocess.run(["node", "--check", "app.js"])
    if result.returncode != 0:
        issues.append(f"JavaScript syntax error: {result.stderr}")
    
    # 7. Interactive HTML without JS check
    if has_interactive_html(html) and "app.js" not in files:
        issues.append("Interactive HTML without app.js")
    
    return issues
```

### Repair Flow

```
┌─────────────────┐
│ Generate App    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Run Validation  │
│ (7 checks)      │
└────────┬────────┘
         │
    ┌────▼────┐
    │ Issues? │
    └────┬────┘
         │
    Yes  │  No
    ─────┼──────
         │      │
         ▼      ▼
┌─────────────┐  ┌─────────────┐
│Build Repair │  │ Validation  │
│  Prompt     │  │   Passed!   │
└──────┬──────┘  └─────────────┘
       │
       ▼
┌─────────────────┐
│ ONE Repair Pass │
│ (orchestrator)  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Re-validate     │
└────────┬────────┘
         │
    ┌────▼────┐
    │ Issues? │
    └────┬────┘
         │
    Yes  │  No
    ─────┼──────
         │      │
         ▼      ▼
┌─────────────┐  ┌─────────────┐
│Report Issues│  │   Success!  │
│ to User     │  └─────────────┘
└─────────────┘
```

### Repair Prompt Construction

```python
def _build_repair_prompt(self, original_prompt: str, issues: list[str]) -> str:
    return (
        f"Repair the existing generated app for this request: {original_prompt}\n\n"
        "Validation found these issues:\n"
        + "\n".join(f"- {issue}" for issue in issues)
        + "\n\nRequirements:\n"
        "- Read the current files before editing.\n"
        "- Prefer write_file over repeated apply_diff retries.\n"
        "- Keep the existing design unless a functional fix requires a layout change.\n"
        "- Ensure the app is usable in the live preview.\n"
        '- Use robust JS startup: if document.readyState !== "loading", '
        'run init immediately, otherwise attach DOMContentLoaded.\n'
        "- Finish with a concise summary of what you fixed."
    )
```

---

## Streaming & Event System

### Server-Sent Events (SSE)

The system uses SSE for real-time communication:

```python
# main.py endpoint
@app.get("/api/runs/stream")
async def stream_run(prompt: str, app_id: str):
    return StreamingResponse(
        runner.run_stream(prompt, app_id),
        media_type="text/event-stream"
    )
```

### Event Types

| Event | Payload | Description |
|-------|---------|-------------|
| `agent_started` | `{agent, prompt, model, temperature, tools, skills, is_subagent}` | Agent begins execution |
| `message_chunk` | `{message_id, agent, role, delta, content}` | Streaming text chunk |
| `message` | `{message_id, agent, role, content}` | Complete message |
| `tool_started` | `{agent, tool, input, started_at}` | Tool execution begins |
| `tool_finished` | `{agent, tool, input, output/error, duration_ms}` | Tool execution complete |
| `agent_finished` | `{agent, final_message, duration_ms, is_subagent}` | Agent completes |
| `workspace` | `{app_id, entries, files}` | Workspace file snapshot |
| `final` | `{message}` | Run complete |
| `status` | `{message: "heartbeat"}` | Keep-alive ping |
| `error` | `{message}` | Error occurred |

### Message Chunking (Typing Effect)

```python
# backend/app/services/streaming.py

async def stream_message(emit, agent, role, content):
    """Stream message with typing animation effect"""
    message_id = str(uuid.uuid4())
    streamed = ""
    
    for chunk in iter_text_chunks(content, chunk_size=32):
        streamed += chunk
        await emit("message_chunk", {
            "message_id": message_id,
            "agent": agent,
            "role": role,
            "delta": chunk,
            "content": streamed
        })
        await asyncio.sleep(0.018)  # 18ms delay for typing effect
    
    # Emit final complete message
    await emit("message", {
        "message_id": message_id,
        "agent": agent,
        "role": role,
        "content": content
    })
    
    return message_id
```

---

## Session & Storage Management

This section details how SQLite persists session data and how agents write output to the filesystem.

### SQLite Session Store

The `SessionStore` class in `backend/app/services/session_store.py` manages persistent chat history using SQLite with WAL mode for concurrent access.

#### Database Location

```
.storage/chat_sessions.sqlite3
```

#### Schema Design

```sql
-- Core session metadata
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,              -- 12-char UUID (e.g., "a1b2c3d4e5f6")
    title TEXT NOT NULL,              -- Auto-generated from first prompt
    app_id TEXT NOT NULL UNIQUE,      -- "app_{session_id}"
    created_at TEXT NOT NULL,         -- ISO8601 timestamp
    updated_at TEXT NOT NULL,         -- Last activity
    last_prompt TEXT NOT NULL,        -- Most recent user message
    last_message_preview TEXT NOT NULL -- Preview for session list
);

-- Chat history (user/assistant/system messages)
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,               -- "user", "assistant", "system"
    agent TEXT NOT NULL,              -- "orchestrator", "page_builder", etc.
    content TEXT NOT NULL,            -- Full message text
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Workspace snapshots for session restoration
CREATE TABLE apps (
    session_id TEXT PRIMARY KEY,
    app_id TEXT NOT NULL UNIQUE,
    entries_json TEXT NOT NULL,       -- Directory tree as JSON
    files_json TEXT NOT NULL,         -- File contents as JSON
    updated_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Execution trace for debugging/replay
CREATE TABLE trace_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,         -- "agent_started", "tool_started", etc.
    payload_json TEXT NOT NULL,       -- Event data as JSON
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
```

#### Session Lifecycle

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SESSION CREATION                                  │
└─────────────────────────────────────────────────────────────────────┘

1. User starts new chat
   │
   ▼
┌─────────────────────────────────────────────────────────────────────┐
│ session_store.create_session(first_prompt)                          │
│   • Generate session_id = uuid4().hex[:12]  (e.g., "d66cae8cbe53")  │
│   • Derive app_id = f"app_{session_id}"     (e.g., "app_d66cae8cbe53")│
│   • Auto-generate title from prompt (max 48 chars)                   │
│   • INSERT INTO sessions (...)                                       │
│   • INSERT INTO apps (session_id, app_id, '[]', '[]', ...)          │
└─────────────────────────────────────────────────────────────────────┘
```

#### Key Operations

```python
# backend/app/services/session_store.py

class SessionStore:
    def create_session(self, first_prompt: str | None = None) -> dict:
        """Create new session with derived app_id"""
        session_id = uuid4().hex[:12]
        app_id = f"app_{session_id}"
        # INSERT session + empty app workspace
        
    def add_message(self, session_id: str, role: str, agent: str, content: str):
        """Persist chat message and update session timestamps"""
        # INSERT INTO messages
        # UPDATE sessions SET updated_at, last_prompt/last_message_preview
        
    def save_workspace(self, session_id: str, app_id: str, entries: list, files: list):
        """Archive workspace snapshot for session restoration"""
        # UPSERT INTO apps (entries_json, files_json)
        
    def add_trace_event(self, session_id: str, event_type: str, payload: dict):
        """Log execution events for debugging/replay"""
        # INSERT INTO trace_events
        
    def get_session_detail(self, session_id: str) -> dict:
        """Retrieve full session with messages, trace, and workspace"""
        # JOIN sessions + messages + apps + trace_events
```

#### Stream Observer Pattern

The `observe_stream` callback in `main.py` persists events to SQLite in real-time:

```python
# backend/app/main.py

async def observe_stream(event: dict) -> None:
    event_type = event["type"]
    payload = event["payload"]
    
    if event_type == "message":
        # Persist completed messages
        session_store.add_message(
            session_id,
            payload.get("role", "assistant"),
            payload.get("agent", "assistant"),
            payload.get("content", ""),
        )
    
    elif event_type in {"agent_started", "agent_finished", "tool_started", "tool_finished"}:
        # Log execution trace
        session_store.add_trace_event(session_id, event_type, payload)
    
    elif event_type == "workspace":
        # Archive workspace snapshot
        session_store.save_workspace(
            session_id,
            payload["app_id"],
            payload.get("entries", []),
            payload.get("files", []),
        )
    
    elif event_type == "final":
        # Final workspace sync on completion
        workspace = _live_workspace_payload(target_app_id)
        session_store.save_workspace(session_id, ...)
```

---

### AgentFS (Virtual Filesystem)

The `AgentFS` class in `backend/app/services/agentfs.py` provides a sandboxed filesystem for each app.

#### Directory Structure

Each app gets an isolated workspace under `workspace/`:

```
workspace/
└── app_{session_id}/           # e.g., app_d66cae8cbe53
    ├── .internal/              # System files (hidden from LLM by default)
    │   ├── todos.json          # Task tracking state
    │   └── logs.json           # Execution logs
    ├── objects/                # Data models / JSON schemas
    ├── pages/                  # Additional page files
    ├── solution.md             # Architecture documentation
    ├── index.html              # Main HTML entry point
    ├── styles.css              # CSS styling
    └── app.js                  # JavaScript functionality
```

#### Security: Path Traversal Protection

```python
def resolve_path(self, app_id: str, relative_path: str) -> Path:
    """Secure path resolution - prevents directory traversal attacks"""
    base = self.app_path(app_id)  # workspace/app_{id}
    candidate = (base / relative_path).resolve()
    
    # SECURITY: Ensure resolved path stays within app workspace
    if base not in candidate.parents and candidate != base:
        raise ValueError(f"Path escapes app workspace: {relative_path}")
    
    return candidate
```

Example attacks blocked:
- `../../../etc/passwd` → Rejected
- `foo/../../bar` → Rejected
- `./normal/path.js` → Allowed

#### Key Operations

```python
class AgentFS:
    def __init__(self):
        self.root = Path("workspace")
        self.max_read_chars = 6000  # Prevent LLM context overflow
    
    def ensure_app(self, app_id: str) -> Path:
        """Initialize app workspace with standard directories"""
        app_root = self.app_path(app_id)
        app_root.mkdir(parents=True, exist_ok=True)
        (app_root / ".internal").mkdir(exist_ok=True)
        (app_root / "objects").mkdir(exist_ok=True)
        (app_root / "pages").mkdir(exist_ok=True)
        return app_root
    
    def read_file(self, app_id, path, start=None, end=None, truncate=True):
        """Read file with optional line range and truncation"""
        content = self.resolve_path(app_id, path).read_text()
        
        # Optional line slicing
        if start is not None or end is not None:
            lines = content.splitlines()
            content = "\n".join(lines[start:end])
        
        # Truncation to prevent LLM context overflow
        if truncate and len(content) > self.max_read_chars:
            return content[:self.max_read_chars]
        return content
    
    def write_file(self, app_id, path, content):
        """Atomic write with automatic parent directory creation"""
        target = self.resolve_path(app_id, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    
    def snapshot(self, app_id) -> list[dict]:
        """Return all files for archival to SessionStore"""
        return [
            {"path": path, "content": self.read_file(app_id, path, truncate=False)}
            for path in self.list_files(app_id)
        ]
```

---

### Dual Storage Strategy

The system uses **two complementary storage layers**:

```
┌─────────────────────────────────────────────────────────────────────┐
│                     LIVE FILESYSTEM (AgentFS)                        │
│                                                                       │
│   workspace/app_{id}/                                                │
│   ├── index.html    ◄── Agents write here during execution          │
│   ├── styles.css                                                     │
│   └── app.js                                                         │
│                                                                       │
│   • Primary source during active sessions                            │
│   • Agents read/write directly via tools                             │
│   • Files served for live preview                                    │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                │ snapshot() on "workspace" / "final" events
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     SQLITE ARCHIVE (SessionStore)                    │
│                                                                       │
│   .storage/chat_sessions.sqlite3                                     │
│   └── apps table                                                     │
│       ├── entries_json: ["index.html", "styles.css", ...]           │
│       └── files_json: [{"path": "index.html", "content": "..."}, ...]│
│                                                                       │
│   • Persistent archive for session restoration                       │
│   • Fallback when AgentFS files are cleared                         │
│   • Enables session replay and browser refresh                       │
└─────────────────────────────────────────────────────────────────────┘
```

#### Workspace Restoration Flow

```python
# backend/app/main.py

def _restore_session_workspace(session_id: str, app_id: str) -> None:
    """Restore workspace from SQLite if AgentFS is empty"""
    
    # Check if live workspace has files
    if agentfs.list_files(app_id):
        return  # Already populated, use live files
    
    # Retrieve archived workspace from SQLite
    workspace = session_store.get_workspace_for_session(session_id)
    if not workspace["files"]:
        return  # No archive available
    
    # Hydrate AgentFS from archive
    agentfs.ensure_app(app_id)
    for entry in workspace["entries"]:
        if entry["is_dir"]:
            agentfs.resolve_path(app_id, entry["path"]).mkdir(parents=True, exist_ok=True)
    for file in workspace["files"]:
        agentfs.write_file(app_id, file["path"], file["content"])
```

#### File Read Priority

```python
def _read_workspace_file(app_id: str, relative_path: str) -> str:
    """Read file with fallback to SQLite archive"""
    try:
        # 1. Try live AgentFS first
        return agentfs.read_file(app_id, relative_path, truncate=False)
    except FileNotFoundError:
        # 2. Fallback to SQLite archive
        stored_workspace = session_store.get_workspace_for_app(app_id)
        if stored_workspace:
            for item in stored_workspace["files"]:
                if item["path"] == relative_path:
                    return item["content"]
        raise  # Re-raise if not found anywhere
```

---

## Data Flow Diagrams

### Complete Request Lifecycle

```
┌──────────────────────────────────────────────────────────────────────┐
│                          USER REQUEST                                 │
│                "Build a todo app with categories"                     │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        AGENT RUNNER                                   │
│  1. Create session in SQLite                                         │
│  2. Initialize AgentFS workspace                                     │
│  3. Spawn event queue + worker task                                  │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR AGENT                                 │
│  System Prompt = Role + Skills(core, app_builder) + Guidelines       │
│                                                                       │
│  Loop {                                                              │
│    1. Gemini.generate() → (message | tool_calls)                     │
│    2. If tool_calls:                                                 │
│       - Execute each tool via ToolBox                                │
│       - Pack results → Gemini.add_tool_outputs()                    │
│    3. If message:                                                    │
│       - Stream to UI → break loop                                    │
│  }                                                                   │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
        ▼                       ▼                       ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│    write_file    │  │   invoke_agent   │  │      todos       │
│                  │  │                  │  │                  │
│  Creates:        │  │  Calls sub-agent │  │  Tracks progress │
│  - solution.md   │  │  with isolated   │  │  in workspace    │
│  - index.html    │  │  context         │  │                  │
│  - styles.css    │  │                  │  │                  │
│  - app.js        │  │                  │  │                  │
└──────────────────┘  └──────────────────┘  └──────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      VALIDATION LAYER                                 │
│  Check: files exist, assets valid, JS wired, syntax OK               │
│                                                                       │
│  If issues → ONE repair pass → re-validate                           │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       RESPONSE                                        │
│  - Emit workspace snapshot                                           │
│  - Emit final message                                                │
│  - Persist to SessionStore                                           │
│  - Stream complete                                                   │
└──────────────────────────────────────────────────────────────────────┘
```

### Tool Execution Sequence

```
Orchestrator                     ToolBox                       AgentFS
    │                               │                             │
    │  execute(read_file, {path})   │                             │
    │───────────────────────────────▶                             │
    │                               │                             │
    │                               │  emit(tool_started)         │
    │                               │─────────────────────▶       │
    │                               │                             │
    │                               │      read_file(path)        │
    │                               │─────────────────────────────▶
    │                               │                             │
    │                               │      file_content           │
    │                               │◀─────────────────────────────
    │                               │                             │
    │                               │  emit(tool_finished)        │
    │                               │─────────────────────▶       │
    │                               │                             │
    │       result: file_content    │                             │
    │◀───────────────────────────────                             │
    │                               │                             │
    │  add_tool_outputs([result])   │                             │
    │─────────────▶                 │                             │
    │                               │                             │
    │  Continue generate() loop...  │                             │
```

---

## Configuration Reference

### Backend Configuration (`backend/app/config.py`)

```python
class AgentConfig:
    max_turns: int = 50           # Maximum agentic loop iterations
    default_model: str = "gemini-3-pro-preview"

class UIConfig:
    stream_keepalive_ms: int = 5000  # Heartbeat interval
```

### Agent Configuration Schema

```yaml
# agents/{name}/config.yaml
name: string          # Agent identifier
role: string          # "orchestrator" or "specialist"
model:
  provider: string    # "google"
  name: string        # "gemini-3-pro-preview"
  temperature: float  # 0.0-1.0
  max_output_tokens: int
  top_p: float
  top_k: int
tools: list[string]   # Enabled tool names
skills: list[string]  # Skill files to load
```

### Environment Variables

```bash
GOOGLE_API_KEY=...              # Gemini API key
LLM_LOG_INCLUDE_TEXT=1          # Enable full text logging (debug)
```

---

## Where Agents Write Output

This section traces the complete path from agent generation to file storage.

### Output Destination Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                      AGENT EXECUTION                                │
│                                                                     │
│       Orchestrator decides to create a file:                        │
│  "I need to create index.html with the app structure"               │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      TOOL CALL                                       │
│                                                                       │
│  {                                                                   │
│    "tool": "write_file",                                             │
│    "payload": {                                                      │
│      "path": "index.html",                                           │
│      "content": "<!DOCTYPE html>..."                                 │
│    }                                                                 │
│  }                                                                   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      TOOLBOX EXECUTION                               │
│  backend/app/services/tools.py                                       │
│                                                                       │
│  async def write_file(self, app_id, agent_name, payload, emit):     │
│      content = payload["content"]                                    │
│      path = payload["path"]                                          │
│                                                                       │
│      # Validate JSON files                                           │
│      if Path(path).suffix == ".json":                                │
│          json.loads(content)  # Raises if invalid                    │
│                                                                       │
│      # Write to AgentFS                                              │
│      self.agentfs.write_file(app_id, path, content)                  │
│      return f"wrote {path}"                                          │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      AGENTFS WRITE                                   │
│  backend/app/services/agentfs.py                                     │
│                                                                       │
│  def write_file(self, app_id, relative_path, content):              │
│      # 1. Resolve and validate path (prevent traversal)             │
│      target = self.resolve_path(app_id, relative_path)               │
│                                                                       │
│      # 2. Create parent directories if needed                        │
│      target.parent.mkdir(parents=True, exist_ok=True)                │
│                                                                       │
│      # 3. Atomic write to filesystem                                 │
│      target.write_text(content)                                      │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      FILESYSTEM                                      │
│                                                                       │
│  workspace/app_d66cae8cbe53/index.html                              │
│  ┌───────────────────────────────────────────────────────────┐      │
│  │ <!DOCTYPE html>                                             │      │
│  │ <html lang="en">                                            │      │
│  │ <head>                                                      │      │
│  │   <meta charset="UTF-8">                                    │      │
│  │   <title>My App</title>                                     │      │
│  │   <link rel="stylesheet" href="styles.css">                 │      │
│  │ </head>                                                     │      │
│  │ ...                                                         │      │
│  └───────────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────────┘
```

### Typical Agent Output Sequence

During a typical app generation, the orchestrator creates files in this order:

```
1. solution.md       ← Architecture planning document
2. index.html        ← Main HTML structure
3. styles.css        ← CSS styling
4. app.js            ← JavaScript functionality
5. [additional files as needed]
```

### Output Events Stream

As files are written, events stream to the frontend:

```javascript
// Event 1: Tool starts
{
  "type": "tool_started",
  "payload": {
    "agent": "orchestrator",
    "tool": "write_file",
    "input": {"path": "index.html", "content": "..."},
    "started_at": 1711900800.123
  }
}

// Event 2: Tool completes
{
  "type": "tool_finished",
  "payload": {
    "agent": "orchestrator",
    "tool": "write_file",
    "input": {"path": "index.html", "content": "..."},
    "output": "wrote index.html",
    "duration_ms": 12.5
  }
}

// Event 3: Final workspace snapshot
{
  "type": "workspace",
  "payload": {
    "app_id": "app_d66cae8cbe53",
    "entries": [
      {"path": "index.html", "name": "index.html", "is_dir": false},
      {"path": "styles.css", "name": "styles.css", "is_dir": false},
      {"path": "app.js", "name": "app.js", "is_dir": false}
    ],
    "files": [
      {"path": "index.html", "content": "<!DOCTYPE html>..."},
      {"path": "styles.css", "content": "/* styles */..."},
      {"path": "app.js", "content": "// app logic..."}
    ]
  }
}
```

### Output Persistence to SQLite

After the workspace event, the observer callback archives to SQLite:

```python
# main.py - observe_stream callback
elif event_type == "workspace":
    session_store.save_workspace(
        session_id,
        payload["app_id"],
        payload.get("entries", []),
        payload.get("files", []),
    )
```

This enables session restoration even after server restarts.

---

## Complete Repair Flow

This section details exactly how validation failures trigger repairs and produce correct output.

### Phase 1: Initial Generation

```
┌─────────────────────────────────────────────────────────────────────┐
│                   INITIAL GENERATION                                 │
│                                                                       │
│  User Prompt: "Build a task manager with categories"                 │
│                                                                       │
│  Orchestrator generates:                                             │
│  ├── solution.md      ✓ Created                                     │
│  ├── index.html       ✓ Created (but has missing asset reference)   │
│  ├── styles.css       ✓ Created                                     │
│  └── app.js           ✓ Created (but missing DOMContentLoaded fix)  │
└─────────────────────────────────────────────────────────────────────┘
```

### Phase 2: Automated Validation

```python
# backend/app/services/runner.py

def _validate_generated_app(self, app_id: str) -> list[str]:
    issues = []
    files = set(self.agentfs.list_files(app_id))
    
    # CHECK 1: Required files exist
    for required in ("index.html", "styles.css", "app.js"):
        if required not in files:
            issues.append(f"Missing required file: {required}")
    
    # CHECK 2: Asset references valid
    html = self.agentfs.read_file(app_id, "index.html")
    for match in LOCAL_ASSET_PATTERN.finditer(html):
        asset_path = normalize_path(match.group(1))
        if asset_path and asset_path not in files:
            issues.append(f"index.html references missing asset: {asset_path}")
    
    # CHECK 3: Script tag present
    if "<script" not in html.lower():
        issues.append("index.html does not load any script")
    
    # CHECK 4: Interactive elements wired to JS
    js = self.agentfs.read_file(app_id, "app.js")
    if INTERACTIVE_HTML_PATTERN.search(html):
        if not has_event_binding(js):
            issues.append("app.js does not bind interactive behavior")
    
    # CHECK 5: Robust JS initialization
    if "DOMContentLoaded" in js and "readyState" not in js:
        issues.append("Missing document.readyState fallback for live preview")
    
    # CHECK 6: JavaScript syntax valid
    result = subprocess.run(["node", "--check", "app.js"])
    if result.returncode != 0:
        issues.append(f"JavaScript syntax error: {result.stderr}")
    
    # CHECK 7: Interactive HTML has JS file
    if INTERACTIVE_HTML_PATTERN.search(html) and "app.js" not in files:
        issues.append("Interactive HTML without app.js")
    
    return deduplicate(issues)
```

### Example Validation Output

```
Validation found issues before finish:
- index.html references a missing local asset: icons/task.svg
- app.js only initializes on DOMContentLoaded without a document.readyState fallback

Running one repair pass.
```

### Phase 3: Repair Prompt Construction

```python
def _build_repair_prompt(self, original_prompt: str, issues: list[str]) -> str:
    return (
        f"Repair the existing generated app for this request: {original_prompt}\n\n"
        "Validation found these issues:\n"
        + "\n".join(f"- {issue}" for issue in issues)
        + "\n\nRequirements:\n"
        "- Read the current files before editing.\n"
        "- Prefer write_file over repeated apply_diff retries.\n"
        "- Keep the existing design unless a functional fix requires a layout change.\n"
        "- Ensure the app is usable in the live preview.\n"
        '- If JavaScript initializes UI behavior, use a robust startup pattern: '
        'if document.readyState !== "loading", run init immediately, '
        'otherwise attach DOMContentLoaded once.\n'
        "- Finish with a concise summary of what you fixed."
    )
```

### Phase 4: Repair Execution

```
┌─────────────────────────────────────────────────────────────────────┐
│                      REPAIR PASS                                     │
│                                                                       │
│  Orchestrator receives repair prompt with specific issues            │
│                                                                       │
│  Step 1: Read existing files                                         │
│  ├── read_file("index.html") → sees icon reference                  │
│  └── read_file("app.js") → sees DOMContentLoaded issue               │
│                                                                       │
│  Step 2: Fix issues                                                  │
│  ├── write_file("index.html") → remove/fix icon reference           │
│  └── write_file("app.js") → add readyState fallback                 │
│                                                                       │
│  Step 3: Summary message                                             │
│  "Fixed missing asset reference and added robust JS initialization"  │
└─────────────────────────────────────────────────────────────────────┘
```

### Phase 5: Re-validation

```python
# After repair, validate again
validation_issues = self._validate_generated_app(app_id)

if validation_issues:
    # Report remaining issues to user (no more repair attempts)
    await stream_message(
        emit,
        "system",
        "assistant",
        "Validation still found issues:\n- " + "\n- ".join(validation_issues),
    )
else:
    # Success!
    await stream_message(
        emit,
        "system",
        "assistant",
        "Validation passed: required files, asset references, and JavaScript wiring look consistent.",
    )
```

### Complete Repair Timeline

```
Timeline
────────────────────────────────────────────────────────────────────────
│
│  t=0s     User submits: "Build a task manager"
│           │
│  t=0.1s   ├── agent_started (orchestrator)
│           │
│  t=2s     ├── tool_started: write_file(solution.md)
│  t=2.1s   ├── tool_finished: wrote solution.md
│           │
│  t=4s     ├── tool_started: write_file(index.html)
│  t=4.2s   ├── tool_finished: wrote index.html
│           │
│  t=6s     ├── tool_started: write_file(styles.css)
│  t=6.1s   ├── tool_finished: wrote styles.css
│           │
│  t=8s     ├── tool_started: write_file(app.js)
│  t=8.2s   ├── tool_finished: wrote app.js
│           │
│  t=10s    ├── message: "I've created the task manager app..."
│           │
│  t=10.1s  ├── [VALIDATION RUNS]
│           │   Issues found:
│           │   - Missing readyState fallback
│           │
│  t=10.2s  ├── message: "Validation found issues... Running repair pass"
│           │
│  t=10.3s  ├── agent_started (orchestrator - REPAIR)
│           │
│  t=11s    ├── tool_started: read_file(app.js)
│  t=11.1s  ├── tool_finished: [content]
│           │
│  t=12s    ├── tool_started: write_file(app.js)  ← FIXED VERSION
│  t=12.1s  ├── tool_finished: wrote app.js
│           │
│  t=13s    ├── message: "Fixed the JavaScript initialization..."
│           │
│  t=13.1s  ├── [RE-VALIDATION RUNS]
│           │   No issues found ✓
│           │
│  t=13.2s  ├── message: "Validation passed"
│           │
│  t=13.3s  ├── workspace (final snapshot)
│           │
│  t=13.4s  └── final
│
────────────────────────────────────────────────────────────────────────
```

### JavaScript Fix Example

**Before repair (problematic):**
```javascript
document.addEventListener('DOMContentLoaded', () => {
    initializeApp();
});
```

**After repair (robust):**
```javascript
function initializeApp() {
    // ... app logic
}

// Robust initialization for live preview environment
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeApp);
} else {
    // DOM already loaded, initialize immediately
    initializeApp();
}
```

### Repair Constraints

| Constraint | Purpose |
|------------|---------|
| **ONE repair pass only** | Prevents infinite loops |
| **Must read before edit** | Ensures repair is based on actual file state |
| **Prefer write_file over apply_diff** | Avoids diff matching failures |
| **Preserve existing design** | Only fix functional issues |
| **Provide fix summary** | User visibility into changes |

### Diff Failure Escalation in Repairs

If the agent tries `apply_diff` during repair and it fails:

```python
# tools.py - apply_diff handler
failure_count = self.apply_diff_failures.get((app_id, path), 0) + 1
self.apply_diff_failures[(app_id, path)] = failure_count

if failure_count == 1:
    raise ValueError(
        f"{error}. Read the latest version of {path} and use write_file to rewrite the full file."
    )
else:
    raise ValueError(
        f"{error}. apply_diff has failed {failure_count} times for {path}. "
        "Stop retrying the diff and rewrite the file with write_file instead."
    )
```

This progressive escalation ensures the agent switches to full-file rewrites when diffs aren't working.

---

## Summary

This multi-agent system implements a sophisticated architecture for generating web applications:

### Core Architecture
1. **Orchestration**: A central orchestrator (temperature 0.7) coordinates specialist agents (temperature 0.3-0.5)
2. **Skills**: Markdown instruction files (`core.md`, `app_builder.md`, `json_rules.md`) shape agent behavior
3. **Tools**: 11 tools enable file operations, search, execution, and agent coordination

### Output & Storage
4. **AgentFS**: Agents write output to sandboxed workspaces (`workspace/app_{id}/`) with path traversal protection
5. **SQLite Persistence**: Sessions, messages, trace events, and workspace snapshots stored in `.storage/chat_sessions.sqlite3`
6. **Dual Storage**: Live filesystem (AgentFS) + SQLite archive for session restoration

### Quality Assurance
7. **Validation**: 7 automated checks verify output quality (required files, asset references, JS wiring, syntax)
8. **Repair**: One-shot repair loop fixes detected issues with specific guidance
9. **Diff Escalation**: Progressive escalation from `apply_diff` to `write_file` on failures

### Real-Time Communication
10. **Streaming**: SSE events provide real-time UI feedback (`agent_started`, `tool_finished`, `workspace`, etc.)
11. **Message Chunking**: 32-char chunks with 18ms delays create typing effect

The design prioritizes:
- **Reliability**: Validation and repair mechanisms catch and fix errors automatically
- **Visibility**: Comprehensive event streaming shows progress at every step
- **Flexibility**: Skill-based instructions are easily modifiable without code changes
- **Safety**: Tool whitelisting, path validation, and command restrictions prevent misuse
- **Persistence**: Dual storage ensures sessions survive restarts and can be replayed
