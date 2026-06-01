from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import subprocess
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from app.config import get_config, get_root_dir
from app.services.agent_registry import AgentProfile, AgentRegistry
from app.services.agentfs import AgentFS
from app.services.tools import ToolBox
from app.services.llm import GeminiModel, LLMConfig
from app.services.streaming import stream_message
from app.services.context import ContextWindowManager, ContextConfig
from app.services.observability import (
    AgentMetrics,
    CorrelationContext,
    get_metrics,
    get_tracer,
    log_agent_event,
)


EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]
StreamObserver = Callable[[dict[str, Any]], Awaitable[None]]
logger = logging.getLogger("app.services.runner")
LOCAL_ASSET_PATTERN = re.compile(r"""(?:src|href)=["']([^"']+)["']""", re.IGNORECASE)
URI_SCHEME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
INTERACTIVE_HTML_PATTERN = re.compile(r"<(button|input|select|textarea|form)\b", re.IGNORECASE)
HTML_ID_PATTERN = re.compile(r"""\bid=["']([^"']+)["']""", re.IGNORECASE)
HTML_CLASS_ATTR_PATTERN = re.compile(r"""\bclass=["']([^"']+)["']""", re.IGNORECASE)
HTML_ROUTE_HASH_PATTERN = re.compile(r"""href=["']#([^/"'][^"']*)["']""", re.IGNORECASE)
ACTIONABLE_HTML_ID_PATTERN = re.compile(r"""<(button|form)\b[^>]*\bid=["']([^"']+)["']""", re.IGNORECASE)
CSS_CLASS_SELECTOR_PATTERN = re.compile(r"""(?<![A-Za-z0-9_-])\.([A-Za-z_][A-Za-z0-9_-]*)""")
JS_GET_ELEMENT_BY_ID_PATTERN = re.compile(r"""getElementById\(\s*["']([^"']+)["']\s*\)""")
JS_QUERY_SELECTOR_ID_PATTERN = re.compile(r"""querySelector(?:All)?\(\s*["']#([A-Za-z][A-Za-z0-9:_-]*)["']\s*\)""")
HTML_DATA_CONTAINER_ID_PATTERN = re.compile(r"""\bid=["']([\w-]+(?:-body|-tbody|-list|-stats|-container))["']""", re.IGNORECASE)
JS_FETCH_PATH_PATTERN = re.compile(r"""fetch\(\s*[`"']([^`"'$]+)[`"']\s*\)""")
JSON_OBJECT_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
SOLUTION_PAGE_FILE_PATTERN = re.compile(r"\*\*([A-Za-z0-9_-]+\.html)\*\*")
STYLE_CONTRACT_IGNORE_CLASSES = {
    "active",
    "btn",
    "button",
    "card",
    "container",
    "current",
    "disabled",
    "error",
    "hidden",
    "input",
    "loading",
    "open",
    "selected",
    "success",
}
STYLE_CONTRACT_SUFFIXES = (
    "-actions",
    "-btn",
    "-card",
    "-content",
    "-form",
    "-grid",
    "-hero",
    "-item",
    "-layout",
    "-list",
    "-nav",
    "-page",
    "-panel",
    "-section",
    "-summary",
    "-table",
)

COMPLEX_REQUEST_HINTS = (
    "multi-page",
    "multiple pages",
    "e-commerce",
    "catalog",
    "cart",
    "checkout",
    "account",
    "admin",
    "dashboard",
    "routing",
    "cross-page",
    "objects/",
    "pages/",
)
READ_ONLY_TOOLS = frozenset({"read_file", "grep", "glob", "web_search", "web_fetch"})
VALIDATOR_STATUSES = frozenset({"VALID", "INVALID"})
NO_RESPONSE_MESSAGES = frozenset({"No response generated."})
NO_RESPONSE_RETRY_LIMIT = 2
MALFORMED_FC_RETRY_LIMIT = 2


def _extract_html_ids(html: str) -> set[str]:
    return {match.group(1).strip() for match in HTML_ID_PATTERN.finditer(html) if match.group(1).strip()}


def _extract_html_class_counts(html_texts: dict[str, str]) -> Counter[str]:
    classes: Counter[str] = Counter()
    for html in html_texts.values():
        for match in HTML_CLASS_ATTR_PATTERN.finditer(html):
            for class_name in match.group(1).split():
                normalized = class_name.strip()
                if normalized:
                    classes[normalized] += 1
    return classes


def _extract_css_classes(css_texts: dict[str, str]) -> set[str]:
    classes: set[str] = set()
    for css in css_texts.values():
        classes.update(match.group(1) for match in CSS_CLASS_SELECTOR_PATTERN.finditer(css))
    return classes


def _extract_actionable_html_ids(html_texts: dict[str, str]) -> set[str]:
    ids: set[str] = set()
    for html in html_texts.values():
        ids.update(match.group(2).strip() for match in ACTIONABLE_HTML_ID_PATTERN.finditer(html) if match.group(2).strip())
    return ids


def _extract_js_ids(js: str) -> set[str]:
    ids = {match.group(1).strip() for match in JS_GET_ELEMENT_BY_ID_PATTERN.finditer(js)}
    ids.update(match.group(1).strip() for match in JS_QUERY_SELECTOR_ID_PATTERN.finditer(js))
    return {item for item in ids if item}


def _should_require_css_class(class_name: str, occurrences: int) -> bool:
    if class_name in STYLE_CONTRACT_IGNORE_CLASSES:
        return False
    if class_name.startswith(("js-", "is-", "has-")):
        return False
    if occurrences >= 2:
        return True
    return any(class_name.endswith(suffix) for suffix in STYLE_CONTRACT_SUFFIXES)


