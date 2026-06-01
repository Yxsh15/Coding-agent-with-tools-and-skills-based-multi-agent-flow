from __future__ import annotations

import json
import os
import re
import hashlib
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse, Response

from app.config import get_config, get_root_dir
from app.logging_utils import configure_logging
from app.schemas import (
    ChatMessage,
    SessionCreateRequest,
    SessionDetail,
    SessionSummary,
    WorkspaceEntry,
    WorkspaceFile,
    WorkspaceSnapshot,
)
from app.services.agentfs import AgentFS
from app.services.runner import AgentRunner
from app.services.session_store import SessionStore
from app.services.observability import get_metrics

# Load environment variables from .env file
env_path = get_root_dir() / ".env"
load_dotenv(env_path)
logger = configure_logging()

app = FastAPI(title="AgentFS App Builder POC")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

runner = AgentRunner()
agentfs = AgentFS()
session_store = SessionStore()
PREVIEW_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _workspace_snapshot_from_payload(payload: dict[str, object]) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        app_id=str(payload["app_id"]),
        entries=[WorkspaceEntry(**item) for item in payload.get("entries", [])],
        files=[WorkspaceFile(path=item["path"], content=item["content"]) for item in payload.get("files", [])],
    )


def _live_workspace_payload(app_id: str) -> dict[str, object]:
    return {
        "app_id": app_id,
        "entries": agentfs.list_entries(app_id),
        "files": agentfs.snapshot(app_id, truncate=False),
    }


def _workspace_snapshot(app_id: str) -> WorkspaceSnapshot:
    live_workspace = _live_workspace_payload(app_id)
    if live_workspace["files"]:
        return _workspace_snapshot_from_payload(live_workspace)

    stored_workspace = session_store.get_workspace_for_app(app_id)
    if stored_workspace is not None and stored_workspace["files"]:
        return _workspace_snapshot_from_payload(stored_workspace)

    return _workspace_snapshot_from_payload(live_workspace)


def _read_workspace_file(app_id: str, relative_path: str) -> str:
    try:
        return agentfs.read_file(app_id, relative_path, truncate=False)
    except FileNotFoundError:
        stored_workspace = session_store.get_workspace_for_app(app_id)
        if stored_workspace is not None:
            for item in stored_workspace["files"]:
                if item["path"] == relative_path:
                    return item["content"]
        raise


def _restore_session_workspace(session_id: str, app_id: str) -> None:
    if agentfs.list_files(app_id):
        return

    workspace = session_store.get_workspace_for_session(session_id)
    if not workspace["files"]:
        return

    agentfs.ensure_app(app_id)
    for entry in workspace["entries"]:
        if entry["is_dir"]:
            agentfs.resolve_path(app_id, entry["path"]).mkdir(parents=True, exist_ok=True)
    for file in workspace["files"]:
        agentfs.write_file(app_id, file["path"], file["content"])


def _inject_preview_runtime(html: str, app_id: str) -> str:
    bridge = f"""
<script>
(() => {{
  const appId = {json.dumps(app_id)};
  const postToParent = (type, payload = {{}}) => {{
    try {{
      if (window.parent && window.parent !== window) {{
        window.parent.postMessage({{
          source: "agentfs-preview",
          appId,
          type,
          payload,
        }}, "*");
      }}
    }} catch (_error) {{
      // Preview diagnostics should never break the generated app.
    }}
  }};
  const fetchJson = async (url) => {{
    const response = await fetch(url);
    if (!response.ok) {{
      throw new Error(`AgentFS request failed: ${{response.status}}`);
    }}
    return response.json();
  }};

  window.__AGENTFS_APP_ID__ = appId;
  window.AgentFS = {{
    appId,
    tree: () => fetchJson(`/api/agentfs/${{appId}}/tree`),
    workspace: () => fetchJson(`/api/workspace/${{appId}}`),
    readFile: async (path) => {{
      const payload = await fetchJson(`/api/agentfs/${{appId}}/file?path=${{encodeURIComponent(path)}}`);
      return payload.content;
    }},
    previewUrl: (path = "") => {{
      const normalized = String(path).replace(/^\\/+/, "");
      const encodedPath = normalized
        .split("/")
        .filter(Boolean)
        .map(encodeURIComponent)
        .join("/");
      return encodedPath ? `/api/preview/${{appId}}/${{encodedPath}}` : `/api/preview/${{appId}}/`;
    }},
  }};
  window.agentfs = window.AgentFS;

  let interactionReported = false;
  const reportReady = () => {{
    postToParent("preview_ready", {{
      href: window.location.href,
      title: document.title,
    }});
  }};

  const reportInteraction = () => {{
    if (interactionReported) {{
      return;
    }}
    interactionReported = true;
    postToParent("preview_interaction", {{
      href: window.location.href,
    }});
  }};

  window.addEventListener("error", (event) => {{
    postToParent("preview_error", {{
      message: event.message || "Preview runtime error",
      filename: event.filename || "",
      lineno: event.lineno || 0,
      colno: event.colno || 0,
    }});
  }});

  window.addEventListener("unhandledrejection", (event) => {{
    const reason = event.reason;
    postToParent("preview_error", {{
      message: reason?.message || String(reason || "Unhandled promise rejection"),
    }});
  }});

  document.addEventListener("pointerdown", reportInteraction, {{
    capture: true,
    passive: true,
  }});

  if (document.readyState === "loading") {{
    document.addEventListener("DOMContentLoaded", reportReady, {{ once: true }});
  }} else {{
    reportReady();
  }}
}})();
</script>
""".strip()

    pattern = re.compile(r"</body>", flags=re.IGNORECASE)
    if pattern.search(html):
        return pattern.sub(f"{bridge}\n</body>", html, count=1)
    return f"{html}\n{bridge}"


