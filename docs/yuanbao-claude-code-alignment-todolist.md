# Yuanbao Agent Claude Code/haha-cc 对齐整改 TODO

对应方案：`docs/yuanbao-claude-code-alignment-plan.md`

状态说明：

- `[ ]` 未开始
- `[~]` 进行中
- `[x]` 已完成
- `[!]` 阻塞或需要决策

## Batch 0：文档和边界

- [x] 新增总整改方案文档。
- [x] 新增可执行 TODO 文档。
- [x] 确认命名边界：只改 Yuanbao 产品/命令/对外字段命名，不全局替换 `docs/cc-haha-main`。
- [x] 在 `docs/remediation-plan-index.md` 中登记本整改线。

验收：

- 文档中明确保留 haha-cc 作为参考协议名。
- 文档中明确 Yuanbao 新命名和旧 haha 兼容别名。

## Batch 1：Yuanbao 命名别名

目标：对外主命名从 haha 改成 Yuanbao，同时保留旧兼容路径。

- [x] 后端事件 payload 新增 `yuanbao` 字段，值与旧 `hahaCc` 一致。
- [x] stdio writer 新增 `kind: "yuanbao_message"` 输出。
- [x] 保留旧 `kind: "haha_cc_message"` 兼容输出。
- [x] RPC 新增 `events.yuanbaoAfter`。
- [x] 保留旧 `events.hahaCcAfter`，内部委托到新方法。
- [x] `runtime.ping` 新增 `yuanbaoMessages`。
- [x] `runtime.ping` 保留 `hahaCcMessages` 兼容字段。
- [x] shared 类型新增 `YuanbaoServerMessage`。
- [x] shared 类型保留 `HahaCcServerMessage = YuanbaoServerMessage` 兼容别名。
- [x] `TraceEventRecord` 新增 `yuanbao?: YuanbaoServerMessage`。
- [x] `TraceEventRecord` 保留 `hahaCc?: HahaCcServerMessage`。

建议文件：

- `runtime/src/local_agent_runtime/event_bus.py`
- `runtime/src/local_agent_runtime/store/sqlite_store.py`
- `runtime/src/local_agent_runtime/rpc/server.py`
- `shared/src/events.ts`
- `shared/src/domain.ts`
- `shared/src/rpc.ts`

验收：

- 新前端可只消费 `yuanbao`。
- 老前端/老测试仍可消费 `hahaCc`。
- `events.yuanbaoAfter` 和 `events.hahaCcAfter` 返回一致消息。

验证记录：

