# Agentic Audit for `app_867d25bda006`

Date: March 31, 2026

Scope reviewed:
- `backend_logs.txt`
- `logs.txt`
- `chat.txt`
- `workspace/app_867d25bda006/**`
- `backend/app/services/runner.py`
- `backend/app/services/tools.py`
- `backend/app/services/diffing.py`
- `backend/app/services/agent_registry.py`
- `agents/orchestrator/config.yaml`
- `agents/validator/config.yaml`

## TL;DR

This is a real multi-agent system already. You have an orchestrator, specialist delegation, tool calling, a validator role, a repair pass, skill bundles, and a guarded unified diff engine.

The current gap is not "is this agentic?" but "does it converge reliably?". Right now it feels closer to a strong agentic prototype than to a Copilot/Claude-grade coding agent because:

- the validator subagent stalls and hits `max_turns`
- the validator contract is prompt-only and not enforced structurally
- the repair pass mostly inspects and does not fix
- the runtime app breaks the `pages/*.html` contract created by the specialists
- built-in object validation has a real code bug, so some important checks never run
- several "agentic" controls are parsed from config but not actually enforced at runtime
- there is config/runtime drift around `max_turns`

My overall assessment: about `5.5/10` toward the bar set by leading coding agents. The architecture is promising. Reliability and loop control are the main missing pieces.

## How to Fix the Reliability Gap

This is the missing "how" for the seven biggest problems in the TL;DR.

### 1. Fix: validator subagent stalls and hits `max_turns`

What to change:

1. Shrink validator scope.
   - Make validator validate one layer at a time:
     - object pass
     - page/root integration pass
   - Do not ask it to reason over everything at once.

2. Give validator a fixed workflow instead of a free-form prompt.
   - Example:
     1. `glob` relevant files
     2. `grep` IDs/classes/routes
     3. `read_file` only the mismatching files
     4. return `VALID` or `INVALID`

3. Add stall detection in the runner.
   - If validator makes `N` consecutive read-only turns with no new files inspected and no final schema output, abort as `validator_stalled`.

4. Lower the validator's degree of freedom.
   - Remove `web_search` and `web_fetch` from local validation runs.
   - Prefer deterministic checks first, then use validator only for higher-level judgment.

Files to touch:
- `agents/validator/config.yaml`
- `backend/app/services/runner.py`
- optionally `backend/app/services/tools.py`

### 2. Fix: validator contract is prompt-only and not enforced structurally

What to change:

1. Make validator return structured JSON, not free text.
   - Example schema:

```json
{
  "status": "VALID",
  "findings": [],
  "summary": "..."
}
```

or

```json
{
  "status": "INVALID",
  "findings": [
    {
      "severity": "high",
      "path": "pages/account.html",
      "owner": "page_builder",
      "issue": "...",
      "fix": "...",
      "diff": "@@ ..."
    }
  ],
  "summary": "..."
}
```

2. Parse that output in `invoke_agent()` or immediately after it returns.
   - If parsing fails, treat it as validator failure.
   - If `status` is missing, treat it as validator failure.
   - If validator times out, stop the workflow or trigger a deterministic fallback.

3. Do not continue to page generation after a failed validator pass.
   - If object validation fails, loop back to `object_builder` or orchestrator repair first.

Files to touch:
- `backend/app/services/runner.py`
- possibly `backend/app/schemas.py` if you want a shared validator-response schema

### 3. Fix: repair pass mostly inspects and does not fix

What to change:

1. Turn repair into a bounded repair controller.
   - Convert validator findings into explicit repair tasks:
     - owner
     - target file(s)
     - allowed tools
     - expected output

2. Require an edit after a small number of repair turns.
   - Example rule:
     - after 4 turns of only `read_file` / `grep` / `glob`, the next turn must be `apply_diff`, `write_file`, or `invoke_agent`
     - otherwise fail closed

3. Hash the workspace before and after a repair pass.
   - If zero files changed, mark the pass as `no_progress`
   - never report that as a successful repair attempt

4. Route fixes back to owners.
   - page issues -> `page_builder`
   - object issues -> `object_builder`
   - integration issues -> orchestrator

5. Bias repair toward `apply_diff`.
   - For focused validator findings, the owning agent should patch, not rewrite blindly.

Files to touch:
- `backend/app/services/runner.py`
- `backend/app/services/tools.py`

### 4. Fix: runtime app breaks the `pages/*.html` contract

What to change:

