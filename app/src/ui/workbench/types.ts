import type { SessionRecord } from "@shared";

export type SystemWorkspaceKind = "new-session" | "scheduled" | "settings";

export type WorkbenchTab =
  | {
      id: `system:${SystemWorkspaceKind}`;
      kind: SystemWorkspaceKind;
      title: string;
      closable?: false;
    }
  | {
      id: `session:${string}`;
      kind: "session";
      title: string;
      sessionId: string;
      closable: true;
    };

export interface WorkbenchTabResult {
  tabs: WorkbenchTab[];
  activeTabId: WorkbenchTab["id"];
}

export type WorkbenchSession = SessionRecord;

