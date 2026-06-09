# haha-cc 前端能力对照审计

本表按 haha-cc 的前端能力拆分到按钮、信息流节点和后端事件。状态含义：

- `已接入`：当前 clean 前端已有可用实现，并已接到现有数据。
- `部分接入`：前端入口或视觉形态已做，但行为、细节或数据仍不完整。
- `前端待补`：现有后端数据够用，主要缺前端实现。
- `后端待补`：前端可预留，但缺事件、字段或接口。
- `暂不做`：不是当前 Yuanbao Agent 主线。

## 本轮新增适配

- 会话页按 haha-cc 的“聊天主线 + 按需文件阅读器”重新核对：主线继续隐藏 synthetic active-task 文件摘要、启动期 `assistant_progress/task_summary/status` 噪声和已完成的上下文读取/搜索/Git inline 工具；失败、阻塞、审批、写入、验证命令仍保持可见。
- 聊天框与输入框关系继续收口：助手正文保留段落/列表间距，composer 去掉无效说明按钮和占位文案；右侧文件区打开时宽屏会给 transcript/composer 让出空间，窄屏改为覆盖，不再制造底部横向滚动条。
- 文件展示继续贴近 haha-cc：右侧文件区默认关闭，打开后以代码/Markdown 阅读器为视觉中心，目录、搜索和本轮改动压成窄侧栏；语法高亮、Markdown 预览/源码、全局找文件、`path:line` 高亮和外部编辑器打开保持可用。
- Diff 展示继续降噪：diff 预览减小圆角、padding、行高和最大高度，多文件 tabs、增删统计、新行号联动右侧源码仍保留，避免改动卡在主聊天里撑成大块旧面板。
- 工具链新增 `toolOperationId/toolOperationLabel`：后端会按搜索、路径读取、文件改动、验证、Git、Web/Browser、Notebook、Memory/Scratchpad、Computer Use 等生成稳定操作链 id，并让同批/跨轮推断出的子工具继承父操作链；显式 `parentToolUseId` 也会继承同批/跨轮父 operation，直接父级缺 operation 时会向祖先追溯。事件、chat message metadata、runtime tool calls 和 haha-clean worklog 都会保留该字段，展开 worklog 会优先按 operation 分段，再按父子树缩进，避免一次文件改动被回读、验证和 Git 复核拆成多个孤立阶段。
- 更多探针工具也开始有真实运行中进度：`read_file/list_dir/search_files/code_search/git_status/git_diff` 在 executor 开始时会发 `tool.progress(activity)`，并继续通过 `tool.output(result_preview)` 写回结构化结果预览；后台命令事件也会透传 operation metadata，stdout/stderr、开始、完成、取消都能合回同一操作链。
- MCP/自定义/未知工具补上通用执行开始流：MCP 会进入独立 `mcp` 阶段，并按 `query/url/path/target/id/key/action` 等稳定 hint 生成 `mcp:<server>:<tool>:<hint>` operation；自定义工具会按 `tool:<name>:<hint>` 生成 operation。没有 `query` 的 custom URL fetch、MCP path lookup 也能进入同一操作链，执行开始文案会优先显示 URL/path/id/key/action 等真实目标；MCP/custom 返回的 `items/results/matches` 里如果带路径或 URL，后续同路径 `read_file` 或同 URL `browser` 会保守挂回它并继承 operation。
- 同批工具会在执行到后续工具前使用已完成的前序结果二次补 parent：同一轮里先 MCP/custom 查询、再立刻打开结果列表里的路径或 URL，也能形成父子链并继承 operation；如果父工具调用参数很空、只有结果里的 `items/results/matches/sources/references` 才带 path/URL，也会在结果落地后补出稳定 operation 供后续工具继承；通用过程流兼容 `messages/diagnostics/stages/tasks`，并把 `ok/error/code/count/durationMs` 等常见字段整理成 `tool.progress(activity)`。
- `computer_use` 执行层从纯占位推进到基础 executor：`inspect` 审批后会返回运行环境信息和结构化 steps，`screenshot` 会尝试用本机 `ImageGrab` 截取并返回 1280px 内预览数据；`click/type/key/scroll` 已有可插拔 executor 接口，executor 可声明 `capabilities/can_execute`，默认优先尝试 `pyautogui`，Windows 下可用 ctypes fallback 处理坐标 click/scroll；selector click 会明确要求浏览器 DOM/无障碍 executor；若宿主注入 Playwright page-like 对象，selector click/type/key/scroll 可直接走 DOM 执行，不可用或参数不足时明确返回 blocked、失败类型和恢复提示。
- Computer Use 权限和执行预览继续结构化：审批事件会带 `selector/x/y/text/direction/amount` 与 `previewRows`，clean 权限卡展示应用、动作、目标、坐标、文本/滚动、权限/风险；执行完成或 blocked 后的 `resultSummary/resultPreview` 也会突出动作、目标、执行器、坐标/截图尺寸、阻塞类型和恢复提示。
- Computer Use selector 路径继续收紧：`click/type/scroll` 只要带 selector 且没有浏览器 DOM/无障碍 executor，就明确 blocked 为 `selector_executor_required`，错误和恢复提示会带 selector 以及已知 `url/pageId/browserContextId`；Playwright page-like executor 已覆盖 selector click/type/scroll/key，审批卡和结果预览也会显示 URL/Page。
- Computer Use 增加可选内置 Playwright browser session executor：安装 runtime extra `computer-use-browser`（或单独安装 `playwright`）并设置 `LOCAL_AGENT_COMPUTER_USE_PLAYWRIGHT=1` 后，runtime 会懒启动 Chromium，并按 `browserContextId/pageId/url` 复用页面执行 selector click/type/scroll/key；browser 目标的 `inspect` 会返回标题、正文摘要和可交互元素提示，`screenshot` 可直接截浏览器页面；默认关闭，不引入硬依赖，shutdown/background worker 结束时会清理可选浏览器 session。设置页 probe 会显示 Playwright 是否安装、开关是否开启。
- 设置页电脑操作面板已同步真实接入进度：不再只显示“屏幕观察/运行时动作待接入”，而是展示权限审计、截图、桌面动作、浏览器 DOM 控制、剪贴板和系统快捷键的能力状态；重新检查会优先调用 Tauri `computer_use_probe` 只读探测 `ImageGrab/pyautogui/Windows fallback`，记录 `checkedAt` 与结构化 `capabilities`，clean shell 下也保持低卡片样式。
- 工具生命周期耗时字段已补齐：后端现在会在 `tool.completed`/`tool.failed`/`tool.blocked`、chat-compat `tool_result`、provider `tool_results` 和工具完成类 `assistant_progress` 中透传 `durationMs`，clean transcript 合并层会保留到工具消息 metadata，主聊天工具行和 worklog/runtime 行可直接使用真实执行耗时。
- 工具开始块也开始透传后端摘要：`content_start(tool_use)` / `tool_use_complete` 会带 `target/inputSummary`，clean transcript metadata 在工具输入完成前就能保留目标与可读标题；前端订阅层也能直接消费 raw `tool.started` 作为兜底，保留 arguments/inputText 但继续显示运行中。
- 工具输出增量旁路也已补摘要：`content_delta(toolOutput)` / `command.output` 会继续带 `target/inputSummary`，乱序到达时同一工具块仍能保留可读目标。
- 命令生命周期也保留摘要：普通 `run_command` 与后台命令的 `command.started/output/completed/failed/cancelled` 源事件、shared 类型、持久 trace、实时 command log cache、`command_log.get/list` 和 clean 订阅层都已接 `toolUseId/target/inputSummary` 及工具树元数据，刷新恢复或命令结果单独收尾时仍能维持同一工具块标题和 semantic parent。
- 前台和后台命令生命周期已进入 clean 主线：`command.started` 会立即创建运行中的命令工具行，后续 stdout/stderr 和 `command.completed/failed/cancelled` 继续合并到同一块。
- `run_command` 执行开始也会主动发 `tool.progress(activity)`，clean 工具块在 stdout/stderr 到达前就能显示“正在运行命令：目标命令”，同步命令和后台命令的运行感更连续。
- `web_fetch` 到 `browser` 的 parent 推断已规范化 URL：大小写、尾斜杠和 `#fragment` 差异不会再把同一次网页读取链拆成两个 worklog 分段。
- `write_file/apply_patch` 真实 handler 也会返回结构化步骤：写文件包含 resolve/scope/diff/approval/mkdir/write，补丁包含 parse/validate/approval/dry_run/apply；审批等待、拒绝/阻止和恢复应用都会通过 `tool.progress(activity)` 进入同一个工具活动块。
- `apply_patch` 后续回读的 parent 推断会从原始 `patchText/diffText` 解析文件路径；即使结果里暂时没有 `changedPaths`，跨轮 `read_file` 也能挂回对应改动。
- 后台 `command.cancel` 已做 pending-cancel 幂等：连续点击停止时只有第一次会报告新取消请求，后续请求保持当前命令状态，不再把已进入取消中的命令当作新的取消动作。
- `command.cancelled` 已成为明确终态事件：后端取消命令不再伪装成 `command.failed(status=cancelled)`，shared 类型、trace/store、前端订阅和 clean/workbench 工具块都会保留 `cancelled` 状态并显示“已取消”。
- 停止任务有明确 pending 态：点击停止后 clean composer/旧 haha composer 会禁用停止按钮并显示“停止中”，clean 浮动状态显示“正在停止任务...”，直到后端 `task.cancel`/刷新完成。
- 前端事件订阅现在会消费 `content_start`：`tool_use` 会先生成可见的工具占位行，`text` 会生成“正在输出回复”的流式提示，避免工具输入到齐前页面完全没反应。
- 后端非流式 `assistant.token` 也会先派生一次 `content_start(text)`，再派生 `content_delta`，让非 streaming provider 的正文也能走同一套 clean 文本块生命周期。
- `content_delta`、`tool_use_complete`、`tool_result` 已透传并保存 `parentToolUseId`；后端 lifecycle/chat-compat 也会把现有 parent id 带出来，并为 provider/最小循环里的同批工具序列补稳定 `toolCallId`，同时带 `toolGroupId/toolIndex/toolTotal`、`toolCategory`、`toolPhaseId/toolPhaseLabel` 和批次 + 阶段粒度的 `toolSemanticParentId/toolSemanticParentLabel`；clean runtime/worklog 已用这些字段稳定同批工具兄弟顺序，并优先按后端语义 parent/阶段汇总。
- Yuanbao flat ServerMessage 只承载模型/工具轨迹：`content_start`、`content_delta`、`tool_use_complete`、`tool_result`、真实 `permission_request`、`thinking`、`message_complete` 和 `error`。`goal_event`、`memory_event`、`task_summary`、`plan_update`、`status` 属于 panel/runtime state，不能作为主聊天 flat 协议输出；clean UI 可以从 activeTask/trace panel state 渲染低卡片，但不把它们混入模型聊天流。
- 后端 `task.updated` 只更新 task/plan panel state，不再派生 `plan_update` chat-compat frame。前端如需展示计划节点，应从 activeTask/structured panel event 消费，而不是从 flat chat transcript 反推。
- `assistant_progress` 现在是正式 shared 事件并进入前端订阅：主路由会发“正在整理上下文”，provider turn 请求模型前会发“正在请求模型”的轻量过程说明，工具首次进入新的 semantic parent 时会发“进入 X 阶段”，子任务会保持 trace/panel 而不污染主聊天。
- 预算收束也开始用 `assistant_progress` 解释：步骤预算 critical 时提示“正在收束当前任务”，max steps 耗尽时提示“正在整理当前进展”，并保留预算 pressure/step/recommendedAction 字段。
- ReAct 步骤预算已和路由/workflow 同源：后端循环优先读 main workflow `budget.maxSteps/resumeMaxSteps`，再读 routing `max_steps`，最后才回退 autonomy profile / policy；doc/code/debug/multi-step 等工作型场景不再被固定 20 步截断，前端看到的工作流预算和实际执行上限一致。
- 高价值工具 started 也会派生 `assistant_progress`：`run_command/apply_patch/write_file/task/computer_use` 有“准备运行命令/应用改动/写入文件/启动子任务/桌面操作”，read/list/search/git 等探针保持安静。
- 工具 completed/failed/blocked 也会派生 `assistant_progress`：高价值工具成功显示完成摘要，后台命令 running 会显示“命令已在后台运行”，失败/阻止则尽量显示一行可读原因，低价值探针成功仍保持安静。
- `run_command` 的输出已能进入工具活动块：前台同步命令会在执行中发 `command.started/output/completed|failed` 并桥接为 `content_delta.toolOutput` 和命令终态，后台命令事件也会带 `toolUseId/toolName/parentToolUseId/toolGroupId/toolIndex/toolTotal/toolCategory`；stdout/stderr 会按 `toolUseId` 累积到同一个工具活动块，`command.completed/failed/cancelled` 会把该块收尾成完成/失败/已取消摘要。`run_command` 真实 handler 也会返回 resolve/validate/scope/approval/log/execute/artifacts 结构化步骤，审批等待、前台执行、后台启动都能补运行过程；`task` 子任务派发会返回 prepare/dispatch/child_task 步骤。read/list/search/git/web/notebook/memory 这类探针和 MCP 工具 started 时会先桥一条 `outputStream=activity` 的轻量过程；新增 `tool.progress`/`tool.output` chat-compat 桥，订阅层也能直接消费 raw `tool.progress/tool.output` 作为兜底，`web_fetch`、`browser`、`notebook`、`memory.remember/recall`、`scratchpad.write/read`、`task` 以及已批准/免审批的 `apply_patch/write_file/computer_use` 已先在执行前主动发一条 `activity`，`web_fetch/browser/notebook/read_file/write_file/apply_patch/list_dir/list_directory/search_files/code_search/git_status/git_diff/task/computer_use/memory.remember/memory.recall/scratchpad.write/scratchpad.read` 成功/错误返回后还会主动发一条 `result_preview`，executor 可继续在运行中直接发 `activity/result_preview` 工具输出；非命令工具结果里带 `steps/logs/events/progress/timeline` 时，执行管线会提前拆成多条 `tool.progress(activity)` 阶段增量，completed 桥去重后再把 `resultPreview` 写入同一工具活动块，同一个工具活动块展开后可同时看到过程与结构化结果预览。
- 普通用户/助手消息已补轻量操作栏：复制、引用、更多。引用会优先写入底部 composer；没有 composer 桥接时退回为复制引用文本。
- `ask_user_question` 已从普通系统行拆成专用信息节点，问题/选项可直接提交回答；前端会把回答作为 supplement 发给当前 task，并在 paused task 上调用 resume。`computer_use_permission` 会消费 `computer_use` 审批事件，专用卡可直接允许/拒绝并用 resolved 事件更新状态，且结构化展示应用、动作、目标、坐标、文本/滚动、权限/风险；resolved payload 带 request/preview 时也会回填专用卡缺失字段。
- 低价值 read/list/git/search/状态探针会继续压进 worklog，不再把主聊天刷成一串工具日志；失败、审批、写入、diff 仍保留为主线节点。
- 右侧文件阅览补了轻量语法高亮，代码关键词、字符串、注释和数字会先按常见语言上色，Markdown 仍保留渲染预览。
- Composer 底部项目目录、上下文、权限不再只是静态按钮：项目/上下文可展开轻量详情，项目详情会显示分支、同步状态和改动文件，权限选项会显示模式说明，Slash 面板补充参数提示。
- Composer 输入卡片继续收口为 textarea、工具栏、项目/上下文状态条三段结构，底部分隔线满铺，减少会话页贴边和层级错乱。
- Markdown 文件阅览补了“预览/源码”切换，源码模式复用右侧代码阅览的行号与轻量高亮。
- Slash 命令面板补了键盘选择：上下键/Home/End 切换选项，Enter/Tab 选中，Escape 关闭当前建议。
- Markdown 预览补了轻量文档大纲，能从右侧大纲快速跳到标题。
- Composer 补了 `@` 文件引用入口：加号菜单可插入 `@`，输入 `@` 后会通过 `workspace.fileSearch` 做 workspace-wide 文件搜索，并保留当前改动文件优先候选，键盘可选择并插入引用；发送时会把 `@path` 合入 message metadata/attachments，后端 ContextBuilder 会把工作区内引用文本加入上下文。
- “完全访问权限”现在会先二次确认，不再一点击危险权限就直接切换。
- 工具/审批标题补了业务化动作名：`read_file`、`apply_patch`、`run_command` 等会优先显示为“读取 xxx”“修改 xxx”“运行 xxx”，不再把内部工具名当主标题。
- 主聊天工具消息补了结构化结果摘要：`status/exitCode/stdout` 会显示成“已完成 · 退出码 0 · …”，`items/files/matches` 会显示成“找到/涉及文件 N 项”，展开后才看完整输入输出，并支持复制详情。
- 工具生命周期事件补了基础 `target/inputSummary/resultSummary/resultPreview/toolCategory/toolPhaseId/toolPhaseLabel/toolSemanticParentId/toolSemanticParentLabel`：clean/runtime 优先用后端摘要、结构化预览和语义 parent/阶段渲染工具行，展开后仍保留原始输入输出，减少前端从 JSON 猜标题、结果和阶段；订阅层可直接用 raw `tool.completed`/`tool.failed`/`tool.blocked` 收尾同一工具块，`blocked` 会作为独立 warning 状态显示，失败/阻塞结果会把 `status/error/failureKind/recoveryHint` 合入结构化预览，错误不必展开 JSON 也能看到恢复线索；`web_fetch/browser`、`notebook`、`memory.remember/recall` 和 `scratchpad.write/read` 已有专用摘要与预览，未知/MCP/自定义工具也会用 `status/target/summary/items/results` 等常见字段生成通用预览兜底。
- chat-compat `tool_result` 也会透传 `target/inputSummary/resultSummary/resultPreview`，前端合并工具输入/结果时保留这些字段并优先显示 `resultSummary/resultPreview`。
- OpenAI Responses API 和 Anthropic Messages SSE 的工具参数流也已进入统一事件流：Responses `function_call_arguments.delta` 与 Anthropic `input_json_delta` 会转成 `tool_call_delta`，再生成 clean 可消费的工具占位和 `toolInput` 增量；流式工具输入的 `content_start/content_delta` 已提前带 `parentToolUseId/toolCategory/toolPhaseId/toolPhaseLabel/toolSemanticParentId/toolSemanticParentLabel`，和 OpenAI Chat SSE 的体感一致且早期占位就能按后端语义阶段归组。
- OpenAI Responses API、OpenAI-compatible/DeepSeek 风格 Chat SSE、Anthropic Messages SSE 和非流式 provider 思考摘要都已进入统一事件流：`reasoning_summary_text.delta` 会转成 `source=provider_reasoning_summary` 的 `thinking` 事件，Chat SSE 的 `reasoning_content/reasoningContent/thinking/reasoning` 与 Anthropic `thinking_delta` 会转成 `source=provider_reasoning_delta` 的 token 级 `thinking`，非流式 provider 明确返回的 `thought_summary/thoughtSummary`、chat response `reasoning_content` 或 Anthropic `thinking` block 会桥成 `source=non_stream_thought_summary` 的 thinking，clean 前端按连续 reasoning 片段累积为轻量思考行，遇到工具/正文/权限等可见块后会切成新的思考段，不混入最终回答正文。
- runtime/worklog 摘要继续清洗：命令 JSON 会显示“已完成 · 退出码 0 · stdout 摘要”，最终 `resultPreview` 会带命令、状态、目录、Shell 和输出短行；文件读取会显示读取字符数，目录/搜索会带样本路径，Git 状态/差异会显示分支、ahead/behind 和改动文件样本；`task`/`computer_use` 结果也会带状态、动作、目标、执行器、坐标/截图尺寸、阻塞类型和恢复提示等预览行；探针工具的 started `activity`、工具返回的结构化步骤流和完成 `resultPreview` 会合并到同一工具块，折叠态优先显示后端 `resultSummary`，展开后再看过程、目录、命中、分支、样例等轻量预览，进一步减少主线里的原始 JSON 噪声。
- runtime `approval` 已从通用工具卡拆成专用审批节点：标题、文件列表、允许一次/拒绝/始终允许和详情折叠更靠近 haha-cc 的轻量请求块。
- runtime `approval` 会保留工具类型，`apply_patch` 多文件审批优先显示“修改 N 个文件”，避免继续暴露 `patch approval request`。
- worklog 展开后改为专用紧凑工具行：读文件、查目录、Git 状态这类低价值步骤不再展开成大卡片，单行仍可继续打开详情和复制。
- worklog 折叠和展开都补了类别信息：折叠头会按“文件改动/验证/命令/Git 检查/搜索/读取上下文”等稳定阶段顺序汇总；后端 lifecycle 会标注 `toolCategory`、可直接显示的 `toolPhaseLabel` 和稳定 `toolSemanticParentLabel`，测试、typecheck、lint、build 这类命令会结构化归为“验证”，前端规则只保留为旧事件兜底；展开态会优先按 semantic parent 再按阶段分段，并为每段显示轻量 synthetic root，后端也会为新 semantic parent 派生一次“进入 X 阶段”过程说明，每段内继续保留轻量类别 chip、父子缩进、动作标题和结果摘要，避免一屏全是相似工具名。
- worklog 顶部新增前端过渡态过程说明：当后端还没给真实的中间 assistant_progress/thinking delta 时，先用已发生的 runtime 类型解释“正在读取上下文/运行命令/处理改动/等待审批”，避免主线直接跳到工具列表。
- 思考和过程说明进一步压成细线轻量行：`assistant_thinking` 默认只显示“正在思考/思考 + 摘要”，展开才看完整文本，避免模型思考节点像普通卡片挤占主聊天。
- worklog 已经贯通 `toolUseId/parentToolUseId` 到 runtime 渲染层，后端会透传现有 parent id，并为 provider/最小循环里的同批工具序列补稳定 `toolCallId`、`toolGroupId/toolIndex/toolTotal/toolSemanticParentId/toolSemanticParentLabel`；其中 `toolSemanticParentId` 会优先使用 `group:<id>:phase:<phase>` 这种批次 + 阶段粒度，同批搜索/代码搜索/目录/Git 探针后紧跟的 `read_file` 会在缺少显式 parent 时保守挂到最近上下文发现工具下面，最小循环里搜索后单独派生的 follow-up `read_file`、provider 跨轮搜索后单独发起的 `read_file` 也会挂到前序搜索结果，`apply_patch/write_file` 后紧跟或跨轮发起的同一改动文件回读、测试/typecheck/lint/build 等验证命令和 Git status/diff 复核也会保守挂到最近文件改动工具下面，没有文件改动 parent 时同批或跨轮 `git_diff` 会保守挂到最近 `git_status` 下面，同批或跨轮 `browser` 读取同 URL 时会保守挂到最近的 `web_fetch` 下面，同批或跨轮 `notebook get_cell/execute_cell` 访问同一 notebook 时会保守挂到最近的 `notebook list_cells` 下面，同批或跨轮 `scratchpad.read` 读取同 key 时会保守挂到最近 `scratchpad.write` 下面；无 id 工具调用会先规范/生成 id，再写回 assistant tool_calls、tool messages 和 provider `tool_results`，避免树关系断裂；展开后能按父子工具缩进展示，并按后端 `toolIndex` 稳定排列同批兄弟工具；跨阶段子工具会跟随父工具所在语义阶段显示，避免文件改动与回读/验证/Git 复核在展开态被拆散；clean 主线会把已完成的子工具 inline 重复项折叠进 worklog 树，失败/阻塞/运行中仍保留可见。
- worklog 折叠态进一步贴近 haha-cc：默认只保留一行过程摘要、状态小标和类别 chips，展开后才展示过程说明与复制摘要入口，减少主线被辅助说明撑高。
- clean 会话会对已被 runtime/worklog 承接的低价值 inline 工具消息做去重，同一个 read/list/git/search 不再在主线重复出现两遍；失败、运行中、写入和审批仍保留。
- 本地过程节点刷新保留已补齐：`api_retry`、`system`/`system_notification`、`compact_summary`、`goal_event`、`ask_user_question`、`computer_use_permission` 等 haha 风格节点不会在后端 messages 刷新时被清掉，减少“过程信息突然消失，只剩最终总结”的问题。
- 会话排序已调整：同一轮里只把最后一条普通助手正文挪到工具后面，前置说明正文按真实时间插在工具/命令之间，减少“主线只剩工具日志，最后一大段总结”的观感。
- 实时事件排序继续补稳：EventBus 会为同一毫秒内连续发布的事件单调推进 `ts`，并透出 `seq`；持久化 messages 默认分配 `createdSeq`，减少前端在工具占位、输出增量、后台命令收尾和消息刷新之间靠毫秒/id 猜顺序的情况。
- patch/runtime 标题继续清洗：`apply_patch` 和 patch runtime 会从 raw diff 里提取真实文件，显示“修改 xxx / 修改 N 个文件”，不再把 `Update xxx`、`patch approval request` 或 diff 头当作主标题。
- patch diff 预览升级为文件级审查面板：多文件 diff 有文件 tabs，当前文件显示新增/删除统计、旧/新行号、长 diff 截断提示和“复制当前文件差异”，更接近 haha-cc 的改动审查体验。
- patch/approval 文件行已经联动右侧文件面板：点文件会打开对应源码预览，同时 patch 行继续选中本地 diff，形成“左看改动、右看文件”的基础工作流；diff 新行号也能打开 `path:line` 并高亮右侧源码行。
- patch 事件和 patch cache 已带 `changedPaths`，前端不再完全依赖完整 diff 文本解析文件；diff 未加载/被截断时也能先显示改动文件并保持右侧文件联动。
- `apply_patch`/`write_file` 审批请求已带 `changedPaths`、`filesChanged` 和 `diffText`，实时事件和持久 trace 刷新都能恢复审批文件列表与 diff 预览；run_command/web_fetch/subagent/computer_use 等非文件审批也有 `preview` 行，Notebook `execute_cell` 复用 `run_command` 审批并显示 Notebook/Cell，减少前端从 JSON 文本里猜路径和动作。
- 右侧文件面板的“在编辑器中打开”已接到宿主 `open_path` 命令，不再只是前端占位。
- 右侧文件面板的筛选框已接入 workspace-wide 全局匹配：输入 2 个以上字符会调用 `workspace.fileSearch`，结果可直接打开文件或展开目录，并加载父目录。
- 右侧文件面板支持 `path:line`、`path#Lline` 和 `path?line=line` 目标：外部联动打开时会读真实文件路径并高亮定位对应行，Markdown 带行号会自动切到源码模式。
- worklog 常态进一步减噪：复制摘要入口只在展开后出现，默认折叠时只保留一条工具组摘要和紧凑行，避免主聊天被辅助操作按钮撑高。
- 工具、审批、改动、diff 和 worklog 的 CSS 继续压低卡片感：去掉多余阴影、减小圆角和 padding，让它们更像 haha-cc 的轻量过程行。
- 聊天正文 Markdown 渲染补了宽松标题、嵌套列表、任务列表和表格，模型输出里的 `##1`、todo、表格和多层要点不再直接按普通文本裸露或打平。
- 聊天正文代码块补了更细的轻量高亮：diff 增删/hunk、JSON 属性、内建值和操作符会分色，长代码块不再像一整块普通文本。
- 聊天正文补了 Mermaid 图表块：显式 `mermaid` 代码围栏和无语言但首行像 `graph/flowchart/sequenceDiagram` 的代码块都会渲染为图表，并带复制源码和放大预览。
- 普通消息补了附件/图片画廊：会从 metadata 的 `attachments/images/files/artifacts` 以及正文里的本地/URL 图片路径提取附件，图片显示为缩略图并可放大预览，支持多图左右切换、缩略图跳转和 Esc 关闭，普通文件显示为可复制路径的轻量 chip。
- 这层是 transcript adapter：能力不足时先把可识别事件接进统一消息流，无法由现有后端真实提供的能力继续记录为后端待补。