1. Pick a single source of truth for page markup.
   - Since you already generate `pages/*.html`, make those fragments authoritative.

2. Change `app.js` so route handlers bind into fetched fragments instead of replacing them.
   - Good:
     - fetch `pages/product-details.html`
     - inject it
     - fill `#product-title`, `#product-price`, `#product-quantity`
     - attach listeners to `#qty-increase`, `#qty-decrease`, `#add-to-cart-form`
   - Bad:
     - fetch the fragment
     - discard it
     - replace `app-root` with a second inline template

3. Add a contract validator.
   - If a route loads `pages/product-details.html`, then runtime JS must bind into that fragment's IDs/classes rather than redefining the whole view.

4. Optionally generate a fragment manifest.
   - Example: `.internal/page_contract.json`
   - Store route -> fragment path -> required IDs/classes
   - Use it during validation

Files to touch:
- `workspace generation behavior in backend/app/services/runner.py`
- generated `app.js` prompts and repair prompts
- deterministic validator in `backend/app/services/runner.py`

### 5. Fix: built-in object validation has a real code bug

What to change:

1. Normalize object keys consistently.
   - Either:
     - store `Product`, `Category`, `CartItem`, etc.
   - or:
     - lowercase everything and make `_validate_object_models()` use lowercase lookups

2. Fix wrong FK fields.
   - `CartItem` validation should use `productId`, not `id`
   - nested order item validation should use `productId`, not `id`

3. Add regression tests for this exact path.
   - test lowercase filenames
   - test valid foreign keys
   - test expired coupons
   - test admin presence

Files to touch:
- `backend/app/services/runner.py`
- add tests under `backend/tests/`

### 6. Fix: several "agentic" controls are parsed from config but not enforced at runtime

What to change:

1. Enforce `execution.timeout_ms`.
   - Wrap agent loops or model/tool calls with real timeouts.

2. Enforce `max_parallel_tools`.
   - If the model emits too many tool calls, cap concurrency with a semaphore or execute in batches.

3. Enforce `context_paths`.
   - Do not only say "Work only from ...".
   - Filter `read_file`, `glob`, and `grep` access through an allowlist for subagent runs.

4. Either implement context compression or stop advertising it as active.
   - Right now it is logged, not applied.

5. Emit effective runtime config at agent start.
   - model
   - tools
   - `max_turns`
   - `timeout_ms`
   - config source path

Files to touch:
- `backend/app/services/runner.py`
- `backend/app/services/agent_registry.py`
- `backend/app/services/context.py`
- possibly `backend/app/services/tools.py`

### 7. Fix: config/runtime drift around `max_turns`

What to change:

1. Log the effective `max_turns` value at runtime.
   - Include:
     - root config value
     - agent profile value
     - final value used by the loop

2. Add a unit test that loads `agents/orchestrator/config.yaml` and asserts the runner uses that exact value.

3. If the backend process is long-lived, verify whether config is being cached or the run was produced before the latest config change.
   - `get_config()` is cached with `lru_cache`
   - agent profiles are loaded fresh, but runtime artifacts may still reflect an older server state

4. Add the effective turn budget into emitted events and `.internal/logs.json`.
   - That makes drift visible immediately in future runs.

Files to touch:
- `backend/app/services/runner.py`
- `backend/app/config.py`
- `backend/tests/test_agent_registry.py`

## Reconstructed Agent Flow

Observed app: `workspace/app_867d25bda006`

Prompt:
- Build a full multi-page e-commerce site with `solution.md`, `objects/`, `pages/`, `index.html`, `styles.css`, and `app.js`

Observed runtime sequence:

1. `orchestrator` starts and writes `solution.md`.
   - Evidence: `logs.txt:3-12`, `backend_logs.txt:5`

2. `orchestrator` invokes `object_builder`.
   - Evidence: `logs.txt:15-16`

3. `object_builder` reads `solution.md` once and writes 7 JSON files under `objects/`.
   - Evidence: `logs.txt:23-88`

4. `orchestrator` invokes `validator` on `solution.md` plus the object files.
   - Evidence: `logs.txt:95-100`

5. `validator` only uses `read_file` 8 times, never returns a useful `VALID`/`INVALID`, and hits `max_turns=8`.
   - Evidence: `logs.txt:103-168`, `workspace/app_867d25bda006/.internal/logs.json:22-39`

6. `orchestrator` continues anyway, does a little file inspection, then invokes `page_builder`.
   - Evidence: `logs.txt:175-191`

