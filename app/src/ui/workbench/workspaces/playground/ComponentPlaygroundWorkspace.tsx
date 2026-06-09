import { Button, Panel, StatusBadge } from "../../../v2/components/ui";
import {
  ApprovalCard,
  CommandOutputPanel,
  ContextBudgetBar,
  PatchPlanCard,
  ProviderSelectionCard,
  RuntimeSignalCard,
  ToolTraceCard,
} from "../../../v2/components/runtime";
import "./playground.css";

export interface ComponentPlaygroundWorkspaceProps {
  onOpenAppearance?: () => void;
  onOpenSkills?: () => void;
}

const componentStates = [
  { label: "默认", tone: "neutral" as const },
  { label: "主色", tone: "primary" as const },
  { label: "成功", tone: "success" as const },
  { label: "警告", tone: "warning" as const },
  { label: "危险", tone: "danger" as const },
];

export function ComponentPlaygroundWorkspace({
  onOpenAppearance,
  onOpenSkills,
}: ComponentPlaygroundWorkspaceProps) {
  return (
    <main className="playground-workspace" aria-labelledby="playground-title">
      <section className="playground-command-strip">
        <div>
          <p className="yb-kicker">组件实验室</p>
          <h1 id="playground-title">组件预览</h1>
          <p>在当前工作台外壳中预览可复用的 V2 控件、密度、空状态和运行时卡片。</p>
        </div>
        <div className="playground-actions">
          <Button variant="primary" onClick={onOpenAppearance} disabled={!onOpenAppearance} disabledReason="外观页不可用">
            外观
          </Button>
          <Button variant="secondary" onClick={onOpenSkills} disabled={!onOpenSkills} disabledReason="技能页不可用">
            智能体技能
          </Button>
        </div>
      </section>

      <section className="playground-grid">
        <Panel title="按钮" eyebrow="控件" description="主按钮、次按钮、幽灵按钮、危险按钮、禁用态和加载态。">
          <div className="playground-button-row">
            <Button variant="primary">主按钮</Button>
            <Button variant="secondary">次按钮</Button>
            <Button variant="ghost">幽灵按钮</Button>
            <Button variant="danger">危险</Button>
            <Button disabled disabledReason="禁用态预览">禁用</Button>
            <Button loading>加载中</Button>
          </div>
        </Panel>

        <Panel title="状态" eyebrow="信号" description="用于顶栏、卡片和侧边面板的紧凑运行时标签。">
          <div className="playground-status-grid">
            {componentStates.map((state) => (
              <StatusBadge key={state.label} label={state.label} tone={state.tone} pulse={state.tone === "success"} />
            ))}
          </div>
        </Panel>

        <Panel title="指标卡片" eyebrow="遥测" description="为高密度运维看板提供稳定卡片尺寸。">
          <div className="playground-metrics">
            <div>
              <span>运行时</span>
              <strong>就绪</strong>
              <small>本地智能体已连接</small>
            </div>
            <div>
              <span>审批</span>
              <strong>0</strong>
              <small>没有待处理风险门禁</small>
            </div>
            <div>
              <span>追踪</span>
              <strong>42</strong>
              <small>最近事件已索引</small>
            </div>
          </div>
        </Panel>

        <Panel title="空状态" eyebrow="兜底" description="无数据布局也应保持可用，而不是显得半成品。">
          <div className="playground-empty">
            <strong>未加载记录</strong>
            <span>连接运行时数据，或切换到已有内容的会话以检查实时状态。</span>
          </div>
        </Panel>

        <Panel title="运行时卡片" eyebrow="组合" description="会话、定时任务和 MCP 页面使用的代表性事件卡片。">
          <div className="playground-runtime-stack">
            <RuntimeSignalCard title="运行时" value="就绪" tone="success" description="本地智能体已连接" />
            <ContextBudgetBar usedTokens={38240} reservedTokens={6000} maxTokens={64000} />
            <ProviderSelectionCard
              decision={{
                providerMode: "auto",
                model: "gpt-5.4",
                useBackground: true,
                reason: "包含工具执行和验证的长时间编码任务。",
                confidence: 0.86,
                createdAt: Date.now(),
              }}
            />
          </div>
        </Panel>

        <Panel title="工具追踪" eyebrow="运行时" description="可展开的工具调用，包含输入、输出、延迟和复制入口。">
          <ToolTraceCard
            toolCall={{
              id: "tool-1",
              serverName: "filesystem",
              toolName: "list_directory",
              status: "success",
              latencyMs: 118,
              inputPreview: '{ "path": "D:/py/yuanbao_agent/app/src" }',
              outputPreview: "返回 18 个条目；没有 stderr 输出。",
              startedAt: Date.now(),
            }}
          />
        </Panel>

        <Panel title="审批" eyebrow="风险门禁" description="审批动作保持可见；不再待处理时进入禁用态。">
          <ApprovalCard
            approval={{
              id: "approval-1",
              title: "运行构建验证",
              kind: "command",
              status: "pending",
              risk: "medium",
              summary: "允许在 app 工作区运行 npm.cmd run build。",
              command: "npm.cmd run build",
              cwd: "D:/py/yuanbao_agent/app",
            }}
            onApprove={() => undefined}
            onReject={() => undefined}
          />
        </Panel>

        <Panel title="补丁 + 命令" eyebrow="执行" description="补丁摘要和命令输出共享 V2 运行时表达。">
          <div className="playground-runtime-stack">
            <PatchPlanCard
              patch={{
                id: "patch-1",
                summary: "引入运行时组件基础件",
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