## 1. 应用壳层与导航

| haha-cc 功能点 | haha-cc 行为/按钮 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 顶部/侧边导航 | 会话列表、设置、项目入口、运行状态 | `CleanAppShell` 已提供侧边会话、标签栏、新建会话、设置、MCP、Skills | 使用现有 session/tab/store | `部分接入`：样式已转 clean，但侧栏信息密度和状态分组还没完全按 haha-cc 打磨 |
| 新建会话标签 | 点击新建会话进入空态页 | 已接入 `new-session` 标签 | 复用现有创建会话接口 | `部分接入`：空态 UI 已简化，项目/分支/工作树选择还不完整 |
| 设置入口 | 设置页里管理模型、权限、MCP、Skills 等 | 设置入口已加回，MCP/Skills 独立入口已暴露，并补了统一低卡片 clean 覆盖 | 复用旧设置/MCP/Skills 后端 | `部分接入`：页面内部仍是旧业务组件，后续可迁移为独立 clean 组件 |
| 总览页 | haha-cc 没有重型“总览”作为主路径 | 我们已淡化总览，主路径切到会话/新建会话 | 无新增后端需求 | `已接入` |
| 标签关闭按钮 | 每个标签有 `x` 关闭 | 已接入 | 复用 tabModel | `已接入` |
| 活动会话状态 | 当前会话运行、等待审批、完成等状态 | 已有浮动状态和会话列表状态点 | activeTask/session status | `部分接入`：状态文案仍需和消息流的真实阶段统一 |