7. `page_builder` reads once, writes 7 HTML fragments under `pages/`, then finishes.
   - Evidence: `logs.txt:199-272`

8. `orchestrator` writes `index.html`, `styles.css`, and `app.js`.
   - Evidence: `logs.txt:279-352`

9. Deterministic validation in `runner.py` catches integration problems that the validator subagent missed.
   - Evidence: `backend_logs.txt:106`, `chat.txt:7-10`, `backend/app/services/runner.py:496-524`

10. A repair pass starts, but the orchestrator mostly loops through `grep` and `read_file`, makes no meaningful edits, and hits `max_turns` again.
   - Evidence: `logs.txt:355-520`

End state:
- the app exists and loads files
- some core browsing/cart flows likely work
- many richer generated page controls are not actually wired
- validation still reports unresolved issues

## What the Validator Actually Does

There are two validators in practice:

### 1. The `validator` subagent

Configured behavior:
- read-only specialist
- expected to inspect files, compare routes/IDs/classes/object keys, and return `VALID` or `INVALID`
- may suggest unified diffs but cannot edit files

Code:
- prompt rules: `backend/app/services/runner.py:408-419`
- config: `agents/validator/config.yaml:1-57`
- raw `invoke_agent()` return path: `backend/app/services/runner.py:543-558`

Observed behavior in this run:
- tools available: `read_file`, `grep`, `glob`, `web_search`, `web_fetch`
- tools actually used: `read_file` only
- result: `validator hit max_turns=8`

Why this matters:
- the specialist validator is currently not giving the orchestrator actionable feedback
- this means the real safety net is the deterministic validator below
- even worse, the workflow does not enforce the validator contract; a failed or malformed validator response is just treated like ordinary text, and the orchestrator can continue anyway

### 2. The deterministic validator in `runner.py`

This is the part that actually found the issues in this app.

Code path:
- `backend/app/services/runner.py:650-781`

What it checks:
- required files exist
- local asset references exist
- `app.js` has syntax errors via `node --check`
- JS selectors vs HTML IDs
- interactive HTML IDs with no JS wiring
- HTML classes with no CSS selectors
- object model consistency checks

Important limitation:
- it compares JS IDs mostly against static HTML files, so IDs created dynamically inside `app.js` templates can be reported as missing even if they exist at runtime
- that explains why `quantity` was flagged even though it is injected by `app.js`
- evidence: `backend/app/services/runner.py:716-746`, `workspace/app_867d25bda006/app.js:223-237`

Important real bug:
- object models are loaded by lowercase filename stem, e.g. `product`, `cartItem`
- `_validate_object_models()` looks up `Product`, `CartItem`, `Order`, `User`, etc.
- that means many object validation checks never fire
- evidence: `backend/app/services/runner.py:185-266`, `backend/app/services/runner.py:763-773`

There is also a second latent bug there:
- cart FK validation uses `"id"` instead of `"productId"`
- order item validation compares item `"id"` instead of `"productId"`
- evidence: `backend/app/services/runner.py:244-248`, `backend/app/services/runner.py:251-264`

## How Unified Diffing Works

Unified diffing is implemented carefully and is one of the stronger parts of the codebase.

Core files:
- `backend/app/services/diffing.py`
- `backend/app/services/tools.py:190-224`

Flow:

1. Parse a unified diff into hunks.
2. Analyze the size and spread of the edit.
3. Decide whether the change is safe for patching or too broad and should become a rewrite.
4. Apply hunks with context anchoring and whitespace normalization.
5. For JSON files, validate the final JSON.

Key safety behavior:
- reject diffs that are too broad
- retry guidance tells the agent to reread and then use `write_file`
- after repeated diff failures, the tool explicitly tells the agent to stop retrying and rewrite

Decision heuristics:
- large line count
- too much of file touched
- too many hunks
- hunks spread across too much of the file
- one huge hunk

Relevant code:
- parse/apply: `backend/app/services/diffing.py:41-319`
- strategy selection: `backend/app/services/diffing.py:199-277`
- tool wrapper: `backend/app/services/tools.py:190-224`

Important observation:
- `apply_diff` was available to the agents in this run
- it was not used at all
- this build relied on `write_file`, `read_file`, `grep`, `glob`, and `invoke_agent`

## Tools Called in This Run

Source of truth for named tool calls: `logs.txt`

### By agent

