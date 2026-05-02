import { Button, Panel, StatusBadge } from "../../../v2/components/ui";
import {
  ApprovalCard,
  CommandOutputPanel,
  ContextBudgetBar,
  PatchPlanCard,
  RoutingDecisionCard,
  RuntimeSignalCard,
  ToolTraceCard,
} from "../../../v2/components/runtime";
import "./playground.css";

export interface ComponentPlaygroundWorkspaceProps {
  onOpenAppearance?: () => void;
  onOpenSkills?: () => void;
}

const componentStates = [
  { label: "Default", tone: "neutral" as const },
  { label: "Primary", tone: "primary" as const },
  { label: "Success", tone: "success" as const },
  { label: "Warning", tone: "warning" as const },
  { label: "Danger", tone: "danger" as const },
];

export function ComponentPlaygroundWorkspace({
  onOpenAppearance,
  onOpenSkills,
}: ComponentPlaygroundWorkspaceProps) {
  return (
    <main className="playground-workspace" aria-labelledby="playground-title">
      <section className="playground-command-strip">
        <div>
          <p className="yb-kicker">Component Lab</p>
          <h1 id="playground-title">Component Playground</h1>
          <p>Preview reusable V2 controls, density, empty states, and runtime cards in the active workbench shell.</p>
        </div>
        <div className="playground-actions">
          <Button variant="primary" onClick={onOpenAppearance} disabled={!onOpenAppearance} disabledReason="Appearance is not available">
            Appearance
          </Button>
          <Button variant="secondary" onClick={onOpenSkills} disabled={!onOpenSkills} disabledReason="Skills are not available">
            Agent Skills
          </Button>
        </div>
      </section>

      <section className="playground-grid">
        <Panel title="Buttons" eyebrow="Controls" description="Primary, secondary, ghost, danger, disabled, and loading surfaces.">
          <div className="playground-button-row">
            <Button variant="primary">Primary</Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="danger">Danger</Button>
            <Button disabled disabledReason="Disabled state preview">Disabled</Button>
            <Button loading>Loading</Button>
          </div>
        </Panel>

        <Panel title="Status" eyebrow="Signals" description="Compact runtime labels for topbar, cards, and side panels.">
          <div className="playground-status-grid">
            {componentStates.map((state) => (
              <StatusBadge key={state.label} label={state.label} tone={state.tone} pulse={state.tone === "success"} />
            ))}
          </div>
        </Panel>

        <Panel title="Metric Cards" eyebrow="Telemetry" description="Stable card dimensions for dense operational dashboards.">
          <div className="playground-metrics">
            <div>
              <span>Runtime</span>
              <strong>Ready</strong>
              <small>Local agent connected</small>
            </div>
            <div>
              <span>Approvals</span>
              <strong>0</strong>
              <small>No pending risk gates</small>
            </div>
            <div>
              <span>Trace</span>
              <strong>42</strong>
              <small>Recent events indexed</small>
            </div>
          </div>
        </Panel>

        <Panel title="Empty State" eyebrow="Fallback" description="No-data layouts should stay useful without looking unfinished.">
          <div className="playground-empty">
            <strong>No records loaded</strong>
            <span>Connect runtime data or switch to a populated session to inspect live states.</span>
          </div>
        </Panel>

        <Panel title="Runtime Card" eyebrow="Composition" description="Representative event card used by session, scheduled, and MCP pages.">
          <div className="playground-runtime-stack">
            <RuntimeSignalCard title="Runtime" value="Ready" tone="success" description="Local agent connected" />
            <ContextBudgetBar usedTokens={38240} reservedTokens={6000} maxTokens={64000} />
            <RoutingDecisionCard
              decision={{
                providerMode: "auto",
                model: "gpt-5.4",
                useBackground: true,
                reason: "Long-running coding task with tool execution and verification.",
                confidence: 0.86,
                createdAt: Date.now(),
              }}
            />
          </div>
        </Panel>

        <Panel title="Tool Trace" eyebrow="Runtime" description="Expandable tool call with input, output, latency, and copy hooks.">
          <ToolTraceCard
            toolCall={{
              id: "tool-1",
              serverName: "filesystem",
              toolName: "list_directory",
              status: "success",
              latencyMs: 118,
              inputPreview: '{ "path": "D:/py/yuanbao_agent/app/src" }',
              outputPreview: "18 entries returned; no stderr output.",
              startedAt: Date.now(),
            }}
          />
        </Panel>

        <Panel title="Approval" eyebrow="Risk Gate" description="Approval actions stay visible and disabled when no longer pending.">
          <ApprovalCard
            approval={{
              id: "approval-1",
              title: "Run build verification",
              kind: "command",
              status: "pending",
              risk: "medium",
              summary: "Allow npm.cmd run build in the app workspace.",
              command: "npm.cmd run build",
              cwd: "D:/py/yuanbao_agent/app",
            }}
            onApprove={() => undefined}
            onReject={() => undefined}
          />
        </Panel>

        <Panel title="Patch + Command" eyebrow="Execution" description="Patch summaries and command output share the V2 runtime language.">
          <div className="playground-runtime-stack">
            <PatchPlanCard
              patch={{
                id: "patch-1",
                summary: "Introduce runtime component primitives",
                status: "ready",
                filesChanged: 3,
                additions: 218,
                deletions: 4,
              }}
              changedFiles={[
                { path: "app/src/ui/v2/components/runtime/RuntimeComponents.tsx", status: "added", additions: 188 },
                { path: "app/src/ui/workbench/workspaces/playground/ComponentPlaygroundWorkspace.tsx", status: "updated", additions: 30, deletions: 4 },
              ]}
              onOpenDiff={() => undefined}
            />
            <CommandOutputPanel
              command={{
                id: "cmd-1",
                command: "npm.cmd run build",
                status: "completed",
                cwd: "D:/py/yuanbao_agent/app",
                exitCode: 0,
                durationMs: 835,
                stdout: "tsc --noEmit && vite build\n83 modules transformed\nbuilt in 835ms",
              }}
            />
          </div>
        </Panel>
      </section>
    </main>
  );
}
