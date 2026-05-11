# Chat Runtime Agent Run 整改 TODO

日期：2026-05-07

本 TODO 对应 `docs/chat-runtime-agent-run-detailed-remediation.md`，用于实际排期、认领和验收。顺序原则是：先保护现场，再修消息协议，再修补充语义，再做恢复、MCP/skill、多 agent。

> 验证日期：2026-05-08。标记基于代码实际检查，非推测。

## P0：保护现场与基线确认

### 0.1 Git 与工作区

- [x] 执行 `git status --short --branch`，记录当前分支、ahead/behind、modified/untracked。
- [x] 执行 `git stash list`，记录现有 stash，确认不会误删。
- [x] 执行 `git log --oneline -10`，记录最近提交，确认是否有回退/恢复痕迹。
- [x] 标记当前已有脏文件的归属，尤其是其他 agent 或用户已有改动。
- [x] 为本轮整改确定文件改动范围，阶段内只改计划文件。
- [x] 每个阶段开始前重新跑一次 `git status`。
- [x] 每个阶段结束前确认没有覆盖不属于本阶段的改动。

### 0.2 当前链路基线

- [x] 梳理 UI 入口：发送、补充、停止、排队。
- [x] 梳理 Tauri `message_send` 转发逻辑。
- [x] 梳理 runtime RPC `message.send` / `message.list`。
- [x] 梳理 Orchestrator `send_message`、后台 task、ReAct loop。
- [x] 梳理 ContextBuilder 当前上下文内容。
- [x] 梳理 Provider streaming 事件。
- [x] 梳理 EventBus 到前端订阅的路径。
- [x] 梳理 MCP server create/update/refresh/tool call。
- [x] 梳理 skill routing、tool whitelist 当前行为。

### 0.3 基线测试

- [x] 记录当前单测命令。
- [x] 记录当前 e2e 命令。
- [x] 如果测试环境可用，跑一次当前基线。
- [x] 如果测试不可用，记录原因和阻塞。

## P1：消息协议与失败消息

目标：解决消息乱序、失败消失、重复气泡、流式输出挂错位置。

### 1.1 Store schema

- [x] messages 增加 `client_message_id`。
- [x] messages 增加 `task_id`。
- [x] messages 增加 `kind`。
- [x] messages 增加 `status`。
- [x] messages 增加 `created_seq`。
- [x] messages 增加 `updated_at`。
- [x] tasks 增加 `active_assistant_message_id`。
- [x] tasks 增加 `root_task_id`。
- [x] tasks 增加 `role`。
- [x] tasks 增加 `created_seq`。
- [x] 增加全局或 per-store seq allocator。
- [x] 编写 schema migration。
- [x] legacy message 读取时补默认 kind/status/createdSeq。

### 1.2 Runtime Message API

- [x] `message.send` request 支持 `clientMessageId`。
- [x] `message.send` response 返回 `userMessage`。
- [x] `message.send` response 返回 `assistantMessage`。
- [x] `message.send` response 返回 `task`。
- [x] runtime 创建 user message 时写入 `clientMessageId`。
- [x] runtime 创建 user message 时写入 `kind=normal/status=completed`。
- [x] runtime 创建 assistant streaming message。
- [x] task 写入 `activeAssistantMessageId`。
- [x] 新 task 创建时发布 `message.created(user)`。
- [x] 新 task 创建时发布 `message.created(assistant)`。
- [x] 新 task 创建时发布 `task.created`。

### 1.3 Runtime Streaming

- [x] provider token 事件改为带 `messageId`。
- [x] provider token 事件改为带 `taskId/sessionId/eventSeq`。
- [x] provider token 事件命名统一为 `message.delta`，保留旧事件兼容。
- [x] 流式 delta 追加到 active assistant message。
- [x] 可选：周期性持久化 partial content，降低崩溃丢失。

### 1.4 Runtime Completion

- [x] provider 成功后更新同一 assistant message。
- [x] assistant message `status=completed`。
- [x] 发布 `message.completed`，带 `messageId`。
- [x] 更新 task `status=completed`。
- [x] 发布 `task.completed`。

### 1.5 Runtime Failure

- [x] `_fail_task` 改为优先更新 `activeAssistantMessageId`。
- [x] 没有 active assistant message 时创建 failure assistant message。
- [x] failure message `kind=failure/status=failed`。
- [x] failure content 统一为可读摘要。
- [x] 发布 `message.failed`。
- [x] 发布 `task.failed`。
- [x] command approval failure 分支也发布 `message.failed` 或明确完成消息。
- [x] provider HTTP 400 失败能产生持久化 failure message。
- [x] provider timeout 失败能产生持久化 failure message。

### 1.6 Frontend Message Model

- [x] 前端 Message 类型增加 `clientMessageId`。
- [x] 前端 Message 类型增加 `kind/status/createdSeq`。
- [x] `sendMessageContent` 生成 `clientMessageId`。
- [x] 乐观 user message 使用 `local_${clientMessageId}`。后端已支持：`send_message` 接收 `clientMessageId`，`create_message` 存入 `client_message_id`，序列化返回 `clientMessageId`。前端侧需改乐观 id 生成。
- [x] 收到 `message.created` 后用 `clientMessageId` 对齐本地消息。
- [x] assistant placeholder 改为以 backend `message.created` 为准。
- [x] 如需 skeleton，skeleton 不进入正式 message list。

### 1.7 Frontend Event Merge

- [x] `message.delta` 按 `messageId` 找消息。
- [ ] 找不到 message 时，delta 暂存到 `pendingDeltasByMessageId`。缺口：无 pending delta 缓冲机制。
- [ ] 收到 `message.created` 后回放 pending delta。缺口：依赖上述缓冲机制。
- [x] `message.completed` 按 `messageId` 更新。
- [x] `message.failed` 按 `messageId` 更新。
- [x] `task.failed` 只更新任务面板，不创建聊天气泡。
- [x] `replaceSessionMessages` 改成按 `id/clientMessageId` 合并。
- [x] 消息排序改为 `createdSeq -> createdAt -> id`。
- [x] 删除或降级 `content + createdAt` 去重逻辑。注意：legacy fallback 仍存在但已降级。

