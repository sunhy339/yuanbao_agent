import "./newSession.css";

export interface NewSessionWorkspaceProps {
  workspacePath: string;
  hostStatusText: string;
}

export function NewSessionWorkspace({ workspacePath, hostStatusText }: NewSessionWorkspaceProps) {
  return (
    <main className="new-session-workspace" aria-labelledby="new-session-title">
      <section className="new-session-desk" aria-label="New session desk">
        <div className="new-session-main">
          <p className="new-session-kicker">Yuanbao Agent</p>
          <h1 id="new-session-title">New Session</h1>
          <p className="new-session-copy">
            Type a task in the command bar below. Yuanbao will create a session and stream the work into the
            conversation.
          </p>

          <div className="new-session-status-row" aria-label="Session status">
            <span data-status="ready">{hostStatusText}</span>
            <span>{workspacePath}</span>
          </div>
        </div>
      </section>
    </main>
  );
}

export default NewSessionWorkspace;
