# Frontend V2 Agent Profile Schema Proposal

Generated: 2026-05-02

## Purpose

Define the minimum agent profile contract needed before the frontend exposes Add agent, Edit agent, or Agent enable/disable controls.

The current UI keeps agent lanes read-only because there is no shared `AgentProfile` type, RPC surface, or runtime persistence backing those controls.

## Proposed Record

```ts
export interface AgentProfileRecord {
  id: string;
  name: string;
  description?: string;
  role: "planner" | "builder" | "reviewer" | "researcher" | "custom";
  cwd?: string;
  enabled: boolean;
  permissionMode?: "ask" | "plan" | "auto" | "skip";
  providerProfileId?: string;
  model?: string;
  skillIds: string[];
  mcpServerIds?: string[];
  toolPolicy?: {
    allowedTools?: string[];
    deniedTools?: string[];
    requiresApproval?: string[];
  };
  systemPrompt?: string;
  createdAt?: number;
  updatedAt?: number;
}
```

## Required RPC Before UI Controls

- `agent.profile.list` returns `{ agents: AgentProfileRecord[] }`.
- `agent.profile.create` validates required fields and returns `{ agent: AgentProfileRecord }`.
- `agent.profile.update` accepts partial changes by `agentId` and returns `{ agent: AgentProfileRecord }`.
- `agent.profile.delete` rejects deletion of built-in/default agents and returns `{ deleted: true, agentId }`.
- `agent.profile.toggle` is optional; if omitted, `update` must accept `{ enabled }`.

## Frontend Rules

- Built-in/default agents should be inspectable but not deletable.
- Add/Edit controls should be shown only when create/update RPC calls are available.
- Enable/disable toggles should be shown only when `enabled` has runtime scheduling/routing meaning, not merely a visual flag.
- Permission mode and tool policy must reuse the global approval vocabulary so agent profiles cannot silently bypass workspace policy.
- Skill assignment should reference existing skill IDs and must not duplicate skill prompt text into the agent record.

## Open Backend Decisions

- Whether agent profiles are workspace-scoped or global.
- Whether provider/model overrides are allowed per agent.
- Whether disabled agents are hidden from routing, blocked from execution, or only hidden from the UI.
- Whether built-in lanes are seeded by migrations or synthesized at runtime.