## 2. 新建会话页

| haha-cc 功能点 | haha-cc 行为/按钮 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 简洁空态 | logo、标题、新会话说明、底部 composer | `CleanNewSessionWorkspace` 已做简洁空态 | 无 | `部分接入`：视觉方向接近，但 logo/品牌图形仍是占位 |
| 项目目录按钮 | 底部显示当前目录，可展开/切换 | composer 下方显示 cwd/context，项目 chip 可展开当前目录、分支、同步状态、改动数和最近文件，并可复制路径/改动文件列表 | workspace root/worktree status | `部分接入`：没有完整目录选择弹层、最近项目、工作树切换 |
| 模型选择 | 模型按钮/下拉 | `CleanComposer` 支持模型下拉 | modelOptions/selectedModelId | `已接入` |
| 权限模式 | 跳过/询问/自动接受等权限模式 | `CleanComposer` 有权限下拉，高风险“完全访问权限”会二次确认；设置页会同步默认 preset 并显示显式规则 | permissionMode callback / permissions config | `部分接入`：确认和规则可见/撤销已补，仍需更细 scope 规则 |
| 上下文按钮 | 上下文占用按钮，弹出详细 breakdown | 显示上下文百分比/标签，已补 token 预算、工具数、当前步骤、压缩节段、上下文组成条、纳入章节 chips 和系统提示层，并可复制上下文摘要；后端 context 已固定为 system -> Stable context prefix -> Dynamic context tail -> Current user request，`budgetStats.promptCache/stablePrefixTokens` 可用于展示缓存前缀，provider usage 返回的 `prompt_tokens_details/input_tokens_details/cache_read_input_tokens` 等 cache read 已能回填到 context preview，寒暄/简单问候会强制保持 `contextMode=minimal`，不会被 routing advisor 升级成大上下文任务 | contextPreview / budgetStats.includedSections / promptLayers / promptCache / provider cacheUsage | `部分接入`：分类 breakdown、稳定前缀指标和 provider cache read 回填已有轻量版，仍缺更完整持久快照与更多 provider 的 cache 细分覆盖 |
| 加号菜单 | 添加文件/图片、斜杠命令 | 加号菜单有“添加文件或图片”“斜杠命令”；输入区支持文件/图片拖拽覆盖层，Tauri 桌面 drop 会使用真实路径，浏览器 drop 用文件名兜底 | file dialog/输入框本地状态/Tauri drag-drop | `部分接入`：`@` 文本文件引用、工作区内文本附件、当前轮外部文本/图片附件和最近历史工作区图片入模型上下文已接上；历史外部附件和更完整附件结构仍待补 |
| 运行按钮 | 发送/运行，运行中切停止 | 已接入发送、停止 | submit/stop callbacks | `已接入` |