def _preview_revision(app_id: str) -> str:
    digest = hashlib.sha1()
    for item in _workspace_snapshot(app_id).files:
        digest.update(item.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:12] or "empty"


def _rewrite_preview_asset_urls(html: str, revision: str) -> str:
    asset_pattern = re.compile(r'(?P<prefix>\b(?:src|href)=["\'])(?P<url>[^"\']+)(?P<suffix>["\'])', re.IGNORECASE)

    def replace(match: re.Match[str]) -> str:
        url = match.group("url")
        if url.startswith(("http://", "https://", "data:", "mailto:", "#", "javascript:", "/")):
            return match.group(0)
        separator = "&" if "?" in url else "?"
        return f"{match.group('prefix')}{url}{separator}v={revision}{match.group('suffix')}"

    return asset_pattern.sub(replace, html)


def _render_preview_html(app_id: str, relative_path: str = "index.html") -> HTMLResponse:
    try:
        content = _read_workspace_file(app_id, relative_path)
        revision = _preview_revision(app_id)
        content = _rewrite_preview_asset_urls(content, revision)
        return HTMLResponse(content=_inject_preview_runtime(content, app_id), headers=PREVIEW_HEADERS)
    except FileNotFoundError:
        return HTMLResponse(
            content="""
            <!DOCTYPE html>
            <html>
            <head><title>No App Generated</title></head>
            <body style="font-family: sans-serif; padding: 40px; text-align: center;">
                <h1>No Application Generated Yet</h1>
                <p>Run the agent with a prompt to generate an application.</p>
            </body>
            </html>
            """,
            status_code=200,
            headers=PREVIEW_HEADERS,
        )


@app.get("/api/health")
async def health() -> dict[str, str]:
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    return {
        "status": "ok",
        "api_key_configured": bool(api_key and api_key != "your_api_key_here"),
        "log_file": os.environ.get("APP_LOG_FILE", ".storage/backend.log"),
        "log_level": os.environ.get("APP_LOG_LEVEL", "INFO").upper(),
    }


@app.get("/api/config")
async def config() -> dict:
    return asdict(get_config())


@app.get("/api/skills")
async def skills() -> list[dict[str, str]]:
    skill_dir = get_root_dir() / "skills"
    return [{"name": path.stem, "body": path.read_text()} for path in sorted(skill_dir.glob("*.md"))]


@app.get("/api/sessions")
async def sessions() -> list[SessionSummary]:
    return [SessionSummary(**item) for item in session_store.list_sessions()]


@app.post("/api/sessions")
async def create_session(payload: SessionCreateRequest) -> SessionSummary:
    return SessionSummary(**session_store.create_session(payload.first_prompt))


