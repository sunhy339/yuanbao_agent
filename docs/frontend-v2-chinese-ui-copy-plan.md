# Frontend V2 中文 UI 文案整改计划

Generated: 2026-05-02

## Goal

把 Frontend V2 的用户可见 UI 先统一成简体中文，让桌面端从“工程调试界面”更像“本地智能体工作台”。本计划只处理用户可见文案和显示口径，不改 runtime 协议字段、事件类型、日志结构、测试夹具 id、MCP/API 等技术标识。

## Design Decisions

- 语言基调：专业、清晰、偏产品化，避免过多内部工程词。
- 信息密度：保持当前工作台式密度，不做营销式大标题。
- 术语策略：核心概念中文优先，必要技术缩写保留英文。
- 状态策略：状态标签统一短词，正文解释使用完整中文句子。
- 交互反馈：所有保存、复制、刷新、测试、失败反馈使用明确中文。
- 布局约束：中文化后必须继续通过窄桌面和长文本视觉回归。

## Translation Rules

- 保留英文：
  - MCP、API、JSON、URL、HTTP、OpenAI、Tauri、CLI、Token、env var 名称、模型名、文件路径、命令、代码。
- 翻译为中文：
  - 页面标题、按钮、空状态、说明文案、toast、错误提示、状态标签、表单字段、卡片标题。
- 中英混排：
  - `MCP 服务`、`API 格式`、`JSON 配置`、`Token 预算`、`Tauri 桌面运行时`。
- 不翻译：
  - `aria-label` 中如果用于测试定位的英文暂时保留，第二阶段统一改测试。
  - runtime 原始状态值只在映射层翻译，不改数据值。

## Glossary

| Current | Chinese UI |
| --- | --- |
| Workbench | 工作台 |
| Overview | 总览 |
| New Session | 新建会话 |
| Session | 会话 |
| Task | 任务 |
| Runtime | 本地运行时 |
| Provider | 模型供应商 |
| Local preview / Browser preview | 本地预览 / 浏览器预览 |
| Composer | 输入区 |
| Approval | 审批 |
| Approve / Reject | 批准 / 拒绝 |
| Patch | 改动 |
| Diff | 差异 |
| Trace / Diagnostics | 诊断 |
| Command | 命令 |
| Scheduled Tasks | 定时任务 |
| Agent Skills | 技能 |
| Settings | 设置 |
| Appearance | 外观 |
| Component Playground | 组件预览 |
| Workspace | 工作区 |
| Project memory | 项目记忆 |
| Project focus | 项目重点 |
| Computer Use | 电脑操作权限 |

## Status Copy Map

| Raw status | Chinese label |
| --- | --- |
| active | 活跃 |
| idle | 空闲 |
| queued | 排队中 |
| running | 运行中 |
| planning | 规划中 |
| verifying | 验证中 |
| waiting_approval | 等待审批 |
| pending | 待处理 |
| completed / succeeded | 已完成 |
| passed | 已通过 |
| failed / error | 失败 |
| cancelled | 已取消 |
| approved | 已批准 |
| rejected | 已拒绝 |
| disabled | 已停用 |
| configured | 已配置 |
| preview | 预览 |
| missing env | 缺少环境变量 |

## Page Display Plan

### 1. Shell / Navigation

- Sidebar:
  - `Overview` -> `总览`
  - `New Session` -> `新建会话`
  - `Scheduled` -> `定时任务`
  - `MCP Center` -> `MCP 中心`
  - `Agent Skills` -> `技能`
  - `Appearance` -> `外观`
  - `Settings` -> `设置`
- Top badges:
  - Runtime 状态显示为 `运行时已连接` / `浏览器预览模式` / `运行时离线`
  - Provider 显示为 `本地预览模型` 或实际模型名。

### 2. Overview

- 首页从功能概览改为“工作台状态总览”。
- 主要模块：
  - `当前会话`
  - `运行时状态`
  - `模型配置`
  - `MCP 服务`
  - `待处理审批`
  - `上下文预算`
- 行动按钮：
  - `开始新会话`
  - `打开设置`
  - `管理 MCP`

### 3. New Session

- 标题：`新建会话`
- 字段：
  - `工作区路径`
  - `会话标题`
  - `模型`
  - `任务模板`
- 按钮：
  - `打开工作区`
  - `创建会话`
  - `使用当前配置`
- 空/禁用提示明确说明：`请先连接本地运行时`、`请选择可写工作区`。

### 4. Session Workbench

- 头部：
  - `会话`
  - `任务状态`
  - `审批模式`
