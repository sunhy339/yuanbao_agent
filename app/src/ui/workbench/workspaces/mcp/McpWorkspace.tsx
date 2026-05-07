import { useEffect, useMemo, useState, type FormEvent } from "react";
import type { McpServerRecord, McpServerTransport } from "@shared";
import { Button, StatusBadge } from "../../../v2/components/ui";
import "./mcp.css";

export interface McpServerDraft {
  name: string;
  transport: McpServerTransport;
  command: string;
  args: string;
  url: string;
  headers: string;
  env: string;
  enabled: boolean;
}

export interface McpWorkspaceProps {
  servers: McpServerRecord[];
  loading?: boolean;
  busyServerId?: string | null;
  lastRefresh?: { refreshed: number; tools: string[] } | null;
  errorMessage?: string | null;
  onRefreshServers: () => void | Promise<void>;
  onCreateServer: (draft: McpServerDraft) => void | Promise<void>;
  onImportServers?: (drafts: McpServerDraft[]) => void | Promise<void>;
  onUpdateServer: (serverId: string, draft: McpServerDraft) => void | Promise<void>;
  onToggleServer: (serverId: string, enabled: boolean) => void | Promise<void>;
  onRefreshTools: (serverId?: string) => void | Promise<void>;
  onDeleteServer: (serverId: string) => void | Promise<void>;
  onDismissError?: () => void;
}

const initialDraft: McpServerDraft = {
  name: "",
  transport: "stdio",
  command: "",
  args: "",
  url: "",
  headers: "",
  env: "",
  enabled: true,
};

function formatTimestamp(value?: number) {
  if (!value) {
    return "从未";
  }
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function formatArgs(args?: string[]) {
  return args?.length ? args.join(" ") : "无参数";
}

function formatRecordKeys(record?: Record<string, string>) {
  const keys = Object.keys(record ?? {});
  return keys.length ? keys.join(", ") : "none";
}

function formatKeyValueRecord(record?: Record<string, string>) {
  return Object.entries(record ?? {})
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
}

function stringifyImportedArgs(value: unknown) {
  if (Array.isArray(value)) {
    return value.map((item) => String(item)).join("\n");
  }
  return typeof value === "string" ? value : "";
}

function stringifyImportedRecord(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "";
  }
  return Object.entries(value as Record<string, unknown>)
    .map(([key, recordValue]) => `${key}=${String(recordValue)}`)
    .join("\n");
}

function parseMcpServersImport(value: string): McpServerDraft[] {
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Expected a JSON object.");
  }
  const record = parsed as Record<string, unknown>;
  const rawServers = record.mcpServers ?? record.servers ?? record;
  if (!rawServers || typeof rawServers !== "object" || Array.isArray(rawServers)) {
    throw new Error("Expected an mcpServers object.");
  }
  return Object.entries(rawServers as Record<string, unknown>)
    .map(([name, raw]) => {
      if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
        return null;
      }
      const server = raw as Record<string, unknown>;
      const transport =
        typeof server.transport === "string"
          ? (server.transport as McpServerTransport)
          : typeof server.url === "string"
            ? "sse"
            : "stdio";
      const enabled = typeof server.enabled === "boolean"
        ? server.enabled
        : typeof server.disabled === "boolean"
          ? !server.disabled
          : true;
      return {
        name,
        transport,
        command: typeof server.command === "string" ? server.command : "",
        args: stringifyImportedArgs(server.args),
        url: typeof server.url === "string" ? server.url : "",
        headers: stringifyImportedRecord(server.headers),
        env: stringifyImportedRecord(server.env),
        enabled,
      };
    })
    .filter((draft): draft is McpServerDraft => Boolean(draft?.name));
}

function draftFromServer(server: McpServerRecord): McpServerDraft {
  return {
    name: server.name,
    transport: server.transport,
    command: server.command ?? "",
    args: server.args?.join("\n") ?? "",
    url: server.url ?? "",
    headers: formatKeyValueRecord(server.headers),
    env: formatKeyValueRecord(server.env),
    enabled: server.enabled,
  };
}

