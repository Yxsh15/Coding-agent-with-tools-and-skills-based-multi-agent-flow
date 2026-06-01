import { useEffect, useMemo, useState } from "react";

const API_BASE = "http://localhost:8000";
const PREVIEW_ORIGIN = new URL(API_BASE).origin;

function buildFileTree(entries, files) {
  const root = { name: "workspace", children: {}, isDir: true };

  const fileContent = new Map(files.map((file) => [file.path, file.content]));
  const sourceEntries = entries.length
    ? entries
    : files.map((file) => ({
        path: file.path,
        name: file.path.split("/").pop(),
        is_dir: false,
      }));

  for (const entry of sourceEntries) {
    const parts = entry.path.split("/").filter(Boolean);
    let current = root;

    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      const currentPath = parts.slice(0, i + 1).join("/");
      const isLast = i === parts.length - 1;
      const isDir = isLast ? entry.is_dir : true;

      if (!current.children[part]) {
        current.children[part] = {
          name: part,
          path: currentPath,
          children: {},
          isDir,
          content: isDir ? null : fileContent.get(currentPath) || null,
        };
      }

      if (isLast) {
        current.children[part].isDir = isDir;
        current.children[part].content = isDir ? null : fileContent.get(currentPath) || null;
      }
      current = current.children[part];
    }
  }

  return root;
}

// Convert tree object to sorted array
function treeToArray(node, expandedDirs, depth = 0) {
  const items = [];
  const children = Object.values(node.children).sort((a, b) => {
    if (a.isDir && !b.isDir) return -1;
    if (!a.isDir && b.isDir) return 1;
    return a.name.localeCompare(b.name);
  });

  for (const child of children) {
    items.push({ ...child, depth });
    if (child.isDir && expandedDirs.has(child.path)) {
      items.push(...treeToArray(child, expandedDirs, depth + 1));
    }
  }

  return items;
}

// File icon based on extension
function getFileIcon(name, isDir) {
  if (isDir) return "📁";
  const ext = name.split(".").pop()?.toLowerCase();
  const icons = {
    html: "🌐",
    css: "🎨",
    js: "📜",
    jsx: "⚛️",
    json: "📋",
    md: "📝",
    py: "🐍",
    txt: "📄",
  };
  return icons[ext] || "📄";
}

function TreeItem({ item, isSelected, onSelect, expanded, onToggle }) {
  const indent = item.depth * 16;
  
  return (
    <div
      className={`tree-item ${isSelected ? "selected" : ""} ${item.isDir ? "directory" : "file"}`}
      style={{ paddingLeft: `${indent + 8}px` }}
      onClick={() => item.isDir ? onToggle(item.path) : onSelect(item.path)}
    >
      {item.isDir && (
        <span className="tree-arrow">{expanded ? "▼" : "▶"}</span>
      )}
      <span className="tree-icon">{getFileIcon(item.name, item.isDir)}</span>
      <span className="tree-name">{item.name}</span>
    </div>
  );
}

