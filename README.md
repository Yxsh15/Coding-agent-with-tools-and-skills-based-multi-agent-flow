# AgentFS App Builder POC

A Claude-Code-style AI coding agent that generates real, working web applications using Google Gemini.

## Features

- **Google Gemini Integration**: Uses Gemini 3 Pro Preview for AI-powered code generation
- **Real Code Generation**: Generates actual HTML, CSS, and JavaScript - not just schemas
- **Live Preview**: Test generated applications directly in the browser with real file serving
- **Unified-Diff Editing**: Smart file modifications instead of full rewrites
- **Multi-Agent Architecture**: Orchestrator + specialist agents for different tasks
- **Complexity-Aware Orchestration**: The orchestrator LLM decides when to stay simple and when to trigger object/page/validation specialists
- **Workspace Tree View**: Visual file explorer like VS Code
- **AgentFS**: Filesystem-backed state management plus a runtime bridge for generated apps
- **Skills System**: Modular skills loaded from markdown files
- **YAML Configuration**: Per-agent configuration files
- **Real-Time Trace UI**: Watch agent runs, tool calls, and durations

## Prerequisites

- **Python 3.10+** (for the FastAPI backend)
- **Node.js 18+** and **npm** (for the React/Vite frontend)
- A **Google Gemini API key** — optional, since the default scripted adapter runs without one (see [Notes](#notes))

## Quick Start

Run the backend and frontend in **two separate terminals**. Start the backend first, then the frontend.

### 1. Set up API Key

Copy the example environment file and add your Gemini API key:

```bash
cp .env.example .env
# Edit .env and add your GOOGLE_API_KEY
# Get your key from: https://aistudio.google.com/apikey
# Optional: set GEMINI_MODEL=gemini-3-pro-preview
```

### 2. Backend Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Backend runs on `http://localhost:8000`.

### 3. Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Frontend runs on `http://localhost:5173`. Open this URL in your browser — it calls the backend directly at `http://localhost:8000` (configured via `API_BASE` in `frontend/src/App.jsx`), so the backend must be running too.

## Running Tests

The backend test suite lives under `backend/tests`:

```bash
cd backend
source .venv/bin/activate
pip install pytest   # not included in requirements.txt
pytest
```

## Usage

1. Enter an app idea such as `calculator app`, `todo list`, or `weather dashboard`
2. Click "Run Agent" to start the generation
3. Watch the agent work in the execution trace panel
4. View generated code in the workspace tree
5. Click "Preview" to test your generated application live!

## Example Prompts

- `simple calculator with add, subtract, multiply, divide`
- `todo list app with add, complete, delete tasks`
- `countdown timer with start, pause, reset`
- `color palette generator`
- `simple note-taking app`
- `multi-page ecommerce website with home, catalog, product, cart, checkout, and admin pages`

## Project Structure

```text
backend/          # FastAPI backend with Gemini integration
agents/           # Agent configurations (orchestrator, specialists)
frontend/         # React UI with live preview
skills/           # Agent skill definitions (markdown)
config.yaml       # Global configuration
.env              # API key (create from .env.example) — gitignored
workspace/        # Generated application files — runtime artifact, gitignored
.storage/         # Chat sessions DB + backend logs — runtime artifact, gitignored
```

## Configuration

### Model Settings (config.yaml)

```yaml
model:
  provider: google
  name: gemini-3-pro-preview
  temperature: 0.7
  max_output_tokens: 8192
  top_p: 0.95
  top_k: 40
```

## Demo Flow

1. Enter an app idea such as `vendor management app`
2. The orchestrator updates `solution.md` and decides whether the request is simple or complex
3. For complex apps, it delegates object modeling, validation, and page building in stages
4. Only the orchestrator can invoke other agents
5. The UI streams each tool call with agent name and duration
6. The generated workspace can be inspected from the right-side preview panel
7. Generated apps can call `window.AgentFS.tree()`, `window.AgentFS.workspace()`, and `window.AgentFS.readFile(path)` at runtime

## Notes

- The default model adapter is intentionally scripted so the whole POC works without an API key.
- Each agent has its own `config.yaml` under `agents/<agent-name>/config.yaml`.
- Only the orchestrator has the `invoke_agent` tool; specialist agents are not told about other agents.
- The validator stays read-only and suggests fixes; orchestrator or the owning specialist applies the actual unified diff.
- The architecture is replaceable: swap `ScriptedMultiAgentModel` for a real tool-calling LLM later.
- JSON edits are validated after unified diff application.