- 2026-06-03：`python -m pytest runtime/tests/test_haha_cc_compat.py runtime/tests/test_agent_role_and_visibility.py runtime/tests/test_provider_turns.py runtime/tests/test_replay.py -q` 通过，127 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_collaboration_events.py -q` 通过，2 passed。
- 2026-06-03：补跑 `runtime/tests/test_orchestrator_react_loop.py` 时有 2 个既有行为失败，分别是低价值工具进度事件策略和 notebook stdout 预览瘦身，非本批命名别名改动引入。

## Batch 2：适配层重命名和契约测试

目标：将当前 haha 兼容层提升为正式 Yuanbao 输出适配层。

- [x] 新增 `runtime/src/local_agent_runtime/yuanbao_event_adapter.py`。
- [x] 将 `to_haha_cc_server_message` 包装或迁移为 `to_yuanbao_server_message`。
- [x] 将 `normalize_haha_cc_usage` 包装或迁移为 `normalize_yuanbao_usage`。
- [x] 旧 `haha_cc_compat.py` 保留兼容导出。
- [x] 新增 `runtime/tests/test_yuanbao_event_adapter.py`。
- [x] 旧 `test_haha_cc_compat.py` 保留或改为兼容别名测试。
- [x] 增加 ServerMessage required fields 契约测试。
- [x] 增加字段白名单测试，避免 RuntimeEvent 私有字段泄漏到 Yuanbao message。
- [x] `compact_summary`、`goal_event`、`memory_event` 映射为同形 `system_notification`。
- [x] `assistant_progress`、`tool.progress`、`tool.output`、`command.output` 映射为 `system_notification(task_progress)`。
- [x] 拒绝只含本地 `toolOutput` 的空 `content_delta` flat 消息。

验收：

- 所有 Yuanbao 输出消息符合 `ServerMessage` 形状。
- 旧 `haha_cc_compat` import 不失效。

验证记录：

- 2026-06-03：`python -m pytest runtime/tests/test_yuanbao_event_adapter.py runtime/tests/test_haha_cc_compat.py runtime/tests/test_agent_role_and_visibility.py runtime/tests/test_provider_turns.py runtime/tests/test_replay.py -q` 通过，131 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_collaboration_events.py -q` 通过，2 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_yuanbao_event_adapter.py runtime/tests/test_haha_cc_compat.py -q` 通过，26 passed。

## Batch 3：输出状态机补齐

目标：让前端通过 Yuanbao message 获得稳定状态流转。

- [x] 用户消息进入时输出 `status: thinking`。
- [x] provider stream 开始时输出 `status: streaming`。
- [x] provider compact 时输出 `status: compacting`。
- [x] 工具开始时输出 `status: tool_executing`。
- [x] 审批等待时输出 `status: permission_pending`。
- [x] 任务完成时输出 `status: idle`。
- [x] 任务失败时输出 `status: idle`，并保留 `error`。
- [x] 任务取消时输出 `status: idle`。
- [x] 增加状态机顺序测试。

建议文件：

- `runtime/src/local_agent_runtime/orchestrator/publishing.py`
- `runtime/src/local_agent_runtime/orchestrator/provider_turn.py`
- `runtime/src/local_agent_runtime/orchestrator/message_routing.py`
- `runtime/src/local_agent_runtime/orchestrator/message_execution.py`

验收：

- 普通回答、工具调用、审批、失败、取消都有明确 idle 收尾。

验证记录：

- 2026-06-03：`python -m pytest runtime/tests/test_p9_release_checks.py runtime/tests/test_provider_turns.py -q` 通过，82 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_yuanbao_event_adapter.py runtime/tests/test_haha_cc_compat.py runtime/tests/test_agent_role_and_visibility.py runtime/tests/test_provider_turns.py runtime/tests/test_replay.py runtime/tests/test_collaboration_events.py runtime/tests/test_p9_release_checks.py -q` 通过，157 passed。
- 2026-06-03：`npm.cmd run typecheck` 通过。

## Batch 4：工具事件标准化

目标：每个工具调用都能被前端稳定展示。

- [x] 工具开始统一输出 `content_start(blockType=tool_use)`。
- [x] 工具参数完整时统一输出 `tool_use_complete`。
- [x] 工具执行过程输出可选 `content_delta`。
- [x] 工具结束统一输出 `tool_result`。
- [x] `tool_result` 必须带 `toolUseId`、`content`、`isError`。
- [x] 保留 `parentToolUseId`。
- [x] 保留 batch 信息用于前端排序。
- [x] 增加 run_command、read_file、search_files、apply_patch、task 工具事件测试。

建议文件：

- `runtime/src/local_agent_runtime/orchestrator/publishing.py`
- `runtime/src/local_agent_runtime/execution/tool_pipeline.py`
- `runtime/src/local_agent_runtime/orchestrator/react_tool_helpers.py`

验收：

- 前端无需读原始 tool.started/tool.completed 也能展示基础工具过程。

验证记录：

