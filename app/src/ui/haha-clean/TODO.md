# haha-clean 能力接入记录

详细到按钮/事件/后端对接的差异表见 [FRONTEND_PARITY_AUDIT.md](./FRONTEND_PARITY_AUDIT.md)。

## 已接入

- 全局 Shell、左侧会话导航、标签栏、底部输入框。
- 新建会话空态、项目目录/模型/权限/上下文入口。
- 会话流：用户消息、助手正文、思考块、工具块、命令、审批、文件改动、worklog 折叠、置底。
- 普通消息操作栏：用户/助手消息支持复制、引用、更多；更多菜单已补“复制为 Markdown / 复制消息 ID / 从这里继续 / 从这里分支 / 删除消息”，其中分支、继续、删除先以明确禁用入口等待后端 transcript mutation/branch 接口。
- haha-cc 式低卡片消息流：过程说明、工具行、文件改动、上下文压缩/Goal/Memory/API retry 等事件均有前端渲染入口。
- transcript adapter：已接入 `content_start` 的 text/tool 占位、`content_delta` 工具输入增量、`tool_use_complete`/`tool_result` 的 `parentToolUseId` 保存。
- 特殊事件适配：`api_retry`、`system_notification`、`compact_summary`、`goal_event`、`memory_event`、`ask_user_question`、`computer_use_permission_request`、`computer_use_permission` 可直接进入消息流。
- `ask_user_question` 和 `computer_use_permission` 已有专用轻量节点，不再混进普通系统消息；目前先展示问题/选项、权限详情、复制操作，以及等待后端的回答/授权按钮占位。
- 工具摘要清洗：目录/文件/Git/搜索类输出不再直接展示原始 JSON，优先显示可读短摘要；主聊天里的工具消息也会把 `{status, exitCode, stdout}`、`items/files/matches` 等结果转成单行中文摘要。
- 工具详情折叠：主聊天工具消息展开后使用轻量“工具详情”面板，并支持复制完整输入/输出。
- runtime 详情的复制标签按 Shell 输出/工具详情区分，文件行状态统一成中文修改/新增/删除。
- 工具/审批标题清洗：常见 `read_file`、`apply_patch`、`write_file`、`run_command` 会显示为“读取/修改/写入/运行 + 目标”，减少内部工具名暴露。
- runtime `approval` 已拆成专用审批节点，允许一次/拒绝/始终允许占位、文件列表和详情折叠不再混在通用工具卡里；泛化的 `patch approval request` 文案会被过滤。
- worklog 展开后使用紧凑工具行，低价值 read/list/git/search/status 记录不会再膨胀成大卡片，单行仍可展开复制详情，工具组也可复制一份简洁执行摘要。
- worklog 已能读取 `parentToolUseId` 并渲染轻量父子缩进，先支持工具树的前端形态。
- 低价值 read/list/git/search/状态探针会压进 worklog，失败、审批、写入、diff 保留在主线，减少“全屏都是工具调用”的噪声。
- clean 会话会隐藏已被 runtime/worklog 承接的低价值 inline 工具消息，避免同一次 read/list/git/search 同时在主线出现两遍；失败、运行中、写入和审批仍保留在主线。
- patch 文件行现在会优先打开本地匹配 diff，并只展示所选文件的差异；没有本地 diff 时再走后端 `onLoadPatch`；改动卡补了复制文件列表和“撤销本轮”的禁用占位入口。
- 改动卡补了“审查改动”入口：先把审查提示和改动文件写入 composer，独立审查/提交接口后续再接。
- 文件区 clean 样式：右侧文件树、预览区、分隔条和搜索框已脱离旧 session CSS 的重卡片样式。
- 代码阅览补了轻量语法高亮，常见关键词、字符串、注释、数字会先上色；Markdown 文件继续走预览渲染。
- 文件区更多菜单补了“在编辑器中打开”的前端入口，当前先通过可注入回调等待宿主/后端接入。
- Composer 补了项目目录/上下文轻量详情、权限模式说明和 Slash 参数提示。
- Composer 项目目录弹层补了复制路径动作。
- Composer 项目目录弹层补了当前改动数和最近改动文件展示。
- Composer 上下文弹层补了复制上下文摘要动作。
- Composer 图片附件改为缩略图显示，普通文件继续保持轻量 chip。
- Markdown 文件补了预览/源码切换，源码模式保留行号和轻量高亮。
- Slash 命令补了键盘选择：上下键/Home/End 切换，Enter/Tab 选中，Escape 关闭。
- Markdown 预览补了轻量文档大纲，方便快速跳到章节。
- 聊天正文 Markdown 补了宽松标题、嵌套列表、任务列表和表格渲染，减少模型输出里 `##1`、表格、todo 直接裸露或层级打平的情况。
- Markdown 代码块复制按钮带语言名，可在多代码块消息里明确区分。
- 聊天正文 Markdown 补了安全图片块渲染，截图/本地图片链接不会再裸露成普通文本。
- Composer 补了 `@` 文件引用入口：加号菜单可插入 `@`，输入后能从当前已知文件候选里选择并插入引用。
- 权限菜单补了“完全访问权限”二次确认，避免误点直接切到危险权限模式。
- 现有后端数据适配：messages、toolCalls、approvals、patches、backgroundJobs、traces、activeTask、contextPreview。
- clean transcript schema：已覆盖 user_text、assistant_text、assistant_progress、thinking、tool_use、tool_result、tool_group、permission_request、computer_use_permission、ask_user_question、background_task、task_summary、plan_update、goal_event、memory_event、compact_summary、api_retry、error、change_set、command、status、system。

## 需要后端补字段

- 真实 token 级 thinking/assistant delta 分段，而不是只在最终消息里得到大段总结；前端已能消费分段事件。
- 稳定的工具 parent/child 树、每个工具的结构化 input/output summary；前端已保存 `parentToolUseId` 并支持 worklog 缩进展示，但仍需要后端稳定提供 parent id、阶段说明和结构化摘要。
- 工具事件时间戳需要更稳定，否则前端只能尽量按 messages/runtime 的已有时间推断插入顺序。
- 审批请求需要稳定提供受影响文件和 diff 字段；当前前端会尽量从 code/rawDetail 里推断文件列表。
- 分支/撤销/真正绑定上下文的消息引用需要稳定 transcript target id；当前文本引用已能写入 composer。
- 项目 git 分支、worktree、上下文快照分类明细。
- 文件/图片引用需要持久化记录和后端读取接口；当前 `@` 候选先来自 `worktreeStatus.files`，还不是全项目文件搜索。
- Computer Use 权限请求、ask_user_question、goal_event、memory_event、compact_summary、api_retry 这些事件前端已能渲染，但后端还需要稳定 emit、真实授权/回答提交接口和补交互字段。

## 暂时占位

- 代码阅览完整语言服务级语法高亮、Markdown 源码/预览滚动同步和 diff/源码联动。
- 聊天正文 Mermaid、复杂图片画廊、代码块更完整语法高亮。
- 全项目文件搜索、`@` 引用持久化、slash command 详情/结果面板。
- Computer Use 权限弹窗。
- MCP/Skills/Settings 页面仍先复用旧业务组件，已加 clean shell 样式覆盖，后续可迁移到独立 clean 组件。