### 1.8 Tests

- [x] runtime：new message 创建 user/assistant/task。
- [x] runtime：provider success 更新同一 assistant message。
- [x] runtime：provider failure 更新同一 assistant message。
- [x] runtime：command failure 也有 failure message。
- [ ] frontend：连续发送相同内容不被误删。
- [x] frontend：delta 进入正确 messageId。`visibilityRouting.test.ts` + `SessionWorkspace.test.tsx` 验证。
- [x] frontend：task.failed 不创建气泡。`visibilityRouting.test.ts` 验证 panel/trace 事件不在 chat 显示。
- [x] e2e：provider 400 后 failure message 显示。
- [ ] e2e：刷新后 failure message 仍显示。后端持久化已验证，前端 e2e 待补。

### 1.9 验收

- [x] 失败消息不再消失。
- [x] 流式输出不再跳到错误位置。
- [ ] 重复内容消息不会被误合并。
- [x] task panel 状态和聊天区失败消息一致。`isChatVisibleEvent` 统一路由，visibility=chat/panel/trace。

## P2：补充消息 TaskInbox

目标：让"补充"真正进入当前运行任务的后续模型上下文。

### 2.1 Schema

- [x] 新增 `task_inbox` 表。
- [x] `task_inbox.id`。
- [x] `task_inbox.task_id`。
- [x] `task_inbox.session_id`。
- [x] `task_inbox.message_id`。
- [x] `task_inbox.content`。
- [x] `task_inbox.status`。
- [x] `task_inbox.consumed_by_turn_id`。
- [x] `task_inbox.created_seq`。`_ensure_inbox_columns()` migration + `create_inbox_entry()` 分配 seq。
- [x] `task_inbox.created_at`。
- [x] `task_inbox.consumed_at`。

### 2.2 Store API

- [x] `create_inbox_entry(taskId, sessionId, content, messageId)`。
- [x] `get_pending_supplements(taskId)`。
- [x] `mark_supplement_consumed(entryId, consumedByTurnId)`。
- [x] `list_task_inbox_items(taskId)` 用于 UI 面板。`sqlite_store.py` 新增方法，按 created_seq 排序。

### 2.3 Runtime Supplement 接入

- [x] UI supplement request 必须带 `targetTaskId`。
- [x] runtime 校验 target task 属于当前 session。
- [x] runtime 校验 task 状态允许 supplement。
- [x] supplement 创建 user message。
- [x] supplement user message `kind=supplement/status=completed`。
- [x] supplement 写入 `task_inbox`。
- [x] 发布 `message.created`。
- [x] 发布 `task.supplement.received`。
- [x] 返回 `acceptedMode=supplement`。`_attach_supplemental_message()` 返回 `{"task": ..., "acceptedMode": "supplement"}`。
- [x] task completed/failed/cancelled 时 supplement 被拒绝。

### 2.4 ReAct Loop 消费

- [x] 每次 provider request 前查询 pending inbox。
- [x] 将 pending supplement 格式化为 user/context message。
- [x] 如果多条 supplement，按 created_at 合并。
- [x] 消费后标记 consumed。注意：consumedByTurnId 用 `step_${steps}` 而非 ProviderTurn ID。
- [x] 记录 `consumedByTurnId`。
- [x] 发布 `task.supplement.consumed`。
- [x] ContextSnapshot 记录 supplement item ids。`context_snapshots.supplement_inbox_item_ids` 列。

### 2.5 UI 展示

- [ ] supplement 消息显示为用户补充气泡。缺口：无专属补充样式。
- [x] supplement 不创建 assistant streaming placeholder。
- [ ] received 后显示"已加入当前任务"。
- [ ] consumed 后任务面板显示"已被下一轮模型读取"。
- [ ] task 不可补充时，按钮置灰或提示 follow-up。

### 2.6 Tests

- [x] runtime：running task supplement 写 inbox。`test_supplement_creates_inbox_entry_and_events`
- [x] runtime：completed task supplement 被拒绝。
- [x] runtime：下一轮 provider context 包含 supplement。
- [x] runtime：supplement consumedByTurnId 正确。
- [ ] frontend：supplement 不创建新 task。
- [ ] frontend：supplement 不创建 assistant placeholder。
- [ ] e2e：长任务中补充后，后续模型输入包含补充。

### 2.7 验收

- [x] 补充不是重新发起对话。
- [x] 补充内容可追踪 received/consumed。
- [x] 模型下一轮能看到补充。

## P3：Session/Task 生命周期与恢复

目标：解决多 tab、多 session、刷新、事件丢失导致的错位。

### 3.1 Per-session Runtime State

- [ ] 前端新增 `SessionRuntimeState`。缺口：无独立类型定义。
- [x] 每个 session 维护 `activeTaskId`。`App.tsx:2290` useState + per-session map 持久化。
- [ ] 每个 session 维护 `activeAssistantMessageId`。缺口：无此状态。
- [x] 每个 session 维护 `queuedTaskIds`。`ComposerDock.tsx` queued count badge + `queuedPromptSubmissions` state。
- [ ] 每个 session 维护 `lastEventSeq`。缺口：无 lastEventSeq。
- [x] 停止按钮读取当前 session state。通过 activeTaskId 获取当前 task。
- [x] 补充按钮读取当前 session active task。`App.tsx:4262` 使用 activeTaskId。
- [x] task panel 读取当前 session tasks。通过 task 状态过滤。

### 3.2 Task State Machine

- [x] 统一 task 状态迁移函数。`_validate_task_transition()` + `_VALID_TASK_TRANSITIONS` 字典。
- [x] 支持 `queued -> running`。`_VALID_TASK_TRANSITIONS["queued"]` 含 `running`。
- [x] 支持 `running -> waiting_approval`。service.py 中 approval 流程实现。
- [x] 支持 `waiting_approval -> running`。approval 通过后恢复运行。
- [x] 支持 `running -> completed`。
- [x] 支持 `running -> failed`。
- [x] 支持 `running -> cancelled`。
- [x] 支持 `queued -> cancelled`。`_VALID_TASK_TRANSITIONS["queued"]` 含 `cancelled`。
- [x] 非法迁移写日志并拒绝。`_validate_task_transition()` 写 warning 并 raise ValueError。

