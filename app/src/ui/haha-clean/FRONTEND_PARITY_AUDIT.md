# haha-cc 前端能力对照审计

本表按 haha-cc 的前端能力拆分到按钮、信息流节点和后端事件。状态含义：

- `已接入`：当前 clean 前端已有可用实现，并已接到现有数据。
- `部分接入`：前端入口或视觉形态已做，但行为、细节或数据仍不完整。
- `前端待补`：现有后端数据够用，主要缺前端实现。
- `后端待补`：前端可预留，但缺事件、字段或接口。
- `暂不做`：不是当前 Yuanbao Agent 主线。

## 本轮新增适配

- 前端事件订阅现在会消费 `content_start`：`tool_use` 会先生成可见的工具占位行，`text` 会生成“正在输出回复”的流式提示，避免工具输入到齐前页面完全没反应。
- `content_delta`、`tool_use_complete`、`tool_result` 已透传并保存 `parentToolUseId`，为后续工具树/父子折叠做准备。
- 新增 haha-cc 风格特殊事件适配：`api_retry`、`system_notification`、`compact_summary`、`goal_event`、`memory_event`、`ask_user_question`、`computer_use_permission_request`、`computer_use_permission`。后端即使暂时只补部分事件，前端也能先渲染为低卡片信息流。
- 普通用户/助手消息已补轻量操作栏：复制、引用、更多。引用会优先写入底部 composer；没有 composer 桥接时退回为复制引用文本。
- `ask_user_question` 和 `computer_use_permission` 已从普通系统行拆成专用信息节点，先展示问题/选项、应用/权限详情；真正提交回答和权限弹窗仍等后端协议补齐。
- 低价值 read/list/git/search/状态探针会继续压进 worklog，不再把主聊天刷成一串工具日志；失败、审批、写入、diff 仍保留为主线节点。
- 右侧文件阅览补了轻量语法高亮，代码关键词、字符串、注释和数字会先按常见语言上色，Markdown 仍保留渲染预览。
- Composer 底部项目目录、上下文、权限不再只是静态按钮：项目/上下文可展开轻量详情，权限选项会显示模式说明，Slash 面板补充参数提示。
- Markdown 文件阅览补了“预览/源码”切换，源码模式复用右侧代码阅览的行号与轻量高亮。
- Slash 命令面板补了键盘选择：上下键/Home/End 切换选项，Enter/Tab 选中，Escape 关闭当前建议。
- Markdown 预览补了轻量文档大纲，能从右侧大纲快速跳到标题。
- Composer 补了 `@` 文件引用入口：加号菜单可插入 `@`，输入 `@` 后能从当前已知文件候选中键盘选择并插入引用。
- “完全访问权限”现在会先二次确认，不再一点击危险权限就直接切换。
- 工具/审批标题补了业务化动作名：`read_file`、`apply_patch`、`run_command` 等会优先显示为“读取 xxx”“修改 xxx”“运行 xxx”，不再把内部工具名当主标题。
- 主聊天工具消息补了结构化结果摘要：`status/exitCode/stdout` 会显示成“已完成 · 退出码 0 · …”，`items/files/matches` 会显示成“找到/涉及文件 N 项”，展开后才看完整输入输出，并支持复制详情。
- runtime `approval` 已从通用工具卡拆成专用审批节点：标题、文件列表、批准/拒绝和详情折叠更靠近 haha-cc 的轻量请求块。
- worklog 展开后改为专用紧凑工具行：读文件、查目录、Git 状态这类低价值步骤不再展开成大卡片，单行仍可继续打开详情和复制。
- worklog 已经贯通 `toolUseId/parentToolUseId` 到 runtime 渲染层，展开后能按父子工具缩进展示，先补齐 haha-cc 信息流里的工具树基础形态。
- 聊天正文 Markdown 渲染补了宽松标题、任务列表和表格，模型输出里的 `##1`、todo、表格不再直接按普通文本裸露。
- 这层是 transcript adapter：能力不足时先把可识别事件接进统一消息流，无法由现有后端真实提供的能力继续记录为后端待补。

## 1. 应用壳层与导航