@app.get("/api/sessions/{session_id}")
async def session_detail(session_id: str) -> SessionDetail:
    try:
        detail = session_store.get_session_detail(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    session = SessionSummary(**detail["session"])
    _restore_session_workspace(session_id, session.app_id)
    workspace = _workspace_snapshot(session.app_id)
    if workspace.files:
        session_store.save_workspace(
            session_id,
            workspace.app_id,
            [entry.model_dump() for entry in workspace.entries],
            [file.model_dump() for file in workspace.files],
        )

    return SessionDetail(
        session=session,
        messages=[ChatMessage(**item) for item in detail["messages"]],
        trace_events=detail.get("trace_events", []),
        workspace=workspace,
    )


@app.get("/api/workspace/{app_id}")
async def workspace(app_id: str) -> WorkspaceSnapshot:
    return _workspace_snapshot(app_id)


@app.get("/api/agentfs/{app_id}/tree")
async def agentfs_tree(app_id: str) -> dict[str, object]:
    workspace_snapshot = _workspace_snapshot(app_id)
    return {
        "app_id": app_id,
        "entries": [entry.model_dump() for entry in workspace_snapshot.entries],
    }


@app.get("/api/agentfs/{app_id}/file")
async def agentfs_file(app_id: str, path: str = Query(..., min_length=1)) -> dict[str, str]:
    try:
        return {
            "app_id": app_id,
            "path": path,
            "content": _read_workspace_file(app_id, path),
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")


@app.get("/api/runs/stream")
async def run_stream(
    prompt: str,
    app_id: str | None = None,
    session_id: str | None = None,
) -> StreamingResponse:
    target_app_id = app_id or "demo_app"
    logger.info(
        "Starting stream run app_id=%s session_id=%s prompt_len=%s",
        target_app_id,
        session_id,
        len(prompt),
    )

    gen_start = time.monotonic()

    async def observe_stream(event: dict[str, object]) -> None:
        if not session_id:
            return

        event_type = event["type"]
        payload = event["payload"]

        if event_type == "message":
            session_store.add_message(
                session_id,
                payload.get("role", "assistant"),
                payload.get("agent", "assistant"),
                payload.get("content", ""),
            )
        elif event_type in {"agent_started", "agent_finished", "tool_started", "tool_finished"}:
            session_store.add_trace_event(session_id, event_type, payload)
        elif event_type == "workspace":
            session_store.save_workspace(
                session_id,
                payload["app_id"],
                payload.get("entries", []),
                payload.get("files", []),
            )
        elif event_type == "final":
            duration_ms = int((time.monotonic() - gen_start) * 1000)
            session_store.save_generation_time(session_id, duration_ms)
            workspace = _live_workspace_payload(target_app_id)
            if workspace["files"]:
                session_store.save_workspace(
                    session_id,
                    target_app_id,
                    workspace["entries"],
                    workspace["files"],
                )

    if session_id:
        try:
            session = session_store.get_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        target_app_id = str(session["app_id"])
        _restore_session_workspace(session_id, target_app_id)
        session_store.add_message(session_id, "user", "user", prompt)
        logger.info(
            "Attached prompt to session session_id=%s app_id=%s prompt_len=%s",
            session_id,
            target_app_id,
            len(prompt),
        )

    return StreamingResponse(
        runner.run_stream(prompt=prompt, app_id=target_app_id, stream_observer=observe_stream),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get("/api/preview/{app_id}")
async def preview_app(app_id: str) -> HTMLResponse:
    """Serve the generated app's index.html for live preview."""
    return _render_preview_html(app_id)


@app.get("/api/preview/{app_id}/")
async def preview_app_root(app_id: str) -> HTMLResponse:
    """Serve the generated app's index.html with a directory-style URL for relative assets."""
    return _render_preview_html(app_id)


@app.get("/api/preview/{app_id}/{file_path:path}")
async def preview_file(app_id: str, file_path: str) -> Response:
    """Serve any file from the generated app for preview (CSS, JS, etc.)."""
    try:
        ext = Path(file_path).suffix.lower()
        if ext == ".html":
            return _render_preview_html(app_id, file_path)

        content = _read_workspace_file(app_id, file_path)

        content_types = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".json": "application/json",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".ico": "image/x-icon",
        }
        content_type = content_types.get(ext, "text/plain")
        return Response(content=content, media_type=content_type, headers=PREVIEW_HEADERS)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")


@app.get("/api/metrics")
async def metrics() -> dict[str, Any]:
    """Return agent execution metrics for observability."""
    collector = get_metrics()
    all_metrics = collector.get_all_metrics()
    return {**all_metrics, "timestamp": time.time()}


@app.get("/api/metrics/prometheus")
async def metrics_prometheus() -> Response:
    """Return metrics in Prometheus text format."""
    collector = get_metrics()
    all_metrics = collector.get_all_metrics()
    lines: list[str] = []

    for name, value in all_metrics.get("counters", {}).items():
        safe_name = name.replace(".", "_").replace("-", "_").replace("{", "_").replace("}", "").replace(",", "_").replace("=", "_")
        lines.append(f"# TYPE {safe_name} counter")
        lines.append(f"{safe_name} {value}")

    for name, value in all_metrics.get("gauges", {}).items():
        safe_name = name.replace(".", "_").replace("-", "_").replace("{", "_").replace("}", "").replace(",", "_").replace("=", "_")
        lines.append(f"# TYPE {safe_name} gauge")
        lines.append(f"{safe_name} {value}")

    for name, stats in all_metrics.get("histograms", {}).items():
        safe_name = name.replace(".", "_").replace("-", "_").replace("{", "_").replace("}", "").replace(",", "_").replace("=", "_")
        lines.append(f"# TYPE {safe_name} summary")
        lines.append(f'{safe_name}_count {stats.get("count", 0)}')
        if stats.get("count", 0) > 0:
            lines.append(f'{safe_name}_sum {stats.get("sum", 0)}')
            lines.append(f'{safe_name}{{quantile="0.5"}} {stats.get("p50", 0)}')
            lines.append(f'{safe_name}{{quantile="0.95"}} {stats.get("p95", 0)}')
            lines.append(f'{safe_name}{{quantile="0.99"}} {stats.get("p99", 0)}')

    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/plain; charset=utf-8",
    )