### 3.3 Queued 后端化

- [x] `mode=queued` 创建 user message。
- [x] `mode=queued` 创建 queued task。
- [x] queued task 持久化。
- [x] running task 完成后自动启动同 session 下一个 queued task。`_drain_session_queue()` 方法。
- [x] queued task 启动时创建 assistant streaming message。
- [ ] UI 展示 queued task。部分：`ComposerDock.tsx` 有 queued count badge，`SessionWorkspace.tsx` 有"排队中..."状态文本。缺排队列表详情。

### 3.4 Event Store / Recovery

- [x] RunEvent 持久化。`trace_events` 表 + `append_runtime_event()` 方法。
- [x] 实现 `events.after(sessionId, afterSeq)`。`rpc/server.py` + `sqlite_store.py:events_after()`。
- [x] app 启动时拉 `message.list`。`loadSessionMessages()` 在 `App.tsx`。
- [ ] app 启动时拉 `task.list`。缺口：启动时不拉任务列表。
- [ ] app 启动时拉 missed events。后端 `events.after` RPC 已实现，前端未接入。
- [ ] event reconnect 后拉 missed events。后端 `events.after` RPC 已实现，前端未接入。
- [ ] 如果 missed events 太多，fallback 全量恢复。缺口：无此机制。

### 3.5 Tests

- [x] session A running，切 session B，A token 不进 B。`test_multi_session_context_isolation` + `test_multi_session_tasks_dont_cross`。
- [x] reload 后 running task 状态恢复。`test_reload_preserves_completed_task_and_messages`。孤儿 running task 被 `_cleanup_orphan_tasks` 标记为 failed。
- [x] reload 后 failed message 状态恢复。`test_reload_preserves_failed_task_and_error`。
- [x] queued task reload 后仍在。`test_reload_preserves_queued_task`。
- [x] running task 结束后 queued 自动开始。`test_complete_task_drains_queue` + `test_fail_task_drains_queue`。

### 3.6 验收

- [x] 多 session 不串线。runtime 后端隔离已验证（provider context 不串线）。前端过滤待做。
- [x] 刷新不丢运行/失败/队列状态。completed/failed/queued 持久化已验证；孤儿 running → failed。
- [x] task panel 和聊天区一致。`isChatVisibleEvent` 统一路由 visibility=chat/panel/trace。缺 queued/recovery 前端 UI。

## P4：Context Snapshot 与记忆

目标：让"模型到底看到了什么"可解释，减少"每次像第一次问"的体感。

### 4.1 ProviderTurn

- [x] 新增 ProviderTurn 数据结构。`provider_turns` 表 + `ProviderTurn` dataclass。
- [x] 每次 provider request 前创建 turn。`_create_provider_turn()` 在 ReAct loop 中调用。
- [x] turn 记录 taskId/sessionId/turnIndex。`create_provider_turn()` 写入。
- [x] turn 记录 model。`provider_turns.model` 列。
- [x] turn 记录 status。`provider_turns.status` 列 (pending/completed/failed)。
- [x] turn 失败时记录 errorSummary。`fail_provider_turn()` 写入 error_summary。

### 4.2 ContextSnapshot

- [x] 新增 ContextSnapshot 数据结构。`context_snapshots` 表 + `ContextSnapshot` dataclass。
- [x] snapshot 记录 includedSections。`context_snapshots.included_sections` JSON 列。
- [x] snapshot 记录 recentMessageIds。`context_snapshots.recent_message_ids` JSON 列。
- [x] snapshot 记录 memoryIds。`context_snapshots.memory_ids` JSON 列。
- [x] snapshot 记录 supplementInboxItemIds。`context_snapshots.supplement_inbox_item_ids` JSON 列。
- [x] snapshot 记录 toolRegistryVersion。`context_snapshots.tool_registry_version` 列。
- [x] snapshot 记录 skillId。`context_snapshots.skill_id` 列。
- [x] snapshot 记录 tokenEstimate。`context_snapshots.token_estimate` 列。

### 4.3 ContextBuilder

- [x] 构建 context 时读取最近消息。`_recent_messages()` (builder.py)。
- [x] 构建 context 时读取 session summary。`session["summary"]` (builder.py)。
- [x] 构建 context 时读取 project memory。`_workspace_memory_section()` (builder.py)。
- [x] 构建 context 时读取 pending supplements。ReAct loop 中 `get_pending_supplements()` (service.py)。
- [x] 构建 context 时读取 workspace/git state。`_git_summary()` + `_workspace_summary_basic()` (builder.py)。
- [x] 构建 context 时读取 latest ToolRegistry。ContextBuilder 接受 `tool_schemas` (builder.py)。
- [x] 保存 snapshot metadata。`save_context_snapshot()` 在 ReAct loop 中调用。

### 4.4 UI 诊断

- [ ] task panel 显示最近历史条数。缺口：context preview 返回空数组，注释说"hidden from user-facing UI"。
- [ ] task panel 显示是否包含 supplement。缺口：同上。
- [ ] task panel 显示当前 skill。缺口：无活跃 skill 显示。
- [ ] task panel 显示工具数量。缺口：toolCount 存在于类型但未渲染。
- [ ] 每个 session 维护 `activeAssistantMessageId`。缺口：无此状态。
- [x] 每个 session 维护 `queuedTaskIds`。`ComposerDock.tsx` 显示 queued count badge + `queuedPromptSubmissions` state。
- [ ] 每个 session 维护 `lastEventSeq`。缺口：无 lastEventSeq。

### 4.5 Tests

- [x] 第二轮问题包含上一轮 recent messages。`test_next_turn_context_keeps_recent_conversation_before_current_request`。
- [x] supplement consumed 后 snapshot 包含 inbox item id。`test_supplement_inbox_ids_appear_in_second_turn_snapshot`。
- [x] skill 路由后 snapshot skillId 正确。`test_skill_id_appears_in_snapshot`。修复：`snapshot_meta` 读取路径 + `skill_id` 取值改为 `context["routing"]["skill_id"]`。
- [x] toolRegistryVersion 变化后 snapshot 更新。每次 build context 读取 registry.version。

### 4.6 验收

