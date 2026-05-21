/**
 * Slash command registry for the composer input.
 *
 * Commands start with `/` and are intercepted before the message is sent to
 * the runtime.  Each command has a name, description, and optional argument
 * hint.  The dispatcher returns a `SlashCommandResult` when the input matches
 * a known command, or `null` when the input should be forwarded as a normal
 * message.
 */

// ── types ──────────────────────────────────────────────────────────────

export interface SlashCommand {
  name: string;
  description: string;
  /** Short usage hint shown in autocomplete, e.g. "[on|off]" */
  argsHint?: string;
}

export type SlashCommandResultKind =
  | "clear"          // clear chat messages
  | "help"           // show help / list commands
  | "init"           // initialize workspace memory files
  | "status"         // show runtime status
  | "model"          // switch or show current model
  | "compact"        // compact / summarize conversation
  | "config"         // show current config
  | "mcp"            // list / refresh MCP servers
  | "skills";        // list / refresh skills

export interface SlashCommandResult {
  kind: SlashCommandResultKind;
  /** The raw argument string after the command name (trimmed, may be empty) */
  args: string;
}

// ── built-in commands ──────────────────────────────────────────────────

export const SLASH_COMMANDS: SlashCommand[] = [
  { name: "/help",    description: "Show available slash commands" },
  { name: "/init",    description: "Initialize YUANBAO.md and memory files for this workspace" },
  { name: "/clear",   description: "Clear current session chat messages" },
  { name: "/compact", description: "Summarize conversation context" },
  { name: "/status",  description: "Show runtime and task status" },
  { name: "/model",   description: "Show or switch provider model", argsHint: "[model-name]" },
  { name: "/config",  description: "Show current provider configuration" },
  { name: "/mcp",     description: "List MCP servers or refresh tools", argsHint: "[refresh]" },
  { name: "/skills",  description: "List skill presets or refresh", argsHint: "[refresh]" },
];

// ── dispatch ───────────────────────────────────────────────────────────

/**
 * Parse *input* and return a `SlashCommandResult` if it is a slash command.
 * Returns `null` for non-slash input (normal message) or unknown commands.
 *
 * An input that starts with `/` but does NOT match a known command returns
 * `null` so it is treated as a normal message — this lets users send literal
 * `/...` text when desired.
 */
export function dispatchSlashCommand(input: string): SlashCommandResult | null {
  const trimmed = input.trim();
  if (!trimmed.startsWith("/")) return null;

  const spaceIdx = trimmed.indexOf(" ");
  const cmd = spaceIdx === -1 ? trimmed : trimmed.slice(0, spaceIdx);
  const args = spaceIdx === -1 ? "" : trimmed.slice(spaceIdx + 1).trim();

  const match = SLASH_COMMANDS.find((c) => c.name === cmd);
  if (!match) return null;

  return {
    kind: match.name.slice(1) as SlashCommandResultKind, // strip leading "/"
    args,
  };
}

/**
 * Return commands whose name starts with the given *prefix* (e.g. "/h").
 * Used for autocomplete.
 */
export function matchCommands(prefix: string): SlashCommand[] {
  if (!prefix.startsWith("/")) return [];
  return SLASH_COMMANDS.filter((c) => c.name.startsWith(prefix.toLowerCase()));
}
