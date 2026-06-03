import { useRef, useState } from "react";
import type {
  SessionRecord,
  TaskRecord,
  WorkspaceRef,
} from "@shared";
import { RuntimeClient, type RuntimeConfig } from "../lib/runtimeClient";
import {
  DEFAULT_SESSION_TITLE,
  DEFAULT_WORKSPACE_PATH,
} from "../state/constants";
import {
  normalizeWorkspacePathForCompare,
} from "../state/providerConfig";
import { sortByUpdatedAtDesc, upsertRecord } from "../state/eventRecordViews";
import { openSessionTab } from "../ui/workbench/tabModel";
import type { HookDeps } from "./types";

const runtimeClient = new RuntimeClient();

export interface UseWorkspaceSessionsDeps extends HookDeps {
  config: RuntimeConfig | null;
  setConfig: React.Dispatch<React.SetStateAction<RuntimeConfig | null>>;

  /** Reset all trace/event/patch/approval state when session changes. */
  onSessionReset: () => void;
  /** Full reset of all trace/event/chat state when workspace changes. */
  onWorkspaceReset: () => void;
  /** Load chat messages for a session. */
  loadSessionMessages: (sessionId: string) => Promise<void>;
  /** Clear streaming token buffer. */
  clearPendingAssistantTokens: () => void;

  // Shared state received from App (owned by App or other hooks)
  taskHistory: TaskRecord[];
  setTaskHistory: React.Dispatch<React.SetStateAction<TaskRecord[]>>;
  setActiveTaskId: (id: string | null) => void;

  // External state setters that workspace handlers write to
  setTask: (task: TaskRecord | null) => void;
  setChatMessages: React.Dispatch<React.SetStateAction<any[]>>;
  setMessageBusy: (busy: boolean) => void;

  // Tab management
  setOpenTabs: React.Dispatch<React.SetStateAction<any[]>>;
  setActiveTabId: (id: any) => void;
}

