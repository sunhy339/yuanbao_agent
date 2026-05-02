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
  enabled: true,
};

function formatTimestamp(value?: number) {
  if (!value) {
    return "Never";
  }
  return new Date(value).toLocaleString("en-US", { hour12: false });
}

function formatArgs(args?: string[]) {
  return args?.length ? args.join(" ") : "No args";
}

function draftFromServer(server: McpServerRecord): McpServerDraft {
  return {
    name: server.name,
    transport: server.transport,
    command: server.command ?? "",
    args: server.args?.join("\n") ?? "",
    url: server.url ?? "",
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
  onUpdateServer,
  onToggleServer,
  onRefreshTools,
  onDeleteServer,
  onDismissError,
}: McpWorkspaceProps) {
  const [draft, setDraft] = useState<McpServerDraft>(initialDraft);
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
          <p className="mcp-kicker">Capability Center</p>
          <h1 id="mcp-title">MCP Control Plane</h1>
          <p>
            Manage Model Context Protocol servers exposed by the local runtime.
            Servers created here are persisted by the backend and can register
            tools into the agent runtime.
          </p>
          <div className="mcp-hero-telemetry" aria-label="MCP runtime telemetry">
            <span>
              <strong>{enabledCount}</strong>
              enabled lanes
            </span>
            <span>
              <strong>{formatTimestamp(lastRefresh?.refreshed)}</strong>
              last refresh
            </span>
            <span>
              <strong>{selectedServer?.name ?? "none"}</strong>
              inspected endpoint
            </span>
          </div>
        </div>
        <div className="mcp-hero-actions">
          <Button type="button" onClick={() => void onRefreshServers()} loading={loading} variant="secondary">
            {loading ? "Refreshing..." : "Refresh servers"}
          </Button>
          <Button type="button" onClick={() => void onRefreshTools()} disabled={loading || !servers.length} variant="primary">
            Refresh all tools
          </Button>
        </div>
      </section>

      {errorMessage ? (
        <section className="mcp-error-banner" role="alert" aria-label="MCP error">
          <div>
            <strong>MCP action failed</strong>
            <span>{errorMessage}</span>
          </div>
          {onDismissError ? (
            <Button type="button" variant="ghost" size="sm" onClick={onDismissError}>
              Dismiss
            </Button>
          ) : null}
        </section>
      ) : null}

      <section className="mcp-metrics" aria-label="MCP summary">
        <div>
          <span>{servers.length}</span>
          <small>Servers</small>
        </div>
        <div>
          <span>{enabledCount}</span>
          <small>Enabled</small>
        </div>
        <div>
          <span>{stdioCount}</span>
          <small>stdio</small>
        </div>
        <div>
          <span>{lastRefresh?.refreshed ?? 0}</span>
          <small>Tools refreshed</small>
        </div>
      </section>

      <section className="mcp-grid">
        <form className="mcp-panel mcp-create-panel" onSubmit={handleSubmit}>
          <div className="mcp-panel-header">
            <div>
              <p className="mcp-kicker">{formMode === "edit" ? "Edit Endpoint" : "New Endpoint"}</p>
              <h2>{formMode === "edit" ? "Edit MCP server" : "Add MCP server"}</h2>
            </div>
            {formMode === "edit" ? (
              <Button type="button" variant="ghost" size="sm" onClick={resetForm} disabled={loading}>
                Cancel
              </Button>
            ) : null}
          </div>
          <label>
            <span>Name</span>
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
            <span>Transport</span>
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
                <span>Command</span>
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
                <span>Args</span>
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
          <label className="mcp-toggle">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(event) => {
                const { checked } = event.currentTarget;
                setDraft((current) => ({ ...current, enabled: checked }));
              }}
            />
            <span>Enable after creation</span>
          </label>
          <div className="mcp-form-actions">
            <Button type="submit" className="mcp-primary-action" disabled={loading || !draft.name.trim()} loading={loading} variant="primary">
              {formMode === "edit" ? "Save server" : "Create server"}
            </Button>
          </div>
        </form>

        <section className="mcp-panel">
          <div className="mcp-panel-header">
            <div>
              <p className="mcp-kicker">Servers</p>
              <h2>Runtime registry</h2>
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
                  <StatusBadge label={server.enabled ? "enabled" : "disabled"} tone={server.enabled ? "success" : "neutral"} compact />
                </button>
              ))
            ) : (
              <div className="mcp-empty-state">
                <strong>No MCP servers yet</strong>
                <small>Create a stdio/http endpoint to expose external tools to the agent.</small>
              </div>
            )}
          </div>
        </section>

        <section className="mcp-panel mcp-detail-panel">
          <div className="mcp-panel-header">
            <div>
              <p className="mcp-kicker">Inspector</p>
              <h2>{selectedServer?.name ?? "No server selected"}</h2>
            </div>
          </div>
          {selectedServer ? (
            <>
              <div className="mcp-inspector-band" data-enabled={selectedServer.enabled}>
                <span className="mcp-status-dot" data-enabled={selectedServer.enabled} aria-hidden="true" />
                <strong>{selectedServer.enabled ? "Endpoint online" : "Endpoint paused"}</strong>
                <small>{selectedServer.transport} transport</small>
              </div>
              <dl className="mcp-definition-list">
                <div>
                  <dt>ID</dt>
                  <dd>{selectedServer.id}</dd>
                </div>
                <div>
                  <dt>Transport</dt>
                  <dd>{selectedServer.transport}</dd>
                </div>
                <div>
                  <dt>Command</dt>
                  <dd>{selectedServer.command || selectedServer.url || "Not configured"}</dd>
                </div>
                <div>
                  <dt>Args</dt>
                  <dd>{formatArgs(selectedServer.args)}</dd>
                </div>
                <div>
                  <dt>Updated</dt>
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
                  Edit
                </Button>
                <Button
                  type="button"
                  onClick={() => void onToggleServer(selectedServer.id, !selectedServer.enabled)}
                  disabled={busyServerId === selectedServer.id}
                  loading={busyServerId === selectedServer.id}
                  size="sm"
                  variant={selectedServer.enabled ? "secondary" : "primary"}
                >
                  {selectedServer.enabled ? "Disable" : "Enable"}
                </Button>
                <Button
                  type="button"
                  onClick={() => void onRefreshTools(selectedServer.id)}
                  disabled={busyServerId === selectedServer.id || !selectedServer.enabled}
                  loading={busyServerId === selectedServer.id}
                  size="sm"
                  variant="secondary"
                >
                  Refresh tools
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
                  Delete
                </Button>
              </div>
              {lastRefresh?.tools.length ? (
                <div className="mcp-tool-preview">
                  <strong>Last refreshed tools</strong>
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
              <strong>Select a server</strong>
              <small>The backend registry is ready; add a server to inspect and refresh tools.</small>
            </div>
          )}
        </section>
      </section>
    </main>
  );
}
