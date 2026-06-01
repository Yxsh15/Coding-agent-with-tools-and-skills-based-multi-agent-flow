import { useEffect, useRef, useState } from "react";
import { ChatPanel } from "./components/ChatPanel";
import { RunTimeline } from "./components/RunTimeline";
import { SessionSidebar } from "./components/SessionSidebar";
import { WorkspacePreview } from "./components/WorkspacePreview";

const API_BASE = "http://localhost:8000";

function buildTimelineStep(event) {
  const { type, payload } = event;
  if (type === "agent_started") {
    const detail = [
      payload.prompt,
      `model: ${payload.model}`,
      `temperature: ${payload.temperature}`,
      `tools: ${payload.tools.join(", ")}`,
      `skills: ${payload.skills.join(", ")}`,
    ].join("\n");
    return {
      type,
      agent: payload.agent,
      title: payload.is_subagent ? "Specialist agent invoked" : "Orchestrator started",
      detail,
      duration_ms: null,
    };
  }
  if (type === "agent_finished") {
    return {
      type,
      agent: payload.agent,
      title: "Agent finished",
      detail: payload.final_message,
      duration_ms: payload.duration_ms,
    };
  }
  if (type === "tool_started") {
    const title =
      payload.tool === "invoke_agent"
        ? `Invoking agent -> ${payload.input.agent}`
        : `Using tool: ${payload.tool}`;
    return {
      type,
      agent: payload.agent,
      title,
      detail: JSON.stringify(payload.input, null, 2),
      duration_ms: null,
    };
  }
  if (type === "tool_finished") {
    return {
      type,
      agent: payload.agent,
      title: payload.error ? `${payload.tool} failed` : `${payload.tool} completed`,
      detail:
        typeof payload.output === "string"
          ? payload.output
          : payload.error || JSON.stringify(payload.output, null, 2),
      duration_ms: payload.duration_ms,
    };
  }
  return null;
}

function upsertMessage(current, nextMessage) {
  const index = current.findIndex((message) => message.id === nextMessage.id);
  if (index === -1) {
    return [...current, nextMessage];
  }

  const updated = [...current];
  updated[index] = {
    ...updated[index],
    ...nextMessage,
  };
  return updated;
}