## 3. Composer 输入框与按钮

| haha-cc 功能点 | haha-cc 行为/按钮 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 多行输入框 | 大输入区，底部工具栏，宽度贴近聊天区 | clean composer 已全局接入 session/new-session，并固定 textarea、工具栏、项目/上下文状态条三段结构 | prompt state | `部分接入`：会话页贴边关系已继续收口，后续仍要补工作树切换和更完整的拖拽覆盖层 |
| `+` 按钮 | 打开工具菜单 | 已接入 | 前端状态 | `已接入` |
| 添加文件或图片 | 文件选择、图片缩略图、移除 | 已有附件列表、图片缩略图、移除按钮和拖拽覆盖层；拖入会合并附件并去重 | attachments array / Tauri drag-drop | `部分接入`：工作区内文本附件和当前轮外部文本附件已能被 ContextBuilder 读取，当前轮外部/工作区图片与最近历史工作区图片会作为多模态输入发送给 provider；完整附件 metadata 还未完全对齐 |
| 斜杠命令 | `/` 面板、命令描述、键盘选择 | 已有 `/` 自动补全、命令描述、参数提示、键盘选择和命令结果/详情节点；`/mcp refresh`、`/skills refresh` 会从运行中更新为完成/失败 | slash 命令 registry / local slash_command transcript event | `部分接入`：后端扩展命令和更多命令来源仍待补 |
| `@` 文件搜索 | 输入 `@` 搜索项目文件 | Composer 已有 `@` 面板、加号菜单入口和键盘选择；输入时会调用 `workspace.fileSearch` 做 workspace-wide fuzzy 搜索，并合并当前改动文件候选；发送后会记录 `fileReferences/attachments`，后端读取工作区内文本片段入上下文 | workspace.fileSearch / worktreeStatus.files / message metadata / ContextBuilder referenced files | `部分接入`：文本文件引用和工作区内文本附件链路已闭环；外部 `@` 引用仍不会越界读取，只有明确 attachment 的当前轮外部文件会按限制进入上下文 |
| 权限按钮 | 下拉权限模式，危险模式二次确认 | 有权限下拉、每种模式说明和“完全访问权限”确认；设置页能看到并恢复“始终允许”规则 | permissionMode / permission.rule.clear | `部分接入`：确认、默认 preset 和规则撤销已补，仍缺更细 scope 编辑 |
| 上下文按钮 | 百分比/状态，点击看详情 | 有百分比短显示、轻量详情浮层、分类 breakdown、纳入章节、provider cache read 和复制上下文摘要；简单问候/寒暄强制走 minimal context，大项目不会只因“你好”加载稳定 workspace 前缀 | contextPreview / routing.contextMode / provider cacheUsage | `部分接入`：缺更完整持久快照与更多 provider 的 cache 细分覆盖 |
| 模型按钮 | provider/model 下拉 | 已接入 | modelOptions | `已接入` |
| 停止按钮 | 运行中停止生成 | 已接入；任务级 terminal cancel 已幂等，点击停止后 composer 会进入“停止中”禁用态，后台命令取消已做 pending-cancel 幂等，取消终态会发 `command.cancelled` | stopPrompt / task cancel | `部分接入`：后续可继续补更细的取消阶段进度 |
| 暂存/排队 | 运行中把下一条加入队列，可引导/调整顺序/删除 | 已有队列项、引导、上移、下移、删除 | queuedPrompts callbacks | `部分接入`：交互已存在，视觉还需更像 haha-cc 的轻量 pending bar |
| 引导按钮 | 把暂存内容注入当前会话上下文 | 已有 `onGuideQueuedPrompt` | 依赖现有队列实现 | `部分接入`：需要后端明确“引导注入”事件，避免只是本地队列状态 |
| 发送按钮 | 不可发送时 disabled，运行中 stop | 已接入 | submit/stop | `已接入` |
| 项目目录 chip | 显示当前 repo/目录/分支 | 目前显示目录、分支、上游、ahead/behind、改动数、最近文件并可复制路径/改动文件列表 | workspace root/context/worktree status | `部分接入`：工作树切换还没统一展示 |

## 4. 主聊天信息流