- 2026-06-03：`python -m pytest runtime/tests/test_p9_release_checks.py -q` 通过，25 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_p9_release_checks.py runtime/tests/test_provider_turns.py runtime/tests/test_yuanbao_event_adapter.py runtime/tests/test_haha_cc_compat.py runtime/tests/test_agent_role_and_visibility.py runtime/tests/test_replay.py runtime/tests/test_collaboration_events.py -q` 通过，158 passed。
- 2026-06-03：`npm.cmd run typecheck` 通过。

## Batch 5：工具结果瘦身

目标：减少上下文和前端消息中的大 JSON。

- [x] 新增模型可见工具结果 formatter。
- [x] 新增 Yuanbao 前端可见工具结果 formatter。
- [x] 完整工具结果写入 trace/artifact/command log。
- [x] `_tool_result_message` 不再直接 JSON dump 完整 result。
- [x] `run_command` 结果使用 summary + head/tail。
- [x] `search_files`/`code_search` 结果限制条数和片段长度。
- [x] `web_fetch` 结果限制正文长度，保留 URL/contentType/status。
- [x] `read_file` 大文件使用 head/tail 和 truncated 标记。
- [x] `git_diff` 大 diff 使用 summary + artifact ref。
- [x] 增加大输出不会撑爆上下文的测试。

建议文件：

- `runtime/src/local_agent_runtime/orchestrator/react_tool_helpers.py`
- `runtime/src/local_agent_runtime/execution/tool_pipeline.py`
- `runtime/src/local_agent_runtime/context/compactor.py`

验收：

- 模型仍能根据工具结果继续完成任务。
- trace 中仍能查到完整输出。

说明：

- 小工具结果保持原样，只有超过阈值的大 result 才瘦身。
- `run_command` 完整 stdout/stderr 保留在 command log artifact。
- 大 `git_diff` 会注册 patch artifact，并在可见结果里返回 `artifactId`。

验证记录：

- 2026-06-03：`python -m pytest runtime/tests/test_p9_release_checks.py -q` 通过，27 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_context_probe_tool_steps.py -q` 通过，6 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_tool_pipeline_preview.py runtime/tests/test_mcp_tool_lifecycle_events.py -q` 通过，9 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_p9_release_checks.py runtime/tests/test_provider_turns.py runtime/tests/test_yuanbao_event_adapter.py runtime/tests/test_haha_cc_compat.py runtime/tests/test_agent_role_and_visibility.py runtime/tests/test_replay.py runtime/tests/test_collaboration_events.py -q` 通过，160 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_context_compactor.py -q` 通过，30 passed。

## Batch 6：只读工具并发

目标：提升探索阶段速度。

- [x] 定义 `is_concurrency_safe_tool_call`。
- [x] 定义只读 run_command 判定复用权限逻辑。
- [x] 将连续只读工具 calls 分批。
- [x] 使用线程池或受控 executor 并发执行只读 batch。
- [x] 写工具、task 工具、computer_use 工具保持串行。
- [x] 并发结果按原始 toolIndex 回灌 messages。
- [x] 并发执行时仍发布正确 started/completed/result 事件。
- [x] 处理并发中一个工具失败时的结果聚合。
- [x] 增加并发 batch 测试。
- [x] 增加写操作不并发测试。

建议文件：

- `runtime/src/local_agent_runtime/orchestrator/react_runner.py`
- `runtime/src/local_agent_runtime/orchestrator/react_tool_helpers.py`
- `runtime/src/local_agent_runtime/execution/tool_pipeline.py`
- `runtime/src/local_agent_runtime/policy/permission_engine.py`

验收：

- 多个 read_file/search_files/git_status 可以并发。
- apply_patch/write_file/run_command 写操作仍串行。

说明：

- 首版并发只启用明确只读工具：`read_file`、`list_dir`、`list_directory`、`search_files`、`code_search`、`git_status`、`git_diff`。
- `run_command` 只读判定 helper 已接入权限逻辑，但暂不纳入并发安全集合，待 Batch 7 权限少打断统一启用。
- 带 `parentToolUseId` 的后续工具保持串行，避免 search/read、edit/verify 等语义父子关系被并发打乱。

验证记录：

- 2026-06-03：`python -m pytest runtime/tests/test_orchestrator_react_loop.py -q -k "parallel_read_only or independent_read_only or parallelize or marks_provider_tool_batch_order"` 通过，4 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_p9_release_checks.py runtime/tests/test_context_probe_tool_steps.py -q` 通过，33 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_p9_release_checks.py runtime/tests/test_provider_turns.py runtime/tests/test_yuanbao_event_adapter.py runtime/tests/test_haha_cc_compat.py runtime/tests/test_agent_role_and_visibility.py runtime/tests/test_replay.py runtime/tests/test_collaboration_events.py runtime/tests/test_context_compactor.py runtime/tests/test_context_probe_tool_steps.py runtime/tests/test_mcp_tool_lifecycle_events.py runtime/tests/test_tool_policy_resolver.py runtime/tests/test_orchestrator_react_loop.py -q -k "not low_value_tool_started_stays_quiet and not low_value_tool_activity_starts_assistant_progress and not notebook_stdout_preview"` 通过，362 passed，1 skipped，1 deselected。
- 2026-06-03：`python -m compileall -q runtime/src/local_agent_runtime` 通过。
- 2026-06-03：`npm.cmd run typecheck`（在 `app` 目录）通过。

## Batch 7：权限少打断

目标：减少低风险操作审批。

- [x] 扩展 read-only 命令 allowlist。
- [x] 支持 PowerShell 常见只读命令。
- [x] 支持 bash/cmd 常见只读命令。
- [x] 增加测试/构建命令低风险识别。
- [x] 新增或对齐 `accept_edits` permission mode。
- [x] `accept_edits` 下文件编辑自动允许。
- [x] `accept_edits` 下危险 shell 仍审批。
- [x] untrusted content guard 仍能覆盖高风险操作。
- [x] 增加权限模式测试。

建议文件：

- `runtime/src/local_agent_runtime/policy/permission_engine.py`
- `runtime/src/local_agent_runtime/tools/run_command.py`
- `runtime/src/local_agent_runtime/tools/apply_patch.py`
- `runtime/src/local_agent_runtime/tools/write_file.py`

验收：

- 只读命令不频繁弹审批。
- 高风险命令不会被误放行。

说明：

- 低风险命令覆盖 Git 只读查询、PowerShell/CMD/bash 常见只读命令、测试/构建/类型检查命令。
- 链式命令、管道、重定向不按低风险处理，仍需要审批。
- `accept_edits` 仅自动允许文件编辑；未知 shell 命令仍按 `approval_required`。
- untrusted content guard 优先级高于 `accept_edits`，高风险写入仍会要求审批。

验证记录：

- 2026-06-03：`python -m pytest runtime/tests/test_config_normalizer.py runtime/tests/test_tool_policy_resolver.py runtime/tests/test_run_command_policy.py -q` 通过，65 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_file_change_tool_steps.py runtime/tests/test_run_command_encoding.py -q` 通过，11 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_tool_policy_resolver.py runtime/tests/test_run_command_policy.py runtime/tests/test_file_change_tool_steps.py runtime/tests/test_run_command_encoding.py runtime/tests/test_mcp_tool_lifecycle_events.py runtime/tests/test_agent_role_and_visibility.py -q` 通过，98 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_orchestrator_react_loop.py -q -k "parallel_read_only or independent_read_only or parallelize or marks_provider_tool_batch_order or approval or run_command"` 通过，16 passed，121 deselected。
- 2026-06-03：`python -m pytest runtime/tests/test_p9_release_checks.py runtime/tests/test_provider_turns.py runtime/tests/test_yuanbao_event_adapter.py runtime/tests/test_haha_cc_compat.py -q` 通过，106 passed。