export function WorkspacePreview({ entries = [], files, selectedPath, setSelectedPath, appId = "demo_app" }) {
  const [expandedDirs, setExpandedDirs] = useState(new Set([""]));
  const [viewMode, setViewMode] = useState("code"); // 'code', 'preview', or 'split'
  const [previewKey, setPreviewKey] = useState(0); // For refreshing iframe
  const [previewStatus, setPreviewStatus] = useState({
    state: "idle",
    detail: "No preview loaded yet",
  });

  const tree = useMemo(() => buildFileTree(entries, files), [entries, files]);
  const treeItems = useMemo(() => treeToArray(tree, expandedDirs), [tree, expandedDirs]);
  const entryCount = entries.length || files.length;
  const workspaceSignature = useMemo(
    () => JSON.stringify(files.map((file) => [file.path, file.content])),
    [files],
  );

  const selectedFile = files.find((file) => file.path === selectedPath);
  const hasIndexHtml = files.some((file) => file.path === "index.html");
  const previewUrl = `${API_BASE}/api/preview/${appId}/?v=${previewKey}`;
  const previewStatusLabel = {
    idle: "Idle",
    loading: "Loading",
    loaded: "Loaded",
    ready: "Live",
    error: "Error",
  }[previewStatus.state] || "Idle";

  useEffect(() => {
    setPreviewKey((k) => k + 1);
  }, [appId, workspaceSignature]);

  useEffect(() => {
    setPreviewStatus(
      hasIndexHtml
        ? { state: "loading", detail: `Loading ${appId} preview` }
        : { state: "idle", detail: "Generate an index.html file to preview the app" },
    );
  }, [appId, hasIndexHtml, previewKey, workspaceSignature]);

  useEffect(() => {
    const handleMessage = (event) => {
      if (event.origin !== PREVIEW_ORIGIN) {
        return;
      }

      const payload = event.data;
      if (!payload || payload.source !== "agentfs-preview" || payload.appId !== appId) {
        return;
      }

      if (payload.type === "preview_ready") {
        setPreviewStatus({
          state: "ready",
          detail: payload.payload?.title || "Preview is ready",
        });
      }

      if (payload.type === "preview_interaction") {
        setPreviewStatus((current) => ({
          state: "ready",
          detail: current.detail || "Preview interaction detected",
        }));
      }

      if (payload.type === "preview_error") {
        setPreviewStatus({
          state: "error",
          detail: payload.payload?.message || "Preview runtime error",
        });
      }
    };

    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [appId]);

  const toggleDir = (path) => {
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  const isExpanded = (path) => expandedDirs.has(path);

  const refreshPreview = () => {
    setPreviewKey((k) => k + 1);
  };

  const handlePreviewLoad = () => {
    setPreviewStatus((current) =>
      current.state === "ready"
        ? current
        : { state: "loaded", detail: `Preview document loaded for ${appId}` },
    );
  };

  return (
    <section className="panel panel-workspace">
      <div className="panel-header">
        <div className="header-left">
          <span className="eyebrow">WORKSPACE FILES</span>
          <span className="file-count">{entryCount} entries</span>
        </div>
        <div className="header-right">
          <button 
            className={`view-btn ${viewMode === 'code' ? 'active' : ''}`}
            onClick={() => setViewMode('code')}
            title="Code View"
          >
            💻 Code
          </button>
          <button 
            className={`view-btn ${viewMode === 'preview' ? 'active' : ''}`}
            onClick={() => setViewMode('preview')}
            title="Live Preview"
          >
            ▶️ Preview
          </button>
          <button 
            className={`view-btn ${viewMode === 'split' ? 'active' : ''}`}
            onClick={() => setViewMode('split')}
            title="Split View"
          >
            ⚡ Split
          </button>
          {(viewMode === 'preview' || viewMode === 'split') && (
            <button 
              className="view-btn refresh-btn"
              onClick={refreshPreview}
              title="Refresh Preview"
            >
              🔄
            </button>
          )}
        </div>
      </div>
      
      <div className={`workspace-content ${viewMode === 'preview' ? 'preview-only' : ''} ${viewMode === 'split' ? 'split-view' : ''}`}>
        {viewMode !== 'preview' && (
          <>
            <div className="file-tree">
              <div className="tree-header">
                <span className="tree-title">📂 Generated App</span>
              </div>
              <div className="tree-list">
                {treeItems.length === 0 ? (
                  <div className="tree-empty">No files generated yet</div>
                ) : (
                  treeItems.map((item) => (
                    <TreeItem
                      key={item.path}
                      item={item}
                      isSelected={item.path === selectedPath}
                      onSelect={setSelectedPath}
                      expanded={isExpanded(item.path)}
                      onToggle={toggleDir}
                    />
                  ))
                )}
              </div>
            </div>
            
            <div className="file-preview-pane">
              <div className="preview-header">
                {selectedFile ? (
                  <>
                    <span className="preview-icon">{getFileIcon(selectedFile.path.split("/").pop(), false)}</span>
                    <span className="preview-path">{selectedFile.path}</span>
                  </>
                ) : (
                  <span className="preview-path">Select a file to preview</span>
                )}
              </div>
              <pre className="file-preview">
                {selectedFile?.content || "// No file selected\n// Click a file in the tree to view its contents"}
              </pre>
            </div>
          </>
        )}
        
        {(viewMode === 'preview' || viewMode === 'split') && (
          <div className="live-preview-pane">
            <div className="preview-header live-preview-header">
              <span className="preview-icon">▶️</span>
              <span className="preview-path">Live Preview - {appId}</span>
              <div className="live-preview-actions">
                <span
                  className={`preview-status preview-status-${previewStatus.state}`}
                  title={previewStatus.detail}
                >
                  {previewStatusLabel}
                </span>
                <a 
                  href={`${API_BASE}/api/preview/${appId}/?v=${previewKey}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="open-external"
                >
                  ↗️ Open in new tab
                </a>
              </div>
            </div>
            <div className="preview-iframe-container">
              {hasIndexHtml ? (
                <iframe
                  key={previewKey}
                  className="preview-iframe"
                  src={previewUrl}
                  title="App Preview"
                  onLoad={handlePreviewLoad}
                />
              ) : (
                <div className="preview-placeholder">
                  <p>No index.html found</p>
                  <p>Generate an application to see the preview</p>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
