function formatDuration(duration) {
  if (duration == null) {
    return "Running...";
  }
  if (duration < 1000) {
    return `${Math.round(duration)}ms`;
  }
  return `${(duration / 1000).toFixed(2)}s`;
}

export function RunTimeline({ steps }) {
  return (
    <section className="panel panel-trace">
      <div className="panel-header">
        <span className="eyebrow">Run Trace</span>
        <h2>Main agent, sub-agents, and tool calls</h2>
      </div>
      <div className="trace-list">
        {steps.length === 0 ? (
          <div className="trace-empty">Run a build to see agent and tool activity here.</div>
        ) : (
          steps.map((step, index) => (
            <article key={`${step.type}-${index}`} className={`trace-card trace-${step.type}`}>
              <div className="trace-topline">
                <span>{step.agent}</span>
                <span>{formatDuration(step.duration_ms)}</span>
              </div>
              <div className="trace-title">{step.title}</div>
              {step.detail ? <div className="trace-detail">{step.detail}</div> : null}
            </article>
          ))
        )}
      </div>
    </section>
  );
}