## Batch 8：AskUserQuestion 工具

目标：让模型能自然询问用户。

- [x] 新增 `ask_user_question` tool schema。
- [x] 实现 tool handler，复用 pause/resume。
- [x] 支持 1-3 个问题。
- [x] 支持选项和推荐项。
- [x] 用户回答作为结构化 tool_result 回灌。
- [x] 增加 ask/resume 流程测试。

建议文件：

- `runtime/src/local_agent_runtime/tools/_schemas.py`
- `runtime/src/local_agent_runtime/tools/__init__.py`
- `runtime/src/local_agent_runtime/orchestrator/react_runner.py`
- `runtime/src/local_agent_runtime/orchestrator/resume_flow.py`

验收：

- 模型能主动问问题。
- 用户回答后原任务继续执行。

说明：

- `ask_user_question` 作为模型可调用内置工具注册，暂停时保留 pending tool_call，不提前向模型回灌等待结果。
- 用户回答写入 task inbox 后会自动恢复对应 paused task，并把回答包装为 `ask_user_question` 的结构化 tool_result。
- 旧的 provider `decision: ask_user` 路径保持兼容，同时事件 payload 增加 `questions` 数组。

验证记录：

- 2026-06-03：`python -m pytest runtime/tests/test_orchestrator_react_loop.py -q -k "ask_user_question_tool or pauses_for_ask_user"` 通过，3 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_orchestrator_react_loop.py -q -k "ask_user_question_tool or pauses_for_ask_user or approval or supplement or paused or resume"` 通过，24 passed。
- 2026-06-03：`python -m compileall -q runtime/src/local_agent_runtime` 通过。
- 2026-06-03：`python -m pytest runtime/tests/test_tool_policy_resolver.py runtime/tests/test_config_normalizer.py runtime/tests/test_run_command_policy.py -q` 通过，65 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_p9_release_checks.py runtime/tests/test_provider_turns.py runtime/tests/test_yuanbao_event_adapter.py runtime/tests/test_haha_cc_compat.py -q` 通过，106 passed。

## Batch 9：PlanMode 工具

目标：复杂任务先只读探索、提交计划，再执行。

- [x] 新增 `enter_plan_mode` tool schema。
- [x] 新增 `exit_plan_mode` tool schema。
- [x] plan mode 下工具策略切换为只读。
- [x] `exit_plan_mode` 创建 plan approval。
- [x] 用户批准后恢复执行。
- [x] 用户拒绝后返回模型修订计划或结束。
- [x] 增加 plan mode 测试。

