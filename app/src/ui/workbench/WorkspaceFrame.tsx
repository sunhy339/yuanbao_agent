import type { ReactNode } from "react";

interface WorkspaceFrameProps {
  children: ReactNode;
  composerVisible: boolean;
}

export function WorkspaceFrame({ children, composerVisible }: WorkspaceFrameProps) {
  return (
    <main className="workspace-frame" data-composer-visible={composerVisible} aria-label="Workspace frame">
      <div className="workspace-scroll">{children}</div>
    </main>
  );
}