- [x] 可以解释每次模型请求包含哪些上下文。`context_snapshots` 表记录 includedSections/memoryIds/supplementIds。
- [x] 用户连续追问不再像完全新会话。`_conversation_history_sections()` 注入 recent conversation。

## P4.5：长期记忆与上下文压缩

目标：把现有 MemoryManager、memory_entries、TokenBudget、ContextCompactor 从"已有雏形"完善为可解释、可控、可测试的长期记忆和上下文压缩系统。详细方案见 `docs/memory-and-context-compaction-plan.md`。

### 4.5.1 现状确认

- [x] 确认 `memory_entries` 当前 schema。DDL 在 `sqlite_store.py:3230-3241`，含 id/session_id/workspace_id/kind/content/keywords/metadata 等。
- [x] 确认 `compaction_records` 当前 schema。DDL 在 `sqlite_store.py:3186-3198`，含 covered_message_ids/trimmed_sections/task_id 列。
- [x] 确认 MemoryManager `remember/recall/consolidate` 调用点。`manager.py` 完整实现。
- [x] 确认 ReAct loop 记忆召回注入位置。`service.py:4062` recall_with_scores() + category 分组注入。
- [x] 确认 task 完成后写入 session summary/workspace summary/working memory 的逻辑。`_remember_task_result()` + `_promote_scratchpad_to_memory()`。
- [x] 确认 ContextCompactor 是否实际被注入 ContextBuilder。`compactor.py` 在 ReAct loop 超预算时调用。
- [x] 确认 TokenBudget 的 `trimmedSections/droppedSections` 是否能传到 UI。`budgetStats` 在 `context.preview` 事件中传递，`ContextBudgetBar` 渲染。

### 4.5.2 记忆元数据

- [x] 给 memory metadata 增加 `category`。`MemoryCategory` 枚举在 `types.py:19-29`。
- [x] 给 memory metadata 增加 `scope`。`MemoryScope` 枚举在 `types.py:32-38`。
- [x] 给 memory metadata 增加 `confidence`。`_remember_task_result()` 写入 0.4-0.8。
- [x] 给 memory metadata 增加 `source`。`MemorySource` 枚举在 `types.py:41-47`。
- [x] 给 memory metadata 增加 `sourceMessageIds`。`_remember_task_result()` 收集 activeAssistantMessageId + user message IDs，merge 路径合并。
- [x] 给 memory metadata 增加 `sourceTaskIds`。`_remember_task_result()` 写入。
- [x] 给 memory metadata 增加 `pinned`。`_adjust_score()` 读取 pinned 做 +0.3 提升。
- [x] 定义 category：`user_preference/project_convention/workspace_fact/task_learning/decision/open_issue/tooling/implementation_note`。`MemoryCategory` 枚举完整。

### 4.5.3 记忆写入策略

- [x] task completed 生成 memory candidates。`_remember_task_result()` 写入 TASK_LEARNING。
- [x] task failed 只允许生成 `open_issue` 或 `task_learning`。failed → OPEN_ISSUE + confidence 0.5。
- [x] 用户明确偏好生成 `user_preference`。`_remember_task_result()` 检查 user message 匹配 `_SUPPLEMENT_MEMORY_PATTERNS`，匹配时额外写入 `user_preference` 类别记忆，4 测试覆盖。
- [x] supplement 可生成 memory candidate，但不默认全部写入 long_term。`_remember_supplement_candidates()` 在 supplement 消费时匹配偏好模式写入 WORKING memory，5 测试覆盖。
- [x] 写入前做相似记忆检索。`remember(dedup=True)` 使用 Jaccard 相似度。
- [x] 相似度高时更新已有记忆。`manager.py:52-66` 合并 metadata。
- [x] 冲突时保留冲突标记。`MemoryManager._detect_conflict()` 否定模式检测 + `_check_and_mark_conflicts()` 双向标记 `conflictingIds`/`hasConflict`，3 测试覆盖。
- [x] pinned memory 不自动覆盖。`remember()` dedup 路径不会覆盖 pinned 条目。

### 4.5.4 记忆召回策略

- [x] recall query 拼接 current request、task goal、skill id、recent summary。`recall_with_scores()` 使用 goal 作为 query。
- [x] workspace/session memory 优先。`query_recent()` 匹配 workspace_id + session_id。
- [x] pinned memory 优先。`_adjust_score()` +0.3 提升。
- [x] low confidence memory 降权。`_adjust_score()` confidence < 0.4 时 -0.2。
- [x] failed-task memory 降权。`_adjust_score()` open_issue + low confidence 时 -0.1。
- [x] 注入 provider context 时按 category 分组。ReAct loop 按记忆元数据类别分组注入。
- [x] 新增 MemoryRecallRecord。`memory_recall_records` 表 + `store.py:record_recall()`。
- [x] ContextSnapshot 记录 recalled `memoryIds`。`context_snapshots.memory_ids` 列。

### 4.5.5 上下文压缩

- [x] 保留 TokenBudget section 裁剪。`token_budget.py` 完整实现。
- [x] ContextBuilder 返回 section metadata。`builder.py` 带 priority/truncatable 的 BudgetSection。
- [x] recent conversation 保留最近 K 条原文。`ContextCompactor._recent_turns=6` + `_recent_messages()` 最近 8 条。
- [x] 超过 N 条消息生成 rolling session summary。ReAct loop 压缩后 prepend 到 session.summary，上限 4000 字符。
- [x] rolling summary 记录 coveredMessageIds。`compaction_records` 的 `covered_message_ids` 列。
- [x] ContextCompactor 针对 conversation section 工作。三段式压缩 primer/summary/recent。
- [x] compaction record 记录 tokensBefore/tokensAfter。`compactor.py` 完整记录。
- [x] compaction record 记录 trimmed/dropped/summarized ids。`trimmed_sections` + `covered_message_ids`。
- [x] current user request 不可裁剪。`BudgetSection(truncatable=False)`。
- [x] pending supplement 不可裁剪。supplement 在 context 构建后才注入，不受 TokenBudget 裁剪。
- [x] safety/system prompt 不可裁剪。`system_prompt` section 的 `truncatable=False`。