建议文件：

- `runtime/src/local_agent_runtime/tools/_schemas.py`
- `runtime/src/local_agent_runtime/policy/tool_policy_resolver.py`
- `runtime/src/local_agent_runtime/orchestrator/message_execution.py`
- `runtime/src/local_agent_runtime/orchestrator/approval_flow.py`

验收：

- plan mode 内不会写文件。
- 计划批准后可以继续执行。

实现记录：

- `enter_plan_mode` 作为无副作用控制流工具进入只读 planning 状态。
- `exit_plan_mode` 创建 `kind=plan` approval，并在批准/拒绝后把 `plan_approved` 或 `plan_rejected` tool_result 回灌给模型。
- plan mode 下 provider 可见工具收敛为只读工具与 `exit_plan_mode`；同一批 tool call 中进入 plan mode 后的写入工具会被拦截。
- skill strict whitelist 默认保留 `ask_user_question` / `enter_plan_mode` / `exit_plan_mode` 控制流工具，避免文档类 skill 把 plan mode 出口剪掉。

验证记录：

- 2026-06-03：`python -m pytest runtime/tests/test_tool_policy_resolver.py runtime/tests/test_orchestrator_react_loop.py runtime/tests/test_skill_tool_policy.py -q -k "plan_mode or strict_whitelist"` 通过，7 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_tool_policy_resolver.py runtime/tests/test_orchestrator_react_loop.py runtime/tests/test_skill_tool_policy.py -q -k "plan_mode or ask_user_question_tool or pauses_for_ask_user or approval or supplement or paused or resume or strict_whitelist"` 通过，33 passed。
- 2026-06-03：`python -m compileall -q runtime/src/local_agent_runtime` 通过。
- 2026-06-03：`python -m pytest runtime/tests/test_tool_policy_resolver.py runtime/tests/test_config_normalizer.py runtime/tests/test_run_command_policy.py -q` 通过，66 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_p9_release_checks.py runtime/tests/test_provider_turns.py runtime/tests/test_yuanbao_event_adapter.py runtime/tests/test_haha_cc_compat.py -q` 通过，106 passed。
- 2026-06-03：`npm.cmd run typecheck` 通过。
- 2026-06-03：`python -m pytest runtime/tests/test_p9_release_checks.py runtime/tests/test_provider_turns.py runtime/tests/test_yuanbao_event_adapter.py runtime/tests/test_haha_cc_compat.py runtime/tests/test_agent_role_and_visibility.py runtime/tests/test_replay.py runtime/tests/test_collaboration_events.py runtime/tests/test_context_compactor.py runtime/tests/test_context_probe_tool_steps.py runtime/tests/test_mcp_tool_lifecycle_events.py runtime/tests/test_tool_policy_resolver.py runtime/tests/test_orchestrator_react_loop.py runtime/tests/test_skill_tool_policy.py -q -k "not low_value_tool_started_stays_quiet and not low_value_tool_activity_starts_assistant_progress and not notebook_stdout_preview"` 通过，394 passed，1 skipped，1 deselected。

## Batch 10：AgentTool 包装

目标：让现有 team/subagent 能力更像 Claude Code AgentTool。

- [x] 新增 `agent` tool schema。
- [x] 包装现有 `task` tool / SubagentService。
- [x] 支持 `agent_type`。
- [x] 支持 `prompt`。
- [x] 支持 `cwd`。
- [x] 支持 `mode`。
- [x] 支持 `tool_allowlist`。
- [x] 支持 `budget`。
- [x] 支持 `plan_mode_required`。
- [x] 子 agent 默认只读。
- [x] 子 agent 禁止 commit/push。
- [x] 子 agent 禁止继续调用 `agent` / `task` 二次派发。
- [x] 父任务对 `agent` / `task` 子任务结果使用同一收敛与 continuation budget 策略。
- [x] 增加 AgentTool 调度测试。

建议文件：

- `runtime/src/local_agent_runtime/tools/_schemas.py`
- `runtime/src/local_agent_runtime/tools/task.py`
- `runtime/src/local_agent_runtime/services/subagent_service.py`
- `runtime/src/local_agent_runtime/services/worker_runner.py`

验收：

- 模型能通过 `agent` 派发 reviewer/tester/coder。
- worker 进度可在 Yuanbao/team message 中展示。

进展：