`orchestrator`
- `write_file`: 4
- `read_file`: 18
- `glob`: 3
- `grep`: 7
- `invoke_agent`: 3
- `apply_diff`: 0
- `bash`: 0
- `todos`: 0
- `web_search`: 0
- `web_fetch`: 0

`object_builder`
- `read_file`: 1
- `write_file`: 7
- `apply_diff`: 0
- `grep`: 0
- `glob`: 0
- `bash`: 0
- `todos`: 0

`validator`
- `read_file`: 8
- `grep`: 0
- `glob`: 0
- `web_search`: 0
- `web_fetch`: 0

`page_builder`
- `read_file`: 1
- `write_file`: 7
- `glob`: 1
- `apply_diff`: 0
- `grep`: 0
- `bash`: 0

### What that tells us

- object creation and page creation were very direct and file-write heavy
- the validator did not use its stronger cross-file inspection tools
- the repair loop over-indexed on `grep` and `read_file`
- `apply_diff` exists but is not being chosen by the agents in the exact phase where it would help most: targeted repairs

## How Skills Were Used

Skills here are prompt bundles, not executable plugins.

Loading path:
- `backend/app/services/agent_registry.py:191-197`
- `backend/app/services/runner.py:303-320`
- `backend/app/services/runner.py:783-846`

That means:
- the skill markdown is concatenated into the system prompt
- skills influence the model only indirectly
- there is no hard enforcement that a skill must be obeyed

Skills used in this run:

`orchestrator`
- `core`
- `app_builder`
- `error_handling`
- `security`

`object_builder`
- `core`
- `json_rules`
- `app_builder`
- `error_handling`

`validator`
- `core`
- `json_rules`
- `testing_qa`
- `security`

`page_builder`
- `core`
- `app_builder`
- `accessibility`
- `error_handling`

What the skills were supposed to encourage:
- `core`: real code, deliberate tool use, unified diff preference for localized edits
- `app_builder`: staged workflow, ownership boundaries, cross-file consistency
- `error_handling`: reread before repair, switch from diff to rewrite after failure
- `json_rules`: pretty JSON, stable keys, validate after edits
- `testing_qa`: verify navigation, form wiring, IDs/classes/routes consistency
- `accessibility`: semantic HTML, labels, focus, accessible controls
- `security`: avoid sensitive storage, validate input, avoid risky DOM patterns

What happened instead:
- the system followed the broad staged workflow
- the detailed skill expectations were not enforced strongly enough to prevent integration drift
- example: `page_builder` had the accessibility skill and created richer accessible fragments, but `app.js` later bypassed most of that work

## App Review: Main Findings

### 1. The `pages/*.html` contract is broken at runtime

The router fetches page fragments, but several initializers immediately replace `#app-root` with brand new inline markup.

Evidence:
- `workspace/app_867d25bda006/app.js:117-136`
- `workspace/app_867d25bda006/app.js:205-239`
- `workspace/app_867d25bda006/app.js:242-297`
- `workspace/app_867d25bda006/app.js:299-386`
- `workspace/app_867d25bda006/app.js:388-434`
- `workspace/app_867d25bda006/app.js:436-528`

Impact:
- specialist output in `pages/` is not the runtime source of truth
- validator sees mismatch between generated fragments and runtime behavior
- repairs become much harder because there are two competing UI implementations

### 2. Several requested data-backed flows are not actually loaded

`app.js` only loads:
- `product.json`
- `category.json`
- `user.json`
- `order.json`

It does not load:
- `address.json`
- `coupon.json`
- `cartItem.json`

Evidence:
- `workspace/app_867d25bda006/app.js:62-67`

Impact:
- coupon flow is effectively fake
- saved addresses are not truly backed by loaded data
- richer account and checkout behaviors cannot be completed cleanly

### 3. The app generated controls that are never wired

Examples:
- catalog filter button: `workspace/app_867d25bda006/pages/catalog.html:21`
- product details quantity controls: `workspace/app_867d25bda006/pages/product-details.html:28-40`
- account address form and tabbed controls: `workspace/app_867d25bda006/pages/account.html:7-112`
- admin add buttons: `workspace/app_867d25bda006/pages/admin.html`

The deterministic validator reported the exact symptom:
- missing JS wiring for IDs such as `account-address-form`, `add-address-btn`, `add-new-address-btn`, `add-to-cart-btn`, `admin-add-category-btn`, `admin-add-coupon-btn`, `admin-add-product-btn`, `apply-price-filter`
- evidence: `backend_logs.txt:106`, `chat.txt:7-10`

