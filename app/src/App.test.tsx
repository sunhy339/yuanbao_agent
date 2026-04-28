import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { buildMockConfig } from "./state/mockData";
import type {
  MessageListResult,
  MessageRecord,
  ScheduledTaskListResult,
  SessionListResult,
  TaskListResult,
} from "@shared";
import type { HostStatus } from "./lib/runtimeClient";

const runtimeMocks = vi.hoisted(() => ({
  getHostStatus: vi.fn(),
  getConfig: vi.fn(),
  listSessions: vi.fn(),
  listTasks: vi.fn(),
  listScheduledTasks: vi.fn(),
  listMessages: vi.fn(),
  subscribeEvents: vi.fn(),
}));

vi.mock("./lib/runtimeClient", () => ({
  RuntimeClient: vi.fn(function RuntimeClient() {
    return runtimeMocks;
  }),
}));

const sessions: SessionListResult["sessions"] = [
  {
    id: "sess_alpha",
    workspaceId: "ws_1",
    title: "Alpha Session",
    status: "active",
    summary: "Alpha summary",
    createdAt: 1,
    updatedAt: 20,
  },
  {
    id: "sess_beta",
    workspaceId: "ws_1",
    title: "Beta Session",
    status: "active",
    summary: "Beta summary",
    createdAt: 2,
    updatedAt: 10,
  },
];

const messagesBySession: Record<string, MessageRecord[]> = {
  sess_alpha: [
    {
      id: "msg_alpha_user",
      sessionId: "sess_alpha",
      role: "user",
      content: "Alpha persisted request",
      createdAt: 100,
    },
    {
      id: "msg_alpha_assistant",
      sessionId: "sess_alpha",
      role: "assistant",
      content: "Alpha persisted answer",
      createdAt: 101,
    },
  ],
  sess_beta: [
    {
      id: "msg_beta_user",
      sessionId: "sess_beta",
      role: "user",
      content: "Beta persisted request",
      createdAt: 200,
    },
    {
      id: "msg_beta_assistant",
      sessionId: "sess_beta",
      role: "assistant",
      content: "Beta persisted answer",
      createdAt: 201,
    },
  ],
};

function setupRuntimeMocks() {
  const config = buildMockConfig();
  const hostStatus: HostStatus = {
    runtimeTransport: "tauri-stdio",
    eventChannel: "agent://event",
    runtimeRunning: true,
    repoRoot: "D:/py/yuanbao_agent",
    pythonModule: "local_agent_runtime.main",
  };
  runtimeMocks.getHostStatus.mockResolvedValue(hostStatus);
  runtimeMocks.getConfig.mockResolvedValue({
    config: {
      ...config,
      workspace: {
        ...config.workspace,
        rootPath: "D:/py/yuanbao_agent",
        writableRoots: ["D:/py/yuanbao_agent"],
      },
    },
  });
  runtimeMocks.listSessions.mockResolvedValue({ sessions });
  runtimeMocks.listTasks.mockResolvedValue({ tasks: [] } satisfies TaskListResult);
  runtimeMocks.listScheduledTasks.mockResolvedValue({
    tasks: [],
  } satisfies ScheduledTaskListResult);
  runtimeMocks.listMessages.mockImplementation(
    async ({ sessionId }: { sessionId: string }): Promise<MessageListResult> => ({
      messages: messagesBySession[sessionId] ?? [],
    }),
  );
  runtimeMocks.subscribeEvents.mockResolvedValue(vi.fn());
}

beforeEach(() => {
  for (const mock of Object.values(runtimeMocks)) {
    mock.mockReset();
  }
  setupRuntimeMocks();
});

afterEach(() => {
  cleanup();
});

describe("App session message recovery", () => {
  it("loads persisted messages for the latest session during app startup", async () => {
    render(<App />);

    await waitFor(() => {
      expect(runtimeMocks.listMessages).toHaveBeenCalledWith({
        sessionId: "sess_alpha",
        limit: 500,
      });
    });
    expect(screen.getByRole("tab", { name: "New Session" })).toHaveAttribute("aria-selected", "true");
  });

  it("loads persisted messages when selecting and switching sessions", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: /Alpha Session/ }));
    expect(await screen.findByText("Alpha persisted request")).toBeInTheDocument();
    expect(screen.getByText("Alpha persisted answer")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Beta Session/ }));
    expect(await screen.findByText("Beta persisted request")).toBeInTheDocument();
    expect(screen.getByText("Beta persisted answer")).toBeInTheDocument();
    expect(screen.queryByText("Alpha persisted request")).not.toBeInTheDocument();

    await waitFor(() => {
      expect(runtimeMocks.listMessages).toHaveBeenCalledWith({
        sessionId: "sess_beta",
        limit: 500,
      });
    });
  });

  it("reloads persisted messages when activating already-open session tabs", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: /Alpha Session/ }));
    expect(await screen.findByText("Alpha persisted request")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Beta Session/ }));
    expect(await screen.findByText("Beta persisted request")).toBeInTheDocument();
    runtimeMocks.listMessages.mockClear();

    await user.click(screen.getByRole("tab", { name: "Alpha Session" }));
    expect(await screen.findByText("Alpha persisted answer")).toBeInTheDocument();
    expect(screen.queryByText("Beta persisted request")).not.toBeInTheDocument();
    expect(runtimeMocks.listMessages).toHaveBeenCalledWith({
      sessionId: "sess_alpha",
      limit: 500,
    });

    await user.click(screen.getByRole("tab", { name: "Beta Session" }));
    expect(await screen.findByText("Beta persisted answer")).toBeInTheDocument();
    expect(screen.queryByText("Alpha persisted request")).not.toBeInTheDocument();
    expect(runtimeMocks.listMessages).toHaveBeenCalledWith({
      sessionId: "sess_beta",
      limit: 500,
    });
  });
});