| haha-cc 信息节点 | haha-cc 行为 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 用户消息 | 右侧简洁气泡，可复制/引用/分支 | 用户消息已渲染，带复制/引用/更多操作；更多菜单支持继续、分支、删除基础 transcript 操作 | messages.role=user / session.branch / session.truncate / message.delete | `部分接入`：复制/引用/复制 Markdown/复制 ID 和基础分支/截断/删除已接入；真正绑定上下文引用、任务副作用撤销仍待补 |
| 助手正文 | 普通文本/Markdown，插在工具调用之间 | 助手正文已渲染，带复制/引用/更多操作；`content_start(text)` 可显示流式提示；非流式 provider 在工具调用前返回的 `message + tool_calls` 会先进入 transcript，再进入后续工具块；本地过程节点会跨持久消息刷新保留 | messages.role=assistant / content_start | `部分接入`：前端可接分段事件，工具调用前正文顺序已接，仍缺更多 provider 的真实 token 级正文/思考分段 |
| 助手正文排序 | 中间说明保留在工具之间，最后结论在尾部 | 同一轮只把最后一条普通助手正文挪到 runtime 后；前面的正文按时间插入 | buildConversationActivity | `已接入`：仍依赖后端提供足够细的中间 assistant 消息 |
| 模型思考 | thinking 块，随流式更新 | 有 thinking/progress 入口，默认渲染为轻量细线行；OpenAI Responses reasoning summary delta、OpenAI-compatible/DeepSeek 风格 `reasoning_content/thinking` token 流、Anthropic Messages SSE `thinking_delta` 和非流式 provider `thought_summary/thoughtSummary/reasoning_content` 会进入 `thinking`，连续 thinking delta 会累积，工具/正文/权限等可见块之后的新 thinking 会开新段 | metadata/status/thinking event | `部分接入`：Responses、OpenAI-compatible/DeepSeek 风格 token thinking、Anthropic Messages 原生 SSE thinking 和非流式显式思考摘要已接；外部 adapter 等更多 provider 的原生 thinking 形态和更细阶段仍待补 |
| 过程说明 | 短句插在工具/命令前后 | `assistant_progress` 已成为正式事件并渲染为轻量过程文本；主路由会发“正在整理上下文”，provider turn 会在请求模型前发“正在请求模型”，预算 critical/exhausted 会说明收束/整理进展，工具首次进入新的 semantic parent 会说明“进入 X 阶段”，高价值工具 started/completed 会说明即将运行/已完成，失败/阻止会显示可读原因 | `assistant_progress` / metadata.kind | `部分接入`：上下文准备、模型请求、步骤预算收束、semantic parent 阶段开始和高价值工具开始/结束、失败/阻止已有真实事件，后续继续补工具结果流式等更细阶段 |
| 工具调用行 | 单行可折叠，显示工具名、目标、状态 | runtime/tool 行已低卡片化，`content_start(tool_use)` 可先显示占位；工具标题会优先用 `target/inputSummary` 转成“读取/修改/运行 + 目标”，并透传 `parentToolUseId` 与同批次 `toolCallId/toolGroupId/toolIndex/toolTotal/toolCategory/toolPhaseId/toolPhaseLabel/toolSemanticParentId/toolSemanticParentLabel`，同批/跨轮搜索/目录/Git 探针到 `read_file`、搜索后单独派生的 follow-up `read_file`、文件改动到同一文件回读/验证/Git 复核、无文件改动 parent 时 Git status 到 diff、web_fetch 同 URL 到 browser、notebook 同路径 list_cells 到 get/execute、scratchpad 同 key 写后读、Computer Use browser observe 到 action 已有保守 parent 推断，provider/最小循环里缺 id 的同批工具会先补稳定 id 再推断父子关系，worklog 已按 `toolIndex` 稳定同批工具顺序，并优先按后端 semantic parent/阶段名分组，同名阶段会按不同 semantic parent id 保留边界；高价值工具 started 会额外发轻量过程说明；OpenAI Chat/Responses 的工具参数增量都会进 `content_delta.toolInput`，前台与后台 `run_command` 输出都会进工具活动块 | runtime items/toolCalls/content_start/tool lifecycle summaries | `部分接入`：常见工具已减少 JSON 猜测并能保留现有/生成的工具 id、父子 id、兄弟顺序与语义分类，已有搜索/目录/Git 到 read、文件改动到回读/验证/Git 复核、Git status 到 diff、web_fetch 到 browser、notebook list 到 cell 操作、scratchpad 写后读、Computer Use 页面观察到动作的窄规则树推断，仍缺后端真正生成更复杂跨工具语义树和更多工具的流式结果 |
| 工具结果 | 和调用合并/紧跟，错误高亮 | 有结果/输出折叠和 copy，并保存 `parentToolUseId`；`run_command` stdout/stderr 会累积到工具活动块，前台命令会在执行中桥接输出，后台 command terminal 事件会收尾状态，read/list/search/git/web/notebook/memory 和 MCP started 会先写入 `activity` 过程，`web_fetch/browser`、`notebook`、`memory.remember/recall`、`scratchpad.write/read`、`task` 以及已批准/免审批的 `apply_patch/write_file/computer_use` 已先通过 `tool.progress` 主动补一条执行前 activity，`web_fetch/browser/notebook/read_file/write_file/apply_patch/list_dir/list_directory/search_files/code_search/git_status/git_diff/task/computer_use/memory.remember/memory.recall/scratchpad.write/scratchpad.read` 已会通过 `tool.output` 主动补一条执行后 `result_preview`，`tool.progress/tool.output` 可在执行中直接进入同一工具活动块，非命令工具结果里的 `steps/logs/events/progress/timeline` 会提前拆成多条 `tool.progress(activity)`，且 `web_fetch/browser` handler 自己已产出 request/response/decode/extract/truncate 等真实步骤，`notebook`、`memory.remember/recall`、`scratchpad.write/read` handler 也会产出加载/解析/读取 cell/审批/执行、准备/scope/存储/检索/关键词等真实步骤，`task` 派发会产出 prepare/dispatch/child_task 子任务步骤，`read_file/list_dir/search_files/code_search/git_status/git_diff` handler 会产出 resolve/read/walk/search/backend/repository/status/diff/files 等上下文探针步骤，`run_command/write_file/apply_patch` handler 会产出 resolve/validate/scope/approval/log/execute/artifacts、resolve/scope/diff/approval/mkdir/write 与 parse/validate/approval/dry_run/apply 等执行/文件改动步骤，completed 阶段去重后再把 `resultPreview` 作为 `content_delta.toolOutput` 进入同一工具活动块，失败/阻塞结果会把状态、错误、失败类型和恢复提示写进 `resultPreview`，最终优先用后端 `resultSummary/resultPreview` 收尾，未知/MCP/自定义工具缺少专用分支时会从 `status/target/summary/items/results` 生成通用预览，再缺失时才把常见 JSON 结果转成单行摘要，展开后先看过程与轻量结构化预览，再看完整输入输出 | runtime output/tool result/tool lifecycle summaries | `部分接入`：父子 id 已能透传并在 worklog 里缩进展示，前台 run_command 输出流和后台收尾已接，read/list/search/git/web/notebook/memory/scratchpad/write/patch/task/computer 已开始主动发 result preview，失败恢复线索和结构化步骤已能进入工具活动块，run/task/web/browser/notebook/memory/scratchpad/context probes/write/patch 已有真实过程 steps，仍需要更多 executor 主动发原生运行中分段 |
| 工具组 | 连续工具折叠为“执行了 N 条命令” | worklog 折叠已做，默认只保留一行工具组摘要；展开后使用语义阶段 root、紧凑工具行和父子缩进；普通 read/list/git/search/状态探针继续压缩，`rg/grep/find/Select-String/Get-Content/cat/head/tail/ls/tree/du/git status/git diff` 等只读上下文命令也会按“搜索/读取上下文/Git 检查”归类并默认收起，且会隐藏对应的重复 inline 工具消息；测试/typecheck/lint/build 类命令仍归为“验证”并保持可见；同批工具按 `toolIndex` 稳定排序，搜索/目录/Git 探针后紧跟的 read、文件改动后紧跟/跨轮发起的同文件回读、验证命令、Git status/diff 复核、无文件改动 parent 时 Git status 到 diff、web_fetch 到 browser、notebook list 到 cell 操作和 scratchpad 同 key 写后读会形成轻量父子树；跨阶段子工具跟随父工具所在语义阶段显示，避免展开态把一次改动链拆成多个孤立段；已完成子工具的重复 inline 也会折叠进 worklog 树，失败/阻塞/运行中保持可见；展开后可复制摘要 | activity worklog | `部分接入`：默认主线已明显减噪，阶段归类和常见窄规则工具树更像 haha-cc，后续还要接真实阶段解释正文和后端更稳定的复杂父子树 |
| 权限请求 | 内嵌审批卡，带 diff/命令预览 | runtime approval 已拆成专用轻量节点，常见审批会显示“修改/写入/运行 + 目标”，多文件 patch 显示“修改 N 个文件”，run_command/web_fetch/subagent/computer_use 等非文件审批会显示结构化预览行；Notebook `execute_cell` 会走 runCommand 权限/审批，预览 Notebook/Cell 并校验源码 hash；允许一次/拒绝/始终允许已接，始终允许会写入 capability 规则，设置页可恢复默认；实时与持久 `approval.requested/resolved` 会带 `kind/request/preview/changedPaths/filesChanged/diffText/decidedBy/decidedAt`，chat-compat `permission_request` 也会把预览行和文件字段送进 clean 主线，非 Computer Use 的 `approval.resolved` 会镜像成 resolved 版 `permission_request`，本地 approval store 查不到但 resolved payload 自带详情时也会镜像，乱序/恢复到达时还能回填缺失预览，缺 requested 但 detailed resolved 先到时也能生成已解决审批卡，状态恢复视图只拿到 resolved trace 时也能还原 request/preview/file 字段，审批卡实时更新和刷新恢复更稳；apply_patch/write_file 审批可从结构化字段恢复文件列表和 diff | approvals/permission_request / permission.rule.clear | `部分接入`：规则闭环和常见非文件预览已补，更多工具类型的专用字段仍可继续扩展 |
| 文件改动卡 | 当前轮改动 summary、查看 diff、撤销 | patch card + diff preview 已接入，并补了复制文件列表、撤销本轮；撤销会反向应用已保存 patch diff，工作区已漂移时拒绝 | patches/changedFiles / task.revertChanges | `部分接入`：patch diff 可回滚，diff 文件匹配仍需加强，非 patch 副作用不覆盖 |
| 任务摘要 | 完成后显示总结，不在刚开始出现 | 已隐藏运行初期 task summary | activeTask | `部分接入`：结束时机和内容质量依赖后端 |
| 上下文压缩 | “上下文已自动压缩”分割节点 | `compact_summary` 已进入 transcript adapter；provider preflight/recovery 压缩会 emit | provider preflight / failure recovery | `部分接入`：模型上下文压缩已能显示，长期会话 compactor 等其他来源仍待统一 |
| Goal/Memory 事件 | 轻量系统/面板节点 | `goal_event`/`memory_event` 属于 runtime panel state；clean UI 可从 activeTask/trace state 渲染，但不再把它们作为主聊天 flat ServerMessage | task lifecycle / memory flow | `部分接入`：Goal/Memory 都有真实事件，后续补更细目标重写/阶段变更 |
| API retry | 重试提示 | `api_retry` 已进入 transcript adapter；provider stream/non-stream 恢复路径会 emit | provider failure recovery | `部分接入`：模型请求恢复已能显示，其他 API/工具级重试来源仍待补 |
| 错误节点 | 失败工具/请求明显但不巨大 | 已有错误态 | failed trace/runtime/message | `部分接入`：需要统一错误摘要和展开详情 |
| 置底按钮 | 用户离开底部时显示向下按钮 | clean session 已有置底按钮 | 前端滚动状态 | `部分接入`：和 composer/右侧分隔布局还需联动打磨 |
| 自动滚动 | 流式时跟随底部，用户滚动时暂停 | 有基本实现 | 前端状态 | `部分接入`：没有 haha-cc 的虚拟列表和滚动快照 |

## 4.1 会话主线输出策略

| 类型 | 默认主线行为 | 展开/详情行为 | 仍需补齐 |
| --- | --- | --- | --- |
| 用户消息、助手正文、最终总结 | 直接显示，Markdown 段落、列表、代码块保持阅读优先 | 复制、引用、继续、分支、删除 | 更完整 token 级正文/思考分段 |
| 权限、失败、阻塞、等待用户输入 | 直接显示，避免关键状态藏在日志里 | 展示请求字段、预览、恢复提示，允许/拒绝/提交回答 | 更多工具的专用审批/问题字段 |
| 文件改动、写入、补丁、验证命令 | 主线保留轻量摘要或 change card | 展开看文件列表、diff、命令输出、复制摘要 | 非 patch 副作用的撤销/审查入口 |
| 读取/搜索/列目录/Git 探针 | 默认合并到一行 worklog 摘要，不再逐条刷屏 | 展开后按“搜索/读取上下文/Git 检查”阶段看紧凑行和详情 | 更复杂跨工具语义树由后端稳定生成 |
| 只读 shell 上下文命令 | `rg/grep/find/Select-String/Get-Content/cat/head/tail/ls/tree/du/git status/git diff` 等归为低噪声上下文动作 | 展开 worklog 才看命令、stdout/stderr 和耗时 | 持续补不同平台命令分类 |
| 高价值 shell 命令 | `npm test/typecheck/lint/build`、`pytest` 等验证命令保留为“验证”，不会被上下文探针规则吞掉 | 展开看完整 Shell 输出，失败突出显示 | 更细停止中/后台运行状态 |
| 普通状态/启动期过程 | “理解任务目标/准备上下文/等待模型”等默认过滤 | 有真实失败、阻塞、审批、完成时再显示 | 后端提供更自然的阶段解释正文 |