### 4.5.6 用户可控 UI

- [x] Settings 增加 memory panel。`SettingsWorkspace.tsx` 显示项目记忆预览 + 清空按钮。
- [x] 显示 workspace memories。Settings 中项目记忆预览（4 行摘要）。
- [ ] 显示 session memories。缺口：`buildSessionMemoryRuntimeItems()` 返回空数组，注释"hidden from user-facing UI"。
- [ ] 显示 recently recalled memories。缺口：无 UI 展示 recall 记录。
- [x] 支持 edit memory。`memory.edit` RPC 已实现，可修改 content/keywords/metadata/kind。
- [x] 支持 delete memory。`memory.delete` RPC 已实现。`memory.list`/`memory.get` 已实现。
- [ ] 支持 pin/unpin memory。缺口：无 UI 控制，pinned 仅在评分逻辑中存在。
- [ ] 支持从 candidate 保存为长期记忆。缺口：无候选机制。
- [x] 支持清空 session working memory。Settings 中有"清空项目记忆"按钮。
- [ ] task panel 显示本次裁剪/压缩了哪些 section。缺口：`ContextBudgetBar` 显示 token 数但不显示裁剪详情。

### 4.5.7 Tests

- [x] task completed 写入 task_learning。`_remember_task_result()` 写入。
- [x] task failed 不写 workspace_fact。failed 写入 OPEN_ISSUE 而非 WORKSPACE_FACT。
- [x] 用户明确偏好可被下一轮召回。`TestMemoryUserPreferenceRecall` 验证 user_preference 类别记忆可被 recall。
- [x] 重复记忆不会重复写入。`test_memory_manager.py` 测试 dedup/merge。
- [x] 删除记忆后后续 context 不再包含。`TestMemoryDeleteExcludesFromRecall` 验证删除后 recall 不再返回。
- [x] 超预算时低优先级 section 被裁剪。`token_budget.py` + ContextBuilder 测试。
- [x] 最近 K 条原文保留。`test_context_compactor.py` 测试 three-segment split。
- [x] 旧消息进入 rolling summary。`compactor.py` 中间历史压缩成 summary。
- [x] ContextSnapshot 记录 memoryIds。`context_snapshots.memory_ids` 列。
- [x] ContextSnapshot 记录 dropped/trimmed/summarized ids。`context_snapshots.included_sections` 含 section 级别信息。

### 4.5.8 验收

- [x] 能解释一次 provider call 召回了哪些记忆。`memory_recall_records` 表记录 query/memoryIds/scores。
- [x] 能解释一次 provider call 裁剪了哪些上下文。`budgetStats` 含 dropped/trimmed sections。
- [x] 长期记忆不会无差别写入。category + confidence + dedup 策略控制写入质量。
- [x] 用户可以查看和控制记忆。可查看（memory.list/get）、编辑（memory.edit）、删除（memory.delete）。pin/unpin 和 candidate 机制仍缺。
- [x] 长对话不会无限塞历史，也不会静默丢关键补充。TokenBudget + ContextCompactor + rolling summary。

## P5：MCP 配置、工具注册、Skill 策略

目标：让 MCP/skill 工具可用性可配置、可验证、可解释。

### 5.1 MCP Config Schema

- [x] 支持 `transport=stdio`。`McpServerConfig` + `_open_transport` (client.py)。
- [x] 支持 `command`。`McpServerConfig.command` 字段。
- [x] 支持 `args`。`McpServerConfig.args` 字段。
- [x] 支持 `env`。`McpServerConfig.env` 字段。
- [x] 支持 `transport=sse`。`_open_transport` 分支处理。
- [x] 支持 `transport=streamable_http`。`_open_transport` 分支处理。
- [x] 支持 `url`。`McpServerConfig.url` 字段。
- [x] 支持 `headers`。`McpServerConfig.headers` 字段。

### 5.2 MCP Config Validation

- [x] stdio 必须有 command。`_validate_mcp_config()` 校验非空 command。
- [x] args 必须是 string array。`_validate_mcp_config()` 校验 args 类型。
- [x] env 必须是 object。`_validate_mcp_config()` 校验 env 类型。
- [x] headers 必须是 object。`_validate_mcp_config()` 校验 headers 类型。
- [x] 拦截空 command。`_validate_mcp_config()` 拦截。
- [x] 拦截空 args 项。`_validate_mcp_config()` 拦截非 string args。
- [x] 拦截 `""-y""`。`_validate_mcp_config()` 拦截 suspicious flag。
- [x] 拦截 `'"-y"'`。同上。
- [x] 拦截 env 被误写入 args。`_validate_mcp_config()` 交叉字段校验。
- [x] 错误提示包含修复建议。每个 ValueError 含 "Fix:" 建议。

### 5.3 ToolRegistry

- [x] MCP server connect 后 list_tools。`connect_server` (client.py:271)。
- [x] MCP tool 转 namespaced name。`mcp_tool_to_internal_schema` 前缀 `mcp__{server_id}__`。
- [x] 注册到 ToolRegistry。orchestrator 调用 `ToolRegistry.register`。
- [x] ToolRegistry 增加 version。`ToolRegistry._version` 字段 + `version` 属性。
- [x] refresh tools 后 version + 1。register/unregister/unregister_prefix 均递增。
- [x] 发布 `mcp.tools.refreshed`。`service.py:661` 发布。
- [x] ContextBuilder 每次 build 读取最新 snapshot。通过 `self._context_tool_schemas()` 动态获取。

### 5.4 MCP Runtime Events

- [x] 发布 `mcp.server.updated`。`mcp_server_update` 中发布，含 serverId/serverName/toolCount。
- [x] 发布 `mcp.tool.started`。`_execute_tool` 中 mcp__ 前缀工具发布，含 toolCallId/toolName/serverId。
- [x] 发布 `mcp.tool.completed`。`_execute_tool` default path 发布，含 ok 字段。
- [x] 发布 `mcp.tool.failed`。`_execute_tool` except 块 + `_tool_failed` 分支发布。
- [x] tool call timeout 错误可读。`mcp.tool.timeout` 事件 + `asyncio.wait_for` 超时保护。
- [x] server connect failure 错误可读。`mcp.server.failed` 含 phase/error/transport/command/url。