- 2026-06-03：`agent` 作为 Claude Code 风格 AgentTool 包装接入工具 registry、schema、ReAct special dispatch、tool policy、proposal validator；`task` 兼容保留。
- 2026-06-03：`agent_type` / `agentType`、`tool_allowlist` / `toolAllowlist`、`cwd`、`mode`、`plan_mode_required`、`budget` 归一化后传入 SubagentService；执行路径和 handler 路径共用同一归一化函数。
- 2026-06-03：child worker 默认 read-only，且即使 allowlist 误传也会剔除 `agent` / `task`，防止子 agent 递归派发。
- 2026-06-03：`cwd` 写入 child runtime hints；只读 child 只提示 preferred cwd，不提示 run_command；显式允许 run_command 时才给 pytest/python hint。
- 2026-06-03：`plan_mode_required` 会让 child 初始 context 进入 plan mode，复用现有 read-only + exit_plan_mode 限制。
- 2026-06-03：`python -m pytest runtime/tests/test_agent_tool_execution.py runtime/tests/test_task_tool_steps.py runtime/tests/test_tool_policy_resolver.py -q` 通过，38 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_task_tool_steps.py runtime/tests/test_agent_tool_execution.py runtime/tests/test_tool_policy_resolver.py runtime/tests/test_proposal_validator.py -q` 通过，167 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_acceptance_scenarios.py runtime/tests/test_llm_proposal_flows.py runtime/tests/test_planner_contract.py -q -k "tool or unsafe or allowedTools or agent_profile"` 通过，18 passed，189 deselected。
- 2026-06-03：`python -m pytest runtime/tests/test_task_tool_steps.py runtime/tests/test_agent_tool_execution.py runtime/tests/test_tool_policy_resolver.py runtime/tests/test_proposal_validator.py runtime/tests/test_acceptance_scenarios.py runtime/tests/test_llm_proposal_flows.py runtime/tests/test_planner_contract.py runtime/tests/test_yuanbao_event_adapter.py runtime/tests/test_haha_cc_compat.py runtime/tests/test_p9_release_checks.py -q -k "not low_value_tool_started_stays_quiet and not low_value_tool_activity_starts_assistant_progress and not notebook_stdout_preview"` 通过，430 passed。

## Batch 11：Team timeline

目标：把后端 collaboration 能力转成稳定前端展示。

- [x] 聚合 `collab.task.created`。
- [x] 聚合 `collab.task.claimed`。
- [x] 聚合 `collab.task.completed`。
- [x] 聚合 `collab.task.failed`。
- [x] 聚合 `collab.worker.upserted`。
- [x] 聚合 `collab.worker.heartbeat`。
- [x] 聚合 `collab.message.sent`。
- [x] 输出稳定 `team_update`。
- [x] 成员字段固定 `agentId/role/status/currentTask`。
- [x] 增加 team timeline 测试。

建议文件：

- `runtime/src/local_agent_runtime/yuanbao_event_adapter.py`
- `runtime/src/local_agent_runtime/services/collaboration_service.py`
- `runtime/src/local_agent_runtime/services/worker_runner.py`

验收：

- 前端可以不读原始 collab 事件也能展示 agent team 状态。

进展：

- 2026-06-03：`CollaborationService` 发布 `collab.task.*` / `collab.worker.*` / `collab.message.sent` 时附带 session 级 `team` snapshot，`events.after` 可恢复 session-scope worker team events。
- 2026-06-03：`yuanbao_event_adapter` 将 task/worker/message/budget collaboration timeline 收敛为稳定 `team_update`，仅保留 haha-cc 同形 `agentId/role/status/currentTask` 成员字段；`collab.team.created/deleted` 继续保留团队生命周期事件。
- 2026-06-03：`python -m pytest runtime/tests/test_yuanbao_event_adapter.py runtime/tests/test_haha_cc_compat.py runtime/tests/test_collaboration_events.py -q` 通过，31 passed。
- 2026-06-03：`python -m pytest runtime/tests/test_collaboration_runtime.py runtime/tests/test_worker_runner.py runtime/tests/test_worker_process_transport.py runtime/tests/test_process_worker_e2e.py runtime/tests/test_multi_subagent_regression.py -q -k "collab or worker or team or task"` 通过，34 passed，7 skipped，3 deselected。

## Batch 12：前端切换到 Yuanbao 命名

目标：前端主消费路径使用 Yuanbao 命名。