### 4. The admin flow is effectively unreachable in the normal experience

Behavior:
- app defaults to the customer user
- admin nav is hidden unless current user is admin
- `#/admin` redirects non-admin users back home

Evidence:
- `workspace/app_867d25bda006/app.js:76-80`
- `workspace/app_867d25bda006/app.js:93-99`
- `workspace/app_867d25bda006/app.js:436-440`

Impact:
- an entire required surface exists but is not reachable in the default UX

### 5. Mock data quality has correctness and security problems

Plaintext passwords:
- `workspace/app_867d25bda006/objects/user.json:8`
- `workspace/app_867d25bda006/objects/user.json:16`
- `workspace/app_867d25bda006/objects/user.json:23`

Those user objects are then persisted into localStorage as current user:
- `workspace/app_867d25bda006/app.js:72-80`

Expired coupons relative to March 31, 2026:
- `workspace/app_867d25bda006/objects/coupon.json:18`
- `workspace/app_867d25bda006/objects/coupon.json:26`

Order totals do not match line items:
- `ord-1`: `299.99 + 45.00 = 344.99`, but total is `339.98`
- `ord-2`: `89.00 + 120.00 = 209.00`, but total is `159.00`
- evidence: `workspace/app_867d25bda006/objects/order.json:18-36`, `workspace/app_867d25bda006/objects/order.json:41-59`

### 6. The repair loop is too inspection-heavy

After validation failed, the repair prompt explicitly asked for targeted fixes.

Observed outcome:
- repeated `grep`
- repeated `read_file`
- no effective repair
- another `max_turns` exit

Evidence:
- `logs.txt:355-520`

This is the clearest reason not to remove turn limits entirely right now.

### 7. There is config/runtime drift around `max_turns`

Current files say:
- root config default: `config.yaml:3` => `20`
- orchestrator profile: `agents/orchestrator/config.yaml:71` => `40`
- validator profile: `agents/validator/config.yaml:53` => `8`

Observed run says:
- orchestrator hit `max_turns=15`
- validator hit `max_turns=8`

Evidence:
- `logs.txt:168`
- `logs.txt:352`
- `logs.txt:520`
- `workspace/app_867d25bda006/.internal/logs.json:38`
- `workspace/app_867d25bda006/.internal/logs.json:80`
- `workspace/app_867d25bda006/.internal/logs.json:103`

Interpretation:
- validator is consistent with current config
- orchestrator is not
- before changing limits, first verify why the live run still used `15`

### 8. Some agentic controls are still prompt-only or config-only

Examples:
- skills are concatenated into the prompt, not executed as policies
- `context_paths` is effectively advisory text, not a hard file-access constraint
- `execution.timeout_ms` and `max_parallel_tools` are parsed but not clearly enforced in the agent loop
- context compression is logged, but not actually implemented

Evidence:
- `backend/app/services/agent_registry.py:137-146`
- `backend/app/services/agent_registry.py:191-197`
- `backend/app/services/runner.py:553-558`
- `backend/app/services/runner.py:849-916`
- `backend/app/services/runner.py:852-864`

Impact:
- the architecture looks more autonomous on paper than it behaves in practice
- the system has fewer real runtime guardrails than the configs suggest

### 9. Small but real implementation flaws

`switchAdminTab` relies on implicit global `event`:
- `workspace/app_867d25bda006/app.js:530-536`

That is fragile and browser-dependent.

## What You Can Do Better

### Tool usage

1. Make `apply_diff` the default repair tool.
   - Right now the system has a good diff engine but agents rarely choose it.
   - For repair prompts, bias toward `grep` -> narrow `read_file` -> `apply_diff`.

2. Put a hard ceiling on read-only loops.
   - If an agent spends `N` turns doing only `read_file`/`grep`/`glob` with no writes, stop and escalate.

3. Narrow toolsets by phase.
   - The validator does not need `web_search`/`web_fetch` for local app consistency checks.
   - Too many tools can make the model wander.

4. Require the validator to use `glob` and `grep` at least once before it can finish.
   - This can be prompt-enforced or code-enforced.

5. Enforce validator output structurally.
   - Parse it into a schema such as:
     - `status: VALID | INVALID`
     - `findings: []`
     - `owner: orchestrator | object_builder | page_builder`
   - If the validator times out, returns free text, or does not satisfy the schema, treat that as validator failure and stop or escalate.