| haha-cc 功能点 | haha-cc 行为/按钮 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 顶部/侧边导航 | 会话列表、设置、项目入口、运行状态 | `CleanAppShell` 已提供侧边会话、标签栏、新建会话、设置、MCP、Skills | 使用现有 session/tab/store | `部分接入`：样式已转 clean，但侧栏信息密度和状态分组还没完全按 haha-cc 打磨 |
| 新建会话标签 | 点击新建会话进入空态页 | 已接入 `new-session` 标签 | 复用现有创建会话接口 | `部分接入`：空态 UI 已简化，项目/分支/工作树选择还不完整 |
| 设置入口 | 设置页里管理模型、权限、MCP、Skills 等 | 设置入口已加回，MCP/Skills 独立入口已暴露 | 复用旧设置/MCP/Skills 后端 | `部分接入`：页面内部仍是旧组件加 clean 覆盖，后续要重做成低卡片列表 |
| 总览页 | haha-cc 没有重型“总览”作为主路径 | 我们已淡化总览，主路径切到会话/新建会话 | 无新增后端需求 | `已接入` |
| 标签关闭按钮 | 每个标签有 `x` 关闭 | 已接入 | 复用 tabModel | `已接入` |
| 活动会话状态 | 当前会话运行、等待审批、完成等状态 | 已有浮动状态和会话列表状态点 | activeTask/session status | `部分接入`：状态文案仍需和消息流的真实阶段统一 |

## 2. 新建会话页

| haha-cc 功能点 | haha-cc 行为/按钮 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 简洁空态 | logo、标题、新会话说明、底部 composer | `CleanNewSessionWorkspace` 已做简洁空态 | 无 | `部分接入`：视觉方向接近，但 logo/品牌图形仍是占位 |
| 项目目录按钮 | 底部显示当前目录，可展开/切换 | composer 下方显示 cwd/context，项目 chip 可展开当前目录详情 | workspace root store | `部分接入`：没有完整目录选择弹层、最近项目、分支信息 |
| 模型选择 | 模型按钮/下拉 | `CleanComposer` 支持模型下拉 | modelOptions/selectedModelId | `已接入` |
| 权限模式 | 跳过/询问/自动接受等权限模式 | `CleanComposer` 有权限下拉，高风险“完全访问权限”会二次确认 | permissionMode callback | `部分接入`：确认已补，仍缺全局默认权限策略和持久化规则 |
| 上下文按钮 | 上下文占用按钮，弹出详细 breakdown | 显示上下文百分比/标签，已补 token 预算、工具数、当前步骤、压缩节段轻量面板 | contextPreview | `部分接入`：还缺更完整的分类 token breakdown 和持久快照 |
| 加号菜单 | 添加文件/图片、斜杠命令 | 加号菜单有“添加文件或图片”“斜杠命令” | file dialog/输入框本地状态 | `部分接入`：缺拖拽覆盖层、真实图片预览、文件引用持久化 |
| 运行按钮 | 发送/运行，运行中切停止 | 已接入发送、停止 | submit/stop callbacks | `已接入` |

## 3. Composer 输入框与按钮

| haha-cc 功能点 | haha-cc 行为/按钮 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 多行输入框 | 大输入区，底部工具栏，宽度贴近聊天区 | clean composer 已全局接入 session/new-session | prompt state | `部分接入`：用户仍反馈会话页尺寸/贴边不稳定，后续要以 shell 容器统一约束宽度 |
| `+` 按钮 | 打开工具菜单 | 已接入 | 前端状态 | `已接入` |
| 添加文件或图片 | 文件选择、图片缩略图、移除 | 已有附件列表和移除按钮 | attachments array | `部分接入`：图片预览/文件读取后端能力未完全对齐 |
| 斜杠命令 | `/` 面板、命令描述、键盘选择 | 已有 `/` 自动补全、命令描述、参数提示和键盘选择 | slash 命令 registry | `部分接入`：后端扩展命令和命令结果面板还没完整 |
| `@` 文件搜索 | 输入 `@` 搜索项目文件 | Composer 已有 `@` 面板、加号菜单入口和键盘选择，候选先来自 `worktreeStatus.files` | worktreeStatus.files / 后续文件搜索接口 | `部分接入`：当前不是全项目搜索，后端需补 workspace-wide fuzzy search 和文件引用持久化 |
| 权限按钮 | 下拉权限模式，危险模式二次确认 | 有权限下拉、每种模式说明和“完全访问权限”确认 | permissionMode | `部分接入`：确认已补，设置页默认权限和规则持久化还需后端/前端继续补 |
| 上下文按钮 | 百分比/状态，点击看详情 | 有百分比短显示和轻量详情浮层 | contextPreview | `部分接入`：缺完整分类视图 |
| 模型按钮 | provider/model 下拉 | 已接入 | modelOptions | `已接入` |
| 停止按钮 | 运行中停止生成 | 已接入 | stopPrompt / task cancel | `部分接入`：后端需避免 terminal task 重复 cancel 报错 |
| 暂存/排队 | 运行中把下一条加入队列，可引导/调整顺序/删除 | 已有队列项、引导、上移、下移、删除 | queuedPrompts callbacks | `部分接入`：交互已存在，视觉还需更像 haha-cc 的轻量 pending bar |
| 引导按钮 | 把暂存内容注入当前会话上下文 | 已有 `onGuideQueuedPrompt` | 依赖现有队列实现 | `部分接入`：需要后端明确“引导注入”事件，避免只是本地队列状态 |
| 发送按钮 | 不可发送时 disabled，运行中 stop | 已接入 | submit/stop | `已接入` |
| 项目目录 chip | 显示当前 repo/目录/分支 | 目前显示目录与上下文 | workspace root/context | `部分接入`：分支、工作树、dirty 状态还没统一展示 |