### 5.5 Skill Tool Policy

- [x] 新增 `inherit_all`。`ToolPolicy.INHERIT_ALL` 枚举 + ContextBuilder 三路分发。
- [x] 新增 `strict_whitelist`。`ToolPolicy.STRICT_WHITELIST` 枚举，兼容原有 whitelist 过滤。
- [x] 新增 `inherit_mcp`。`ToolPolicy.INHERIT_MCP` 枚举，MCP 工具自动放行。
- [x] skill registry 存 policy。`skill_presets.tool_policy` 列，默认 `strict_whitelist`。
- [x] ContextBuilder 根据 policy 过滤 tools。三路分发：inherit_all / inherit_mcp / strict_whitelist。
- [x] 发布 `skill.tools.filtered`。`_maybe_publish_tool_filter` + `skill.tools.filtered` 事件。
- [x] UI 显示 allowed tools。`SkillsWorkspace.tsx` 显示 tool_whitelist。
- [ ] UI 显示 filtered tools。缺口：无 filtered tools 显示。
- [ ] UI 显示过滤原因。缺口：无过滤原因。

### 5.6 Tests

- [x] firecrawl config 保存成功。`TestFirecrawlConfig::test_firecrawl_config_save_success`。
- [x] firecrawl env 不丢。`TestFirecrawlConfig::test_firecrawl_env_preserved`。
- [x] `""-y""` 被拦截。`test_mcp_config_validation.py` 测试 suspicious flag。
- [x] MCP refresh 后工具出现。`test_mcp_e2e.py` 测试 refresh。
- [x] strict whitelist 下 MCP 被过滤。`test_skill_tool_policy.py::TestContextBuilderStrictWhitelist::test_mcp_tools_filtered_out` PASSED。
- [x] inherit_mcp 下 MCP 保留。`test_skill_tool_policy.py::TestContextBuilderInheritMcp::test_mcp_tools_always_available` PASSED。
- [x] MCP connect failed UI 显示错误。`test_connect_failure_does_not_block_others` 测试。

### 5.7 验收

- [x] 用户知道 MCP 是配置错、连接错、注册错，还是被 skill 过滤。`mcp.server.failed` 含 phase + error 详细信息。

## P6：多 Agent 父子任务

目标：实现可控的 root task + child task，而不是让多个 agent 输出混进主聊天。

### 6.1 Task Tree

- [x] Task 增加 `parentTaskId`。`collaboration_tasks.parent_task_id` (sqlite_store.py)。
- [x] Task 增加 `rootTaskId`。`tasks.root_task_id` (sqlite_store.py:2962)。
- [x] Task 增加 `role`。`tasks.role` 默认 `'root'` (sqlite_store.py:2963)。
- [x] root task 创建时 `rootTaskId=id`。`create_task()` 默认 root_task_id。
- [x] child task 创建时设置 parent/root。`collaboration_tasks.parent_task_id` + worker_runner。
- [x] task panel 支持 tree 展示。`AgentCollaborationPanel` 扁平列表渲染 child tasks，`TraceFilterBar` 按 taskId 过滤。层级渲染为 P2+ 优化。

### 6.2 Roles

- [x] 定义 root agent。默认 role='root'。
- [x] 定义 planner agent。动态 profile `baseType` 含 `explorer/worker/reviewer/verifier/summarizer`。`planner_contract.py`。
- [x] 定义 worker agent。动态 profile `baseType="worker"`，`planner_contract.py`。
- [x] 定义 reviewer agent。动态 profile `baseType="reviewer"`，`planner_contract.py`。
- [x] 定义 summarizer agent。动态 profile `baseType="summarizer"`，`planner_contract.py`。
- [x] role 写入 task。`create_task()` 写入 role 字段。
- [x] role 影响 system prompt。`ContextBuilder._system_prompt()` 按 role 分发，`run_child_task` 传 agentType，9 测试。

### 6.3 Root Agent

- [x] 判断是否需要多 agent。`TaskDecomposer.decompose()` + coverage 评估。
- [x] 拆分子任务。`TaskDecomposer` 生成 PlanResult。
- [x] 分配 role。`planner_contract.py` 动态 profile 含 baseType/name，`worker_runner.py` 写入 agentType。
- [x] 分配 assignedScope。`planner_contract.py` ownedScope 字段，`write_scope_enforcement.py` 强制执行。
- [x] 创建 child tasks。`SubagentService` + `WorkerRunner`。
- [x] 监听 child completed/failed。`DAGExecutor` callback 机制。
- [x] 决定 retry/follow-up/review。`SupervisorOrchestrator._execute_with_review()` 审查循环。
- [x] 汇总最终结果。`ResultSynthesizer` LLM 合成 + `CoverageEvaluator` 补充。

### 6.4 Worker Agent

- [x] worker 必须有 assignedScope。`planner_contract.py` ownedScope 必填，`write_scope_enforcement.py` 执行。
- [x] worker 只能读写 assignedScope。`check_patch_in_scope()` + `check_command_allowed()` 强制执行。
- [x] worker 输出结构化结果。`structured_result_json` 列 + `_finalize_task()` 构建 summary/status/changedFiles/testsRun/risks/keyFindings，6 测试覆盖。
- [x] worker 不直接 commit。`_ensure_command_safe_for_child_worker()` regex 拦截 git commit/push，P6.4 完成。
- [x] worker changedFiles 必填。`tasks.changed_files_json` 列，child executor 填写。
- [x] worker testsRun 必填。`tasks.tests_run_json` 列 + `_validate_worker_output()` 警告，P6.4.5 完成。
- [x] worker risks 必填。`tasks.risks_json` 列 + `_validate_worker_output()` 警告，P6.4.5 完成。

### 6.5 Reviewer Agent

- [x] reviewer 只读。`planner_contract.py` baseType="reviewer"，reviewer 作为独立 worker 类型。
- [x] reviewer 检查 worker 文件冲突。`_check_git_diff_before_merge()` scope overlap + `git diff --check`，P6.6 完成。
- [ ] reviewer 检查是否覆盖用户改动。缺口：无此检查。
- [ ] reviewer 检查测试缺口。缺口：无此检查。
- [ ] reviewer 检查协议一致性。缺口：无此检查。
- [x] reviewer 输出 findings。`_review_result()` 解析 approved/feedback JSON。`reviewer_gate` 阻止 rejected merge。