export function useWorkspaceSessions(deps: UseWorkspaceSessionsDeps) {
  const {
    addToast, toastError, setError,
    config, setConfig,
    onSessionReset, onWorkspaceReset,
    loadSessionMessages, clearPendingAssistantTokens,
    taskHistory, setTaskHistory,
    setActiveTaskId,
    setTask, setChatMessages,
    setMessageBusy,
    setOpenTabs, setActiveTabId,
  } = deps;

  const [workspacePath, setWorkspacePath] = useState(DEFAULT_WORKSPACE_PATH);
  const [sessionTitle, setSessionTitle] = useState(DEFAULT_SESSION_TITLE);
  const [workspace, setWorkspace] = useState<WorkspaceRef | null>(null);
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [session, setSession] = useState<SessionRecord | null>(null);
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
  const [workspaceFocusBusy, setWorkspaceFocusBusy] = useState(false);
  const [workspaceMemoryBusy, setWorkspaceMemoryBusy] = useState(false);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [sessionListBusy, setSessionListBusy] = useState(false);
  const sessionActiveTaskMapRef = useRef<Map<string, string>>(new Map());

  function setActiveTaskForSession(taskId: string | null, sessionId?: string | null) {
    setActiveTaskId(taskId);
    const sid = sessionId ?? session?.id;
    if (sid) {
      if (taskId) {
        sessionActiveTaskMapRef.current.set(sid, taskId);
      } else {
        sessionActiveTaskMapRef.current.delete(sid);
      }
    }
  }

  async function ensureWorkspaceAtPath(path: string): Promise<WorkspaceRef> {
    const requestedPath = path.trim();
    if (!requestedPath) {
      throw new Error("Enter a workspace path before connecting.");
    }
    setWorkspacePath(requestedPath);

    if (workspace && normalizeWorkspacePathForCompare(workspace.rootPath) === normalizeWorkspacePathForCompare(requestedPath)) {
      return workspace;
    }

    const result = await runtimeClient.openWorkspace(requestedPath);
    setWorkspace(result.workspace);
    setConfig((current: RuntimeConfig | null) =>
      current
        ? {
            ...current,
            workspace: {
              ...current.workspace,
              rootPath: result.workspace.rootPath,
              writableRoots: [result.workspace.rootPath],
            },
          }
        : current,
    );
    return result.workspace;
  }

  async function ensureWorkspace(): Promise<WorkspaceRef> {
    return ensureWorkspaceAtPath(workspacePath);
  }

  function selectSession(nextSession: SessionRecord | null) {
    clearPendingAssistantTokens();
    setSession(nextSession);
    setActiveTaskId(null);
    onSessionReset();

    if (!nextSession) {
      return;
    }

    void loadSessionMessages(nextSession.id);

    const restoredTaskId = sessionActiveTaskMapRef.current.get(nextSession.id);
    const fallbackTask = sortByUpdatedAtDesc(
      taskHistory.filter((item) => item.sessionId === nextSession.id),
    )[0];
    const nextTask = restoredTaskId
      ? taskHistory.find((item) => item.id === restoredTaskId) ?? fallbackTask
      : fallbackTask;
    setTask(nextTask ?? null);
    setActiveTaskForSession(nextTask?.id ?? null, nextSession.id);
  }

  async function refreshSessionHistory(preferredSessionId?: string) {
    setSessionListBusy(true);
    setError(null);

    try {
      const result = await runtimeClient.listSessions();
      const taskResult = await runtimeClient.listTasks();
      const nextSessions = result.sessions;
      const nextTasks = taskResult.tasks;
      setSessions(nextSessions);
      setTaskHistory(nextTasks);

      const preferredSession =
        nextSessions.find((item) => item.id === preferredSessionId) ??
        (session ? nextSessions.find((item) => item.id === session.id) : undefined) ??
        nextSessions[0] ??
        null;

      setSession(preferredSession);

      if (!preferredSession) {
        setTask(null);
        setActiveTaskId(null);
        return;
      }

      const restoredTaskId = sessionActiveTaskMapRef.current.get(preferredSession.id);
      const fallbackTask = sortByUpdatedAtDesc(
        nextTasks.filter((item) => item.sessionId === preferredSession.id),
      )[0];
      const nextTask = restoredTaskId
        ? nextTasks.find((item) => item.id === restoredTaskId) ?? fallbackTask
        : fallbackTask;
      setTask(nextTask ?? null);
      setActiveTaskForSession(nextTask?.id ?? null, preferredSession.id);
      void loadSessionMessages(preferredSession.id);
    } catch (reason) {
      toastError(reason);
    } finally {
      setSessionListBusy(false);
    }
  }

  async function handleOpenWorkspace() {
    if (!workspacePath.trim()) {
      setError("Enter a workspace path before connecting.");
      return;
    }

    setWorkspaceBusy(true);
    setError(null);

    try {
      const result = await runtimeClient.openWorkspace(workspacePath.trim());
      clearPendingAssistantTokens();
      setWorkspace(result.workspace);
      setSession(null);
      setTask(null);
      setTaskHistory([]);
      setSessions([]);
      setActiveTaskId(null);
      sessionActiveTaskMapRef.current.clear();
      onWorkspaceReset();
      setChatMessages([]);
      await refreshSessionHistory();
    } catch (reason) {
      toastError(reason);
    } finally {
      setWorkspaceBusy(false);
    }
  }

  async function handleClearWorkspaceMemory() {
    if (!workspace) {
      setError("Open a workspace before clearing project memory.");
      return;
    }

    setWorkspaceMemoryBusy(true);
    setError(null);
    try {
      const result = await runtimeClient.clearWorkspaceMemory({ workspaceId: workspace.id });
      setWorkspace(result.workspace);
    } catch (reason) {
      toastError(reason);
    } finally {
      setWorkspaceMemoryBusy(false);
    }
  }

  async function handleSaveWorkspaceFocus(focus: string) {
    if (!workspace) {
      setError("Open a workspace before saving project focus.");
      return;
    }

    setWorkspaceFocusBusy(true);
    setError(null);
    try {
      const result = await runtimeClient.updateWorkspaceFocus({
        workspaceId: workspace.id,
        focus,
      });
      setWorkspace(result.workspace);
    } catch (reason) {
      toastError(reason);
    } finally {
      setWorkspaceFocusBusy(false);
    }
  }

  async function handleCreateSession() {
    setSessionBusy(true);
    setError(null);

    try {
      const nextWorkspace = await ensureWorkspace();
      const result = await runtimeClient.createSession({
        workspaceId: nextWorkspace.id,
        title: sessionTitle.trim() || DEFAULT_SESSION_TITLE,
      });

      setSessions((current) => upsertRecord(current, result.session));
      selectSession(result.session);
      handleOpenSessionTab(result.session);
    } catch (reason) {
      toastError(reason);
    } finally {
      setSessionBusy(false);
    }
  }

  function handleOpenSessionTab(nextSession: { id: string; title?: string }) {
    setOpenTabs((current: any[]) => {
      const result = openSessionTab(current, { id: nextSession.id, title: nextSession.title ?? "" });
      setActiveTabId(result.activeTabId);
      return result.tabs;
    });
  }

  async function handleRenameSession(sessionId: string, newTitle: string) {
    try {
      const result = await runtimeClient.updateSession({ sessionId, title: newTitle });
      setSessions((current) => upsertRecord(current, result.session));
      setSession((current) =>
        current && current.id === sessionId ? result.session : current,
      );
      setOpenTabs((current: any[]) => {
        const tabId = `session:${sessionId}`;
        return current.map((tab) =>
          tab.id === tabId ? { ...tab, title: newTitle } : tab,
        );
      });
    } catch (err) {
      setError(String(err));
    }
  }

  async function handleDeleteSession(sessionId: string) {
    try {
      await runtimeClient.deleteSession({ sessionId });
      sessionActiveTaskMapRef.current.delete(sessionId);
      setSessions((current) => current.filter((s) => s.id !== sessionId));
      setSession((current) => (current && current.id === sessionId ? null : current));
      setOpenTabs((current: any[]) => {
        const tabId = `session:${sessionId}`;
        const remaining = current.filter((tab) => tab.id !== tabId);
        if (remaining.length === current.length) return current;
        const fallback = remaining.length > 0 ? remaining[remaining.length - 1].id : "system:new-session";
        setActiveTabId(fallback);
        if (fallback.startsWith("session:")) {
          const sid = fallback.slice("session:".length);
          selectSession(sessions.find((item) => item.id === sid) ?? null);
        } else {
          selectSession(null);
        }
        return remaining;
      });
    } catch (err) {
      setError(String(err));
    }
  }

  function selectTask(taskId: string) {
    const nextTask = taskHistory.find((item) => item.id === taskId);
    if (!nextTask) {
      return;
    }

    setTask(nextTask);
    setActiveTaskForSession(nextTask.id);
  }

  return {
    workspacePath, setWorkspacePath,
    sessionTitle, setSessionTitle,
    workspace, setWorkspace,
    sessions, setSessions,
    session, setSession,
    workspaceBusy, workspaceFocusBusy, workspaceMemoryBusy,
    sessionBusy, setSessionBusy, sessionListBusy,
    sessionActiveTaskMapRef,
    setActiveTaskForSession,
    ensureWorkspace,
    ensureWorkspaceAtPath,
    selectSession,
    refreshSessionHistory,
    handleOpenWorkspace,
    handleClearWorkspaceMemory,
    handleSaveWorkspaceFocus,
    handleCreateSession,
    handleRenameSession,
    handleDeleteSession,
    selectTask,
  };
}
