import type { SkillPresetRecord } from "@shared";
import type { SkillDraft } from "../ui/workbench/workspaces/skills/SkillsWorkspace";
import type { McpServerDraft } from "../ui/workbench/workspaces/mcp/McpWorkspace";
import type { SettingsSkillConfig } from "../ui/workbench/workspaces/settings/SettingsWorkspace";

export function normalizeSkillForSettings(skill: SkillPresetRecord): SettingsSkillConfig {
  const toolWhitelist = skill.toolWhitelist ?? skill.tool_whitelist ?? [];
  const systemPrompt = skill.systemPrompt ?? skill.system_prompt;
  const isBuiltin = Boolean(skill.isBuiltin ?? skill.is_builtin);
  return {
    id: skill.id,
    name: skill.name,
    description:
      skill.description ||
      (toolWhitelist.length ? `工具：${toolWhitelist.join(", ")}` : "运行时技能预设"),
    path: skill.category ? `category:${skill.category}` : undefined,
    systemPrompt,
    toolWhitelist,
    isBuiltin,
    enabled: true,
    updateAvailable: false,
  };
}

export function stripWrappingShellQuotes(value: string): string {
  const token = value.trim();
  if (token.length >= 2 && token[0] === token[token.length - 1] && (token[0] === '"' || token[0] === "'")) {
    return token.slice(1, -1);
  }
  return token;
}

export function splitShellLikeArgs(value: string): string[] {
  const args: string[] = [];
  let current = "";
  let quote: '"' | "'" | null = null;
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    if (quote) {
      if (char === quote) {
        quote = null;
      } else {
        current += char;
      }
      continue;
    }
    if (char === '"' || char === "'") {
      quote = char;
      continue;
    }
    if (/\s/.test(char)) {
      if (current) {
        args.push(current);
        current = "";
      }
      continue;
    }
    current += char;
  }
  if (current) {
    args.push(current);
  }
  return args;
}

export function parseMcpArgs(value: string): string[] {
  const trimmed = value.trim();
  if (!trimmed) {
    return [];
  }
  if (trimmed.startsWith("[")) {
    try {
      const parsed = JSON.parse(trimmed) as unknown;
      if (Array.isArray(parsed)) {
        return parsed.map((item) => stripWrappingShellQuotes(String(item))).filter(Boolean);
      }
    } catch {
      // Fall through to line/comma parsing.
    }
  }
  const rawParts = /\r?\n|,/.test(trimmed) ? trimmed.split(/\r?\n|,/) : splitShellLikeArgs(trimmed);
  return rawParts.map(stripWrappingShellQuotes).filter(Boolean);
}

export function parseMcpKeyValuePairs(value: string): Record<string, string> {
  const trimmed = value.trim();
  if (!trimmed) {
    return {};
  }
  if (trimmed.startsWith("{")) {
    try {
      const parsed = JSON.parse(trimmed) as unknown;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return Object.fromEntries(
          Object.entries(parsed as Record<string, unknown>)
            .filter(([key]) => key.trim())
            .map(([key, item]) => [key.trim(), String(item)]),
        );
      }
    } catch {
      // Fall through to KEY=value parsing.
    }
  }
  return Object.fromEntries(
    trimmed
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const separator = line.indexOf("=");
        if (separator === -1) {
          return [line, ""] as const;
        }
        return [line.slice(0, separator).trim(), line.slice(separator + 1).trim()] as const;
      })
      .filter(([key]) => Boolean(key)),
  );
}

export function buildMcpServerPayload(draft: McpServerDraft) {
  return {
    name: draft.name.trim(),
    transport: draft.transport,
    command: draft.transport === "stdio" ? stripWrappingShellQuotes(draft.command) : undefined,
    args: draft.transport === "stdio" ? parseMcpArgs(draft.args) : [],
    url: draft.transport === "stdio" ? undefined : draft.url.trim(),
    headers: draft.transport === "stdio" ? {} : parseMcpKeyValuePairs(draft.headers),
    env: parseMcpKeyValuePairs(draft.env),
    enabled: draft.enabled,
  };
}

export function parseSkillToolWhitelist(value: string): string[] {
  return value
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function buildSkillPayload(draft: SkillDraft) {
  return {
    name: draft.name.trim(),
    description: draft.description.trim(),
    system_prompt: draft.systemPrompt.trim(),
    tool_whitelist: parseSkillToolWhitelist(draft.toolWhitelist),
    category: draft.category.trim() || "custom",
  };
}