## 4. 主聊天信息流

| haha-cc 信息节点 | haha-cc 行为 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 用户消息 | 右侧简洁气泡，可复制/引用/分支 | 用户消息已渲染，带复制/引用/更多操作 | messages.role=user | `部分接入`：复制/引用已接入，分支/撤回仍需稳定 transcript target id |
| 助手正文 | 普通文本/Markdown，插在工具调用之间 | 助手正文已渲染，带复制/引用/更多操作，`content_start(text)` 可显示流式提示 | messages.role=assistant / content_start | `部分接入`：前端可接分段事件，但后端目前仍常把最终总结集中到一个消息，缺真正分段 delta |
| 模型思考 | thinking 块，随流式更新 | 有 thinking/progress 入口 | metadata/status 推断 | `后端待补`：缺真实 token 级 thinking delta |
| 过程说明 | 短句插在工具/命令前后 | `assistant_progress` 已预留和渲染 | 依赖 metadata.kind | `后端待补`：后端需要输出阶段性自然语言，不要只输出工具日志 |
| 工具调用行 | 单行可折叠，显示工具名、目标、状态 | runtime/tool 行已低卡片化，`content_start(tool_use)` 可先显示占位；工具标题会优先转成“读取/修改/运行 + 目标” | runtime items/toolCalls/content_start | `部分接入`：前端已清洗常见工具标题，后端仍需提供结构化 summary 避免前端猜 JSON |
| 工具结果 | 和调用合并/紧跟，错误高亮 | 有结果/输出折叠和 copy，并保存 `parentToolUseId`；主聊天工具消息会把常见 JSON 结果转成单行摘要，展开后看完整输入输出 | runtime output/tool result | `部分接入`：父子 id 已能保存并在 worklog 里缩进展示，但仍需要后端稳定树结构和更完整工具摘要 |
| 工具组 | 连续工具折叠为“执行了 N 条命令” | worklog 折叠已做，展开后使用紧凑工具行和父子缩进；普通 read/list/git/search/状态探针继续压缩 | activity worklog | `部分接入`：视觉已更轻，后续还要接真实阶段解释正文和更完整的父子折叠 |
| 权限请求 | 内嵌审批卡，带 diff/命令预览 | runtime approval 已拆成专用轻量节点，常见审批会显示“修改/写入/运行 + 目标”，并保留批准/拒绝 | approvals/permission_request | `部分接入`：审批节点已拆出，permission request 内完整 diff/规则类永久批准还未补 |
| 文件改动卡 | 当前轮改动 summary、查看 diff、撤销 | patch card + diff preview 已接入 | patches/changedFiles | `部分接入`：当前没有 turn 级撤销，diff 文件匹配仍需加强 |
| 任务摘要 | 完成后显示总结，不在刚开始出现 | 已隐藏运行初期 task summary | activeTask | `部分接入`：结束时机和内容质量依赖后端 |
| 上下文压缩 | “上下文已自动压缩”分割节点 | `compact_summary` 已进入 transcript adapter | 等后端真实事件 | `部分接入`：前端已接，后端待稳定 emit |
| Goal/Memory 事件 | 轻量系统节点 | `goal_event`/`memory_event` 已进入 transcript adapter | 等后端真实事件 | `部分接入`：前端已接，后端待稳定 emit |
| API retry | 重试提示 | `api_retry` 已进入 transcript adapter | 等后端真实事件 | `部分接入`：前端已接，后端待稳定 emit |
| 错误节点 | 失败工具/请求明显但不巨大 | 已有错误态 | failed trace/runtime/message | `部分接入`：需要统一错误摘要和展开详情 |
| 置底按钮 | 用户离开底部时显示向下按钮 | clean session 已有置底按钮 | 前端滚动状态 | `部分接入`：和 composer/右侧分隔布局还需联动打磨 |
| 自动滚动 | 流式时跟随底部，用户滚动时暂停 | 有基本实现 | 前端状态 | `部分接入`：没有 haha-cc 的虚拟列表和滚动快照 |