def _mock_items(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    mock_data = payload.get("mockData", [])
    return [item for item in mock_data if isinstance(item, dict)] if isinstance(mock_data, list) else []


def _mock_ids(payload: dict[str, Any] | None) -> set[str]:
    return {str(item["id"]) for item in _mock_items(payload) if "id" in item}


def _normalize_object_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _get_object_model(object_models: dict[str, dict[str, Any]], *names: str) -> dict[str, Any] | None:
    for name in names:
        model = object_models.get(_normalize_object_key(name))
        if isinstance(model, dict):
            return model
    return None


def _extract_json_object(text: str) -> dict[str, Any] | None:
    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    fenced_match = JSON_OBJECT_BLOCK_PATTERN.search(text)
    if fenced_match:
        candidates.insert(0, fenced_match.group(1).strip())
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and start < end:
        candidates.append(text[start : end + 1].strip())

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _parse_validator_result(text: str) -> dict[str, Any] | None:
    payload = _extract_json_object(text)
    if not isinstance(payload, dict):
        return None

    status = str(payload.get("status", "")).upper()
    if status not in VALIDATOR_STATUSES:
        return None

    findings_value = payload.get("findings", [])
    findings = [item for item in findings_value if isinstance(item, dict)] if isinstance(findings_value, list) else []
    if status == "INVALID" and not findings:
        return None

    return {
        "status": status,
        "summary": str(payload.get("summary", "")).strip(),
        "findings": findings,
    }


def _expected_page_paths_from_solution(solution_text: str) -> set[str]:
    return {
        f"pages/{match.group(1).strip()}"
        for match in SOLUTION_PAGE_FILE_PATTERN.finditer(solution_text)
        if match.group(1).strip()
    }


def _expired_coupon_count(payload: dict[str, Any]) -> tuple[int, int]:
    expired = 0
    total = 0
    now = datetime.now(timezone.utc)
    for item in _mock_items(payload):
        expiry = item.get("expiryDate")
        if not isinstance(expiry, str) or not expiry.strip():
            continue
        total += 1
        try:
            parsed = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed <= now:
            expired += 1
    return expired, total


def _missing_foreign_keys(
    source_items: list[dict[str, Any]],
    foreign_key: str,
    valid_ids: set[str],
) -> list[str]:
    missing: list[str] = []
    for item in source_items:
        value = item.get(foreign_key)
        item_id = str(item.get("id", value))
        if value is None:
            continue
        if str(value) not in valid_ids:
            missing.append(item_id)
    return missing


def _validate_object_models(object_models: dict[str, dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    product_payload = _get_object_model(object_models, "Product", "product")
    category_payload = _get_object_model(object_models, "Category", "category")
    user_payload = _get_object_model(object_models, "User", "user")
    order_payload = _get_object_model(object_models, "Order", "order")
    cart_payload = _get_object_model(object_models, "CartItem", "cartitem", "cartItem")
    address_payload = _get_object_model(object_models, "Address", "address")
    coupon_payload = _get_object_model(object_models, "Coupon", "coupon")

    product_items = _mock_items(product_payload)
    category_items = _mock_items(category_payload)
    user_items = _mock_items(user_payload)
    order_items = _mock_items(order_payload)
    cart_items = _mock_items(cart_payload)
    address_items = _mock_items(address_payload)

    if product_items and len(product_items) < 6:
        issues.append(
            f"objects/Product.json only defines {len(product_items)} mock products. Rich catalog-style apps should provide at least 6 representative products."
        )
    if category_items and len(category_items) < 3:
        issues.append(
            f"objects/Category.json only defines {len(category_items)} mock categories. Multi-page commerce apps should provide at least 3 categories."
        )
    if user_items and len(user_items) < 3:
        issues.append(
            f"objects/User.json only defines {len(user_items)} mock users. Include at least two customers and one admin for realistic flows."
        )
    if order_items and len(order_items) < 2:
        issues.append(
            f"objects/Order.json only defines {len(order_items)} mock orders. Add more representative order history for account and admin views."
        )
    if user_items and "admin" not in {str(item.get("role", "")).lower() for item in user_items}:
        issues.append("objects/User.json does not include an admin user, which leaves admin flows hard to validate.")

    if coupon_payload:
        expired_count, total_count = _expired_coupon_count(coupon_payload)
        if total_count and expired_count == total_count:
            issues.append("objects/Coupon.json only contains expired coupons, so coupon flows cannot be exercised in the current app.")

    product_ids = _mock_ids(product_payload)
    category_ids = _mock_ids(category_payload)
    user_ids = _mock_ids(user_payload)

    missing_product_categories = _missing_foreign_keys(product_items, "categoryId", category_ids)
    if missing_product_categories:
        sample = ", ".join(missing_product_categories[:4])
        issues.append(
            f"objects/Product.json references missing category ids for product entries: {sample}."
        )

    missing_address_users = _missing_foreign_keys(address_items, "userId", user_ids)
    if missing_address_users:
        sample = ", ".join(missing_address_users[:4])
        issues.append(
            f"objects/Address.json references missing user ids for address entries: {sample}."
        )

    missing_order_users = _missing_foreign_keys(order_items, "userId", user_ids)
    if missing_order_users:
        sample = ", ".join(missing_order_users[:4])
        issues.append(
            f"objects/Order.json references missing user ids for order entries: {sample}."
        )

    missing_cart_products = _missing_foreign_keys(cart_items, "productId", product_ids)
    if missing_cart_products:
        sample = ", ".join(missing_cart_products[:4])
        issues.append(
            f"objects/CartItem.json references missing product ids for cart entries: {sample}."
        )

    invalid_order_items: list[str] = []
    for order in order_items:
        for item in order.get("items", []):
            if not isinstance(item, dict):
                continue
            product_id = item.get("productId")
            if product_id is not None and str(product_id) not in product_ids:
                invalid_order_items.append(str(order.get("id", product_id)))
                break
    if invalid_order_items:
        sample = ", ".join(invalid_order_items[:4])
        issues.append(
            f"objects/Order.json contains order items that reference missing product ids: {sample}."
        )

    return issues


def summarize_workspace_for_prompt(agentfs: AgentFS, app_id: str, limit: int = 24) -> str:
    files = agentfs.list_files(app_id)
    if not files:
        return "Workspace state: empty workspace. Create the app from scratch."

    important = [
        path
        for path in ("solution.md", "index.html", "styles.css", "app.js")
        if path in files
    ]
    object_files = sorted(path for path in files if path.startswith("objects/"))
    page_files = sorted(path for path in files if path.startswith("pages/"))
    remaining = [
        path
        for path in files
        if path not in important and path not in object_files and path not in page_files
    ]

    ordered = important + object_files + page_files + remaining
    preview = ordered[:limit]
    hidden_count = max(len(ordered) - len(preview), 0)

    lines = [
        f"Workspace state: existing workspace with {len(files)} files.",
        "Update the current app deliberately instead of rebuilding unrelated files.",
    ]
    if preview:
        lines.append("Visible files:")
        lines.extend(f"- {path}" for path in preview)
    if hidden_count:
        lines.append(f"- ... plus {hidden_count} more files")
    return "\n".join(lines)


def build_system_prompt(
    profile: AgentProfile,
    skill_bundle: str,
    prompt: str,
    workspace_summary: str,
) -> str:
    """Build the system prompt for an agent."""
    role_desc = (
        "orchestrator that coordinates the app building process"
        if profile.role == "orchestrator"
        else f"specialist agent for {profile.name.replace('_', ' ')}"
    )

    base_prompt = f"""You are an AI {role_desc}. You build web applications by creating real, working code files.

## Your Skills and Instructions:
{skill_bundle}

## Available Tools:
{', '.join(profile.tools)}

## Workspace Context:
{workspace_summary}

## Important Guidelines:
1. Always generate REAL, WORKING CODE or concrete validation feedback - not vague plans.
2. Read existing files before editing them, especially during follow-up prompts in the same session.
3. Preserve working architecture and evolve it instead of regenerating unrelated files.
4. Create actual HTML, CSS, JavaScript, and JSON artifacts that can run in a browser or guide implementation precisely.
5. The generated app is served from /api/preview/{{app_id}}/ so relative file paths must work in a browser.
6. The generated app can inspect its AgentFS workspace via window.AgentFS and window.agentfs at runtime.
7. If apply_diff fails, read the latest file and use write_file instead of retrying the same diff repeatedly.
8. Before declaring success, make sure the generated app has working asset references and interactive JavaScript wiring.
9. Do not rely only on a bare DOMContentLoaded handler for startup. If the page may already be loaded, initialize immediately when document.readyState is not "loading".
10. Use unified diffs for targeted edits to one logical area or a few nearby regions, and use full rewrites for brand-new files or broad cross-cutting replacements.
11. Use grep and glob to verify route strings, IDs, CSS classes, and object keys across files before validating or repairing the app.
12. When a route loads a fragment from pages/**, treat that fragment as the runtime source of truth. Populate placeholders and attach listeners; do not replace the route with a second inline template for the same screen.
13. For follow-up feature requests on an existing app, stay inside the current app workspace. Do not edit backend files or create unrelated sidecar files unless the user explicitly asks for platform changes.
14. Use bash only for lightweight inspection or verification. Do not use bash as a substitute for writing app code.
15. After writing a file, use validate_syntax to confirm it has no syntax errors. Fix errors immediately in the same turn.
16. After writing app.js and styles.css, call validate_workspace to check for broken references, missing IDs, and CSS gaps. Fix any issues it reports before finishing.

## Standard File Structure:
- solution.md - Architecture and implementation plan
- objects/ - Structured object definitions for richer apps
- pages/ - Page-level artifacts for richer apps
- index.html - Main entry and routing shell
- styles.css - Shared styling system
- app.js - Shared JavaScript behavior

## Your Task:
{prompt}

Start by understanding the requirements, then create or update the actual workspace artifacts."""

    if profile.role == "orchestrator":
        base_prompt += """

## Orchestrator Decision Gate:
Before doing substantial implementation work, decide whether the request is SIMPLE or COMPLEX.

Treat a request as SIMPLE when one page and a small amount of state can satisfy it cleanly.
Treat a request as COMPLEX when it needs multiple pages, multiple domain objects, rich relationships, routing, cross-page state, dashboards, catalogs, carts, checkout flows, admin surfaces, or several coordinated features.

You do NOT need a separate backend route for complexity. You must decide from the prompt and the current workspace.

## If You Judge the Request SIMPLE:
1. Write or update solution.md.
2. Implement the app directly in the root files.
3. Skip specialist agents unless they would clearly help.

## If You Judge the Request COMPLEX:
You MUST follow this staged workflow:
1. Write or update solution.md with the page map, object model, feature scope, and integration plan.
2. Invoke object_builder to create or update files inside objects/ describing entities, attributes, relationships, validation rules, and representative state.
3. Invoke validator to review solution.md and objects/ before page generation.
4. Apply the validator feedback yourself with apply_diff or write_file, or re-invoke the owning specialist to repair its own files.
5. Invoke page_builder to create or update pages/ artifacts using the approved object model. Always include context_paths: ["solution.md", "objects", "pages"] so page_builder can read the object model and verify its own output.
6. After page_builder completes, do TWO mandatory greps before writing root files:
   a. Run grep('id="', 'pages/') to collect every DOM ID actually present in page fragments. Use those exact IDs in every getElementById() and querySelector('#...') call in app.js. Do NOT invent IDs — the HTML is the source of truth.
   b. Run grep('class="', 'pages/') to collect every CSS class used in page fragments. For every structural class (ending in -page, -grid, -card, -layout, -header, -body, -container, -form, -table, -list, -section, -stats, -panel, -sidebar) ensure a matching rule exists in styles.css.
   Then create or update index.html, styles.css, and app.js so the app routes between pages and feels consistent.
7. Invoke validator again to review the integrated result before you finish.
8. Fix any remaining issues before finalizing.

## Ownership and Unified Diff Rules:
- The orchestrator owns solution.md, index.html, styles.css, app.js, and any cross-cutting integration changes.
- object_builder owns objects/**.
- page_builder owns pages/**.
- validator is read-only. It must inspect files, return findings, and may suggest unified diffs, but it does not edit files itself.
- When validator suggests localized fixes, prefer apply_diff by the owning agent. If the fix spans many regions or much of a file, rewrite that file instead.
- Do not continue past validator failures or malformed validator output. Repair the owning layer first.
- Keep pages/** as the page-level source of truth. When integrating app.js, wire behavior into those fragments instead of duplicating page markup inline. Always grep pages/ for actual IDs before writing app.js — never assume what IDs page_builder used.
- When a page defines multiple tab or section containers (multiple IDs ending in -body, -tbody, -list, -stats), populate ALL of them in app.js. Never implement only the first tab and leave the rest empty.
- After writing app.js, run grep('class="', 'pages/') and ensure every structural CSS class used in pages/ has a matching rule in styles.css.
- On follow-up prompts, update existing objects and pages before inventing parallel structures.
- On follow-up styling or UI tweaks to an existing app, patch the current app files directly instead of re-running the full specialist pipeline unless the request changes data, routing, or page structure.
- Keep the user informed of progress."""
    elif profile.name == "object_builder":
        base_prompt += """

## Specialist Focus:
- You are responsible for objects/**.
- Read solution.md and any existing objects/** files before editing.
- For richer apps, create one file per core domain object or tightly related object family.
- Each object definition should capture attributes, relationships, derived data, validation rules, and representative examples.
- Prefer apply_diff when revising existing object files.
- Do not take ownership of pages/** or root integration files unless the orchestrator explicitly asks for it."""
    elif profile.name == "page_builder":
        base_prompt += """

## Specialist Focus:
- You are responsible for pages/**.
- Read solution.md, relevant objects/** files, and existing pages/** files before editing.
- Create real page-level HTML, CSS, and JavaScript artifacts with meaningful interactions, not placeholders.
- Keep multi-page flows consistent with the object model and navigation shell described in solution.md.
- Emit stable IDs and classes that app.js can wire directly. The orchestrator should enhance these fragments, not replace them with duplicate templates.
- Prefer apply_diff when revising existing page files.
- Do not take ownership of solution.md or root integration files unless the orchestrator explicitly asks for it."""
    elif profile.name == "validator":
        base_prompt += """

## Specialist Focus:
- You are a read-only validator. You do NOT edit files.
- Return only strict JSON — no prose before or after it.
- Use this exact shape:
  {
    "status": "VALID" | "INVALID",
    "summary": "short summary",
    "findings": [
      {
        "severity": "high" | "medium" | "low",
        "path": "relative/path",
        "owner": "orchestrator" | "object_builder" | "page_builder",
        "issue": "what is wrong",
        "why": "why it matters",
        "fix": "concrete fix suggestion",
        "diff": "@@ ... optional unified diff sketch ..."
      }
    ]
  }
- If status is INVALID, findings must be non-empty and ordered by severity.

## MANDATORY Validation Workflow — execute every step with tool calls:

### Step 1 — Inventory
Run glob("**/*") to list all workspace files.

### Step 2 — ID consistency (JavaScript → HTML)
Run grep("getElementById", "app.js") to get every DOM ID that app.js queries.
For EACH ID extracted from that grep output, run grep("<id_string>", "pages/") to confirm it exists in an HTML page.
Only report a mismatch if the grep confirms the ID is present in app.js BUT absent from all pages/*.html files.
NEVER report an ID as missing unless you have grep output proving it is absent.

### Step 3 — Route consistency
Run grep("path ===", "app.js") or grep("hash", "app.js") to extract all route strings handled by the router.
Run grep("href=\"#/", "") to find all link targets used in index.html and pages/*.
Report any link target that has no matching route handler, and any route handler that has no reachable link.

### Step 4 — CSS class consistency
Run grep("class=\"", "pages/") to collect structural class names used in HTML.
Run grep("<class_name>", "styles.css") for any class that looks like a layout shell (e.g. ends in -page, -shell, -layout, -grid, -container).
Report only classes that are genuinely missing from styles.css AND are structural (not utility or state classes).

### Step 5 — Object foreign key integrity
For each objects/*.json file, read it and verify that every foreign key value (e.g. userId, tripId) references a valid id in the corresponding object file.

### Step 6 — Required files
Confirm index.html, app.js, styles.css, solution.md, at least one objects/*.json, and at least one pages/*.html all exist.

## CRITICAL RULES:
- You MUST run the grep tool calls in steps 2–4. Do not skip them and do not infer from memory.
- A finding is only valid if you can cite the exact grep output (or file read) that proves the issue.
- If grep shows app.js uses an ID and the same ID appears in the HTML grep results, that ID is VALID — do not report it.
- If you have not yet grepped for an ID, you do not know if it is missing. Run the grep first.
- Do not report issues based on naming conventions, assumptions, or what IDs "should" be called.
- Focus on completeness, consistency between solution/object/page layers, navigation, asset references, and JavaScript wiring."""

    return base_prompt


class AgentRunner:
    def __init__(self) -> None:
        self.config = get_config()
        self.agentfs = AgentFS()
        self.registry = AgentRegistry()
        self.web_documents = json.loads((get_root_dir() / "backend/app/data/web_knowledge.json").read_text())
        self.toolbox = ToolBox(self.agentfs, self.invoke_agent, self.resolve_web, self.registry)
        self.metrics = AgentMetrics(get_metrics())
        self.tracer = get_tracer()
        self._context_managers: dict[str, ContextWindowManager] = {}

    def resolve_web(self, mode: str, value: str) -> Any:
        if mode == "search":
            query = value.lower()
            matches = []
            for document in self.web_documents:
                haystack = f"{document['title']} {document['body']}".lower()
                if any(term in haystack for term in query.split()):
                    matches.append(
                        {
                            "title": document["title"],
                            "url": document["url"],
                            "snippet": document["body"],
                        }
                    )
            return matches
        for document in self.web_documents:
            if document["url"] == value:
                return document
        raise ValueError(f"No local web document for {value}")

    async def emit_to_queue(self, sink: "asyncio.Queue[dict[str, Any]]", event_type: str, payload: dict[str, Any]) -> None:
        await sink.put({"type": event_type, "payload": payload})

    async def run_stream(
        self,
        prompt: str,
        app_id: str,
        stream_observer: StreamObserver | None = None,
    ) -> AsyncIterator[str]:
        self.agentfs.ensure_app(app_id)
        event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def emit(event_type: str, payload: dict[str, Any]) -> None:
            await self.emit_to_queue(event_queue, event_type, payload)

        worker = asyncio.create_task(self.run_agent(prompt, app_id, emit))

        try:
            while True:
                if worker.done() and event_queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=self.config.ui.stream_keepalive_ms / 1000)
                    if stream_observer is not None:
                        await stream_observer(event)
                    yield f"data: {json.dumps(event)}\n\n"
                except TimeoutError:
                    yield "data: {\"type\":\"status\",\"payload\":{\"message\":\"heartbeat\"}}\n\n"
            await worker
        except Exception as exc:
            error_event = {"type": "error", "payload": {"message": str(exc)}}
            if stream_observer is not None:
                await stream_observer(error_event)
            yield f"data: {json.dumps(error_event)}\n\n"

    async def run_agent(self, prompt: str, app_id: str, emit: EventEmitter) -> None:
        final_message = await self._run_loop(prompt=prompt, app_id=app_id, agent_name="orchestrator", emit=emit)
        recovery_notes = await self._enforce_complex_workflow(prompt, app_id, emit)
        if recovery_notes:
            final_message = "\n\n".join(part for part in (final_message, *recovery_notes) if part)

        validation_issues = self._validate_generated_app(app_id)
        if validation_issues and not final_message.startswith("LLM error:"):
            logger.warning("Validation failed for app_id=%s issues=%s", app_id, validation_issues)
            await stream_message(
                emit,
                "system",
                "assistant",
                "Validation found issues before finish:\n- " + "\n- ".join(validation_issues) + "\n\nRunning one repair pass.",
            )
            repair_prompt = self._build_repair_prompt(prompt, validation_issues)
            repair_before = self._workspace_fingerprint(app_id)
            final_message = await self._run_loop(
                prompt=repair_prompt,
                app_id=app_id,
                agent_name="orchestrator",
                emit=emit,
            )
            recovery_notes = await self._enforce_complex_workflow(prompt, app_id, emit)
            if recovery_notes:
                final_message = "\n\n".join(part for part in (final_message, *recovery_notes) if part)
            validation_issues = self._validate_generated_app(app_id)
            if self._workspace_fingerprint(app_id) == repair_before:
                no_progress_issue = "Repair pass made no non-internal file changes and appears to have stalled."
                if no_progress_issue not in validation_issues:
                    validation_issues.insert(0, no_progress_issue)
                await stream_message(
                    emit,
                    "system",
                    "assistant",
                    no_progress_issue,
                )

        if validation_issues and not final_message.startswith("LLM error:"):
            await stream_message(
                emit,
                "system",
                "assistant",
                "Validation still found issues:\n- " + "\n- ".join(validation_issues),
            )
            final_message = f"{final_message}\n\nValidation still found issues:\n- " + "\n- ".join(validation_issues)
        elif not final_message.startswith("LLM error:"):
            await stream_message(
                emit,
                "system",
                "assistant",
                "Validation passed: required files, asset references, and JavaScript wiring look consistent.",
            )

        await emit(
            "workspace",
            {
                "app_id": app_id,
                "entries": self.agentfs.list_entries(app_id),
                "files": self.agentfs.snapshot(app_id),
            },
        )
        await emit("final", {"message": final_message})

    def _specialist_artifact_issues(self, app_id: str, agent_name: str) -> list[str]:
        files = set(self.agentfs.list_files(app_id))
        issues: list[str] = []

        if agent_name == "object_builder":
            object_files = sorted(path for path in files if path.startswith("objects/") and path.endswith(".json"))
            if not object_files:
                issues.append("object_builder did not create any objects/*.json files.")
            elif object_files == ["objects/models.json"]:
                issues.append(
                    "object_builder created only objects/models.json. Create one file per core object or object family so later repairs can target specific artifacts."
                )
            return issues

        if agent_name == "page_builder":
            page_files = sorted(path for path in files if path.startswith("pages/") and path.endswith(".html"))
            if not page_files:
                issues.append("page_builder did not create any pages/*.html files.")
                return issues
            try:
                solution_text = self.agentfs.read_file(app_id, "solution.md", truncate=False)
            except FileNotFoundError:
                solution_text = ""
            expected_pages = _expected_page_paths_from_solution(solution_text)
            missing_pages = sorted(expected_pages - set(page_files))
            if missing_pages:
                issues.append(
                    "page_builder did not create all pages declared in solution.md: "
                    + ", ".join(missing_pages[:8])
                )
        return issues

    async def invoke_agent(
        self,
        app_id: str,
        name: str,
        instructions: str,
        context_paths: list[str],
        emit: EventEmitter,
    ) -> str:
        profile = self.registry.load_profile(name)
        isolated_prompt = instructions
        if context_paths:
            isolated_prompt = f"{instructions}. Work only from: {', '.join(context_paths)}"
        if context_paths:
            self.toolbox.set_path_constraints(app_id, profile.name, context_paths)
        try:
            attempted_models: list[str] = []
            result = ""
            retry_profiles = [profile]
            retry_profiles.extend(
                replace(profile, model_name=fallback_model)
                for fallback_model in getattr(profile, "fallback_models", [])
                if fallback_model and fallback_model != getattr(profile, "model_name", "")
            )
            recovered = False
            for attempt_index, attempt_profile in enumerate(retry_profiles):
                attempt_model_name = getattr(attempt_profile, "model_name", profile.name)
                max_attempts_for_model = 1 + NO_RESPONSE_RETRY_LIMIT
                for model_attempt in range(max_attempts_for_model):
                    attempted_models.append(attempt_model_name)
                    result = await self._run_loop(
                        prompt=isolated_prompt,
                        app_id=app_id,
                        agent_name=profile.name,
                        emit=emit,
                        profile_override=attempt_profile,
                    )
                    if result.strip() in NO_RESPONSE_MESSAGES and profile.name != "validator":
                        artifact_issues = self._specialist_artifact_issues(app_id, profile.name)
                        if not artifact_issues:
                            result = f"{profile.name} completed owned artifact generation."
                            await stream_message(
                                emit,
                                "system",
                                "assistant",
                                (
                                    f"{profile.name} produced owned artifacts but returned no final text. "
                                    "Accepting the artifacts and continuing."
                                ),
                            )
                    if result.strip() not in NO_RESPONSE_MESSAGES:
                        recovered = True
                        break
                    if model_attempt < max_attempts_for_model - 1:
                        await stream_message(
                            emit,
                            "system",
                            "assistant",
                            (
                                f"{profile.name} returned no usable content on {attempt_model_name}. "
                                f"Retrying the same model ({model_attempt + 2}/{max_attempts_for_model})."
                            ),
                        )
                if recovered:
                    break
                if attempt_index < len(retry_profiles) - 1:
                    next_model = getattr(retry_profiles[attempt_index + 1], "model_name", profile.name)
                    await stream_message(
                        emit,
                        "system",
                        "assistant",
                        (
                            f"{profile.name} returned no usable content on {attempt_model_name} after "
                            f"{max_attempts_for_model} attempts. Retrying with fallback model {next_model}."
                        ),
                    )
            if not recovered:
                attempt_counts = Counter(attempted_models)
                attempts_summary = ", ".join(
                    f"{model} x{count}" if count > 1 else model
                    for model, count in attempt_counts.items()
                )
                raise ValueError(
                    f"{profile.name} returned no usable content after {len(attempted_models)} attempts: {attempts_summary}."
                )
        finally:
            if context_paths:
                self.toolbox.clear_path_constraints(app_id, profile.name)

        if profile.name == "validator":
            parsed = _parse_validator_result(result)
            if parsed is None:
                raise ValueError(
                    "Validator did not return valid structured JSON with status VALID/INVALID and concrete findings."
                )
            return json.dumps(parsed, indent=2)

        artifact_issues = self._specialist_artifact_issues(app_id, profile.name)
        if artifact_issues:
            raise ValueError(" ".join(artifact_issues))

        # Enrich response with artifact summary so orchestrator knows what was created
        try:
            all_files = self.agentfs.list_files(app_id)
        except Exception:
            return result

        artifacts: list[str] = []
        if profile.name == "object_builder":
            artifacts = sorted(p for p in all_files if p.startswith("objects/") and p.endswith(".json"))
        elif profile.name == "page_builder":
            artifacts = sorted(p for p in all_files if p.startswith("pages/") and p.endswith(".html"))

        if artifacts:
            return f"{result}\n\n[Artifacts created: {', '.join(artifacts)}]"
        return result

    def _is_complex_request(self, prompt: str, app_id: str) -> bool:
        lowered_prompt = prompt.lower()
        hint_matches = sum(1 for hint in COMPLEX_REQUEST_HINTS if hint in lowered_prompt)
        if hint_matches >= 2:
            return True

        files = set(self.agentfs.list_files(app_id))
        has_existing_app_shell = {"index.html", "styles.css", "app.js"}.issubset(files)
        if has_existing_app_shell:
            return False

        if any(path.startswith("objects/") and path.endswith(".json") for path in files):
            return True

        try:
            solution = self.agentfs.read_file(app_id, "solution.md", truncate=False).lower()
        except FileNotFoundError:
            return False

        return "page map" in solution or "integration plan" in solution or "checkout" in solution or "admin" in solution

    def _has_agent_log_entry(self, app_id: str, agent_name: str) -> bool:
        try:
            logs = self.agentfs.load_json(app_id, ".internal/logs.json")
        except FileNotFoundError:
            return False

        return any(
            isinstance(entry, dict) and entry.get("agent") == agent_name
            for entry in logs
        )

    def _has_page_artifacts(self, app_id: str) -> bool:
        return any(
            path.startswith("pages/") and path.endswith(".html")
            for path in self.agentfs.list_files(app_id)
        )

    async def _enforce_complex_workflow(
        self,
        prompt: str,
        app_id: str,
        emit: EventEmitter,
    ) -> list[str]:
        if not self._is_complex_request(prompt, app_id):
            return []

        files = set(self.agentfs.list_files(app_id))
        has_objects = any(path.startswith("objects/") and path.endswith(".json") for path in files)
        if not has_objects:
            return []

        page_builder_ran = self._has_agent_log_entry(app_id, "page_builder")
        has_pages = self._has_page_artifacts(app_id)
        if page_builder_ran and has_pages:
            return []

        logger.warning(
            "Complex workflow incomplete app_id=%s page_builder_ran=%s has_pages=%s; forcing page_builder pass",
            app_id,
            page_builder_ran,
            has_pages,
        )
        await stream_message(
            emit,
            "system",
            "assistant",
            "Complex workflow recovery: object artifacts exist but page generation was not completed by page_builder. Running page_builder now to complete pages/** before final validation.",
        )
        try:
            result = await self.invoke_agent(
                app_id=app_id,
                name="page_builder",
                instructions=(
                    "Workflow recovery for a complex app. Read solution.md and all objects/*.json, then create or repair every required HTML fragment inside pages/. "
                    "Produce complete page artifacts for the full page map with meaningful structure and interactions, not placeholders. "
                    "Preserve any good existing work, but page_builder must own pages/** for this run."
                ),
                context_paths=["solution.md", "objects", "pages"],
                emit=emit,
            )
        except ValueError as exc:
            message = f"Workflow recovery failed: {exc}"
            await stream_message(emit, "system", "assistant", message)
            return [message]
        return [f"Workflow recovery executed: {result}"]

    def _build_repair_prompt(self, original_prompt: str, issues: list[str]) -> str:
        has_css_issue = any("css" in i.lower() or "class" in i.lower() or "selector" in i.lower() for i in issues)
        has_tab_issue = any("tab" in i.lower() or "-body" in i.lower() or "container" in i.lower() or "unpopulated" in i.lower() for i in issues)
        extra = ""
        if has_css_issue:
            extra += (
                "- CSS gap fix: run grep('class=\"', 'pages/') to collect every class name used in HTML fragments. "
                "For each structural class (ending in -page, -grid, -card, -layout, -header, -body, -container, "
                "-form, -table, -list, -section, -stats, -panel, -sidebar) that is missing from styles.css, "
                "add a rule. Fix all missing classes in a single write_file or apply_diff to styles.css.\n"
            )
        if has_tab_issue:
            extra += (
                "- Tab completeness fix: for every page that defines multiple tab/section content containers "
                "(IDs ending in -body, -tbody, -list, -stats), you MUST populate ALL of them in app.js — "
                "not just the first one. Read the HTML to find every container ID, then implement the "
                "corresponding render logic for each one.\n"
            )
        return (
            f"Repair the existing generated app for this request: {original_prompt}\n\n"
            "Validation found these issues:\n"
            + "\n".join(f"- {issue}" for issue in issues)
            + "\n\nRequirements:\n"
            "- Investigate with at most 3 consecutive read-only turns, then start editing. "
            "Do not spend 4 or more consecutive turns in read-only tools without writing a file.\n"
            "- Use grep and glob to compare routes, IDs, CSS classes, and object keys before editing.\n"
            "- Use apply_diff for localized fixes in one logical area, and switch to write_file for broader rewrites.\n"
            + extra
            + "- Keep the existing design unless a functional fix requires a layout change.\n"
            "- Ensure the app is usable in the live preview.\n"
            "- If a route already loads pages/** fragments, preserve those fragments as the source of truth and wire app.js into them instead of replacing them with duplicate markup.\n"
            '- If JavaScript initializes UI behavior, use a robust startup pattern: if document.readyState !== "loading", run init immediately, otherwise attach DOMContentLoaded once.\n'
            "- Finish with a concise summary of what you fixed."
        )

    def _validate_generated_app(self, app_id: str) -> list[str]:
        issues: list[str] = []
        files = set(self.agentfs.list_files(app_id))
        required_files = ("index.html", "styles.css", "app.js")

        if not files:
            return ["No application files were generated."]

        for required_file in required_files:
            if required_file not in files:
                issues.append(f"Missing required file: {required_file}")

        html_files = {
            path: self.agentfs.read_file(app_id, path, truncate=False)
            for path in files
            if path.endswith(".html")
        }
        css_files = {
            path: self.agentfs.read_file(app_id, path, truncate=False)
            for path in files
            if path.endswith(".css")
        }
        object_files = sorted(path for path in files if path.startswith("objects/") and path.endswith(".json"))
        page_files = sorted(path for path in files if path.startswith("pages/") and path.endswith(".html"))

        html = ""
        if "index.html" in files:
            html = html_files["index.html"]
            if "<script" not in html.lower():
                issues.append("index.html does not load any script, so interactive behavior is unlikely.")

        for html_path, html_content in html_files.items():
            for match in LOCAL_ASSET_PATTERN.finditer(html_content):
                raw_path = match.group(1).strip()
                if raw_path.startswith(("#", "/")) or URI_SCHEME_PATTERN.match(raw_path):
                    continue
                normalized_path = raw_path.split("?", 1)[0].split("#", 1)[0].lstrip("./")
                if normalized_path and normalized_path not in files:
                    issues.append(f"{html_path} references a missing local asset: {normalized_path}")

        js = ""
        if "app.js" in files:
            js = self.agentfs.read_file(app_id, "app.js", truncate=False)
            combined_html = "\n".join(html_files.values())
            if INTERACTIVE_HTML_PATTERN.search(combined_html) and not any(
                token in js for token in ("addEventListener(", ".onclick", "onsubmit", "querySelector(", "getElementById(")
            ):
                issues.append("app.js does not appear to bind interactive behavior to the generated HTML.")
            if "DOMContentLoaded" in js and "readyState" not in js:
                issues.append(
                    'app.js only initializes on DOMContentLoaded without a document.readyState fallback, which can leave the live preview non-interactive.'
                )

            node_path = shutil.which("node")
            if node_path:
                check = subprocess.run(
                    [node_path, "--check", str(self.agentfs.resolve_path(app_id, "app.js"))],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if check.returncode != 0:
                    issues.append(f"app.js has a JavaScript syntax error: {check.stderr.strip()}")
        elif INTERACTIVE_HTML_PATTERN.search(html):
            issues.append("Interactive HTML was generated without an app.js file.")

        # Check JS fetch() references for missing local files (e.g. objects/*.json)
        if js:
            for match in JS_FETCH_PATH_PATTERN.finditer(js):
                fetch_path = match.group(1).strip().lstrip("./")
                if fetch_path.startswith(("http://", "https://", "//")):
                    continue
                if fetch_path and fetch_path not in files:
                    issues.append(f"app.js fetches a missing local file: {fetch_path}")

        all_html_ids = set().union(*(_extract_html_ids(content) for content in html_files.values())) if html_files else set()
        if "#/" in js:
            route_hash_examples: list[str] = []
            for html_path, html_content in html_files.items():
                page_ids = _extract_html_ids(html_content)
                for match in HTML_ROUTE_HASH_PATTERN.finditer(html_content):
                    target = match.group(1).strip()
                    if target and target not in page_ids:
                        route_hash_examples.append(f"{html_path} -> #{target}")
            if route_hash_examples:
                issues.append(
                    "Found route-like hash links without the expected '#/' prefix: "
                    + ", ".join(route_hash_examples[:4])
                )

        if js:
            referenced_js_ids = _extract_js_ids(js)
            missing_js_ids = sorted(referenced_js_ids - all_html_ids)
            if missing_js_ids:
                issues.append(
                    "app.js references DOM ids that do not exist in the generated HTML/pages: "
                    + ", ".join(missing_js_ids[:6])
                )

            actionable_html_ids = _extract_actionable_html_ids(html_files)
            unwired_actionable_ids = sorted(actionable_html_ids - referenced_js_ids)
            if len(unwired_actionable_ids) >= 2:
                issues.append(
                    "Generated pages contain button/form ids with no matching JavaScript wiring: "
                    + ", ".join(unwired_actionable_ids[:8])
                )

            # Check for partial tab/section implementation: a page defines multiple data
            # container IDs (ending in -body, -tbody, -list, -stats, -container) but app.js
            # only wires some of them — classic sign of "first tab only" shortcuts.
            for page_path, page_html in html_files.items():
                if not page_path.startswith("pages/"):
                    continue
                container_ids = {
                    m.group(1)
                    for m in HTML_DATA_CONTAINER_ID_PATTERN.finditer(page_html)
                    if m.group(1)
                }
                if len(container_ids) < 2:
                    continue
                wired = container_ids & referenced_js_ids
                unwired = sorted(container_ids - referenced_js_ids)
                if wired and unwired:
                    issues.append(
                        f"{page_path} defines {len(container_ids)} data containers but only "
                        f"{len(wired)} are wired in app.js. Unpopulated: {', '.join(unwired[:4])}"
                    )

        if css_files and html_files:
            html_class_counts = _extract_html_class_counts(html_files)
            css_classes = _extract_css_classes(css_files)
            unstylled_classes = sorted(
                class_name
                for class_name, occurrences in html_class_counts.items()
                if class_name not in css_classes and _should_require_css_class(class_name, occurrences)
            )
            if unstylled_classes:
                issues.append(
                    "Generated HTML uses structural classes with no matching CSS selectors: "
                    + ", ".join(unstylled_classes[:8])
                )

        if object_files:
            if object_files == ["objects/models.json"]:
                issues.append(
                    "objects/ contains only a bundled objects/models.json file. Create one file per core object or object family so specialists can repair the model incrementally."
                )
            object_models: dict[str, dict[str, Any]] = {}
            for object_path in object_files:
                try:
                    payload = self.agentfs.load_json(app_id, object_path)
                except Exception as exc:
                    issues.append(f"{object_path} is not valid JSON: {exc}")
                    continue
                if isinstance(payload, dict):
                    stem = object_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    object_models[_normalize_object_key(stem)] = payload
                    payload_name = payload.get("name")
                    if isinstance(payload_name, str) and payload_name.strip():
                        object_models[_normalize_object_key(payload_name)] = payload
            issues.extend(_validate_object_models(object_models))

        try:
            solution_text = self.agentfs.read_file(app_id, "solution.md", truncate=False)
        except FileNotFoundError:
            solution_text = ""
        expected_pages = _expected_page_paths_from_solution(solution_text)
        if expected_pages:
            missing_pages = sorted(expected_pages - set(page_files))
            if missing_pages:
                issues.append(
                    "solution.md declares pages that were not generated: "
                    + ", ".join(missing_pages[:8])
                )

        deduped_issues: list[str] = []
        seen: set[str] = set()
        for issue in issues:
            if issue not in seen:
                seen.add(issue)
                deduped_issues.append(issue)
        return deduped_issues

    async def _run_loop(
        self,
        prompt: str,
        app_id: str,
        agent_name: str,
        emit: EventEmitter,
        profile_override: AgentProfile | None = None,
    ) -> str:
        profile = profile_override or self.registry.load_profile(agent_name)
        skill_bundle = self.registry.load_skill_bundle(profile.skills)
        started_at = time.perf_counter()
        timeout_ms = profile.execution.timeout_ms if hasattr(profile, "execution") else 120000
        max_turns = profile.execution.max_turns if hasattr(profile, "execution") else self.config.agent.max_turns
        max_parallel_tools = profile.execution.max_parallel_tools if hasattr(profile, "execution") else 1
        config_source = str((self.registry.root / profile.name / "config.yaml").resolve())
        
        # Set observability context
        CorrelationContext.set_agent_name(agent_name)
        
        # Get or create context manager for this session
        context_key = f"{app_id}:{agent_name}"
        if context_key not in self._context_managers:
            self._context_managers[context_key] = ContextWindowManager(
                ContextConfig(
                    max_tokens=profile.memory.max_tokens,
                    max_messages=profile.memory.max_messages,
                    compression_threshold=profile.memory.compression_threshold,
                )
            )
        context_manager = self._context_managers[context_key]
        
        logger.info(
            "Agent loop started agent=%s app_id=%s model=%s prompt_len=%s tools=%s max_turns=%s timeout_ms=%s max_parallel_tools=%s config_source=%s",
            agent_name,
            app_id,
            profile.model_name,
            len(prompt),
            profile.tools,
            max_turns,
            timeout_ms,
            max_parallel_tools,
            config_source,
        )
        
        log_agent_event("started", agent_name, app_id=app_id, prompt_len=len(prompt))
        self.metrics.record_agent_started(agent_name)
        
        await emit(
            "agent_started",
            {
                "agent": agent_name,
                "prompt": prompt,
                "is_subagent": profile.role != "orchestrator",
                "model": profile.model_name,
                "temperature": profile.temperature,
                "tools": profile.tools,
                "skills": profile.skills,
                "max_turns": max_turns,
                "timeout_ms": timeout_ms,
                "max_parallel_tools": max_parallel_tools,
                "config_source": config_source,
            },
        )

        # Build system prompt and LLM config
        workspace_summary = summarize_workspace_for_prompt(self.agentfs, app_id)
        system_prompt = build_system_prompt(profile, skill_bundle, prompt, workspace_summary)
        llm_config = LLMConfig(
            model=profile.model_name,
            temperature=profile.temperature,
            max_output_tokens=profile.max_output_tokens,
            top_p=profile.top_p,
            top_k=profile.top_k,
            thinking_budget=profile.thinking_budget,
        )

        # Create Gemini model instance
        model = GeminiModel(
            prompt=prompt,
            app_id=app_id,
            system_prompt=system_prompt,
            tools=profile.tools,
            config=llm_config,
        )

        final_message = ""
        final_message_streamed = False
        consecutive_read_only_turns = 0
        malformed_fc_retries = 0
        is_repair_run = prompt.startswith("Repair the existing generated app")

        for step in range(max_turns):
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            if timeout_ms is not None and elapsed_ms > timeout_ms:
                final_message = f"{agent_name} hit timeout_ms={timeout_ms}"
                break
            # Check if context compression is needed
            if profile.memory.compression_enabled:
                history = model._history() if hasattr(model, '_history') else []
                if history and context_manager.should_compress(history):
                    compressed_history = context_manager.compress_history(history)
                    compression_context = context_manager.get_compression_context()
                    logger.info(
                        "Compressing context agent=%s step=%s history_len=%s compressed_len=%s",
                        agent_name,
                        step,
                        len(history),
                        len(compressed_history),
                    )
                    if hasattr(model, "compress_history"):
                        model.compress_history(compressed_history, compression_context)

            try:
                message, tool_calls = model.generate()
                malformed_fc_retries = 0  # reset on success
            except Exception as e:
                if "MALFORMED_FUNCTION_CALL" in str(e) and malformed_fc_retries < MALFORMED_FC_RETRY_LIMIT:
                    malformed_fc_retries += 1
                    logger.warning(
                        "MALFORMED_FUNCTION_CALL retry %s/%s agent=%s app_id=%s step=%s model=%s",
                        malformed_fc_retries,
                        MALFORMED_FC_RETRY_LIMIT,
                        agent_name,
                        app_id,
                        step,
                        profile.model_name,
                    )
                    await stream_message(
                        emit,
                        agent_name,
                        "assistant",
                        f"Retrying after malformed function call ({malformed_fc_retries}/{MALFORMED_FC_RETRY_LIMIT}).",
                    )
                    continue
                logger.exception(
                    "Agent loop LLM failure agent=%s app_id=%s step=%s model=%s",
                    agent_name,
                    app_id,
                    step,
                    profile.model_name,
                )
                final_message = f"LLM error: {str(e)}"
                await stream_message(emit, agent_name, "assistant", final_message)
                final_message_streamed = True
                break

            # Handle text response (end of turn or final message)
            if message and not tool_calls:
                await stream_message(emit, agent_name, "assistant", message)
                final_message = message
                final_message_streamed = True
                break

            # Handle tool calls
            if tool_calls:
                overflow_calls = []
                if len(tool_calls) > max_parallel_tools:
                    overflow_calls = tool_calls[max_parallel_tools:]
                    tool_calls = tool_calls[:max_parallel_tools]
                tool_outputs: list[dict[str, Any]] = []
                for tool_call in tool_calls:
                    tool_name = tool_call["tool"]
                    payload = tool_call["payload"]

                    try:
                        result = await self.toolbox.execute(app_id, profile, agent_name, tool_name, payload, emit)
                        tool_outputs.append(
                            {
                                "tool": tool_name,
                                "response": {"result": result},
                            }
                        )
                    except Exception as e:
                        error_msg = str(e)
                        tool_outputs.append(
                            {
                                "tool": tool_name,
                                "response": {"error": error_msg},
                            }
                        )
                for overflow_call in overflow_calls:
                    tool_outputs.append(
                        {
                            "tool": overflow_call["tool"],
                            "response": {
                                "error": (
                                    f"Too many tool calls requested in one turn ({len(tool_calls) + len(overflow_calls)}). "
                                    f"Replan with at most {max_parallel_tools} tool calls per turn."
                                )
                            },
                        }
                    )

                if tool_outputs:
                    model.add_tool_outputs(tool_outputs)

                # Emit progress so UI knows agent is working (especially for tool-call-only agents)
                tool_names = [tc["tool"] for tc in tool_calls]
                await emit(
                    "agent_progress",
                    {
                        "agent": agent_name,
                        "step": step + 1,
                        "max_steps": max_turns,
                        "tools_used": tool_names,
                        "elapsed_ms": round((time.perf_counter() - started_at) * 1000),
                    },
                )

                if all(tool_call["tool"] in READ_ONLY_TOOLS for tool_call in tool_calls):
                    consecutive_read_only_turns += 1
                else:
                    consecutive_read_only_turns = 0
                if is_repair_run and consecutive_read_only_turns >= 10:
                    final_message = (
                        f"LLM error: {agent_name} stalled in read-only repair loop after "
                        f"{consecutive_read_only_turns} turns without editing files. "
                        "Repair pass must use write_file or apply_diff or invoke_agent to fix issues."
                    )
                    await stream_message(emit, agent_name, "assistant", final_message)
                    final_message_streamed = True
                    break
            else:
                # No message and no tool call — generate() should raise before returning (None, None),
                # but guard here in case of unexpected SDK behaviour.
                final_message = (
                    f"LLM error: {agent_name} returned empty response (no text, no tool calls). "
                    "Thinking tokens may have consumed all of max_output_tokens. "
                    "Increase max_output_tokens or lower thinking_budget in the agent config."
                )
                await stream_message(emit, agent_name, "assistant", final_message)
                final_message_streamed = True
                break
        else:
            final_message = f"{agent_name} hit max_turns={max_turns}"

        if final_message and not final_message_streamed:
            await stream_message(emit, agent_name, "assistant", final_message)

        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        self._append_log(app_id, agent_name, final_message, profile)
        
        # Record metrics
        success = not final_message.startswith("LLM error:")
        self.metrics.record_agent_finished(agent_name, duration_ms, success)
        log_agent_event(
            "finished",
            agent_name,
            app_id=app_id,
            duration_ms=duration_ms,
            success=success,
        )
        
        logger.info(
            "Agent loop finished agent=%s app_id=%s duration_ms=%s final_len=%s",
            agent_name,
            app_id,
            duration_ms,
            len(final_message),
        )
        await emit(
            "agent_finished",
            {
                "agent": agent_name,
                "final_message": final_message,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
                "is_subagent": profile.role != "orchestrator",
            },
        )
        return final_message

    def _workspace_fingerprint(self, app_id: str) -> str:
        digest = hashlib.sha1()
        for relative_path in sorted(path for path in self.agentfs.list_files(app_id) if not path.startswith(".internal/")):
            digest.update(relative_path.encode("utf-8"))
            digest.update(b"\0")
            try:
                content = self.agentfs.read_file(app_id, relative_path, truncate=False)
            except FileNotFoundError:
                continue
            digest.update(content.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()

    def _append_log(self, app_id: str, agent_name: str, message: str, profile: AgentProfile) -> None:
        try:
            logs = list(self.agentfs.load_json(app_id, ".internal/logs.json"))
        except FileNotFoundError:
            logs = []
        logs.append(
            {
                "agent": agent_name,
                "model": profile.model_name,
                "tools": profile.tools,
                "skills": profile.skills,
                "max_turns": profile.execution.max_turns,
                "timeout_ms": profile.execution.timeout_ms,
                "max_parallel_tools": profile.execution.max_parallel_tools,
                "config_source": str((self.registry.root / profile.name / "config.yaml").resolve()),
                "message": message,
            }
        )
        self.agentfs.save_json(app_id, ".internal/logs.json", logs)