### Agent flow

1. Make one layer the source of truth.
   - If `pages/` exists, `app.js` should bind into those fragments instead of replacing them wholesale.

2. Route repair work back to owners.
   - If validator says a page control is unwired, re-invoke `page_builder` or assign a focused repair to the owning agent.
   - Do not let the orchestrator absorb every repair alone.

3. Add phase budgets instead of removing turn limits.
   - Example:
     - planning/orchestrator setup: 4-6 turns
     - specialist build pass: 6-10 turns each
     - repair pass: 6-8 turns
     - if still failing, stop with a high-quality failure report

4. Add "no edits made" detection.
   - If a repair pass ends with zero writes/diffs, it should be marked as failed repair, not normal completion.

5. Enforce `context_paths` for subagents.
   - Right now "Work only from ..." is just prompt text.
   - For higher reliability, restrict `read_file` / `glob` scope in tooling, not just in prompting.

### Instructions

Add stronger instructions such as:

- "Do not replace fetched `pages/*.html` fragments with new inline templates. Use the fetched fragment as the source of truth and only fill placeholders or attach event listeners."
- "If validator reports issues, the next 3-5 turns must contain either a file edit or a delegated repair."
- "When a page introduces form/button IDs, wire them in JS before finishing."
- "If admin pages are required, the app must expose a demo path to them."

### Deterministic validation

Fix these code issues first:

1. object-model key mismatch in `_validate_object_models()`
2. `CartItem` FK check should use `productId`
3. order item validation should use `productId`
4. JS-generated DOM vs static HTML false-positive handling
5. add checks that required object files are actually loaded when pages depend on them
6. treat validator contract failure as workflow failure, not informational text
7. actually enforce `timeout_ms` / `max_parallel_tools` or remove them from the config surface until they are real

## Should You Remove `orchestrator max_turns`?

Short answer: `No, not yet.`

Why:
- the repair pass already showed the failure mode you would get without a cap: repeated inspection with no convergence
- the orchestrator did not stall because 15 was too low; it stalled because it had no stronger repair policy

Safer alternatives:

1. Keep a hard cap.
2. Fix the config/runtime mismatch first.
3. Add phase-specific budgets.
4. Add loop detection:
   - repeated `grep`/`read_file`
   - no writes in last N turns
   - same issue list repeated twice
5. Add a wall-clock timeout alongside turn limits.
6. Escalate to a structured failure report when the repair loop is not making edits.
7. Consider a repair rule like: "after 4 read-only turns, the next turn must be `apply_diff`, `write_file`, `invoke_agent`, or fail closed."

If you want more room, raising the orchestrator budget after the drift issue is fixed is reasonable. Removing the cap entirely is not.

## How Close This Is to "Top-Tier Agentic"

### Already strong

- multi-agent architecture
- specialist ownership
- tool schemas and validation
- deterministic post-build validation
- repair loop
- skill bundles
- explicit diff engine
- good observability artifacts

### Still missing

- reliable validator outputs
- repair-loop convergence
- strong enforcement of ownership contracts
- one clear source of truth for generated UI
- adaptive stopping and stall detection
- better eval coverage for the full generated app contract
- runtime/browser-level verification instead of mostly static checks

### Honest assessment

This is much closer to a serious agentic system than a toy demo, but it is not yet at the reliability bar of the best coding agents. The biggest next leap is not "more tools" or "more turns". It is tighter workflow control and better repair/eval behavior.

## Verification I Ran

Targeted backend tests:

```bash
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_diffing.py backend/tests/test_agent_registry.py
```

Result:
- `15 passed in 0.04s`

Also checked:
- `node --check workspace/app_867d25bda006/app.js`
- result: syntax is valid

Parallel review also reported:

```bash
PYTHONPATH=backend .venv/bin/pytest backend/tests -q
```

Reported result:
- `90 passed`

That is a useful signal: the backend test suite is mostly green, so the problem is more about missing coverage around the orchestration/validation workflow than obvious baseline breakage.

## Recommended Next Order of Operations

1. Fix deterministic object validation bugs in `runner.py`.
2. Fix the config/runtime turn-budget drift.
3. Enforce the `pages/*.html` source-of-truth contract.
4. Make repair passes edit-or-escalate instead of inspect-forever.
5. Tighten validator output format and tool usage.
6. Add regression tests for:
   - object-model validation
   - page/JS contract alignment
   - repair-loop stall detection
   - required-object loading for generated flows
