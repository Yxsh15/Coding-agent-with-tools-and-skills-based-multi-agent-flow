function formatRelativeTime(value) {
  if (!value) return "";

  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "";

  const diffMs = Date.now() - timestamp;
  const diffMinutes = Math.max(1, Math.round(diffMs / 60000));

  if (diffMinutes < 60) {
    return `${diffMinutes}m`;
  }

  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) {
    return `${diffHours}h`;
  }

  const diffDays = Math.round(diffHours / 24);
  return `${diffDays}d`;
}

function formatGenerationDuration(ms) {
  if (!ms) return null;
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes === 0) return `${seconds}s`;
  return `${minutes}m ${seconds}s`;
}

function buildSessionPreview(session) {
  if (session.last_message_preview) {
    return session.last_message_preview;
  }
  if (session.last_prompt) {
    return session.last_prompt;
  }
  return "Start a new app build in this session.";
}

export function SessionSidebar({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewSession,
  running,
}) {
  return (
    <aside className="panel session-sidebar">
      <div className="panel-header session-header">
        <div>
          <span className="eyebrow">Codex</span>
          <h2>Chats</h2>
        </div>
        <button
          type="button"
          className="session-new-button"
          onClick={onNewSession}
          disabled={running}
        >
          + New Session
        </button>
      </div>

      <div className="session-list">
        {sessions.length === 0 ? (
          <div className="session-empty">
            <p>No saved chats yet.</p>
            <p>Your first prompt will create the first SQLite-backed session.</p>
          </div>
        ) : (
          sessions.map((session) => (
            <button
              key={session.id}
              type="button"
              className={`session-item ${session.id === activeSessionId ? "active" : ""}`}
              onClick={() => onSelectSession(session.id)}
              disabled={running}
            >
              <div className="session-item-topline">
                <span className="session-title">{session.title}</span>
                <span className="session-age">{formatRelativeTime(session.updated_at)}</span>
              </div>
              <div className="session-preview">{buildSessionPreview(session)}</div>
              <div className="session-meta">
                <span>{session.message_count} messages</span>
                {formatGenerationDuration(session.generation_duration_ms) && (
                  <span title="Generation time">⏱ {formatGenerationDuration(session.generation_duration_ms)}</span>
                )}
              </div>
            </button>
          ))
        )}
      </div>
    </aside>
  );
}
