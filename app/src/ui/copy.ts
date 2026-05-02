import type { SystemWorkspaceKind } from "./workbench/types";

const STATUS_LABELS: Record<string, string> = {
  active: "活跃",
  applied: "已应用",
  approved: "已批准",
  background: "后台",
  cancelled: "已取消",
  changed: "已修改",
  checking: "检查中",
  clear: "已清空",
  completed: "已完成",
  configured: "已配置",
  degraded: "部分可用",
  disabled: "已停用",
  empty: "空",
  enabled: "已启用",
  error: "错误",
  expired: "已过期",
  failed: "失败",
  healthy: "健康",
  idle: "空闲",
  inline: "前台",
  loading: "加载中",
  offline: "离线",
  ok: "正常",
  passed: "已通过",
  paused: "已暂停",
  pending: "待处理",
  planning: "规划中",
  preview: "预览",
  queued: "排队中",
  ready: "就绪",
  recorded: "已记录",
  rejected: "已拒绝",
  running: "运行中",
  skipped: "已跳过",
  standby: "待命",
  succeeded: "已成功",
  success: "成功",
  verifying: "验证中",
  waiting: "等待中",
  waiting_approval: "等待审批",
  warning: "警告",
  "low risk": "低风险",
  "medium risk": "中风险",
  "high risk": "高风险",
  "missing env": "缺少环境变量",
  missing_env: "缺少环境变量",
  mocked: "本地预览",
  not_configured: "未配置",
  started: "已启动",
};

export const SYSTEM_WORKSPACE_LABELS: Record<SystemWorkspaceKind, string> = {
  overview: "总览",
  "new-session": "新建会话",
  scheduled: "定时任务",
  mcp: "MCP 中心",
  skills: "技能",
  appearance: "外观",
  playground: "组件预览",
  settings: "设置",
};

export function formatStatusLabel(status?: string | null): string {
  if (!status) {
    return "未知";
  }

  const normalized = status.trim().toLowerCase();
  return STATUS_LABELS[normalized] ?? status;
}

export function formatSystemWorkspaceLabel(kind?: string | null): string {
  if (!kind) {
    return "会话";
  }

  return SYSTEM_WORKSPACE_LABELS[kind as SystemWorkspaceKind] ?? kind;
}

export function formatRuntimeModeLabel(value?: string | null): string {
  if (!value) {
    return "本地运行时";
  }

  if (value === "mock") {
    return "本地预览";
  }

  if (value === "openai-compatible") {
    return "OpenAI 兼容";
  }

  return value;
}

export function formatCountLabel(count: number, singular: string, plural = singular): string {
  return `${count} ${count === 1 ? singular : plural}`;
}