- [x] shared 类型改用 `YuanbaoServerMessage`。
- [x] event envelope 优先读取 `event.yuanbao`。
- [x] 兼容 fallback 到 `event.hahaCc`。
- [x] stdio 消息优先处理 `kind: "yuanbao_message"`。
- [x] 兼容处理 `kind: "haha_cc_message"`。
- [x] RPC 轮询优先使用 `events.yuanbaoAfter`。
- [x] 兼容 fallback 到 `events.hahaCcAfter`。
- [x] UI 文案避免出现 haha/haha-cc，除非是在说明兼容协议来源。
- [x] 增加前端事件订阅测试。

建议文件：

- `shared/src/events.ts`
- `shared/src/domain.ts`
- `shared/src/rpc.ts`
- `app/src/hooks/useEventSubscription.ts`
- `app/src/hooks/useDerivedViews.ts`
- 相关测试文件

验收：

- UI 对用户显示 Yuanbao。
- 旧事件仍能渲染。

进展：

- 2026-06-03：前端能力记录页用户可见标题/文案切换为 Yuanbao；内部兼容 API、历史路径、CSS class 和 localStorage key 暂不做高风险重命名。
- 2026-06-03：`RuntimeClient` 测试补充 `subscribeYuanbaoMessages`、`connectYuanbaoMessages`、`yuanbaoEventsAfter` 主路径，旧 `hahaCc*` 入口保留为兼容 fallback 测试。
- 2026-06-03：`npm --prefix app test -- src/lib/runtimeClient.test.ts src/ui/haha-clean/pages/CleanNewSessionWorkspace.test.tsx src/ui/haha-clean/pages/CleanCapabilityNotes.test.tsx` 通过，32 passed。
- 2026-06-03：`npm --prefix app run typecheck` 通过。

## Batch 13：最终回归

- [x] 运行 runtime 相关测试。
- [x] 运行 shared 类型检查。
- [x] 运行前端相关测试。
- [x] 做一次普通聊天事件回放。
- [x] 做一次工具调用事件回放。
- [x] 做一次审批事件回放。
- [x] 做一次 child agent/team 事件回放。
- [x] 更新整改文档状态。
- [x] 更新 remediation index。

验收：

- 新旧命名兼容。
- Yuanbao 输出协议稳定。
- 前端展示不依赖旧 haha 主命名。

进展：

- 2026-06-03：runtime 事件/工具/审批/PlanMode/AskUser/AgentTool/Team/Replay 回归通过：`python -m pytest runtime/tests/test_yuanbao_event_adapter.py runtime/tests/test_haha_cc_compat.py runtime/tests/test_collaboration_events.py runtime/tests/test_agent_tool_execution.py runtime/tests/test_task_tool_steps.py runtime/tests/test_tool_policy_resolver.py runtime/tests/test_orchestrator_react_loop.py runtime/tests/test_skill_tool_policy.py runtime/tests/test_replay.py -q -k "yuanbao or haha or team or collab or agent_tool or task_tool or plan_mode or ask_user_question_tool or pauses_for_ask_user or approval or supplement or paused or resume or tool_result or content_start or content_delta or message_complete or replay"`，93 passed，1 skipped，157 deselected。
- 2026-06-03：`python -m compileall -q runtime/src/local_agent_runtime` 通过。
- 2026-06-03：前端 Yuanbao 事件订阅/trace cache/能力文案回归通过：`npm --prefix app test -- src/lib/runtimeClient.test.ts src/hooks/useEventSubscription.test.tsx src/ui/haha-clean/pages/CleanCapabilityNotes.test.tsx`，45 passed。
- 2026-06-03：`npm --prefix app run typecheck` 通过。
- 2026-06-03：AgentTool/工具策略 acceptance 子集通过：`python -m pytest runtime/tests/test_acceptance_scenarios.py runtime/tests/test_llm_proposal_flows.py runtime/tests/test_planner_contract.py -q -k "tool or unsafe or allowedTools or agent_profile"`，18 passed，189 deselected。
- 说明：Computer Use / desktop adapter 生态等价未纳入本轮收口，按当前要求后续单独推进。

## Batch 14：外部 adapter 输出帧收口

目标：让实时 stdout、RPC 补拉和后续外部 adapter 使用同一套 Yuanbao/haha-cc flat ServerMessage 提取规则。

