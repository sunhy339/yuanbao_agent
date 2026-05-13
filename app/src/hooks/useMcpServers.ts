import { useState } from "react";
import type { McpServerRecord } from "@shared";
import type { McpServerDraft } from "../ui/workbench/workspaces/mcp/McpWorkspace";
import { RuntimeClient } from "../lib/runtimeClient";
import { buildMcpServerPayload } from "../state/mcpSkillPayloads";
import type { HookDeps } from "./types";
import { getErrorMessage } from "./types";

const runtimeClient = new RuntimeClient();

export function useMcpServers(deps: HookDeps) {
  const { addToast, toastError, setError } = deps;

  const [mcpServers, setMcpServers] = useState<McpServerRecord[]>([]);
  const [mcpBusyServerId, setMcpBusyServerId] = useState<string | null>(null);
  const [mcpLoading, setMcpLoading] = useState(false);
  const [mcpLastRefresh, setMcpLastRefresh] = useState<{ refreshed: number; tools: string[] } | null>(null);
  const [mcpError, setMcpError] = useState<string | null>(null);

  async function refreshMcpServers() {
    setMcpLoading(true);
    setError(null);
    try {
      const result = await runtimeClient.listMcpServers();
      setMcpServers(result.servers);
      setMcpError(null);
    } catch (reason) {
      setMcpError(getErrorMessage(reason));
      toastError(reason);
    } finally {
      setMcpLoading(false);
    }
  }

  async function handleCreateMcpServer(draft: McpServerDraft) {
    setMcpLoading(true);
    setError(null);
    try {
      const result = await runtimeClient.createMcpServer(buildMcpServerPayload(draft));
      setMcpServers((current) => [
        result.server,
        ...current.filter((server) => server.id !== result.server.id),
      ]);
      setMcpError(null);
      addToast("success", "MCP 服务器已创建");
    } catch (reason) {
      setMcpError(getErrorMessage(reason));
      toastError(reason);
      throw reason;
    } finally {
      setMcpLoading(false);
    }
  }

  async function handleImportMcpServers(drafts: McpServerDraft[]) {
    setMcpLoading(true);
    setError(null);
    try {
      const imported: McpServerRecord[] = [];
      for (const draft of drafts) {
        const result = await runtimeClient.createMcpServer(buildMcpServerPayload(draft));
        imported.push(result.server);
      }
      setMcpServers((current) => [
        ...imported,
        ...current.filter((server) => !imported.some((item) => item.id === server.id)),
      ]);
      setMcpError(null);
      addToast("success", `Imported ${imported.length} MCP server${imported.length === 1 ? "" : "s"}`);
    } catch (reason) {
      setMcpError(getErrorMessage(reason));
      toastError(reason);
      throw reason;
    } finally {
      setMcpLoading(false);
    }
  }

  async function handleUpdateMcpServer(serverId: string, draft: McpServerDraft) {
    setMcpBusyServerId(serverId);
    setError(null);
    try {
      const result = await runtimeClient.updateMcpServer({
        serverId,
        ...buildMcpServerPayload(draft),
      });
      setMcpServers((current) =>
        current.map((server) => (server.id === result.server.id ? result.server : server)),
      );
      setMcpError(null);
      addToast("success", "MCP 服务器已更新");
    } catch (reason) {
      setMcpError(getErrorMessage(reason));
      toastError(reason);
      throw reason;
    } finally {
      setMcpBusyServerId(null);
    }
  }

  async function handleToggleMcpServer(serverId: string, enabled: boolean) {
    setMcpBusyServerId(serverId);
    setError(null);
    try {
      const result = await runtimeClient.updateMcpServer({ serverId, enabled });
      setMcpServers((current) =>
        current.map((server) => (server.id === result.server.id ? result.server : server)),
      );
      setMcpError(null);
      addToast("success", enabled ? "MCP 服务器已启用" : "MCP 服务器已停用");
    } catch (reason) {
      setMcpError(getErrorMessage(reason));
      toastError(reason);
    } finally {
      setMcpBusyServerId(null);
    }
  }

  async function handleRefreshMcpTools(serverId?: string) {
    setMcpBusyServerId(serverId ?? "__all__");
    setError(null);
    try {
      const result = await runtimeClient.refreshMcpTools(serverId ? { serverId } : {});
      setMcpLastRefresh(result);
      await refreshMcpServers();
      setMcpError(null);
      addToast("success", `已刷新 ${result.refreshed} 个 MCP 工具`);
    } catch (reason) {
      setMcpError(getErrorMessage(reason));
      toastError(reason);
    } finally {
      setMcpBusyServerId(null);
    }
  }

  async function handleDeleteMcpServer(serverId: string) {
    setMcpBusyServerId(serverId);
    setError(null);
    try {
      await runtimeClient.deleteMcpServer({ serverId });
      setMcpServers((current) => current.filter((server) => server.id !== serverId));
      setMcpError(null);
      addToast("success", "MCP 服务器已删除");
    } catch (reason) {
      setMcpError(getErrorMessage(reason));
      toastError(reason);
    } finally {
      setMcpBusyServerId(null);
    }
  }

  return {
    mcpServers,
    setMcpServers,
    mcpBusyServerId,
    setMcpBusyServerId,
    mcpLoading,
    setMcpLoading,
    mcpLastRefresh,
    setMcpLastRefresh,
    mcpError,
    setMcpError,
    refreshMcpServers,
    handleCreateMcpServer,
    handleImportMcpServers,
    handleUpdateMcpServer,
    handleToggleMcpServer,
    handleRefreshMcpTools,
    handleDeleteMcpServer,
  };
}
