import { useEffect, useMemo, useRef, useState } from "react";
import { Play, RotateCcw, Send, Square } from "lucide-react";
import type { TerminalEvent, TerminalSessionRecord } from "@shared";
import { RuntimeClient } from "../../../../lib/runtimeClient";
import { Button } from "../../../v2/components/ui";

const terminalClient = new RuntimeClient();
const DEFAULT_COLS = 110;
const DEFAULT_ROWS = 30;

interface TerminalLine {
  id: string;
  text: string;
  kind: TerminalEvent["kind"];
}

export interface LocalTerminalPanelProps {
  workspaceRoot?: string;
  workspaceLabel?: string;
}

function displayWorkspace(path?: string) {
  const normalized = String(path ?? "").trim();
  if (!normalized) {
    return "workspace";
  }
  return normalized.replace(/\\/g, "/");
}

function appendOutput(lines: TerminalLine[], event: TerminalEvent): TerminalLine[] {
  if (event.kind === "output" && event.chunk) {
    return [
      ...lines,
      {
        id: `${event.terminalId}-${event.ts ?? Date.now()}-${lines.length}`,
        text: event.chunk,
        kind: "output" as const,
      },
    ].slice(-300);
  }
  if (event.kind === "error") {
    return [
      ...lines,
      {
        id: `${event.terminalId}-${event.ts ?? Date.now()}-error`,
        text: event.message || "Terminal error",
        kind: "error" as const,
      },
    ].slice(-300);
  }
  if (event.kind === "exit") {
    return [
      ...lines,
      {
        id: `${event.terminalId}-${event.ts ?? Date.now()}-exit`,
        text: event.exitCode === null || event.exitCode === undefined ? "Terminal exited" : `Terminal exited with ${event.exitCode}`,
        kind: "exit" as const,
      },
    ].slice(-300);
  }
  return lines;
}

function newlineForShell(shell?: string) {
  const normalized = String(shell ?? "").toLowerCase();
  return normalized.includes("powershell") || normalized.includes("pwsh") ? "\r" : "\n";
}

export function LocalTerminalPanel({ workspaceRoot, workspaceLabel }: LocalTerminalPanelProps) {
  const [terminal, setTerminal] = useState<TerminalSessionRecord | null>(null);
  const [lines, setLines] = useState<TerminalLine[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const outputRef = useRef<HTMLPreElement | null>(null);
  const terminalIdRef = useRef<string | null>(null);
  const cwd = workspaceRoot || workspaceLabel || "";
  const isRunning = terminal?.status === "running";
  const subtitle = useMemo(() => displayWorkspace(cwd), [cwd]);
  const prompt = useMemo(() => {
    const path = displayWorkspace(cwd);
    return path.match(/^[A-Za-z]:\//) ? `${path}>` : `${path} $`;
  }, [cwd]);

  useEffect(() => {
    let disposed = false;
    let unsubscribe: (() => void) | undefined;
    terminalClient
      .subscribeTerminalEvents((event) => {
        if (event.terminalId !== terminalIdRef.current) {
          return;
        }
        setLines((current) => appendOutput(current, event));
        if (event.kind === "exit" || event.kind === "error") {
          setTerminal((current) => (current ? { ...current, status: event.kind === "error" ? "failed" : "exited" } : current));
        }
      })
      .then((nextUnsubscribe) => {
        if (disposed) {
          nextUnsubscribe();
          return;
        }
        unsubscribe = nextUnsubscribe;
      })
      .catch((reason) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      disposed = true;
      unsubscribe?.();
    };
  }, []);

  useEffect(() => {
    const element = outputRef.current;
    if (!element) {
      return;
    }
    element.scrollTop = element.scrollHeight;
  }, [lines]);

  async function startTerminal() {
    setBusy(true);
    setError(null);
    try {
      const result = await terminalClient.terminalStart({
        cwd: workspaceRoot || undefined,
        rows: DEFAULT_ROWS,
        cols: DEFAULT_COLS,
      });
      terminalIdRef.current = result.terminal.id;
      setTerminal(result.terminal);
      setLines([
        {
          id: `${result.terminal.id}-ready`,
          text: `Started ${result.terminal.shell} in ${result.terminal.cwd}\n`,
          kind: "output",
        },
      ]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function stopTerminal() {
    if (!terminal) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await terminalClient.terminalStop({ terminalId: terminal.id });
      setTerminal(result.terminal);
      terminalIdRef.current = null;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function sendInput() {
    const text = input;
    if (!terminal || !text.trim()) {
      return;
    }
    setInput("");
    setError(null);
    try {
      await terminalClient.terminalWrite({ terminalId: terminal.id, data: `${text}${newlineForShell(terminal.shell)}` });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  return (
    <section className="session-local-terminal" aria-label="本地终端">
      <header className="session-local-terminal-header">
        <div>
          <span className="session-local-terminal-title">本地终端</span>
          <strong>{terminal?.shell || "Terminal"}</strong>
          <small title={subtitle}>{subtitle}</small>
        </div>
        <div className="session-local-terminal-controls">
          <span data-status={terminal?.status ?? "idle"}>{terminal?.status === "running" ? "运行中" : terminal ? "已停止" : "未启动"}</span>
          <Button
            size="xs"
            variant="secondary"
            loading={busy && !isRunning}
            disabled={isRunning}
            onClick={() => {
              void startTerminal();
            }}
          >
            <Play size={13} aria-hidden="true" />
            启动
          </Button>
          <Button
            size="xs"
            variant="secondary"
            loading={busy && isRunning}
            disabled={!terminal}
            onClick={() => {
              void stopTerminal();
            }}
          >
            <Square size={13} aria-hidden="true" />
            停止
          </Button>
        </div>
      </header>
      <pre className="session-local-terminal-output" aria-label="本地终端输出" ref={outputRef}>
        {lines.length ? lines.map((line) => line.text).join("") : `${prompt} `}
      </pre>
      {error ? <p className="session-tool-error">{error}</p> : null}
      <div className="session-local-terminal-input">
        <input
          aria-label="终端输入"
          disabled={!isRunning}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              void sendInput();
            }
          }}
          placeholder={isRunning ? "输入命令后按 Enter" : "先启动终端"}
          value={input}
        />
        <Button size="xs" variant="secondary" disabled={!isRunning || !input.trim()} onClick={() => void sendInput()}>
          <Send size={13} aria-hidden="true" />
          发送
        </Button>
        <Button
          size="xs"
          variant="secondary"
          onClick={() => {
            setLines([]);
          }}
        >
          <RotateCcw size={13} aria-hidden="true" />
          清空
        </Button>
      </div>
    </section>
  );
}