export function McpWorkspace({
  servers,
  loading = false,
  busyServerId = null,
  lastRefresh = null,
  errorMessage = null,
  onRefreshServers,
  onCreateServer,
  onImportServers,
  onUpdateServer,
  onToggleServer,
  onRefreshTools,
  onDeleteServer,
  onDismissError,
}: McpWorkspaceProps) {
  const [draft, setDraft] = useState<McpServerDraft>(initialDraft);
  const [importText, setImportText] = useState("");
  const [importError, setImportError] = useState<string | null>(null);
  const [formMode, setFormMode] = useState<"create" | "edit">("create");
  const [editingServerId, setEditingServerId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(servers[0]?.id ?? null);
  const enabledCount = useMemo(() => servers.filter((server) => server.enabled).length, [servers]);
  const stdioCount = useMemo(() => servers.filter((server) => server.transport === "stdio").length, [servers]);
  const selectedServer = servers.find((server) => server.id === expandedId) ?? servers[0] ?? null;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!draft.name.trim()) {
      return;
    }
    try {
      if (formMode === "edit" && editingServerId) {
        await onUpdateServer(editingServerId, draft);
      } else {
        await onCreateServer(draft);
      }
      setFormMode("create");
      setEditingServerId(null);
      setDraft(initialDraft);
    } catch {
      // Parent owns persistent error state; keep the draft intact for correction.
    }
  }

  async function handleImport() {
    try {
      const drafts = parseMcpServersImport(importText);
      if (!drafts.length) {
        throw new Error("No MCP servers found.");
      }
      if (onImportServers) {
        await onImportServers(drafts);
        setImportText("");
      } else {
        setDraft(drafts[0]);
      }
      setImportError(null);
    } catch (reason) {
      setImportError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function startEditing(server: McpServerRecord) {
    setExpandedId(server.id);
    setEditingServerId(server.id);
    setFormMode("edit");
    setDraft(draftFromServer(server));
  }

  function resetForm() {
    setFormMode("create");
    setEditingServerId(null);
    setDraft(initialDraft);
  }

  useEffect(() => {
    if (editingServerId && !servers.some((server) => server.id === editingServerId)) {
      resetForm();
    }
  }, [editingServerId, servers]);

  return (
    <main className="mcp-workspace" aria-labelledby="mcp-title">
      <section className="mcp-hero">
        <div>
          <p className="mcp-kicker">能力中心</p>
          <h1 id="mcp-title">MCP 控制台</h1>
          <p>
            管理本地运行时暴露的 Model Context Protocol 服务器。这里创建的服务器会由后端持久化，
            并可将工具注册到智能体运行时。
          </p>
          <div className="mcp-hero-telemetry" aria-label="MCP 运行时遥测">
            <span>
              <strong>{enabledCount}</strong>
              已启用通道
            </span>
            <span>
              <strong>{formatTimestamp(lastRefresh?.refreshed)}</strong>
              上次刷新
            </span>
            <span>
              <strong>{selectedServer?.name ?? "无"}</strong>
              当前检查端点
            </span>
          </div>
        </div>
        <div className="mcp-hero-actions">
          <Button type="button" onClick={() => void onRefreshServers()} loading={loading} variant="secondary">
            {loading ? "刷新中..." : "刷新服务器"}
          </Button>
          <Button type="button" onClick={() => void onRefreshTools()} disabled={loading || !servers.length} variant="primary">
            刷新全部工具
          </Button>
        </div>
      </section>

      {errorMessage ? (
        <section className="mcp-error-banner" role="alert" aria-label="MCP 错误">
          <div>
            <strong>MCP 操作失败</strong>
            <span>{errorMessage}</span>
          </div>
          {onDismissError ? (
            <Button type="button" variant="ghost" size="sm" onClick={onDismissError}>
              关闭
            </Button>
          ) : null}
        </section>
      ) : null}

      <section className="mcp-metrics" aria-label="MCP 概览">
        <div>
          <span>{servers.length}</span>
          <small>服务器</small>
        </div>
        <div>
          <span>{enabledCount}</span>
          <small>已启用</small>
        </div>
        <div>
          <span>{stdioCount}</span>
          <small>stdio</small>
        </div>
        <div>
          <span>{lastRefresh?.refreshed ?? 0}</span>
          <small>已刷新工具</small>
        </div>
      </section>

      <section className="mcp-grid">
        <form className="mcp-panel mcp-create-panel" onSubmit={handleSubmit}>
          <div className="mcp-panel-header">
            <div>
              <p className="mcp-kicker">{formMode === "edit" ? "编辑端点" : "新建端点"}</p>
              <h2>{formMode === "edit" ? "编辑 MCP 服务器" : "添加 MCP 服务器"}</h2>
            </div>
            {formMode === "edit" ? (
              <Button type="button" variant="ghost" size="sm" onClick={resetForm} disabled={loading}>
                取消
              </Button>
            ) : null}
          </div>
          {formMode === "create" ? (
            <div className="mcp-import-box">
              <label>
                <span>MCP JSON</span>
                <textarea
                  aria-label="MCP JSON"
                  value={importText}
                  onChange={(event) => {
                    setImportText(event.currentTarget.value);
                    setImportError(null);
                  }}
                  placeholder={'{ "mcpServers": { "firecrawl-mcp": { "command": "npx", "args": ["-y", "firecrawl-mcp"], "env": { "FIRECRAWL_API_KEY": "..." } } } }'}
                  rows={4}
                />
              </label>
              <div className="mcp-import-actions">
                <Button type="button" variant="secondary" size="sm" disabled={!importText.trim() || loading} onClick={() => void handleImport()}>
                  Import JSON
                </Button>
                {importError ? <span role="alert">{importError}</span> : null}
              </div>
            </div>
          ) : null}
          <label>
            <span>名称</span>
            <input
              value={draft.name}
              onChange={(event) => {
                const { value } = event.currentTarget;
                setDraft((current) => ({ ...current, name: value }));
              }}
              placeholder="filesystem"
              required
            />
          </label>
          <label>
            <span>传输方式</span>
            <select
              value={draft.transport}
              onChange={(event) => {
                const value = event.currentTarget.value as McpServerTransport;
                setDraft((current) => ({ ...current, transport: value }));
              }}
            >
              <option value="stdio">stdio</option>
              <option value="sse">sse</option>
              <option value="http">http</option>
            </select>
          </label>
          {draft.transport === "stdio" ? (
            <>
              <label>
                <span>命令</span>
                <input
                  value={draft.command}
                  onChange={(event) => {
                    const { value } = event.currentTarget;
                    setDraft((current) => ({ ...current, command: value }));
                  }}
                  placeholder="npx @modelcontextprotocol/server-filesystem"
                />
              </label>
              <label>
                <span>参数</span>
                <textarea
                  value={draft.args}
                  onChange={(event) => {
                    const { value } = event.currentTarget;
                    setDraft((current) => ({ ...current, args: value }));
                  }}
                  placeholder="D:\\py\\yuanbao_agent"
                  rows={3}
                />
              </label>
            </>
          ) : (
            <label>
              <span>URL</span>
              <input
                value={draft.url}
                onChange={(event) => {
                  const { value } = event.currentTarget;
                  setDraft((current) => ({ ...current, url: value }));
                }}
                placeholder="http://127.0.0.1:8787/sse"
              />
            </label>
          )}
          {draft.transport !== "stdio" ? (
            <label>
              <span>Headers</span>
              <textarea
                aria-label="Headers"
                value={draft.headers}
                onChange={(event) => {
                  const { value } = event.currentTarget;
                  setDraft((current) => ({ ...current, headers: value }));
                }}
                placeholder="Authorization=Bearer ..."
                rows={3}
              />
            </label>
          ) : null}
          <label>
            <span>Env</span>
            <textarea
              aria-label="Env"
              value={draft.env}
              onChange={(event) => {
                const { value } = event.currentTarget;
                setDraft((current) => ({ ...current, env: value }));
              }}
              placeholder="FIRECRAWL_API_KEY=..."
              rows={3}
            />
          </label>
          <label className="mcp-toggle">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(event) => {
                const { checked } = event.currentTarget;
                setDraft((current) => ({ ...current, enabled: checked }));
              }}
            />
            <span>创建后启用</span>
          </label>
          <div className="mcp-form-actions">
            <Button type="submit" className="mcp-primary-action" disabled={loading || !draft.name.trim()} loading={loading} variant="primary">
              {formMode === "edit" ? "保存服务器" : "创建服务器"}
            </Button>
          </div>
        </form>

        <section className="mcp-panel">
          <div className="mcp-panel-header">
            <div>
              <p className="mcp-kicker">服务器</p>
              <h2>运行时注册表</h2>
            </div>
          </div>
          <div className="mcp-server-list">
            {servers.length ? (
              servers.map((server) => (
                <button
                  key={server.id}
                  type="button"
                  className={server.id === selectedServer?.id ? "mcp-server-card is-active" : "mcp-server-card"}
                  onClick={() => setExpandedId(server.id)}
                >
                  <span className="mcp-status-dot" data-enabled={server.enabled} aria-hidden="true" />
                  <span>
                    <strong>{server.name}</strong>
                    <small>{server.transport}</small>
                  </span>
                  <StatusBadge label={server.enabled ? "已启用" : "已停用"} tone={server.enabled ? "success" : "neutral"} compact />
                </button>
              ))
            ) : (
              <div className="mcp-empty-state">
                <strong>暂无 MCP 服务器</strong>
                <small>创建 stdio/http 端点后，可将外部工具暴露给智能体。</small>
              </div>
            )}
          </div>
        </section>

        <section className="mcp-panel mcp-detail-panel">
          <div className="mcp-panel-header">
            <div>
              <p className="mcp-kicker">检查器</p>
              <h2>{selectedServer?.name ?? "未选择服务器"}</h2>
            </div>
          </div>
          {selectedServer ? (
            <>
              <div className="mcp-inspector-band" data-enabled={selectedServer.enabled}>
                <span className="mcp-status-dot" data-enabled={selectedServer.enabled} aria-hidden="true" />
                <strong>{selectedServer.enabled ? "端点在线" : "端点已暂停"}</strong>
                <small>{selectedServer.transport} 传输</small>
              </div>
              <dl className="mcp-definition-list">
                <div>
                  <dt>ID</dt>
                  <dd>{selectedServer.id}</dd>
                </div>
                <div>
                  <dt>传输方式</dt>
                  <dd>{selectedServer.transport}</dd>
                </div>
                <div>
                  <dt>命令</dt>
                  <dd>{selectedServer.command || selectedServer.url || "未配置"}</dd>
                </div>
                <div>
                  <dt>参数</dt>
                  <dd>{formatArgs(selectedServer.args)}</dd>
                </div>
                <div>
                  <dt>Env</dt>
                  <dd>{formatRecordKeys(selectedServer.env)}</dd>
                </div>
                <div>
                  <dt>更新时间</dt>
                  <dd>{formatTimestamp(selectedServer.updatedAt)}</dd>
                </div>
              </dl>
              <div className="mcp-detail-actions">
                <Button
                  type="button"
                  onClick={() => startEditing(selectedServer)}
                  disabled={busyServerId === selectedServer.id || loading}
                  size="sm"
                  variant="secondary"
                >
                  编辑
                </Button>
                <Button
                  type="button"
                  onClick={() => void onToggleServer(selectedServer.id, !selectedServer.enabled)}
                  disabled={busyServerId === selectedServer.id}
                  loading={busyServerId === selectedServer.id}
                  size="sm"
                  variant={selectedServer.enabled ? "secondary" : "primary"}
                >
                  {selectedServer.enabled ? "停用" : "启用"}
                </Button>
                <Button
                  type="button"
                  onClick={() => void onRefreshTools(selectedServer.id)}
                  disabled={busyServerId === selectedServer.id || !selectedServer.enabled}
                  loading={busyServerId === selectedServer.id}
                  size="sm"
                  variant="secondary"
                >
                  刷新工具
                </Button>
                <Button
                  type="button"
                  className="is-danger"
                  onClick={() => void onDeleteServer(selectedServer.id)}
                  disabled={busyServerId === selectedServer.id}
                  loading={busyServerId === selectedServer.id}
                  size="sm"
                  variant="danger"
                >
                  删除
                </Button>
              </div>
              {lastRefresh?.tools.length ? (
                <div className="mcp-tool-preview">
                  <strong>上次刷新的工具</strong>
                  <ul>
                    {lastRefresh.tools.slice(0, 8).map((tool) => (
                      <li key={tool}>{tool}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </>
          ) : (
            <div className="mcp-empty-state">
              <strong>选择一个服务器</strong>
              <small>后端注册表已就绪；添加服务器后即可检查并刷新工具。</small>
            </div>
          )}
        </section>
      </section>
    </main>
  );
}
