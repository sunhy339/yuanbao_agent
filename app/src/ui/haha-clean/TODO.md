# haha-clean 能力接入记录

详细到按钮/事件/后端对接的差异表见 [FRONTEND_PARITY_AUDIT.md](./FRONTEND_PARITY_AUDIT.md)。

## 已接入

- 全局 Shell、左侧会话导航、标签栏、底部输入框。
- 新建会话空态、项目目录/模型/权限/上下文入口。
- 会话流：用户消息、助手正文、思考块、工具块、命令、审批、文件改动、worklog 折叠、置底。
- haha-cc 式低卡片消息流：过程说明、工具行、文件改动、上下文压缩/Goal/Memory/API retry 等事件均有前端渲染入口。
- transcript adapter：已接入 `content_start` 的 text/tool 占位、`content_delta` 工具输入增量、`tool_use_complete`/`tool_result` 的 `parentToolUseId` 保存。
- 特殊事件适配：`api_retry`、`system_notification`、`compact_summary`、`goal_event`、`memory_event`、`ask_user_question`、`computer_use_permission_request`、`computer_use_permission` 可直接进入消息流。
- 工具摘要清洗：目录/文件/Git/搜索类输出不再直接展示原始 JSON，优先显示可读短摘要。
- 文件区 clean 样式：右侧文件树、预览区、分隔条和搜索框已脱离旧 session CSS 的重卡片样式。
- 现有后端数据适配：messages、toolCalls、approvals、patches、backgroundJobs、traces、activeTask、contextPreview。
- clean transcript schema：已覆盖 user_text、assistant_text、assistant_progress、thinking、tool_use、tool_result、tool_group、permission_request、computer_use_permission、ask_user_question、background_task、task_summary、plan_update、goal_event、memory_event、compact_summary、api_retry、error、change_set、command、status、system。

## 需要后端补字段

- 真实 token 级 thinking/assistant delta 分段，而不是只在最终消息里得到大段总结；前端已能消费分段事件。
- 稳定的工具 parent/child 树、每个工具的结构化 input/output summary；前端已保存 `parentToolUseId`，但还没有完整树形 UI。
- 工具事件时间戳需要更稳定，否则前端只能尽量按 messages/runtime 的已有时间推断插入顺序。
- 分支/撤销/引用消息需要稳定 transcript target id。
- 项目 git 分支、worktree、上下文快照分类明细。
- 文件/图片引用需要持久化记录和后端读取接口。
- Computer Use 权限请求、ask_user_question、goal_event、memory_event、compact_summary、api_retry 这些事件前端已能渲染，但后端还需要稳定 emit 和补交互字段。

## 暂时占位

- 代码阅览完整语法高亮和 Markdown 专用预览。
- 文件搜索、@文件引用、slash command 详情面板。
- Computer Use 权限弹窗。
- MCP/Skills/Settings 页面仍先复用旧业务组件，已加 clean shell 样式覆盖，后续可迁移到独立 clean 组件。