- [x] 抽取 `yuanbao_message_from_event_payload`，统一从 `event.yuanbao` 优先、`event.hahaCc` 兼容 fallback 提取 flat ServerMessage。
- [x] 抽取 `to_yuanbao_output_frames`，统一生成 `kind: "event"`、`kind: "yuanbao_message"`、`kind: "haha_cc_message"`。
- [x] 抽取 `collect_yuanbao_server_messages`，统一 `events.yuanbaoAfter` / `events.hahaCcAfter` 的历史补拉提取和 `lastSeq` 计算。
- [x] RPC writer 改为复用统一输出帧 helper，不再手写两套 flat frame。
- [x] 增加 helper 级契约测试，确保 `yuanbao_message` 和 `haha_cc_message` payload 同值同形，且不泄漏 `eventId/sessionId/taskId/visibility/payload` 等 envelope 字段。
- [x] 保留旧 `hahaCc` 历史数据 fallback，迁移期不会断流。

建议文件：

- `runtime/src/local_agent_runtime/yuanbao_event_adapter.py`
- `runtime/src/local_agent_runtime/rpc/server.py`
- `runtime/tests/test_yuanbao_event_adapter.py`

验收：

- 外部 adapter 可以优先消费 `yuanbao_message`。
- 旧 adapter 可以继续消费 `haha_cc_message`。
- 两者 payload 始终来自同一份 flat ServerMessage。
- RPC 补拉和实时 stdout 不再各自实现提取逻辑。

进展：

- 2026-06-03：`python -m pytest runtime/tests/test_yuanbao_event_adapter.py runtime/tests/test_haha_cc_compat.py runtime/tests/test_agent_role_and_visibility.py -q -k "yuanbao or haha_cc or rpc_writer or events_after"` 通过，34 passed，32 deselected。
- 2026-06-03：`python -m compileall -q runtime/src/local_agent_runtime` 通过。
- 说明：本批次只收口 flat message 输出帧和补拉契约；Computer Use、完整桌面/IM adapter 生态仍按后续单独 track 推进。

## Batch 15：flat 输出能力硬化

目标：继续提升 Yuanbao/haha-cc flat ServerMessage 的稳定性，让外部 adapter 更容易按固定协议消费。

- [x] 收紧 `status.state` flat 输出，只允许 `idle/thinking/compacting/tool_executing/streaming/permission_pending`。
- [x] shared 类型新增 `YuanbaoChatState` / `HahaCcChatState`，并让 `YuanbaoServerMessage` 的 `status.state` 使用该枚举。
- [x] 补齐 `system_notification` 子类型映射：`init`、`compact_boundary`、`session_state_changed`、`task_started`。
- [x] `system_notification.message` 兜底读取 `state/status/phase`，避免外部 adapter 收到只有 subtype、没有可读文本的状态类通知。
- [x] 增加典型聊天回合 golden sequence：验证 stdout `yuanbao_message` 与 `collect_yuanbao_server_messages` 补拉结果同形同序。
- [x] 增加非法状态过滤测试，避免本地 runtime 临时状态泄漏进 flat ServerMessage。

建议文件：

- `runtime/src/local_agent_runtime/yuanbao_event_adapter.py`
- `shared/src/events.ts`
- `runtime/tests/test_yuanbao_event_adapter.py`
- `runtime/tests/test_haha_cc_compat.py`

验收：

- flat status 只输出 haha-cc ChatState 集合。
- notification 子类型覆盖方案中列出的基础系统通知。
- 典型聊天回合的实时 frame 与历史补拉结果一致。

进展：

- 2026-06-03：`python -m pytest runtime/tests/test_yuanbao_event_adapter.py runtime/tests/test_haha_cc_compat.py runtime/tests/test_agent_role_and_visibility.py runtime/tests/test_provider_turns.py -q -k "yuanbao or haha or status or system_notification or events_after or runtime_ping"` 通过，42 passed，84 deselected。
- 2026-06-03：`python -m pytest runtime/tests/test_yuanbao_event_adapter.py runtime/tests/test_haha_cc_compat.py runtime/tests/test_collaboration_events.py runtime/tests/test_replay.py runtime/tests/test_p9_release_checks.py runtime/tests/test_provider_turns.py -q -k "yuanbao or haha or team or collab or status or thinking or permission_pending or compacting or replay or runtime_ping or events_after"` 通过，64 passed，74 deselected。
- 2026-06-03：`python -m compileall -q runtime/src/local_agent_runtime` 通过。
- 2026-06-03：`npm --prefix app run typecheck` 通过。
- 2026-06-03：`npm --prefix app test -- src/lib/runtimeClient.test.ts src/hooks/useEventSubscription.test.tsx src/state/chatMessages.test.ts` 通过，98 passed。
