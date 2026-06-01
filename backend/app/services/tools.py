from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shlex
import shutil
import subprocess
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.services.agentfs import AgentFS
from app.services.agent_registry import AgentProfile, AgentRegistry
from app.services.diffing import analyze_unified_diff, apply_unified_diff, choose_patch_strategy, validate_diff
from app.services.tool_schemas import validate_tool_input, validate_tool_output, ToolSchemaValidator
from app.services.observability import AgentMetrics, log_tool_event, get_metrics


EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]
TaskRunner = Callable[[str, str, str, list[str], EventEmitter], Awaitable[str]]
WebResolver = Callable[[str, str], Any]
logger = logging.getLogger("app.services.tools")


# Bash command security configuration
ALLOWED_BASH_COMMANDS = {
    "python3": {"args_allowed": True, "timeout": 30},
    "python": {"args_allowed": True, "timeout": 30},
    "ls": {"args_allowed": True, "timeout": 5},
    "pwd": {"args_allowed": False, "timeout": 5},
    "echo": {"args_allowed": True, "timeout": 5},
    "cat": {"args_allowed": True, "timeout": 10},
    "mkdir": {"args_allowed": True, "timeout": 5},
    "find": {"args_allowed": True, "timeout": 30},
    "head": {"args_allowed": True, "timeout": 10},
    "tail": {"args_allowed": True, "timeout": 10},
    "wc": {"args_allowed": True, "timeout": 5},
    "grep": {"args_allowed": True, "timeout": 10},
    "sort": {"args_allowed": True, "timeout": 10},
}

# Dangerous argument patterns to block
DANGEROUS_ARG_PATTERNS = [
    r"-exec\s",           # find -exec can run arbitrary commands
    r"-c\s+['\"]",        # python -c, bash -c
    r"eval\s",            # eval command
    r"\$\(",              # command substitution
    r"`",                 # backtick command substitution
    r";\s*rm",            # chained rm
    r"\|\s*sh",           # piping to shell
    r"\|\s*bash",         # piping to bash
    r">\s*/",             # redirect to root
    r">>\s*/",            # append to root
    r"--version",         # Often used in exploit probes
]

# Blocked network-related patterns
BLOCKED_NETWORK_PATTERNS = [
    "curl", "wget", "http://", "https://", "ftp://",
    "nc ", "netcat", "telnet", "ssh ", "scp ",
]