## 5. 消息操作栏

| haha-cc 按钮 | haha-cc 行为 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 复制 | 复制消息/工具输出 | runtime 输出和普通消息正文均已接入复制 | Clipboard | `已接入` |
| 引用 | 把该消息作为后续输入引用 | 普通消息可引用到 composer；没有桥接时复制 Markdown 引用 | composer prompt / transcript id | `部分接入`：文本引用已可用；真正绑定某条 transcript 的上下文引用仍需后端语义接入 |
| 删除/撤回 | 删除本地消息或撤销当前轮 | 更多菜单可删除单条 transcript 记录；change card 可撤销已保存 patch diff | message.delete / task.revertChanges | `部分接入`：消息删除和 patch 回滚已可用；命令副作用仍不覆盖 |
| 更多 `...` | 展开更多操作 | 普通消息已有轻量菜单，支持复制 Markdown、复制消息 ID、从这里继续、从这里分支、删除消息 | session.branch/session.truncate/message.delete | `部分接入`：基础 transcript 操作已闭环；仍缺真正的上下文引用绑定 |
| 从这里分支 | 基于某条消息创建分支会话 | 更多菜单会复制目标消息之前的 transcript 到新会话，并打开新标签 | session.branch / transcript message id | `部分接入`：会话记录分支已接入；任务/trace/文件副作用不会被复制 |
| 撤销本轮改动 | 当前轮 change card 撤销 | 改动卡已接 `task.revertChanges`，仅回滚已保存且仍可反向应用的 patch diff | task.revertChanges | `部分接入`：不覆盖 shell 命令产生的副作用 |

## 6. 工具/命令/审批渲染

| haha-cc 功能点 | haha-cc 行为/按钮 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 工具折叠按钮 | 行首 chevron 展开输入/输出 | 已接入；worklog 内用更轻的单行展开 | runtime expanded state | `已接入` |
| 工具状态 badge | running/completed/failed/skipped | 已接入 | item.status | `部分接入`：状态文案需统一成更少、更稳的集合 |
| 工具目标摘要 | `read_file path`、`run_command cmd` | 已清洗 read/list/git/search/run/write/apply_patch/notebook 的标题和常见 JSON 输出，主聊天工具行支持 status/exitCode/stdout/items/files/matches 摘要，并优先消费后端 `target/inputSummary/resultSummary`；未知/MCP/自定义工具也会收到通用 `resultPreview` | runtime input/output/tool lifecycle summaries | `部分接入`：基础摘要已接，后续补父子树、流式输入和更多工具专用字段 |
| 复制输出 | 复制工具输出 | 已接入，复制标签会区分 Shell 输出/工具详情 | Clipboard | `已接入` |
| 刷新命令 | 查看最新命令输出 | 已接入 command refresh | command job id | `已接入` |
| 停止命令 | 停止运行中的命令 | 已接入；后台 `command.cancel` 已对 pending cancel 做幂等处理，连续点击不会重复报告新的取消动作，最终以 `command.cancelled` 收尾并在工具块显示“已取消” | command job id | `部分接入`：仍需更细的停止中 UI 状态 |
| 审批批准 | approve | 已接入，并在专用审批节点内显示为“允许一次” | approvals API | `已接入` |
| 审批拒绝 | reject | 已接入，并在专用审批节点内展示 | approvals API | `已接入` |
| 永久批准/规则 | haha-cc 有 always/规则类操作 | “始终允许”已写入 capability 规则；设置页显示显式规则并可一键恢复默认 | approval.allowAlways / permission.rule.clear | `部分接入`：能力级规则闭环已补，路径/命令 scope 编辑仍待补 |
| 审批 diff 预览 | write/edit/apply_patch 展示 diff | 审批节点可展示文件列表和详情折叠；apply_patch/write_file 审批的 `changedPaths/filesChanged/diffText` 已从请求、实时事件和持久 trace 贯通，已有 diff 时复用 diff preview；非文件审批补了 `preview` 行用于命令/URL/应用动作展示，Notebook 执行审批也会显示 notebook 路径和 cell | approvals/patches | `部分接入`：文件写入和常见非文件预览已接，仍需更多工具类型的专用字段 |
| Computer Use 权限 | 专用弹窗，选择 app/权限项 | 已有专用低卡片节点，可结构化展示 app/action/selector/坐标/文本/滚动/URL/Page/权限/风险、复制详情，并通过 `approval.submit` 允许/拒绝/始终允许；resolved payload 带 request/preview 时会回填专用卡缺失字段；`computer_use` 已是正式工具 schema，可发起 `inspect/screenshot/click/type/key/scroll` 请求，inspect 会返回运行环境 steps，screenshot 会尝试本机截图，click/type/key/scroll 会在可用桌面控制 executor 下执行，宿主注入 Playwright page-like 对象时 browser inspect/screenshot 和 selector click/type/scroll/key 可走 DOM；也可安装 runtime extra `computer-use-browser` 并通过 `LOCAL_AGENT_COMPUTER_USE_PLAYWRIGHT=1` 启用内置 Playwright browser session executor，按 `browserContextId/pageId/url` 复用页面并在退出时清理，否则明确 blocked 为 `selector_executor_required` 并带 URL/Page 恢复线索；执行和 blocked 结果会带专用 resultSummary/resultPreview；设置页电脑操作面板会展示权限审计、截图、桌面动作、浏览器 DOM、剪贴板、系统快捷键的能力状态 | `computer_use` tool / approval -> computer_use_permission events / settings computer-use probe | `部分接入`：基础授权闭环、结构化权限卡、resolved 回填、始终允许规则、inspect/screenshot、可插拔桌面控制 executor、可选 Playwright browser session executor、Playwright page-like DOM executor 适配和设置页能力矩阵已接，完整无障碍 selector、宿主原生浏览器会话管理和弹窗细节待补 |
| AskUserQuestion | 工具向用户提问，有选项/输入 | 已有专用问题节点，可展示问题/选项、复制问题，选项/输入会作为 supplement 提交并恢复 paused task | `ask_user_question` / supplement / task.resume | `部分接入`：ReAct `ask_user` 和预算收敛提问已能闭环，仍需更多工具级专用问题字段 |

## 7. 文件改动与 diff

| haha-cc 功能点 | haha-cc 行为/按钮 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 当前轮改动卡 | 显示文件数、增删、每文件行 | 已有 patch card，支持复制改动文件列表；文件列表会过滤 `Update xxx` 和 diff 头伪文件；`changedPaths` 已从后端/状态层贯通，diff 未加载时也能先列文件 | patches/changedFiles | `部分接入`：仍需更多 patch 格式兼容和更完整右侧 diff viewer |
| 查看文件差异 | 点按钮打开 diff | 有“查看文件差异”按钮和 preview；patch runtime 会保留完整 diff，文件行会优先打开本地匹配 diff，并只展示所选文件的差异，同时打开右侧源码预览 | loadPatch / local diff / FileWorkspacePanel | `部分接入`：diff viewer 仍在聊天内，右侧还不是完整 diff/source 双栏 |
| 文件行点击 | 点击文件打开右侧预览 | patch/approval/通用 runtime 文件行会触发右侧 `FileWorkspacePanel` 打开对应文件，文件面板支持外部 active file 请求、展开父目录，并可消费 `path:line`/`path#Lline` 高亮具体行；diff 新行号已会传 `path:line` | FileWorkspacePanel | `部分接入`：还缺完整 diff/source 双栏同步和删除行/旧版本源码定位 |
| diff 语法色 | 增删颜色、hunk header、行号 | 已有基本 diff preview | patch text | `部分接入`：还不是真正完整 diff viewer |
| 撤销本轮 | change card 上撤销 | 前端入口已可用，后端会 `git apply -R --check` 后再应用 | task.revertChanges | `部分接入`：工作区漂移时拒绝，非 patch 改动暂不处理 |
| 审核/提交入口 | 在当前改动上进入审查 | 改动卡已补“审查改动”，先把审查提示写回 composer | composer quote bridge / 后续 review API | `部分接入`：独立审查/提交接口仍需后端或路由补齐 |

## 8. 代码阅览与文件区

| haha-cc 功能点 | haha-cc 行为/按钮 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 文件树 | 搜索、目录折叠、打开文件 | 复用 `FileWorkspacePanel` 并加 clean CSS | workspace file APIs | `部分接入`：样式接近，交互和图标还需精简 |
| 右侧文件区 | haha-cc 不让文件区污染主聊天，文件预览按需打开，代码阅读优先 | clean session 已改为默认关闭的右侧文件区；宽屏打开时 transcript/composer 主动让出空间，窄屏覆盖；文件区内部以源码/Markdown 阅读器为主，目录、搜索、本轮改动压成窄侧栏，不再保留会导致横向滚动的大分隔条 | 前端状态 / FileWorkspacePanel | `部分接入`：主流程和输入框不再被文件区压乱；后续补更完整 diff/source 双栏同步、文件区小按钮/菜单细节和旧版本源码定位 |
| 代码阅览 | 行号、语法高亮、横向滚动 | 已有行号、横向滚动、轻量关键词/字符串/注释/数字高亮；外部打开 `path:line`/`path#Lline` 会高亮定位目标行 | file content | `部分接入`：还不是完整语言服务级高亮，后续可复用编辑器能力 |
| Markdown 阅览 | md 渲染预览/源码切换 | 已有 Markdown 预览、源码切换和轻量文档大纲 | file content | `部分接入`：源码/预览滚动同步还没做 |
| 文件搜索框 | 筛选文件 / 全局找文件 | 本地树筛选保留，输入 2 个以上字符会调用 `workspace.fileSearch` 显示“全局匹配”，点结果可直接打开文件或展开目录 | workspace files / workspace.fileSearch | `部分接入`：全局找文件已接，后续补内容搜索和行号跳转 |
| 更多菜单 | 复制路径、自动换行、在编辑器打开 | 已有复制路径/自动换行/Markdown 源码切换，并补了编辑器打开入口 | existing actions / `open_path` | `部分接入`：外部打开已接宿主命令，偏好编辑器/行号打开还未做 |
| 在编辑器打开 | 打开外部编辑器 | 已接到 Tauri `open_path`，会交给系统打开当前文件或路径 | shell open API | `部分接入`：目前是系统默认打开，还不是指定 IDE 与行号 |

