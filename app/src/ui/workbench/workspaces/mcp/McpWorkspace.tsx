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

function validateMcpDraft(draft: McpServerDraft): string | null {
  if (!draft.name.trim()) {
    return "请输入服务器名称。";
  }
  if (draft.transport === "stdio" && !draft.command.trim()) {
    return "请输入启动命令。";
  }
  if (draft.transport !== "stdio" && !draft.url.trim()) {
    return "请输入服务器 URL。";
  }
  return null;
}

function serverEndpoint(server: McpServerRecord) {
  return server.command || server.url || "未配置";
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
  const [serverQuery, setServerQuery] = useState("");
  const [serverFilter, setServerFilter] = useState<"all" | "enabled" | "disabled">("all");

  const enabledCount = useMemo(() => servers.filter((server) => server.enabled).length, [servers]);
  const visibleServers = useMemo(() => {
    const query = serverQuery.trim().toLowerCase();
    return servers.filter((server) => {
      if (serverFilter === "enabled" && !server.enabled) return false;
      if (serverFilter === "disabled" && server.enabled) return false;
      if (!query) return true;
      return [
        server.name,
        server.transport,
        server.command ?? "",
        server.url ?? "",
        ...(server.args ?? []),
        ...Object.keys(server.env ?? {}),
        ...Object.keys(server.headers ?? {}),
      ].some((value) => value.toLowerCase().includes(query));
    });
  }, [serverFilter, serverQuery, servers]);

  const selectedServer = servers.find((server) => server.id === expandedId) ?? servers[0] ?? null;
  const draftError = validateMcpDraft(draft);
  const formTitle = formMode === "edit" ? "编辑服务器" : "添加服务器";
  const formHint = draftError
    ?? (formMode === "edit" ? "保存后会更新当前服务器配置。" : "可手动配置，也可粘贴 MCP JSON 批量导入。");

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (draftError) {
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

  function requestDeleteServer(server: McpServerRecord) {
    if (window.confirm(`删除 MCP 服务器“${server.name}”？`)) {
      void onDeleteServer(server.id);
    }
  }

  useEffect(() => {
    if (editingServerId && !servers.some((server) => server.id === editingServerId)) {
      resetForm();
    }
  }, [editingServerId, servers]);

  return (
    <main className="mcp-workspace" aria-labelledby="mcp-title">
      <section className="mcp-command-strip" aria-label="MCP 管理栏">
        <div className="mcp-command-main">
          <p className="mcp-kicker">MCP</p>
          <h1 id="mcp-title">MCP 服务器</h1>
          <div className="mcp-meta-strip" aria-label="MCP 摘要">
            <span>{enabledCount}/{servers.length} 已启用</span>
            <span>上次刷新 {formatTimestamp(lastRefresh?.refreshed)}</span>
            <span>{lastRefresh?.tools.length ?? 0} 个工具</span>
          </div>
        </div>
        <div className="mcp-command-actions">
          <Button type="button" onClick={resetForm} disabled={loading} variant="secondary">
            新建服务器
          </Button>
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

      <section className="mcp-layout">
        <section className="mcp-pane mcp-list-pane" aria-label="MCP 服务器列表">
          <div className="mcp-pane-header">
            <div>
              <p className="mcp-kicker">服务器</p>
              <h2>运行时注册表</h2>
            </div>
            <small>{visibleServers.length}/{servers.length}</small>
          </div>

          <div className="mcp-list-tools" aria-label="服务器筛选">
            <label>
              <span>搜索</span>
              <input
                aria-label="搜索 MCP 服务器"
                value={serverQuery}
                placeholder="名称、命令、URL、env"
                onChange={(event) => setServerQuery(event.currentTarget.value)}
              />
            </label>
            <div className="mcp-filter-tabs" role="tablist" aria-label="MCP 状态筛选">
              {[
                { id: "all", label: "全部" },
                { id: "enabled", label: "启用" },
                { id: "disabled", label: "停用" },
              ].map((option) => (
                <button
                  key={option.id}
                  type="button"
                  role="tab"
                  aria-selected={serverFilter === option.id}
                  onClick={() => setServerFilter(option.id as typeof serverFilter)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="mcp-server-list">
            {visibleServers.length ? (
              visibleServers.map((server) => (
                <button
                  key={server.id}
                  type="button"
                  className={server.id === selectedServer?.id ? "mcp-server-card is-active" : "mcp-server-card"}
                  aria-current={server.id === selectedServer?.id ? "true" : undefined}
                  onClick={() => setExpandedId(server.id)}
                >
                  <span className="mcp-status-dot" data-enabled={server.enabled} aria-hidden="true" />
                  <span className="mcp-server-main">
                    <strong>{server.name}</strong>
                    <small>{serverEndpoint(server)}</small>
                  </span>
                  <span className="mcp-server-meta">
                    <small>{server.transport}</small>
                    <StatusBadge label={server.enabled ? "已启用" : "已停用"} tone={server.enabled ? "success" : "neutral"} compact />
                  </span>
                </button>
              ))
            ) : servers.length ? (
              <div className="mcp-empty-state">
                <strong>没有匹配的 MCP 服务器</strong>
                <small>调整搜索词或状态筛选后再查看。</small>
              </div>
            ) : (
              <div className="mcp-empty-state">
                <strong>暂无 MCP 服务器</strong>
                <small>创建 stdio/http 端点后，可将外部工具暴露给智能体。</small>
              </div>
            )}
          </div>
        </section>

        <section className="mcp-pane mcp-inspector-pane" aria-label="MCP 检查器">
          <div className="mcp-pane-header">
            <div>
              <p className="mcp-kicker">检查器</p>
              <h2>{selectedServer?.name ?? "未选择服务器"}</h2>
            </div>
            {selectedServer ? (
              <div className="mcp-selected-actions">
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
                  onClick={() => requestDeleteServer(selectedServer)}
                  disabled={busyServerId === selectedServer.id}
                  loading={busyServerId === selectedServer.id}
                  size="sm"
                  variant="danger"
                >
                  删除
                </Button>
              </div>
            ) : null}
          </div>

          {selectedServer ? (
            <div className="mcp-inspector-summary" data-enabled={selectedServer.enabled}>
              <div className="mcp-inspector-status">
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
                  <dt>入口</dt>
                  <dd>{serverEndpoint(selectedServer)}</dd>
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
            </div>
          ) : (
            <div className="mcp-empty-state">
              <strong>选择一个服务器</strong>
              <small>后端注册表已就绪；添加服务器后即可检查并刷新工具。</small>
            </div>
          )}

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

          <form className="mcp-editor-form" onSubmit={handleSubmit}>
            <div className="mcp-editor-heading">
              <div>
                <p className="mcp-kicker">{formMode === "edit" ? "编辑端点" : "配置"}</p>
                <h2>{formTitle}</h2>
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
                    rows={3}
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

            <div className="mcp-form-grid">
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
            </div>

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
              <span>{formMode === "edit" ? "启用此服务器" : "创建后启用"}</span>
            </label>
            <div className="mcp-form-actions">
              <p className="mcp-form-hint" data-tone={draftError ? "danger" : "neutral"}>{formHint}</p>
              <Button type="submit" className="mcp-primary-action" disabled={loading || Boolean(draftError)} loading={loading} variant="primary">
                {formMode === "edit" ? "保存服务器" : "创建服务器"}
              </Button>
            </div>
          </form>
        </section>
      </section>
    </main>
  );
}