class ToolBox:
    def __init__(
        self,
        agentfs: AgentFS,
        task_runner: TaskRunner,
        web_resolver: WebResolver,
        registry: AgentRegistry | None = None,
    ) -> None:
        self.agentfs = agentfs
        self.task_runner = task_runner
        self.web_resolver = web_resolver
        self.registry = registry
        self.apply_diff_failures: dict[tuple[str, str], int] = {}
        self.path_constraints: dict[tuple[str, str], tuple[str, ...]] = {}
        self._file_diff_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.validator = ToolSchemaValidator()
        self.metrics = AgentMetrics(get_metrics())
        
        # Keep backward compatibility
        self.allowed_bash_prefixes = set(ALLOWED_BASH_COMMANDS.keys())

    def set_path_constraints(self, app_id: str, agent_name: str, paths: list[str]) -> None:
        normalized = tuple(
            sorted(
                {
                    path.strip().strip("/")
                    for path in paths
                    if isinstance(path, str) and path.strip()
                }
            )
        )
        if normalized:
            self.path_constraints[(app_id, agent_name)] = normalized

    def clear_path_constraints(self, app_id: str, agent_name: str) -> None:
        self.path_constraints.pop((app_id, agent_name), None)

    def _is_path_allowed(self, app_id: str, agent_name: str, relative_path: str) -> bool:
        allowed = self.path_constraints.get((app_id, agent_name))
        if not allowed:
            return True
        normalized = relative_path.strip().lstrip("./").strip("/")
        return any(
            normalized == prefix or normalized.startswith(f"{prefix}/")
            for prefix in allowed
        )

    def _filter_allowed_paths(self, app_id: str, agent_name: str, paths: list[str]) -> list[str]:
        return [path for path in paths if self._is_path_allowed(app_id, agent_name, path)]

    def _filter_allowed_matches(self, app_id: str, agent_name: str, matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            match
            for match in matches
            if isinstance(match, dict) and self._is_path_allowed(app_id, agent_name, str(match.get("path", "")))
        ]

    def _effective_tool_timeout_ms(
        self,
        profile: AgentProfile,
        tool_name: str,
        payload: dict[str, Any],
    ) -> int | None:
        timeout_ms = profile.get_tool_config(tool_name).timeout_ms
        if timeout_ms is not None:
            timeout_ms = max(timeout_ms, 1)
        if tool_name != "invoke_agent" or self.registry is None:
            return timeout_ms

        target_name = payload.get("agent")
        if not isinstance(target_name, str) or not target_name.strip():
            return timeout_ms

        try:
            target_profile = self.registry.load_profile(target_name)
        except Exception:
            return timeout_ms

        fallback_models = {
            model
            for model in target_profile.fallback_models
            if model and model != target_profile.model_name
        }
        attempts = 1 + len(fallback_models)
        if target_profile.execution.timeout_ms is None:
            return None
        subagent_budget_ms = target_profile.execution.timeout_ms * attempts
        if timeout_ms is None:
            return subagent_budget_ms + 30000
        return max(timeout_ms, subagent_budget_ms + 30000)

    async def execute(
        self,
        app_id: str,
        profile: AgentProfile,
        agent_name: str,
        tool_name: str,
        payload: dict[str, Any],
        emit: EventEmitter,
    ) -> Any:
        if tool_name not in profile.tools:
            raise ValueError(f"Tool '{tool_name}' is not enabled for agent '{profile.name}'")
        
        # Input validation
        validation_errors = validate_tool_input(tool_name, payload)
        if validation_errors:
            error_msg = f"Input validation failed: {'; '.join(validation_errors)}"
            logger.warning(
                "Tool input validation failed tool=%s agent=%s errors=%s",
                tool_name,
                agent_name,
                validation_errors,
            )
            raise ValueError(error_msg)
        
        started_at = time.perf_counter()
        await emit(
            "tool_started",
            {
                "agent": agent_name,
                "tool": tool_name,
                "input": payload,
                "started_at": started_at,
            },
        )
        
        log_tool_event("started", tool_name, agent_name, payload=payload)
        
        try:
            effective_timeout_ms = self._effective_tool_timeout_ms(profile, tool_name, payload)
            if effective_timeout_ms is None:
                result = await getattr(self, tool_name)(app_id, agent_name, payload, emit)
            else:
                result = await asyncio.wait_for(
                    getattr(self, tool_name)(app_id, agent_name, payload, emit),
                    timeout=effective_timeout_ms / 1000,
                )
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            
            # Output validation (log warning but don't fail)
            output_errors = validate_tool_output(tool_name, result)
            if output_errors:
                logger.warning(
                    "Tool output validation warnings tool=%s agent=%s errors=%s",
                    tool_name,
                    agent_name,
                    output_errors,
                )
            
            self.metrics.record_tool_invocation(tool_name, agent_name, True, duration_ms)
            log_tool_event("finished", tool_name, agent_name, duration_ms=duration_ms, success=True)
            
        except asyncio.TimeoutError as exc:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            error = ValueError(f"Tool '{tool_name}' timed out after {effective_timeout_ms}ms")
            self.metrics.record_tool_invocation(tool_name, agent_name, False, duration_ms)
            log_tool_event("failed", tool_name, agent_name, duration_ms=duration_ms, error=str(error))
            await emit(
                "tool_finished",
                {
                    "agent": agent_name,
                    "tool": tool_name,
                    "input": payload,
                    "error": str(error),
                    "duration_ms": duration_ms,
                },
            )
            raise error from exc
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            self.metrics.record_tool_invocation(tool_name, agent_name, False, duration_ms)
            log_tool_event("failed", tool_name, agent_name, duration_ms=duration_ms, error=str(exc))
            
            await emit(
                "tool_finished",
                {
                    "agent": agent_name,
                    "tool": tool_name,
                    "input": payload,
                    "error": str(exc),
                    "duration_ms": duration_ms,
                },
            )
            raise

        await emit(
            "tool_finished",
            {
                "agent": agent_name,
                "tool": tool_name,
                "input": payload,
                "output": result,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            },
        )
        return result

    async def read_file(
        self,
        app_id: str,
        agent_name: str,
        payload: dict[str, Any],
        emit: EventEmitter,
    ) -> str:
        if not self._is_path_allowed(app_id, agent_name, payload["path"]):
            raise ValueError(f"Path '{payload['path']}' is outside the allowed read scope for {agent_name}")
        return self.agentfs.read_file(
            app_id,
            payload["path"],
            payload.get("start"),
            payload.get("end"),
            payload.get("summary", False),
        )

    async def write_file(
        self,
        app_id: str,
        agent_name: str,
        payload: dict[str, Any],
        emit: EventEmitter,
    ) -> str:
        content = payload["content"]
        path = payload["path"]
        if Path(path).suffix == ".json":
            json.loads(content)
        self.agentfs.write_file(app_id, path, content)
        byte_count = len(content.encode("utf-8"))
        line_count = content.count("\n") + 1
        return f"wrote {path} ({line_count} lines, {byte_count} bytes)"

    async def apply_diff(
        self,
        app_id: str,
        agent_name: str,
        payload: dict[str, Any],
        emit: EventEmitter,
    ) -> str:
        path = payload["path"]
        lock_key = f"{app_id}:{path}"
        async with self._file_diff_locks[lock_key]:
            original = self.agentfs.read_file(app_id, path)
            diff_text = payload["diff"]

            # Pre-validate: check all hunks are locatable before attempting apply
            validation = validate_diff(original, diff_text)
            if not validation.applicable:
                failure_key = (app_id, path)
                failure_count = self.apply_diff_failures.get(failure_key, 0) + 1
                self.apply_diff_failures[failure_key] = failure_count
                issues_summary = "; ".join(validation.hunk_issues[:3])
                hint = (
                    f"Diff pre-validation failed for {path}: {validation.reason}. {issues_summary}. "
                    f"Re-read {path} to get its current content and regenerate the diff with correct context lines."
                )
                if failure_count > 1:
                    hint = (
                        f"Diff pre-validation failed {failure_count} times for {path}. "
                        f"Stop retrying the diff — use write_file to rewrite the file instead."
                    )
                raise ValueError(hint)

            stats = analyze_unified_diff(original, diff_text, path)
            strategy, reason = choose_patch_strategy(stats)
            if strategy != "diff":
                raise ValueError(
                    f"This edit is too broad for safe unified diffing because {reason}. "
                    f"Read the latest version of {path} and use write_file for a full rewrite."
                )
            try:
                updated = apply_unified_diff(original, diff_text, path)
            except Exception as exc:
                failure_key = (app_id, path)
                failure_count = self.apply_diff_failures.get(failure_key, 0) + 1
                self.apply_diff_failures[failure_key] = failure_count
                fallback_hint = (
                    f"{exc}. apply_diff is best for small, localized edits after rereading the latest file. "
                    f"Read the latest version of {path} and use write_file if the change spans multiple regions."
                )
                if failure_count > 1:
                    fallback_hint = (
                        f"{exc}. apply_diff has failed {failure_count} times for {path}. "
                        "Stop retrying the diff and rewrite the file with write_file instead."
                    )
                raise ValueError(fallback_hint) from exc
            self.apply_diff_failures.pop((app_id, path), None)
            self.agentfs.write_file(app_id, path, updated)
            return f"patched {path} ({reason})"

    async def search(
        self,
        app_id: str,
        agent_name: str,
        payload: dict[str, Any],
        emit: EventEmitter,
    ) -> list[dict]:
        return self.agentfs.search(app_id, payload["query"], payload.get("path"))

    async def grep(
        self,
        app_id: str,
        agent_name: str,
        payload: dict[str, Any],
        emit: EventEmitter,
    ) -> list[dict]:
        matches = self.agentfs.search(app_id, payload["query"], payload.get("path"))
        return self._filter_allowed_matches(app_id, agent_name, matches)

    async def glob(
        self,
        app_id: str,
        agent_name: str,
        payload: dict[str, Any],
        emit: EventEmitter,
    ) -> list[str]:
        matches = self.agentfs.glob(app_id, payload["pattern"], payload.get("path"))
        return self._filter_allowed_paths(app_id, agent_name, matches)

    async def todos(
        self,
        app_id: str,
        agent_name: str,
        payload: dict[str, Any],
        emit: EventEmitter,
    ) -> list[dict]:
        relative_path = ".internal/todos.json"
        try:
            todos = list(self.agentfs.load_json(app_id, relative_path))
        except FileNotFoundError:
            todos = []
        action = payload["action"]
        if action == "replace":
            todos = payload["items"]
        elif action == "mark_done":
            task_id = payload["id"]
            for todo in todos:
                if todo["id"] == task_id:
                    todo["status"] = "done"
                    break
        elif action == "mark_in_progress":
            task_id = payload["id"]
            for todo in todos:
                if todo["id"] == task_id:
                    todo["status"] = "in_progress"
                    break
        else:
            raise ValueError(f"Unsupported todos action: {action}")
        self.agentfs.save_json(app_id, relative_path, todos)
        return todos

    async def bash(
        self,
        app_id: str,
        agent_name: str,
        payload: dict[str, Any],
        emit: EventEmitter,
    ) -> dict[str, Any]:
        command = payload["command"]
        
        # Parse command
        try:
            parts = shlex.split(command)
        except ValueError as e:
            raise ValueError(f"Invalid command syntax: {e}")
        
        if not parts:
            raise ValueError("Empty bash command")
        
        cmd_name = parts[0]
        
        # Check if command is allowed
        if cmd_name not in ALLOWED_BASH_COMMANDS:
            raise ValueError(
                f"Command '{cmd_name}' is not allowed. "
                f"Allowed commands: {', '.join(sorted(ALLOWED_BASH_COMMANDS.keys()))}"
            )
        
        cmd_config = ALLOWED_BASH_COMMANDS[cmd_name]
        
        # Check if arguments are allowed for this command
        if not cmd_config["args_allowed"] and len(parts) > 1:
            raise ValueError(f"Command '{cmd_name}' does not accept arguments")
        
        # Check for network-related commands/patterns
        for pattern in BLOCKED_NETWORK_PATTERNS:
            if pattern in command.lower():
                raise ValueError("Network-related commands are blocked in this POC")
        
        # Check for dangerous argument patterns
        for pattern in DANGEROUS_ARG_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                raise ValueError(f"Potentially dangerous command pattern detected")
        
        # Additional security checks for specific commands
        if cmd_name in ("python3", "python"):
            # Block -c flag with inline code (potential RCE)
            if "-c" in parts:
                raise ValueError("Python -c flag is not allowed for security reasons")
            # Block eval/exec in scripts
            if any("eval" in arg or "exec" in arg for arg in parts[1:]):
                raise ValueError("Python eval/exec is not allowed for security reasons")
        
        if cmd_name == "find":
            # Block -exec and -delete
            if any(arg in ("-exec", "-delete", "-execdir") for arg in parts):
                raise ValueError("find -exec/-delete/-execdir is not allowed for security reasons")
        
        # Get timeout from config
        timeout = cmd_config.get("timeout", 30)
        
        logger.info(
            "Executing bash command app_id=%s agent=%s cmd=%s timeout=%s",
            app_id,
            agent_name,
            cmd_name,
            timeout,
        )
        
        try:
            process = await asyncio.create_subprocess_exec(
                *parts,
                cwd=str(self.agentfs.app_path(app_id)),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise ValueError(f"Command timed out after {timeout} seconds")
            
            result = {
                "exit_code": process.returncode,
                "stdout": stdout.decode(errors="replace"),
                "stderr": stderr.decode(errors="replace"),
            }
            
            # Log non-zero exit codes
            if process.returncode != 0:
                logger.warning(
                    "Bash command returned non-zero exit_code=%s cmd=%s stderr=%s",
                    process.returncode,
                    cmd_name,
                    result["stderr"][:200],
                )
            
            return result
            
        except FileNotFoundError:
            raise ValueError(f"Command '{cmd_name}' not found on this system")

    async def invoke_agent(
        self,
        app_id: str,
        agent_name: str,
        payload: dict[str, Any],
        emit: EventEmitter,
    ) -> str:
        return await self.task_runner(
            app_id,
            payload["agent"],
            payload["instructions"],
            payload.get("context_paths", []),
            emit,
        )

    async def web_search(
        self,
        app_id: str,
        agent_name: str,
        payload: dict[str, Any],
        emit: EventEmitter,
    ) -> list[dict[str, str]]:
        return self.web_resolver("search", payload["query"])

    async def web_fetch(
        self,
        app_id: str,
        agent_name: str,
        payload: dict[str, Any],
        emit: EventEmitter,
    ) -> dict[str, str]:
        return self.web_resolver("fetch", payload["url"])

    # ── Phase 1 tools: close the feedback loop ──────────────────────

    _LOCAL_ASSET_RE = re.compile(r"""(?:src|href)=["']([^"']+)["']""", re.IGNORECASE)
    _URI_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
    _HTML_ID_RE = re.compile(r"""\bid=["']([^"']+)["']""", re.IGNORECASE)
    _HTML_CLASS_ATTR_RE = re.compile(r"""\bclass=["']([^"']+)["']""", re.IGNORECASE)
    _CSS_CLASS_SEL_RE = re.compile(r"""(?<![A-Za-z0-9_-])\.([A-Za-z_][A-Za-z0-9_-]*)""")
    _JS_GETID_RE = re.compile(r"""getElementById\(\s*["']([^"']+)["']\s*\)""")
    _JS_QS_ID_RE = re.compile(r"""querySelector(?:All)?\(\s*["']#([A-Za-z][A-Za-z0-9:_-]*)["']\s*\)""")
    _INTERACTIVE_RE = re.compile(r"<(button|input|select|textarea|form)\b", re.IGNORECASE)
    _ACTIONABLE_ID_RE = re.compile(r"""<(button|form)\b[^>]*\bid=["']([^"']+)["']""", re.IGNORECASE)
    _DATA_CONTAINER_ID_RE = re.compile(r"""\bid=["']([\w-]+(?:-body|-tbody|-list|-stats|-container))["']""", re.IGNORECASE)
    _STYLE_IGNORE = {"active", "btn", "button", "card", "container", "current", "disabled", "error", "hidden", "input", "loading", "open", "selected", "success"}
    _STYLE_SUFFIXES = ("-actions", "-btn", "-card", "-content", "-form", "-grid", "-hero", "-item", "-layout", "-list", "-nav", "-page", "-panel", "-section", "-summary", "-table")

    async def validate_workspace(
        self,
        app_id: str,
        agent_name: str,
        payload: dict[str, Any],
        emit: EventEmitter,
    ) -> dict[str, Any]:
        """Run structural validation on the workspace and return issues the agent can fix."""
        issues: list[str] = []
        files = set(self.agentfs.list_files(app_id))

        if not files:
            return {"status": "error", "issues": ["No application files were generated."]}

        # Required files
        for rf in ("index.html", "styles.css", "app.js"):
            if rf not in files:
                issues.append(f"Missing required file: {rf}")

        # Read all relevant files once
        html_files: dict[str, str] = {}
        for p in files:
            if p.endswith(".html"):
                try:
                    html_files[p] = self.agentfs.read_file(app_id, p, truncate=False)
                except Exception:
                    issues.append(f"Could not read {p}")

        css_content = ""
        if "styles.css" in files:
            try:
                css_content = self.agentfs.read_file(app_id, "styles.css", truncate=False)
            except Exception:
                pass

        js = ""
        if "app.js" in files:
            try:
                js = self.agentfs.read_file(app_id, "app.js", truncate=False)
            except Exception:
                issues.append("Could not read app.js")

        # index.html must load a script
        if "index.html" in html_files:
            if "<script" not in html_files["index.html"].lower():
                issues.append("index.html does not load any script.")

        # Asset references must resolve
        for html_path, html_content in html_files.items():
            for match in self._LOCAL_ASSET_RE.finditer(html_content):
                raw = match.group(1).strip()
                if raw.startswith(("#", "/")) or self._URI_SCHEME_RE.match(raw):
                    continue
                normalized = raw.split("?", 1)[0].split("#", 1)[0].lstrip("./")
                if normalized and normalized not in files:
                    issues.append(f"{html_path} references missing asset: {normalized}")

        # JS syntax check
        if js:
            node_path = shutil.which("node")
            if node_path:
                try:
                    check = subprocess.run(
                        [node_path, "--check", str(self.agentfs.resolve_path(app_id, "app.js"))],
                        capture_output=True, text=True, timeout=10, check=False,
                    )
                    if check.returncode != 0:
                        issues.append(f"app.js syntax error: {check.stderr.strip()}")
                except Exception:
                    pass

        # JS fetch() references must resolve to existing files
        if js:
            fetch_re = re.compile(r"""fetch\(\s*[`"']([^`"'$]+)[`"']\s*\)""")
            for match in fetch_re.finditer(js):
                fetch_path = match.group(1).strip().lstrip("./")
                if fetch_path.startswith(("http://", "https://", "//")):
                    continue
                if fetch_path and fetch_path not in files:
                    issues.append(f"app.js fetches missing file: {fetch_path}")

        # JS must bind interactive elements
        combined_html = "\n".join(html_files.values())
        if js and self._INTERACTIVE_RE.search(combined_html):
            if not any(tok in js for tok in ("addEventListener(", ".onclick", "onsubmit", "querySelector(", "getElementById(")):
                issues.append("app.js does not bind interactive behavior to the HTML.")

        # DOM ID cross-reference check
        all_html_ids: set[str] = set()
        for content in html_files.values():
            all_html_ids.update(m.group(1).strip() for m in self._HTML_ID_RE.finditer(content) if m.group(1).strip())

        if js:
            js_ids: set[str] = set()
            js_ids.update(m.group(1).strip() for m in self._JS_GETID_RE.finditer(js))
            js_ids.update(m.group(1).strip() for m in self._JS_QS_ID_RE.finditer(js))
            js_ids.discard("")

            missing_ids = sorted(js_ids - all_html_ids)
            if missing_ids:
                issues.append(f"app.js references DOM ids not in HTML: {', '.join(missing_ids[:8])}")

            # Unwired interactive elements
            actionable_ids: set[str] = set()
            for content in html_files.values():
                actionable_ids.update(m.group(2).strip() for m in self._ACTIONABLE_ID_RE.finditer(content) if m.group(2).strip())
            unwired = sorted(actionable_ids - js_ids)
            if len(unwired) >= 2:
                issues.append(f"Button/form ids with no JS wiring: {', '.join(unwired[:8])}")

            # Partial tab/container implementation
            for page_path, page_html in html_files.items():
                if not page_path.startswith("pages/"):
                    continue
                container_ids = {m.group(1) for m in self._DATA_CONTAINER_ID_RE.finditer(page_html) if m.group(1)}
                if len(container_ids) < 2:
                    continue
                wired = container_ids & js_ids
                unwired_containers = sorted(container_ids - js_ids)
                if wired and unwired_containers:
                    issues.append(f"{page_path}: {len(container_ids)} data containers but only {len(wired)} wired. Unpopulated: {', '.join(unwired_containers[:4])}")

        # CSS class coverage
        if css_content and html_files:
            html_class_counts: Counter[str] = Counter()
            for content in html_files.values():
                for match in self._HTML_CLASS_ATTR_RE.finditer(content):
                    for cls in match.group(1).split():
                        cls = cls.strip()
                        if cls:
                            html_class_counts[cls] += 1

            css_classes: set[str] = set()
            css_classes.update(m.group(1) for m in self._CSS_CLASS_SEL_RE.finditer(css_content))

            unstyled = sorted(
                cls for cls, count in html_class_counts.items()
                if cls not in css_classes and cls not in self._STYLE_IGNORE
                and not cls.startswith(("js-", "is-", "has-"))
                and (count >= 2 or any(cls.endswith(s) for s in self._STYLE_SUFFIXES))
            )
            if unstyled:
                issues.append(f"HTML classes with no CSS: {', '.join(unstyled[:8])}")

        # Deduplicate
        seen: set[str] = set()
        deduped = []
        for issue in issues:
            if issue not in seen:
                seen.add(issue)
                deduped.append(issue)

        status = "pass" if not deduped else "fail"
        return {"status": status, "issue_count": len(deduped), "issues": deduped}

    async def validate_syntax(
        self,
        app_id: str,
        agent_name: str,
        payload: dict[str, Any],
        emit: EventEmitter,
    ) -> dict[str, Any]:
        """Check syntax of a single file. Supports .js, .json, .html, .css."""
        path = payload["path"]
        try:
            content = self.agentfs.read_file(app_id, path, truncate=False)
        except FileNotFoundError:
            return {"valid": False, "path": path, "errors": [f"File not found: {path}"]}

        errors: list[str] = []
        ext = Path(path).suffix.lower()

        if ext == ".js":
            node_path = shutil.which("node")
            if node_path:
                try:
                    check = subprocess.run(
                        [node_path, "--check", str(self.agentfs.resolve_path(app_id, path))],
                        capture_output=True, text=True, timeout=10, check=False,
                    )
                    if check.returncode != 0:
                        errors.append(check.stderr.strip())
                except subprocess.TimeoutExpired:
                    errors.append("Syntax check timed out")
            else:
                errors.append("node not available for JS syntax check")

        elif ext == ".json":
            try:
                json.loads(content)
            except json.JSONDecodeError as e:
                errors.append(f"JSON parse error: {e}")

        elif ext == ".html":
            # Basic HTML checks: unclosed tags, missing doctype
            open_tags = re.findall(r"<([a-zA-Z][a-zA-Z0-9]*)\b[^/]*>", content)
            close_tags = re.findall(r"</([a-zA-Z][a-zA-Z0-9]*)>", content)
            void_elements = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
            open_filtered = [t.lower() for t in open_tags if t.lower() not in void_elements]
            close_filtered = [t.lower() for t in close_tags]
            if len(open_filtered) > 0 and len(close_filtered) == 0:
                errors.append("HTML has opening tags but no closing tags")
            if content.strip() and "<!doctype" not in content[:100].lower() and "<html" not in content[:200].lower():
                errors.append("Missing <!DOCTYPE html> declaration")

        elif ext == ".css":
            # Check for balanced braces
            open_count = content.count("{")
            close_count = content.count("}")
            if open_count != close_count:
                errors.append(f"Unbalanced braces: {open_count} opening vs {close_count} closing")

        return {
            "valid": len(errors) == 0,
            "path": path,
            "line_count": content.count("\n") + 1,
            "errors": errors,
        }
