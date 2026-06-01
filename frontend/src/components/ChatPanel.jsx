function formatGenerationDuration(ms) {
  if (!ms) return null;
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes === 0) return `${seconds}s`;
  return `${minutes}m ${seconds}s`;
}

export function ChatPanel({ prompt, setPrompt, onRun, running, elapsedMs, messages, activeSession }) {
  const hasSession = Boolean(activeSession?.id);
  const heading = hasSession ? activeSession.title : "Zero -> Working App";
  const subheading = hasSession
    ? "Continue this session by asking for a new feature or refinement."
    : "Describe the app you want the agent to build. Your first prompt creates a new chat session.";

  const genBadge = running
    ? `Generating… ${formatGenerationDuration(elapsedMs) || "0s"}`
    : activeSession?.generation_duration_ms
    ? `Generated in ${formatGenerationDuration(activeSession.generation_duration_ms)}`
    : null;

  return (
    <section className="panel panel-chat">
      <div className="panel-header">
        <span className="eyebrow">Agentic Chat</span>
        <h1>{heading}</h1>
        {genBadge && (
          <p className={`panel-generation-time${running ? " panel-generation-time--live" : ""}`}>
            {genBadge}
          </p>
        )}
        <p className="panel-subcopy">{subheading}</p>
      </div>
      <div className="composer">
        <label htmlFor="prompt">
          {hasSession ? "What do you want to add or change in this app?" : "Describe the app you want the agent to build"}
        </label>
        <textarea
          id="prompt"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder={hasSession ? "add authentication and a settings page" : "vendor management app"}
        />
        <button type="button" onClick={onRun} disabled={running || !prompt.trim()}>
          {running ? "Running agent..." : hasSession ? "Continue session build" : "Create session and build app"}
        </button>
      </div>
      <div className="message-list">
        {messages.length === 0 ? (
          <article className="message message-empty">
            <div className="message-role">system</div>
            <div>
              Start a new session from the sidebar or choose an older build to continue working on it.
            </div>
          </article>
        ) : (
          messages.map((message, index) => (
            <article key={message.id || `${message.role}-${index}`} className={`message message-${message.role}`}>
              <div className="message-role">{message.agent || message.role}</div>
              <div>{message.content}</div>
            </article>
          ))
        )}
      </div>
    </section>
  );
}