## 5. 消息操作栏

| haha-cc 按钮 | haha-cc 行为 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 复制 | 复制消息/工具输出 | runtime 输出和普通消息正文均已接入复制 | Clipboard | `已接入` |
| 引用 | 把该消息作为后续输入引用 | 普通消息可引用到 composer；没有桥接时复制 Markdown 引用 | composer prompt / transcript id | `部分接入`：文本引用已可用，真正绑定某条 transcript 的上下文引用仍需后端 id |
| 删除/撤回 | 删除本地消息或撤销当前轮 | 未完整接入 | 需要 session transcript mutation | `后端待补` |
| 更多 `...` | 展开更多操作 | 普通消息已有轻量菜单，支持复制消息 ID，分支入口置灰 | 需要动作定义 | `部分接入`：菜单形态已接入，分支/撤销等动作还缺后端 |
| 从这里分支 | 基于某条消息创建分支会话 | 未接入 clean 流 | 需要 branchSession/transcript id | `后端待补 + 前端待补` |
| 撤销本轮改动 | 当前轮 change card 撤销 | 未接入 | 需要后端 revert turn | `后端待补` |

## 6. 工具/命令/审批渲染

| haha-cc 功能点 | haha-cc 行为/按钮 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 工具折叠按钮 | 行首 chevron 展开输入/输出 | 已接入；worklog 内用更轻的单行展开 | runtime expanded state | `已接入` |
| 工具状态 badge | running/completed/failed/skipped | 已接入 | item.status | `部分接入`：状态文案需统一成更少、更稳的集合 |
| 工具目标摘要 | `read_file path`、`run_command cmd` | 已清洗 read/list/git/search/run/write/apply_patch 的标题和常见 JSON 输出，主聊天工具行支持 status/exitCode/stdout/items/files/matches 摘要 | runtime input/output | `部分接入`：仍需要后端提供稳定结构化 summary，减少前端猜测 |
| 复制输出 | 复制工具输出 | 已接入 | Clipboard | `已接入` |
| 刷新命令 | 查看最新命令输出 | 已接入 command refresh | command job id | `已接入` |
| 停止命令 | 停止运行中的命令 | 已接入 | command job id | `部分接入`：后端 terminal state 需要阻止 cancelled -> cancelled |
| 审批批准 | approve | 已接入，并在专用审批节点内展示 | approvals API | `已接入` |
| 审批拒绝 | reject | 已接入，并在专用审批节点内展示 | approvals API | `已接入` |
| 永久批准/规则 | haha-cc 有 always/规则类操作 | 未接入 | 需要 permission rule 后端 | `后端待补` |
| 审批 diff 预览 | write/edit/apply_patch 展示 diff | 审批节点可展示文件列表和详情折叠，已有 diff 时复用 diff preview | approvals/patches | `部分接入`：真实 permission request diff 字段还需要后端稳定提供 |
| Computer Use 权限 | 专用弹窗，选择 app/权限项 | 已有专用低卡片节点，可展示 app/action/details 并复制详情 | transcript adapter | `部分接入`：前端展示已接，真实权限弹窗和授权提交仍需后端 |
| AskUserQuestion | 工具向用户提问，有选项/输入 | 已有专用问题节点，可展示问题/选项并复制问题 | transcript adapter | `部分接入`：前端展示已接，交互式回答提交仍需后端 |

