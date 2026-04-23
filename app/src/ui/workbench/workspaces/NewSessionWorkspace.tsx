import "./newSession.css";

export interface NewSessionWorkspaceProps {
  workspacePath: string;
  hostStatusText: string;
}

export function NewSessionWorkspace({
  workspacePath,
  hostStatusText,
}: NewSessionWorkspaceProps) {
  return (
    <main className="new-session-workspace" aria-labelledby="new-session-title">
      <section className="new-session-hero">
        <div className="new-session-title-block">
          <div className="new-session-mark" aria-hidden="true">
            <span />
          </div>
          <div>
            <p className="new-session-kicker">民国桌面 · Local agent desk</p>
            <h1 id="new-session-title">新会话</h1>
            <span className="new-session-english">New Session</span>
          </div>
        </div>
        <p className="new-session-copy">
          像在旧办公室摊开一张新笺。底部输入栏会接住下一条指令，
          当前桌面已经连接到本地运行时，适合开启一个专注任务。
        </p>

        <div className="new-session-console" aria-label="Workbench readiness">
          <div className="console-header">
            <span />
            <strong>AGENT TERMINAL</strong>
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

        <dl className="new-session-metadata" aria-label="Workspace metadata">
          <div>
            <dt>工作目录</dt>
            <dd title={workspacePath}>{workspacePath}</dd>
          </div>
          <div>
            <dt>运行时</dt>
            <dd>{hostStatusText}</dd>
          </div>
        </dl>
      </section>
    </main>
  );
}

export default NewSessionWorkspace;
