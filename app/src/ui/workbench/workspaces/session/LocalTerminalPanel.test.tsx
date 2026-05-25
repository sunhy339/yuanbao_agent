import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TerminalEvent } from "@shared";
import { LocalTerminalPanel } from "./LocalTerminalPanel";

const terminalStart = vi.hoisted(() => vi.fn());
const terminalWrite = vi.hoisted(() => vi.fn());
const terminalStop = vi.hoisted(() => vi.fn());
const subscribeTerminalEvents = vi.hoisted(() => vi.fn());

vi.mock("../../../../lib/runtimeClient", () => ({
  RuntimeClient: class {
    terminalStart = terminalStart;
    terminalWrite = terminalWrite;
    terminalStop = terminalStop;
    subscribeTerminalEvents = subscribeTerminalEvents;
  },
}));

describe("LocalTerminalPanel", () => {
  let terminalHandler: ((event: TerminalEvent) => void) | undefined;

  beforeEach(() => {
    terminalHandler = undefined;
    terminalStart.mockReset();
    terminalWrite.mockReset();
    terminalStop.mockReset();
    subscribeTerminalEvents.mockReset();
    subscribeTerminalEvents.mockImplementation((handler: (event: TerminalEvent) => void) => {
      terminalHandler = handler;
      return Promise.resolve(vi.fn());
    });
  });

  afterEach(() => {
    cleanup();
  });

  it("starts a real local terminal session and streams PTY output", async () => {
    const user = userEvent.setup();
    terminalStart.mockResolvedValueOnce({
      terminal: {
        id: "term_1",
        cwd: "D:/project",
        shell: "powershell.exe",
        status: "running",
        startedAt: 1,
      },
    });
    terminalWrite.mockResolvedValueOnce({
      terminal: {
        id: "term_1",
        cwd: "D:/project",
        shell: "powershell.exe",
        status: "running",
        startedAt: 1,
      },
    });

    render(<LocalTerminalPanel workspaceRoot="D:/project" />);

    await user.click(screen.getByRole("button", { name: /启动/ }));
    expect(terminalStart).toHaveBeenCalledWith({ cwd: "D:/project", rows: 30, cols: 110 });

    await waitFor(() => expect(screen.getByText(/Started powershell\.exe/)).toBeInTheDocument());
    terminalHandler?.({
      terminalId: "term_1",
      kind: "output",
      chunk: "hello from pty\r\n",
      ts: 2,
    });
    expect(await screen.findByText(/hello from pty/)).toBeInTheDocument();

    await user.type(screen.getByLabelText("终端输入"), "pwd");
    fireEvent.keyDown(screen.getByLabelText("终端输入"), { key: "Enter" });
    expect(terminalWrite).toHaveBeenCalledWith({ terminalId: "term_1", data: "pwd\r\n" });
  });

  it("uses carriage return for PowerShell terminals", async () => {
    const user = userEvent.setup();
    terminalStart.mockResolvedValueOnce({
      terminal: {
        id: "term_ps",
        cwd: "D:/project",
        shell: "powershell.exe",
        status: "running",
        startedAt: 1,
      },
    });
    terminalWrite.mockResolvedValueOnce({
      terminal: {
        id: "term_ps",
        cwd: "D:/project",
        shell: "powershell.exe",
        status: "running",
        startedAt: 1,
      },
    });

    render(<LocalTerminalPanel workspaceRoot="D:/project" />);

    await user.click(screen.getByRole("button", { name: /启动/ }));
    await user.type(screen.getByLabelText("终端输入"), "dir");
    fireEvent.keyDown(screen.getByLabelText("终端输入"), { key: "Enter" });

    expect(terminalWrite).toHaveBeenCalledWith({ terminalId: "term_ps", data: "dir\r\n" });
  });

  it("stops the terminal session", async () => {
    const user = userEvent.setup();
    terminalStart.mockResolvedValueOnce({
      terminal: {
        id: "term_1",
        cwd: "D:/project",
        shell: "powershell.exe",
        status: "running",
        startedAt: 1,
      },
    });
    terminalStop.mockResolvedValueOnce({
      terminal: {
        id: "term_1",
        cwd: "D:/project",
        shell: "powershell.exe",
        status: "exited",
        startedAt: 1,
      },
    });

    render(<LocalTerminalPanel workspaceRoot="D:/project" />);

    await user.click(screen.getByRole("button", { name: /启动/ }));
    await user.click(screen.getByRole("button", { name: /停止/ }));

    expect(terminalStop).toHaveBeenCalledWith({ terminalId: "term_1" });
  });

  it("cleans terminal control sequences and Windows extended paths", async () => {
    const user = userEvent.setup();
    terminalStart.mockResolvedValueOnce({
      terminal: {
        id: "term_clean",
        cwd: "\\\\?\\D:\\project",
        shell: "powershell.exe",
        status: "running",
        startedAt: 1,
      },
    });

    render(<LocalTerminalPanel workspaceRoot="\\\\?\\D:\\project" />);

    await user.click(screen.getByRole("button", { name: /启动/ }));
    await waitFor(() => expect(screen.getByText(/Started powershell\.exe in D:\/project/)).toBeInTheDocument());

    terminalHandler?.({
      terminalId: "term_clean",
      kind: "output",
      chunk: "\u001b[6nhello\u001b[0m\r\n",
      ts: 2,
    });

    const output = await screen.findByLabelText("本地终端输出");
    expect(output).toHaveTextContent("hello");
    expect(output.textContent).not.toContain("[6n");
    expect(output.textContent).not.toContain("\u001b");
  });
});