## 7. 文件改动与 diff

| haha-cc 功能点 | haha-cc 行为/按钮 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 当前轮改动卡 | 显示文件数、增删、每文件行 | 已有 patch card | patches/changedFiles | `部分接入`：标题和文件匹配已修一轮，仍需更多 patch 格式兼容 |
| 查看文件差异 | 点按钮打开 diff | 有“查看文件差异”按钮和 preview | loadPatch | `部分接入`：右侧 diff/源文件联动还不够像 haha-cc |
| 文件行点击 | 点击文件打开右侧预览 | 已接入 openFile | FileWorkspacePanel | `部分接入` |
| diff 语法色 | 增删颜色、hunk header、行号 | 已有基本 diff preview | patch text | `部分接入`：还不是真正完整 diff viewer |
| 撤销本轮 | change card 上撤销 | 未接入 | 需要 revert API | `后端待补` |
| 审核/提交入口 | 在当前改动上进入审查 | 旧 app 有审查相关，clean 未完整统一 | existing review route | `前端待补` |

## 8. 代码阅览与文件区

| haha-cc 功能点 | haha-cc 行为/按钮 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 文件树 | 搜索、目录折叠、打开文件 | 复用 `FileWorkspacePanel` 并加 clean CSS | workspace file APIs | `部分接入`：样式接近，交互和图标还需精简 |
| 分隔条 | 拖拽左右宽度 | clean session 有 paneWidth 分隔 | 前端状态 | `已接入` |
| 代码阅览 | 行号、语法高亮、横向滚动 | 已有行号、横向滚动和轻量关键词/字符串/注释/数字高亮 | file content | `部分接入`：还不是完整语言服务级高亮，后续可复用编辑器能力 |
| Markdown 阅览 | md 渲染预览/源码切换 | 已有 Markdown 预览、源码切换和轻量文档大纲 | file content | `部分接入`：源码/预览滚动同步还没做 |
| 文件搜索框 | 筛选文件 | 复用旧 file panel | workspace files | `已接入` |
| 更多菜单 | 复制路径、自动换行、在编辑器打开 | 旧 panel 部分有，clean 样式覆盖 | existing actions | `部分接入`：菜单项和按钮位置需统一 |
| 在编辑器打开 | 打开外部编辑器 | 旧 panel 部分能力 | shell open API | `部分接入` |

## 9. Markdown/正文渲染

| haha-cc 能力 | haha-cc 行为 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 标题 | `#` 变标题，不显示原始 `##` | `CleanMarkdown` 已处理标题 | assistant content | `已接入` |
| 列表 | 有序/无序列表缩进自然 | 已处理基础列表和任务列表 | assistant content | `部分接入`：嵌套列表仍简单 |
| inline code | 背景 chip | 已接入 | assistant content | `已接入` |
| fenced code | 代码块、语言、复制、高亮 | 有代码块、语言栏、复制按钮和简单高亮 | assistant content | `部分接入`：完整语法高亮待补 |
| 表格 | Markdown 表格转表格 UI | 已接入简单表格渲染 | assistant content | `部分接入`：复杂对齐/嵌套内容待补 |
| Mermaid | 图表渲染 | 未接入 | assistant content | `前端待补` |
| 图片内联 | 用户/助手图片画廊 | 附件有基础列表 | attachments/content refs | `后端待补 + 前端待补` |

## 10. 设置 / MCP / Skills

| haha-cc 功能点 | haha-cc 行为/按钮 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 设置总页 | 模型、权限、通知、外观、适配器 | 设置页已保留 | existing settings | `部分接入`：视觉仍旧，需要拆成简洁分组列表 |
| 模型供应商 | 添加/编辑 provider、模型 | 旧设置能力保留 | settings store/API | `部分接入` |
| 权限设置 | 默认权限模式、危险模式确认 | composer 有当前权限，设置页保留 | settings store | `部分接入` |
| MCP 管理 | MCP 服务器列表、启停、配置 | 入口已加回，旧页面复用 | existing MCP APIs | `部分接入`：需 clean 化列表和详情页 |
| Skills 管理 | skills 列表、启用/禁用、详情 | 入口已加回，旧页面复用 | existing skills APIs | `部分接入`：需 clean 化 |
| 外观设置 | 主题/密度 | 旧设置保留 | settings | `部分接入` |