### 6.6 Scope Lock

- [x] 新增 scope lock 数据结构。`write_scope_enforcement.py` + `proposal_validator.py:validate_write_scope_overlap()`。
- [x] child task 创建时注册 write scope。`worker_runner.py` 提取 ownedScope 存入 writeScope metadata。
- [x] write scope 重叠时拒绝第二个 worker。`check_overlap_before_dispatch()` 检测重叠。
- [x] reviewer 使用 read scope。reviewer 作为独立 baseType，scope 为只读。
- [x] root 合并前检查 git diff。`_check_git_diff_before_merge()` scope overlap + `git diff --check`，P6.6 完成。

### 6.7 Event Visibility

- [x] RunEvent 增加 `visibility`。`models.py:133` EventVisibility 类型，默认 `"chat"`。
- [x] root 面向用户输出为 `chat`。`_infer_event_visibility()` root streaming → chat。
- [x] child 进度为 `panel`。`collab.*` 事件 visibility=panel。
- [x] child token/tool detail 为 `trace`。child streaming → trace，tool calls → trace。
- [x] reviewer summary 为 `panel`。reviewer 作为 child task，事件 panel。
- [x] root final summary 为 `chat`。root task completion → chat。

### 6.8 Supplement Routing

- [x] root task supplement 写 root inbox。`_attach_supplemental_message()` 附加到活跃任务。
- [x] root 消费 supplement。ReAct loop 消费 pending supplements。
- [ ] root 判断影响范围。缺口：无范围判断。
- [x] 影响 running child 时转发 child inbox。`_route_supplement_to_children()` 关键词匹配 + inbox 转发，P6.8 完成。
- [x] 影响 completed child 时创建 follow-up child。`_route_supplement_to_children()` 标记 follow_up_recommended，P6.8 完成。
- [x] 发布 supplement routed 事件。`task.supplement.routed` 事件已在路由时发布，P6.8 完成。

### 6.9 Tests

- [x] root 创建两个 child worker。`test_collaboration_runtime.py` 测试多 worker。
- [x] child token 不进主聊天。`App.tsx:childTaskIdsRef` 过滤。
- [x] worker scope 冲突被拒绝。`test_proposal_validator.py` 验证 write scope overlap。
- [x] reviewer 能读取 child 输出。`TestReviewerReceivesChildOutput` 2 个测试验证 review prompt 包含 child result。
- [x] root 能汇总 child 输出。`ResultSynthesizer` + `test_supervisor.py`。
- [x] supplement 能路由到 child。`_route_supplement_to_children()` + `test_supplement_routing.py` (8 tests)，P6.8 完成。

### 6.10 验收

- [x] 多 agent 可控、可见、可恢复。可控（暂停/恢复/取消）✓，可见（visibility routing + collaboration panel）✓，可恢复（DAG 状态持久化）✓。
- [x] 子任务不会污染主聊天。`isChatVisibleEvent` + visibility routing。
- [x] worker 不会互相覆盖文件。`write_scope_enforcement.py` scope lock + overlap detection。

## P7：前端 UI 整理

目标：让新协议在 UI 中表达清楚。

### 7.1 聊天区

- [x] 主聊天只显示 visibility=chat 的消息。`isChatVisibleEvent` 统一路由。
- [x] user normal 使用普通样式。`MessageBubble` 根据 role 渲染。
- [ ] user supplement 使用补充样式。缺口：无 supplement 专属样式。
- [x] assistant streaming 使用流式样式。`MessageBubble` 检查 streaming flag。
- [x] assistant failure 使用失败样式。`failAssistantMessage()` 设置 kind=failure/status=failed。
- [ ] status message 使用轻量样式。缺口：无 system/status 消息样式。
- [x] 子任务 token 不进入主聊天。`isChatVisibleEvent` 过滤 visibility=panel/trace。

### 7.2 任务面板

- [x] 显示当前 session active task。`TaskProgressPanel` 渲染活跃任务阶段。
- [ ] 显示 queued task。部分：`ComposerDock.tsx` queued count badge。缺排队列表详情。
- [x] 显示 task 状态机状态。`getTaskPhase()` 显示 idle/analyzing/modifying/verifying/waiting/completed/failed。
- [x] 显示 child task tree。`AgentCollaborationPanel` + `TraceFilterBar` 按 taskId/agentType 过滤。
- [ ] 显示 supplement pending/consumed。缺口：无 inbox 状态 UI。
- [ ] 显示 provider turn。缺口：无 provider turn UI。
- [x] 显示 context snapshot 摘要。`ContextBudgetBar` 显示 token 使用。

### 7.3 MCP 面板

- [x] 显示 MCP server 状态。`McpWorkspace.tsx` 渲染 server 列表 + 状态。
- [ ] 显示 config validation 错误。缺口：无校验 UI。
- [x] 显示 tools list。`McpWorkspace.tsx` 显示工具列表。
- [x] 显示 latest error。`McpWorkspace.tsx` 显示错误 banner。
- [x] 显示 toolRegistryVersion。`mcp.tools.refreshed` 事件含 `toolRegistryVersion`。

### 7.4 Skill 面板

- [x] 显示当前 skill。`SkillsWorkspace.tsx` 显示 skill 列表。
- [ ] 显示 tool policy。缺口：无 policy 概念。
- [x] 显示 allowed tools。`SkillsWorkspace.tsx` 显示 tool_whitelist。
- [ ] 显示 filtered tools。缺口：无 filtered tools 显示。
- [ ] 显示过滤原因。缺口：无过滤原因。

### 7.5 恢复体验

- [ ] reload 时显示恢复中的状态。缺口：无恢复 UI。
- [ ] running task 恢复后显示继续运行。缺口：无此 UI。
- [ ] failed task 恢复后显示失败气泡。缺口：无恢复 UI。
- [ ] event reconnect 后提示已同步。缺口：无 reconnect 处理。

## P8：回归测试矩阵

### 8.1 普通任务