export default function App() {
  const [prompt, setPrompt] = useState("");
  const [running, setRunning] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [activeSession, setActiveSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [steps, setSteps] = useState([]);
  const [entries, setEntries] = useState([]);
  const [files, setFiles] = useState([]);
  const [selectedPath, setSelectedPath] = useState("");
  const [elapsedMs, setElapsedMs] = useState(0);
  const genStartRef = useRef(null);

  useEffect(() => {
    loadSessions();
  }, []);

  useEffect(() => {
    if (!running) {
      setElapsedMs(0);
      genStartRef.current = null;
      return;
    }
    genStartRef.current = Date.now();
    const interval = setInterval(() => {
      setElapsedMs(Date.now() - genStartRef.current);
    }, 500);
    return () => clearInterval(interval);
  }, [running]);

  const resetWorkspaceState = () => {
    setMessages([]);
    setSteps([]);
    setEntries([]);
    setFiles([]);
    setSelectedPath("");
  };

  const loadSessions = () => {
    fetch(`${API_BASE}/api/sessions`)
      .then((response) => response.json())
      .then((data) => {
        setSessions(data || []);
      })
      .catch(() => {});
  };

  const loadSession = (sessionId) => {
    fetch(`${API_BASE}/api/sessions/${sessionId}`)
      .then((response) => response.json())
      .then((data) => {
        setActiveSession(data.session);
        setMessages(data.messages || []);
        setSteps((data.trace_events || []).map(buildTimelineStep).filter(Boolean));
        setEntries(data.workspace?.entries || []);
        setFiles(data.workspace?.files || []);
        if (data.workspace?.files?.length) {
          setSelectedPath(data.workspace.files[0].path);
        } else {
          setSelectedPath("");
        }
      })
      .catch(() => {});
  };

  const createSession = async (firstPrompt) => {
    const response = await fetch(`${API_BASE}/api/sessions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ first_prompt: firstPrompt }),
    });

    if (!response.ok) {
      throw new Error("Unable to create session");
    }

    const session = await response.json();
    setActiveSession(session);
    setSessions((current) => [session, ...current.filter((item) => item.id !== session.id)]);
    setEntries([]);
    setFiles([]);
    setSelectedPath("");
    return session;
  };

  const onNewSession = () => {
    if (running) return;
    setActiveSession(null);
    setPrompt("");
    resetWorkspaceState();
  };

  const onSelectSession = (sessionId) => {
    if (running || sessionId === activeSession?.id) {
      return;
    }
    setPrompt("");
    loadSession(sessionId);
  };

  const onRun = async () => {
    const trimmedPrompt = prompt.trim();
    if (!trimmedPrompt) {
      return;
    }

    let session = activeSession;
    if (!session) {
      try {
        session = await createSession(trimmedPrompt);
      } catch (error) {
        setMessages([
          {
            id: Date.now(),
            role: "system",
            agent: "system",
            content: "Could not create a new session.",
          },
        ]);
        return;
      }
    }

    setRunning(true);
    setSteps([]);
    setPrompt("");
    setMessages((current) => [
      ...current,
      {
        id: Date.now(),
        role: "user",
        agent: "user",
        content: trimmedPrompt,
      },
    ]);

    const stream = new EventSource(
      `${API_BASE}/api/runs/stream?prompt=${encodeURIComponent(trimmedPrompt)}&session_id=${encodeURIComponent(session.id)}`,
    );

    stream.onmessage = (messageEvent) => {
      const event = JSON.parse(messageEvent.data);
      if (event.type === "heartbeat") {
        return;
      }

      if (event.type === "message_chunk") {
        setMessages((current) =>
          upsertMessage(current, {
            id: event.payload.message_id,
            role: event.payload.role,
            agent: event.payload.agent,
            content: event.payload.content,
          }),
        );
      }

      if (event.type === "message") {
        setMessages((current) =>
          upsertMessage(current, {
            id: event.payload.message_id || `${event.payload.agent}-${current.length}-${Date.now()}`,
            role: event.payload.role,
            agent: event.payload.agent,
            content: event.payload.content,
          }),
        );
      }

      const timelineStep = buildTimelineStep(event);
      if (timelineStep) {
        setSteps((current) => [...current, timelineStep]);
      }

      if (event.type === "workspace") {
        setEntries(event.payload.entries || []);
        setFiles(event.payload.files);
        if (event.payload.files?.length) {
          setSelectedPath(event.payload.files[0].path);
        } else {
          setSelectedPath("");
        }
      }

      if (event.type === "final") {
        setRunning(false);
        stream.close();
        loadSessions();
        loadSession(session.id);
      }

      if (event.type === "error") {
        setMessages((current) => [
          ...current,
          {
            id: `error-${Date.now()}`,
            role: "assistant",
            agent: "system",
            content: event.payload.message,
          },
        ]);
        setRunning(false);
        stream.close();
        loadSessions();
        loadSession(session.id);
      }
    };

    stream.onerror = () => {
      setRunning(false);
      stream.close();
      loadSessions();
    };
  };

  return (
    <main className="app-shell">
      <div className="hero">
        <span className="hero-badge">React + FastAPI + AgentFS</span>
        <p>
          A small-scale Claude-code-style POC with an orchestrator, isolated specialist agents,
          unified diff edits, per-agent config files, and a live execution trace.
        </p>
      </div>
      <div className="layout-shell">
        <SessionSidebar
          sessions={sessions}
          activeSessionId={activeSession?.id}
          onSelectSession={onSelectSession}
          onNewSession={onNewSession}
          running={running}
        />
        <div className="content-shell">
          <div className="main-grid">
            <ChatPanel
              prompt={prompt}
              setPrompt={setPrompt}
              onRun={onRun}
              running={running}
              elapsedMs={elapsedMs}
              messages={messages}
              activeSession={activeSession}
            />
            <RunTimeline steps={steps} />
          </div>
          <WorkspacePreview
            appId={activeSession?.app_id || "demo_app"}
            entries={entries}
            files={files}
            selectedPath={selectedPath}
            setSelectedPath={setSelectedPath}
          />
        </div>
      </div>
    </main>
  );
}