## 11. 后端事件/协议差异

| haha-cc ServerMessage | 用途 | 我们当前来源 | 状态 |
| --- | --- | --- | --- |
| `connected` | websocket/session ready | app connection state | `部分接入` |
| `content_start` | 开始 text/tool_use，并给 id | chat-compat 事件 / provider turn | `部分接入`：前端已渲染 text/tool 占位，后端需保证真实顺序和 id 稳定 |
| `content_delta` | token/工具输入流式增量 | chat-compat 事件 / assistant token | `部分接入`：正文/toolInput 已能进流，真实 thinking delta 仍待补 |
| `tool_use_complete` | 工具输入完整、parentToolUseId | toolCalls/runtime/chat-compat | `部分接入`：前端已保存 parentToolUseId，工具树 UI 待补 |
| `tool_result` | 工具结果 | runtime output/tool result | `部分接入`：前端已保存 parentToolUseId，并能清洗常见 JSON 结果；仍需后端提供稳定结构化 summary |
| `permission_request` | 审批请求 | approvals | `已接入` |
| `computer_use_permission_request` | computer use 授权 | transcript adapter | `部分接入`：前端已能显示，后端能力/弹窗详情待补 |
| `message_complete` | 本轮消息结束 | task/session status 推断 | `后端待补` |
| `thinking` | 思考内容 | status/thinking metadata 推断 | `后端待补` |
| `status` | 当前运行状态 | traces/activeTask | `部分接入` |
| `api_retry` | API 重试提示 | transcript adapter | `部分接入`：前端已能显示，后端待 emit |
| `error` | 错误 | failed runtime/message | `部分接入` |
| `system_notification` | 系统通知 | traces/system/transcript adapter | `部分接入` |
| `compact_summary` | 上下文压缩节点 | transcript adapter | `部分接入`：前端已能显示，后端待 emit |
| `goal_event` / `memory_event` | 目标/记忆节点 | transcript adapter | `部分接入`：前端已能显示，后端待 emit |
| `ask_user_question` | 工具向用户提问 | transcript adapter | `部分接入`：前端已能显示，交互式回答后端待补 |
| `task_update` | 子任务/团队任务状态 | activeTask/backgroundJobs | `部分接入` |
| `session_title_updated` | 自动标题 | session title | `部分接入` |

## 12. 当前优先整改顺序

1. `后端事件流`：让 assistant 正文/thinking/tool/status 按时间进入 transcript，而不是最后汇成一大段。前端已加 adapter，可先吃部分 haha-cc 风格事件。
2. `工具摘要`：后端给 read/list/git/search/run/write/apply_patch 的结构化 summary、target、parentToolUseId；前端已保存 parentToolUseId 并能在 worklog 做父子缩进。
3. `Composer`：项目/上下文/权限详情、Slash 参数提示和键盘选择已补一层；下一步固定会话页宽度与右侧分隔区关系，补 `@文件`、权限危险确认。
4. `消息操作栏`：复制、引用、更多已接入；下一步补分支、删除/撤回、撤销当前轮所需的后端 target id 和 mutation。
5. `Diff/File Viewer`：右侧文件区已补轻量高亮、Markdown 预览/源码切换和文档大纲；下一步补完整 diff viewer、源码/预览滚动同步、右侧 diff/源码联动。
6. `Settings/MCP/Skills`：保留能力但重做成 haha-cc 式低卡片列表。

## 13. 当前结论

这次 clean 前端已经把主聊天、composer、文件区、权限、diff、worklog 和设置入口接回来了，并新增了一层 transcript adapter：能接 `content_start/content_delta/tool_use_complete/tool_result`，也能预先渲染 `api_retry/compact_summary/goal_event/memory_event/ask_user_question/computer_use_permission` 等 haha-cc 风格事件。普通消息的复制/引用/更多也已接入，引用能写回 composer。它还不是完整 haha-cc parity。最大差异不是单个样式按钮，而是后端 transcript 粒度：haha-cc 的前端依赖细粒度事件，所以能自然呈现“思考 -> 工具 -> 解释 -> 再工具 -> 最终结论”。我们当前还有不少内容是从最终 messages、runtime 和 task 状态反推，因此仍要继续补真实事件流、结构化工具摘要和工具树 UI。
