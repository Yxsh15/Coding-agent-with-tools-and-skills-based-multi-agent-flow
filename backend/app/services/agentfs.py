from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import get_config, get_root_dir


class AgentFS:
    def __init__(self) -> None:
        config = get_config()
        self.root = (get_root_dir() / config.agentfs.root).resolve()
        self.max_read_chars = config.agentfs.max_read_chars

    def app_path(self, app_id: str) -> Path:
        return (self.root / app_id).resolve()

    def resolve_path(self, app_id: str, relative_path: str) -> Path:
        base = self.app_path(app_id)
        candidate = (base / relative_path).resolve()
        if base not in candidate.parents and candidate != base:
            raise ValueError(f"Path escapes app workspace: {relative_path}")
        return candidate

    def ensure_app(self, app_id: str) -> Path:
        app_root = self.app_path(app_id)
        app_root.mkdir(parents=True, exist_ok=True)
        (app_root / ".internal").mkdir(exist_ok=True)
        (app_root / "objects").mkdir(exist_ok=True)
        (app_root / "pages").mkdir(exist_ok=True)
        return app_root

    def list_entries(self, app_id: str) -> list[dict[str, Any]]:
        app_root = self.ensure_app(app_id)
        entries = [
            {
                "path": str(path.relative_to(app_root)),
                "name": path.name,
                "is_dir": path.is_dir(),
            }
            for path in app_root.rglob("*")
        ]
        return sorted(
            entries,
            key=lambda item: (
                item["path"].count("/"),
                0 if item["is_dir"] else 1,
                item["path"],
            ),
        )

    def list_files(self, app_id: str) -> list[str]:
        return [entry["path"] for entry in self.list_entries(app_id) if not entry["is_dir"]]

    def read_file(
        self,
        app_id: str,
        relative_path: str,
        start: int | None = None,
        end: int | None = None,
        summary: bool = False,
        truncate: bool = True,
    ) -> str:
        path = self.resolve_path(app_id, relative_path)
        content = path.read_text()
        if start is not None or end is not None:
            lines = content.splitlines()
            sliced = lines[start or 0 : end]
            content = "\n".join(sliced)
        if truncate and len(content) > self.max_read_chars:
            truncated = content[: self.max_read_chars]
            if summary:
                line_count = len(content.splitlines())
                return (
                    f"[truncated to {self.max_read_chars} chars from {line_count} lines]\n"
                    f"{truncated}"
                )
            return truncated
        return content

    def write_file(self, app_id: str, relative_path: str, content: str) -> None:
        path = self.resolve_path(app_id, relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def search(self, app_id: str, query: str, path_prefix: str | None = None) -> list[dict]:
        base = self.resolve_path(app_id, path_prefix) if path_prefix else self.app_path(app_id)
        matches: list[dict] = []
        for file_path in base.rglob("*"):
            if not file_path.is_file():
                continue
            text = file_path.read_text()
            for line_number, line in enumerate(text.splitlines(), start=1):
                if query.lower() in line.lower():
                    matches.append(
                        {
                            "path": str(file_path.relative_to(self.app_path(app_id))),
                            "line": line_number,
                            "snippet": line.strip(),
                        }
                    )
        return matches

    def glob(self, app_id: str, pattern: str, path_prefix: str | None = None) -> list[str]:
        base = self.resolve_path(app_id, path_prefix) if path_prefix else self.app_path(app_id)
        return sorted(
            str(path.relative_to(self.app_path(app_id)))
            for path in base.glob(pattern)
            if path.exists()
        )

    def load_json(self, app_id: str, relative_path: str) -> object:
        return json.loads(self.read_file(app_id, relative_path, truncate=False))

    def save_json(self, app_id: str, relative_path: str, data: object) -> None:
        self.write_file(app_id, relative_path, json.dumps(data, indent=2))

    def snapshot(self, app_id: str, truncate: bool = False) -> list[dict]:
        files: list[dict] = []
        for relative_path in self.list_files(app_id):
            files.append(
                {
                    "path": relative_path,
                    "content": self.read_file(app_id, relative_path, truncate=truncate),
                }
            )
        return files
