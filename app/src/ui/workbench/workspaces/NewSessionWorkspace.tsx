import "./newSession.css";

export interface NewSessionWorkspaceProps {
  workspacePath: string;
  hostStatusText: string;
}

export function NewSessionWorkspace({ workspacePath, hostStatusText }: NewSessionWorkspaceProps) {
  return (
    <main className="new-session-workspace" aria-labelledby="new-session-title">
      <section className="new-session-desk">
        <div className="new-session-heading">
          <div>
            <p className="new-session-kicker">本地代理工作台 · Local agent desk</p>
            <h1 id="new-session-title">新会话</h1>
            <span className="new-session-english">New Session</span>
            <p className="new-session-copy">
              新会话已准备就绪。底部指令栏会创建下一段工作记录，左侧会话列表保留已有会话。
            </p>
          </div>
          <div className="new-session-stamp" aria-hidden="true">
            <span>Ready</span>
          </div>
        </div>

        <div className="new-session-readiness" aria-label="Command readiness">
          <div className="new-session-readiness-title">
            <span aria-hidden="true" />
            <strong>Command readiness</strong>
          </div>
          <dl>
            <div>
              <dt>CWD</dt>
              <dd title={workspacePath}>{workspacePath}</dd>
            </div>
            <div>
              <dt>Runtime</dt>
              <dd>{hostStatusText}</dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>Shown in the composer dock</dd>
            </div>
          </dl>
        </div>

        <div className="new-session-console" aria-label="Workbench terminal">
          <div className="console-header">
            <span />
            <strong>DESK TERMINAL</strong>
          </div>
          <p>
            <span aria-hidden="true">&gt;</span> cwd: {workspacePath}
          </p>
          <p>
            <span aria-hidden="true">&gt;</span> runtime: {hostStatusText}
          </p>
          <p>
            <span aria-hidden="true">&gt;</span> composer: ready for a new session
          </p>
        </div>

        <aside className="new-session-ledger-note" aria-label="Recent session affordance">
          <strong>Recent sessions live in the session ledger.</strong>
          <span>Open an existing record from the left rail, or start fresh by sending a command below.</span>
        </aside>
      </section>
    </main>
  );
}

export default NewSessionWorkspace;