- Runtime lanes:
  - `执行` -> `命令执行`
  - `Patch` -> `改动队列`
  - `Diagnostics` -> `重要诊断`
- Activity stream:
  - `Messages and operations` -> `消息与操作`
  - `Conversation activity` -> `会话活动`
- Buttons:
  - `Refresh task` -> `刷新任务`
  - `Refresh diagnostics` -> `刷新诊断`
  - `Stop task` -> `停止任务`
  - `Load diff / Open diff` -> `查看差异`
  - `Copy output` -> `复制输出`
  - `Copy detail` -> `复制详情`
  - `Copy path` -> `复制路径`
- Empty states:
  - `No messages yet` -> `还没有消息`
  - `No commands running` -> `暂无运行中的命令`
  - `No patch loaded` -> `暂无改动`
  - `No important signals` -> `暂无重要诊断`

### 5. Scheduled Tasks

- 页面标题：`定时任务`
- 模块：
  - `任务列表`
  - `执行记录`
  - `最近一次运行`
- Buttons:
  - `New task` -> `新建任务`
  - `Run` -> `立即运行`
  - `Enable / Disable` -> `启用 / 停用`
- Empty state:
  - `No scheduled tasks` -> `暂无定时任务`

### 6. MCP Center

- 页面标题：`MCP 中心`
- 术语：
  - `Server` -> `服务`
  - `Tools` -> `工具`
  - `Refresh tools` -> `刷新工具`
  - `Create server` -> `新增服务`
  - `Transport` -> `传输方式`
- 保留：`stdio`、`HTTP`、命令、参数、环境变量。

### 7. Skills

- 页面标题：`技能`
- 模块：
  - `可用技能`
  - `技能详情`
  - `系统提示词`
  - `允许工具`
  - `自定义预设`
- 内置技能只读文案：
  - `内置技能由运行时提供，当前不可编辑。`

### 8. Settings

- 页面标题：`设置`
- Tabs:
  - `模型供应商`
  - `权限`
  - `通用`
  - `消息桥接`
  - `智能体`
  - `技能`
  - `电脑操作`
  - `关于`
- Provider 表单：
  - `Name` -> `名称`
  - `Endpoint` -> `接口地址`
  - `API format` -> `API 格式`
  - `API key` -> `API 密钥`
  - `Model mapping` -> `模型映射`
  - `Test connection` -> `测试连接`
  - `Save` -> `保存`
- 注意保留 JSON/env var 的原始内容。

### 9. Appearance

- 页面标题：`外观`
- 控件：
  - `Theme` -> `主题`
  - `Density` -> `密度`
  - `Radius` -> `圆角`
  - `Motion` -> `动效`
  - `Accent color` -> `强调色`
  - `Font scale` -> `字体缩放`
  - `Language` -> `语言`

### 10. Component Playground

- 标为内部验证页：
  - `组件预览`
  - `用于检查组件状态，不属于业务工作流。`

## Implementation Order

1. 建立中文文案映射工具：
   - `status` 显示映射。
   - 页面/按钮固定文案。
   - provider/runtime 显示文案。
2. Shell 与全局反馈中文化：
   - AppShell、ComposerDock、toast、runtime unavailable。
3. 核心会话工作台中文化：
   - SessionWorkspace。
   - runtime cards。
   - approval/patch/command/trace 文案。
4. 设置与模型供应商中文化：
   - SettingsWorkspace。
   - provider test result 摘要。
5. MCP、Skills、Scheduled、Appearance 中文化。
6. 测试更新：
   - 先改组件测试断言。
   - 再改 E2E 文本断言。
7. 视觉回归：
   - 运行 1440 桌面、1024 窄桌面、长文本压力页。
   - 修复中文变长导致的按钮换行、标签溢出。

## Verification

- `npm.cmd test`
- `npm.cmd run build`
- `npm.cmd run visual:regression`
- `npm.cmd run e2e:desktop:ui`
- 若 MCP 文案或交互变更：`npm.cmd run e2e:desktop:mcp`
- 若会话恢复/状态文案变更：`npm.cmd run e2e:desktop:recovery`

## Acceptance Criteria

- 默认 UI 页面用户可见文案 90% 以上为简体中文。
- 关键操作按钮、错误、成功反馈全部中文。
- 技术标识不被误译：MCP、API、JSON、URL、env var、模型名、命令、路径保留原样。
- 状态标签统一中文，不再混用 `running`、`pending`、`completed` 等英文裸状态。
- 中文文案不造成横向溢出、按钮裁切或卡片互相遮挡。
- 浏览器预览/本地预览仍保留 fallback，但用户界面不再出现 “mock” 作为产品文案。

