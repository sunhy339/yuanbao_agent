import { useMemo, useState, type FormEvent } from "react";
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
  onRefreshServers: () => void | Promise<void>;
  onCreateServer: (draft: McpServerDraft) => void | Promise<void>;
  onToggleServer: (serverId: string, enabled: boolean) => void | Promise<void>;
  onRefreshTools: (serverId?: string) => void | Promise<void>;
  onDeleteServer: (serverId: string) => void | Promise<void>;
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

export function McpWorkspace({
  servers,
  loading = false,
  busyServerId = null,
  lastRefresh = null,
  onRefreshServers,
  onCreateServer,
  onToggleServer,
  onRefreshTools,
  onDeleteServer,
}: McpWorkspaceProps) {
  const [draft, setDraft] = useState<McpServerDraft>(initialDraft);
  const [expandedId, setExpandedId] = useState<string | null>(servers[0]?.id ?? null);
  const enabledCount = useMemo(() => servers.filter((server) => server.enabled).length, [servers]);
  const stdioCount = useMemo(() => servers.filter((server) => server.transport === "stdio").length, [servers]);
  const selectedServer = servers.find((server) => server.id === expandedId) ?? servers[0] ?? null;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!draft.name.trim()) {
      return;
    }
    await onCreateServer(draft);
    setDraft(initialDraft);
  }

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
              <p className="mcp-kicker">New Endpoint</p>
              <h2>Add MCP server</h2>
            </div>
          </div>
          <label>
            <span>Name</span>
            <input
              value={draft.name}
              onChange={(event) => setDraft((current) => ({ ...current, name: event.currentTarget.value }))}
              placeholder="filesystem"
              required
            />
          </label>
          <label>
            <span>Transport</span>
            <select
              value={draft.transport}
              onChange={(event) =>
                setDraft((current) => ({ ...current, transport: event.currentTarget.value as McpServerTransport }))
              }
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
                  onChange={(event) => setDraft((current) => ({ ...current, command: event.currentTarget.value }))}
                  placeholder="npx @modelcontextprotocol/server-filesystem"
                />
              </label>
              <label>
                <span>Args</span>
                <textarea
                  value={draft.args}
                  onChange={(event) => setDraft((current) => ({ ...current, args: event.currentTarget.value }))}
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
                onChange={(event) => setDraft((current) => ({ ...current, url: event.currentTarget.value }))}
                placeholder="http://127.0.0.1:8787/sse"
              />
            </label>
          )}
          <label className="mcp-toggle">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(event) => setDraft((current) => ({ ...current, enabled: event.currentTarget.checked }))}
            />
            <span>Enable after creation</span>
          </label>
          <Button type="submit" className="mcp-primary-action" disabled={loading || !draft.name.trim()} loading={loading} variant="primary">
            Create server
          </Button>
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