## 9. Markdown/正文渲染

| haha-cc 能力 | haha-cc 行为 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 标题 | `#` 变标题，不显示原始 `##` | `CleanMarkdown` 已处理标题 | assistant content | `已接入` |
| 列表 | 有序/无序列表缩进自然 | 已处理嵌套无序/有序列表和任务列表 | assistant content | `已接入` |
| inline code | 背景 chip | 已接入 | assistant content | `已接入` |
| fenced code | 代码块、语言、复制、高亮 | 有代码块、语言栏、复制按钮和轻量语义高亮；diff 增删/hunk、JSON 属性、内建值和操作符会分色；复制按钮带语言名 | assistant content | `部分接入`：语言服务级高亮待补 |
| 表格 | Markdown 表格转表格 UI | 已接入简单表格渲染 | assistant content | `部分接入`：复杂对齐/嵌套内容待补 |
| Mermaid | 图表渲染 | 已接基础 Mermaid 渲染，支持显式 `mermaid` 围栏、无语言图表检测、复制源码和放大预览 | assistant content | `部分接入`：复杂主题、拖拽缩放细节和失败恢复还可继续打磨 |
| 图片内联 | 用户/助手图片画廊 | Markdown 独立图片块可渲染；普通消息会从 metadata 和正文路径提取图片缩略图，支持放大预览、多图切换、缩略图跳转和键盘关闭；普通附件显示为可复制路径 chip；文本 `@` 文件引用、工作区内文本附件以及当前轮外部/工作区图片、最近历史工作区图片会随消息 metadata 进入上下文 | attachments/content refs / provider multimodal input | `部分接入`：图片入模型基础链路已接上；历史外部图片和完整图片消息结构仍需补齐 |

## 10. 设置 / MCP / Skills

| haha-cc 功能点 | haha-cc 行为/按钮 | 我们当前实现 | 后端/状态对接 | 差异与下一步 |
| --- | --- | --- | --- | --- |
| 设置总页 | 模型、权限、通知、外观、适配器 | 设置页已保留，顶部概览/操作条/面板行已加低卡片 clean 覆盖 | existing settings | `部分接入`：业务组件仍旧，后续可拆成独立 clean 分组列表 |
| 模型供应商 | 添加/编辑 provider、模型 | 旧设置能力保留 | settings store/API | `部分接入` |
| 权限设置 | 默认权限模式、危险模式确认、规则管理 | composer 有当前权限；设置页保留默认权限模式，并新增显式 capability 规则列表与恢复默认 | settings store / permission.rule.clear | `部分接入`：默认模式和规则撤销已接，scope 级编辑还未补 |
| Computer Use 设置 | 开关、权限状态、能力检查 | 设置页保留截图/浏览器/剪贴板/系统快捷键/敏感确认开关，并新增能力矩阵，重新检查会通过 Tauri `computer_use_probe` 显示权限审计、截图、桌面动作、浏览器 DOM、剪贴板和系统快捷键状态，包括 Playwright 是否安装和 `LOCAL_AGENT_COMPUTER_USE_PLAYWRIGHT` 是否开启 | settings local state / Tauri computer-use probe | `部分接入`：宿主只读探测和可选 Playwright session 状态已接，仍缺原生浏览器会话与无障碍服务的实时健康检查 |
| MCP 管理 | MCP 服务器列表、启停、配置 | 入口已加回，旧页面复用，hero/列表/按钮已加低卡片 clean 覆盖 | existing MCP APIs | `部分接入`：详情页交互仍复用旧组件 |
| Skills 管理 | skills 列表、启用/禁用、详情 | 入口已加回，旧页面复用，概览/操作条/列表行已加低卡片 clean 覆盖 | existing skills APIs | `部分接入`：详情页交互仍复用旧组件 |
| 外观设置 | 主题/密度 | 旧设置保留 | settings | `部分接入` |

## 11. 后端事件/协议差异

当前后端仍保留本地 `AgentEventEnvelope` 作为主协议，但每条可兼容事件只新写并只读取 `yuanbao` 扁平 ServerMessage。shared `YuanbaoServerMessage` 已按 `docs/cc-haha-main` 原始 `ServerMessage` 收紧，flat message 只声明 haha-cc 同形字段，本地扩展继续留在 envelope `payload`；legacy `hahaCc` 字段只作为历史迁移术语保留在旧文档/负向断言中，不再作为新事件、新 stdout frame、新桌面通道或 replay fallback。Tauri 只广播 `yuanbao://message`，stdio 侧只写出 `kind=yuanbao_message`；`trace.list`、`events.after` 和 replay timeline 读取同一份 flat message，补拉入口统一为后端 `events.yuanbaoAfter`、桌面 `yuanbao_events_after` 与 `RuntimeClient.yuanbaoEventsAfter()`，避免实时流、补拉和回放出现两套输出，也让外部 adapter 不必自行剥本地 envelope。`connected/pong` 已从后端 `runtime.ping` 接到 Tauri `runtime_ping` 和前端 `RuntimeClient.runtimePing()`，并通过 `connectYuanbaoMessages()` 在订阅后主动补 `connected+pong` 和后续 keepalive `pong`；`task_update`、`team_created/team_update`、`session_title_updated` 已用真实后端 RPC/发布流覆盖：普通 `task.updated`、协作任务创建/认领/消息/完成，以及 `session.update` 标题变化都会在实时 envelope 和 trace 中携带同形 flat message；摘要/记忆刷新会通过 `changedFields` 避免误派生标题更新。

| haha-cc ServerMessage | 用途 | 我们当前来源 | 状态 |
| --- | --- | --- | --- |
| `connected` | websocket/session ready | `runtime.ping` / Tauri `runtime_ping` / `RuntimeClient.connectYuanbaoMessages()` | `部分接入`：订阅 helper 会先派发扁平 `connected` ServerMessage；仍不是原生 websocket connect 事件 |
| `pong` | keepalive 探活 | `runtime.ping` / Tauri `runtime_ping` / `RuntimeClient.connectYuanbaoMessages()` | `部分接入`：订阅 helper 会先派发 `pong` 并按间隔继续 keepalive；仍是 RPC 驱动，不是 websocket 协议层自动 pong |
| `content_start` | 开始 text/tool_use，并给 id | chat-compat 事件 / provider turn / non-stream assistant.token bridge | `部分接入`：前端已渲染 text/tool 占位；非流式正文、工具调用前 message 和工具输入都有基础起始事件；EventBus 已保证实时事件 `ts` 单调推进，后续继续稳固 id |
| `content_delta` | token/工具输入/工具输出流式增量 | chat-compat 事件 / assistant token / provider tool_call_delta / command.output / tool lifecycle activity/result preview / tool.progress / tool.output | `部分接入`：正文、OpenAI Chat/Responses 工具输入、run_command stdout/stderr、read/list/search/git/web/notebook/memory/MCP started `activity`、web/browser/notebook/memory/scratchpad/task 和已批准/免审批写入/桌面操作执行前 `tool.progress`、read/list/search/git/web/browser/notebook/memory/scratchpad/write/patch/task/computer 执行后 `tool.output.result_preview`、非命令工具结构化步骤提前进入 `tool.progress(activity)`、`tool.progress/tool.output` 和 `resultPreview` 已能进流；工具输入流早期事件已带 parent/阶段 metadata，仍需更多 executor 主动发执行中分段 |
| `tool_use_complete` | 工具输入完整、parentToolUseId、toolGroupId/toolIndex/toolTotal/toolCategory/toolPhaseId/toolPhaseLabel/toolSemanticParentId/toolSemanticParentLabel | toolCalls/runtime/chat-compat | `部分接入`：前后端已透传 parentToolUseId、同批工具顺序、语义分类、可显示阶段名和批次 + 阶段粒度的 semantic parent id/label，真正跨工具语义树和更多 UI 折叠仍待补 |
| `tool_result` | 工具结果 | runtime output/tool result/tool lifecycle summaries | `部分接入`：前端已保存 parentToolUseId、`toolGroupId/toolIndex/toolTotal`、`toolCategory/toolPhaseId/toolPhaseLabel/toolSemanticParentId/toolSemanticParentLabel` 和 `target/inputSummary/resultSummary/resultPreview`，并优先消费后端基础结构化摘要与预览，失败/阻塞工具会带 `failureKind/recoveryHint` 等恢复线索，未知/MCP/自定义工具有通用预览兜底，展开 worklog 会把 semantic parent 显示成轻量 root；仍需真正生成跨工具语义父子树和更完整专用字段 |
| `permission_request` | 审批请求 | approvals | `已接入`：requested/resolved 都带结构化请求、预览、文件改动字段和决策时间；非 Computer Use 的 resolved 会镜像到 chat-compat `permission_request`，本地 approval store 缺失时可直接用 resolved payload 详情，持久 trace 刷新后也保留同样信息，只有 detailed resolved trace 时主线和状态视图也会用这些字段恢复审批卡 |
| `computer_use_permission_request` | computer use 授权 | `computer_use` approval / transcript adapter | `部分接入`：后端会把 `computer_use` 审批镜像成专用事件并带结构化动作/目标/坐标/文本/滚动预览，前端可允许/拒绝；inspect/screenshot、可插拔 click/type/key/scroll executor 和 Playwright page-like DOM executor 适配已有基础闭环，完整无障碍/宿主浏览器会话控制和弹窗详情待补 |
| `message_complete` | 本轮消息结束 | task lifecycle `message.completed` -> chat-compat `message_complete` | `已接入`：后端完成任务时会发布，前端收到后清理 thinking/streaming 占位；haha-cc 扁平 usage 已稳定为 `input_tokens/output_tokens/cache_read_tokens/cache_creation_tokens`，内部 envelope 仍可保留 provider 原始 usage 和预算字段 |
| `thinking` | 思考内容 | status/thinking metadata + OpenAI Responses reasoning summary delta + OpenAI-compatible/DeepSeek `reasoning_content/thinking` + Anthropic Messages SSE `thinking_delta` + non-stream `thought_summary/reasoning_content` | `部分接入`：Responses reasoning summary、OpenAI-compatible/DeepSeek token thinking、Anthropic Messages 原生 SSE thinking 和非流式显式 thought summary 已接；外部 adapter 等更多 thinking 形态与更完整思考阶段仍待补 |
| `status` | 当前运行状态 | traces/activeTask panel state | `部分接入`：不是 flat chat ServerMessage；普通 thinking/streaming 由 provider thinking/text/tool 生命周期表达，失败/阻塞只在需要用户注意时进入错误或权限块 |
| `background_task` | 后台/子任务进展 | transcript adapter | `部分接入` |
| `task_summary` | 本轮任务摘要 | activeTask/task panel state | `部分接入`：不是 flat chat ServerMessage；只在 runtime panel 或明确摘要面板中展示 |
| `plan_update` | 计划/步骤更新 | activeTask.plan / structured panel events | `部分接入`：不是 flat chat ServerMessage；前端从 task/plan panel state 渲染计划节点 |
| `api_retry` | API 重试提示 | provider failure recovery + transcript adapter | `部分接入`：provider 恢复/重试已 emit，其他 API/工具级重试来源待补 |
| `error` | 错误 | `message.failed` / `task.failed` | `部分接入`：失败事件会派生 haha-cc `error` ServerMessage，并在实时流、trace、补拉中保留同形字段 |
| `system_notification` | 系统通知 | provider preflight switch + transcript adapter | `部分接入`：模型配置切换已 emit，其他系统级提示待统一 |
| `compact_summary` | 上下文压缩节点 | provider preflight/recovery + transcript adapter | `部分接入`：模型上下文压缩已 emit，其他 compaction 来源待统一 |
| `goal_event` / `memory_event` | 目标/记忆节点 | task lifecycle / memory flow + panel state | `部分接入`：不是 flat chat ServerMessage；Goal start/complete/fail/cancel 与 Memory 写入已 emit，目标重写/阶段变更待补 |
| `ask_user_question` | 工具向用户提问 | ReAct `ask_user` / budget convergence + transcript adapter | `部分接入`：前端可提交回答，后端会通过 supplement + resume 继续；更多工具级问题来源仍待统一 |
| `task_update` | 子任务/团队任务状态 | `task.updated` / activeTask/backgroundJobs | `部分接入`：真实 `task.updated` 会携带扁平 `task_update`，`currentStep/goal/title` 会作为 progress 兜底；团队任务的完整生态仍待补 |
| `team_update` / `team_created` / `team_deleted` | 团队 agent 状态 | collaboration RPC / worker/task events | `部分接入`：`collab.task.created/claimed/message.sent/completed` 已派生 haha-cc 同形团队消息并进入 trace，`members` 已收紧为 `TeamMemberStatus[]` 且空闲成员省略 `currentTask`；haha-cc 的 teamWatcher/团队配置生态仍未完全等价 |
| `session_title_updated` | 自动标题 | `session.update` / `session.updated` | `部分接入`：RPC 标题更新会发布 `session.updated`，实时 envelope、`events.after` 和 trace mirror 都会携带同一份 `session_title_updated`；payload 带 `changedFields`，摘要/记忆刷新不会误派生标题更新 |