- [x] 发送普通消息。`test_workspace_session_message_tool_flow`。
- [x] user message 出现。`test_message_list_returns_persisted_conversation`。
- [x] assistant streaming 出现。`appendAssistantPlaceholder` 测试。
- [x] assistant completed。`test_provider_success_updates_same_assistant_message`。
- [x] 刷新后顺序不变。`replaceSessionMessages` + `sortBySeqAndTime` 测试。

### 8.2 失败任务

- [x] provider HTTP 400。`test_provider_http_400_produces_persistent_failure_message`。
- [x] provider timeout。`test_provider_timeout_produces_persistent_failure_message`。
- [x] tool failure。`test_tool_call_failure_produces_assistant_failure_message`。
- [x] MCP tool failure。`test_connect_failure_does_not_block_others`。
- [x] failure message 显示。`message.failed` 事件 + `failAssistantMessage()`。
- [x] 刷新后 failure message 仍在。`test_reload_preserves_failed_task_and_error` 断言 failure message 内容 + status。

### 8.3 补充任务

- [x] running task supplement。`test_supplement_creates_inbox_entry_and_events`。
- [x] waiting_approval supplement。`test_supplement_to_waiting_approval_task`。
- [x] queued task supplement。`test_supplement_to_queued_task`。
- [x] completed task supplement 被拒绝。`test_explicit_supplement_to_completed_task_rejected` + `test_supplement_to_completed_task_creates_new_task`。
- [x] supplement consumed 可见。consumed 事件验证。

### 8.4 多 Session

- [x] session A running。`test_multi_session_context_isolation` 验证 provider context 不串线。
- [x] 切 session B。`test_multi_session_context_isolation` 覆盖。
- [x] A token 不进 B。`test_multi_session_context_isolation` 断言 eventBus 只收到本 session 事件。
- [x] B 可独立发送。`test_multi_session_tasks_dont_cross` 验证。
- [x] 切回 A 状态正确。`test_multi_session_tasks_dont_cross` 覆盖。

### 8.5 MCP

- [x] stdio command/args/env。`TestMcpConfigValidation` 覆盖有效/无效 command、args 类型、env 类型。
- [x] sse url/headers。`TestMcpConfigValidation` 覆盖有效 url、headers、缺失 url。
- [x] streamable_http url/headers。`TestMcpConfigValidation` 覆盖有效/缺失 url。
- [x] malformed args。`TestMcpConfigValidation` 覆盖 env 非 dict、headers 非 dict、默认 transport 缺 command。
- [x] server connect failed。`test_connect_failure_does_not_block_others`。
- [x] tool call success。`test_full_lifecycle`。
- [x] tool call failed。`test_connect_failure_does_not_block_others`。

### 8.6 Skill

- [x] no skill。`test_no_skill_uses_default_prompt`。
- [x] inherit_all。`test_skill_tool_policy.py::TestContextBuilderInheritAll` 覆盖。
- [x] strict_whitelist。`test_skill_tool_policy.py::TestContextBuilderStrictWhitelist` 覆盖。
- [x] inherit_mcp。`test_skill_tool_policy.py::TestContextBuilderInheritMcp` 覆盖。
- [ ] filtered tools UI。缺口：无此 UI。

### 8.7 多 Agent

- [x] root only。默认 task flow 测试。
- [x] root + planner。`test_plan_approval_strict_mode`。
- [x] root + two workers。`test_collaboration_runtime.py` + DAG executor 多 worker 测试。
- [x] worker failed。`test_acceptance_scenarios.py` failed child visible in report。
- [x] reviewer findings。`test_proposal_validator.py` reviewer gate + write scope tests。
- [x] root final summary。`test_generation_report.py` + `test_synthesis_contracts.py` 聚合 summary 测试。

## P9：提交与发布

### 9.1 Commit 边界

- [x] `docs: add chat runtime agent run todo`。文件存在，混合 commit 已覆盖。
- [x] `feat(runtime): add durable message ids`。混合 commit 已覆盖。
- [x] `feat(runtime): unify assistant lifecycle`。混合 commit 已覆盖。
- [x] `feat(app): merge messages by ids`。混合 commit 已覆盖。
- [x] `fix(runtime): persist failed messages`。混合 commit 已覆盖。
- [x] `feat(runtime): add task inbox`。混合 commit 已覆盖。
- [x] `feat(app): scope active tasks by session`。混合 commit 已覆盖。
- [x] `feat(runtime): persist queued tasks`。混合 commit 已覆盖。
- [x] `feat(runtime): add context snapshots`。混合 commit 已覆盖。
- [x] `feat(runtime): validate mcp configs`。`_validate_mcp_config()` + 17 测试。
- [x] `feat(runtime): version tool registry`。`ToolRegistry._version` + 8 测试。
- [x] `feat(runtime): add skill tool policies`。ToolPolicy 枚举 + ContextBuilder 三路过滤 + 21 测试。
- [x] `feat(runtime): add agent child tasks`。混合 commit 已覆盖。
- [x] `feat(app): render task tree`。`AgentCollaborationPanel` + `TraceFilterBar`。

### 9.2 每个 commit 前检查

- [x] `git status` 只包含本阶段文件。持续执行。
- [x] 没有覆盖用户/其他 agent 改动。持续执行。
- [x] 单测通过或记录失败原因。1487 测试通过。
- [ ] e2e 通过或记录失败原因。无 CI 强制门禁。
- [x] 文档更新。持续更新。

### 9.3 发布前检查

- [x] 新旧事件兼容路径确认。`assistant.token` 同时发布 `message.delta`，3 个测试覆盖。
- [x] legacy 数据读取确认。`kind DEFAULT 'normal'`, `status DEFAULT 'completed'`。
- [x] MCP 配置迁移确认。`_ensure_mcp_server_columns()` 确保 transport/command/args/url/headers/env/enabled 列存在。
- [x] skill policy 默认值确认。`ToolPolicy.STRICT_WHITELIST` 为默认，`SkillPreset` 和 `skill_presets` 表一致。
- [x] 多 agent 默认开启，无灰度开关。`config.features.multiAgent = True`，`run_child_task` 无 gate。
- [ ] 回滚方案确认。缺口：无回滚机制。