## 12. 当前优先整改顺序

1. `后端事件流/上下文流`：让 assistant 正文/thinking/tool/status 按时间进入 transcript，而不是最后汇成一大段；让 provider 请求保持 system -> Stable context prefix -> Dynamic context tail -> Current user request 的稳定顺序，提升可缓存前缀命中。前端已加 adapter，可先吃部分 Yuanbao flat 事件；实时 EventBus 已补单调时间戳和 `seq` 透出，messages 默认有 `createdSeq`，实时/补拉/trace/replay 已统一消费 `yuanbao` ServerMessage，`task_update/team_update/session_title_updated` 已用真实后端流程锁住，OpenAI Responses、OpenAI-compatible/DeepSeek 风格 thinking、Anthropic Messages SSE thinking/text/tool input 和 Anthropic 非流式 thinking block 已进入同一条 `thinking/content_delta/tool_call_delta -> yuanbao` 输出链，真实 provider usage 的 cache read 也能回填 provider turn/status/context preview；寒暄/简单问候会保持 `contextMode=minimal`，下一步继续补更细的工具结果分段、更多外部 adapter 原生 thinking 形态和更多 provider 的实际 cache 细分返回。
2. `工具摘要/工具树`：read/list/git/search/run/write/apply_patch/web_fetch/browser/notebook/memory/scratchpad 已有基础 `target/inputSummary/resultSummary/resultPreview/toolCategory/toolPhaseId/toolPhaseLabel/toolSemanticParentId/toolSemanticParentLabel`，目录/搜索/Git/Web/Browser/Notebook/Memory/Scratchpad 摘要和展开预览会带关键样本路径、分支同步信息、状态码、标题、正文摘要、cell 摘要、执行状态、关键词、键值和命中/改动计数；失败/阻塞结果会把状态、错误、失败类型和恢复提示写进结构化预览，未知/MCP/自定义工具也会从 `status/target/summary/items/results` 生成通用结果预览，MCP 工具 started 会先进入 `activity` 过程流，非命令工具结果里的 `steps/logs/events/progress/timeline` 会提前拆成多段 `tool.progress(activity)`，completed 阶段去重后再接 `resultPreview`，`tool.progress/tool.output` 已可作为 executor 执行中分段入口，`web_fetch`、`browser`、`notebook`、`memory.remember/recall`、`scratchpad.write/read`、`task` 以及已批准/免审批的 `apply_patch/write_file/computer_use` 已先主动发执行前 activity，`web_fetch/browser/notebook/read_file/write_file/apply_patch/list_dir/list_directory/search_files/code_search/git_status/git_diff/task/computer_use/memory.remember/memory.recall/scratchpad.write/scratchpad.read` 已先主动发执行后 result preview。这些字段也会进入 provider `tool_results`，让模型下一轮少从原始 JSON 猜结果；`parentToolUseId` 已可透传，provider/最小循环里的同批工具序列也有稳定 `toolCallId`、`toolGroupId/toolIndex/toolTotal`，并已用于 clean worklog 兄弟顺序，同批/跨轮搜索/目录/Git 探针到 read、搜索 follow-up read、文件改动到同文件回读/验证/Git 复核、无文件改动 parent 时 Git status 到 diff、web_fetch 同 URL 到 browser、notebook 同路径 list_cells 到 get/execute 和 scratchpad 同 key 写后读已有窄规则 parent 推断；clean 会把测试/typecheck/lint/build 类命令归为后端“验证”阶段，展开态也会优先按批次 + 阶段粒度的 semantic parent/语义阶段分段，显示轻量 root，后端首次进入新 semantic parent 会派生“进入 X 阶段”，并把已完成子工具重复 inline 折叠进 worklog 树，OpenAI Chat/Responses 工具输入、run_command 输出和非命令工具 activity/resultPreview 已流式进入 clean；下一步补后端真正生成复杂跨工具语义层级、让更多 executor 主动发工具实时流式结果和更多工具专用摘要。
3. `会话页 UI/文件区`：主聊天宽度、右侧文件区和 composer 关系已按 haha-cc 的按需文件阅读器收口；宽屏并排让位，窄屏覆盖，默认关闭；下一步补完整 diff/source 双栏同步、文件区小按钮/菜单细节、工作树切换和旧版本源码定位。
4. `Composer`：项目/上下文/权限详情、上下文分类 breakdown、Slash 参数提示、键盘选择、命令结果详情节点、workspace-wide `@文件` 搜索、附件拖拽、权限危险确认和项目 git 状态已补一层；下一步补工作树切换和更完整附件/拖拽覆盖层。
5. `消息操作栏`：复制、引用、更多、从这里继续、从这里分支和删除消息已接入基础 transcript mutation；下一步补真正绑定上下文引用、任务副作用/当前轮改动回滚。
6. `Diff/File Viewer`：右侧文件区已补轻量高亮、Markdown 预览/源码切换、文档大纲、workspace-wide 全局找文件、外部 `path:line` 高亮定位、diff 新行号联动和聊天文件行联动；diff 卡已继续压低密度；下一步补完整 diff/source 双栏同步、源码/预览滚动同步、删除行/旧版本源码定位。
7. `Settings/MCP/Skills`：已先用 clean 覆盖压低卡片感；下一步再把旧业务组件拆成独立 clean 列表/详情组件。

## 13. 当前结论

这次 clean 前端已经把主聊天、composer、文件区、权限、diff、worklog 和设置入口接回来了，并新增了一层 transcript adapter：能接 `content_start/content_delta/tool_use_complete/tool_result`，也能预先渲染 `api_retry/compact_summary/goal_event/memory_event/ask_user_question/computer_use_permission/slash_command` 等 Yuanbao flat 事件。普通消息的复制/引用/更多也已接入，引用能写回 composer；常见工具生命周期也开始带 `target/inputSummary/resultSummary` 和可透传的 `parentToolUseId`，不再全靠前端猜 JSON。它还不是完整参考体验 parity。最大差异不是单个样式按钮，而是后端 transcript 粒度：参考前端依赖细粒度事件，所以能自然呈现“思考 -> 工具 -> 解释 -> 再工具 -> 最终结论”。我们当前仍有一部分内容是从最终 messages、runtime 和 task 状态反推，因此还要继续补真实事件流、稳定生成工具树和更多专用摘要。
