# Yuanbao Agent 与 Claude Code/haha-cc 对齐整改方案

## 1. 目标

本方案用于指导 Yuanbao Agent 后续优化，让当前后端在不推倒现有 task-first orchestrator 的前提下，补齐 Claude Code 好用体验中的关键能力：

- 输出协议更稳定，前端可以按 haha-cc `ServerMessage` 风格消费。
- 对外命名统一为 Yuanbao，旧 haha/haha-cc 命名保留兼容别名。
- 工具执行更快，尤其是只读探索阶段。
- 工具结果更干净，减少上下文膨胀。
- 权限打断更少，但高风险操作仍保持可控。
- AskUser、PlanMode、Agent Team 更接近 Claude Code 的自然交互方式。

## 2. 非目标

- 不把后端整体改成 Claude Code 的 conversation-first query loop。
- 不删除现有 RuntimeEvent、task、approval、trace、worktree、collaboration 等后端能力。
- 不全局替换 `docs/cc-haha-main` 参考实现里的 haha/cc-haha 命名。
- 不一次性移除旧字段 `hahaCc`、旧 RPC `events.hahaCcAfter`、旧输出 `haha_cc_message`。

## 3. 命名整改原则

### 3.1 对外主命名改为 Yuanbao

新增并优先使用这些 Yuanbao 命名：

| 类型 | 新命名 | 旧兼容命名 |
|---|---|---|
| 事件字段 | `yuanbao` | `hahaCc` |
| stdio 输出 kind | `yuanbao_message` | `haha_cc_message` |
| RPC 方法 | `events.yuanbaoAfter` | `events.hahaCcAfter` |
| ping 结果字段 | `yuanbaoMessages` | `hahaCcMessages` |
| shared 类型 | `YuanbaoServerMessage` | `HahaCcServerMessage` |
| 适配层 | `YuanbaoEventAdapter` | `haha_cc_compat.py` |

### 3.2 兼容策略

- 第一阶段同时输出 `yuanbao` 和 `hahaCc`。
- 第一阶段同时支持 `events.yuanbaoAfter` 和 `events.hahaCcAfter`。
- 第一阶段同时写出 `yuanbao_message` 和 `haha_cc_message`，或至少确保新前端消费 `yuanbao_message`，旧前端仍能消费 `haha_cc_message`。
- 迁移稳定后，再决定是否关闭旧兼容输出。

### 3.3 不改参考源码

`docs/cc-haha-main` 是对照 Claude Code/haha-cc 行为的参考目录，不参与命名替换。文档中允许继续称为 haha-cc 协议或 haha-cc 参考实现。

## 4. 目标输出架构

当前输出：

```text
RuntimeEvent
  -> haha_cc_compat.to_haha_cc_server_message()
  -> event.hahaCc
  -> kind: haha_cc_message
```

目标输出：

```text
RuntimeEvent
  -> YuanbaoEventAdapter
  -> event.yuanbao
  -> kind: yuanbao_message

RuntimeEvent
  -> legacy alias
  -> event.hahaCc
  -> kind: haha_cc_message
```

原则：

- `RuntimeEvent` 仍是完整事实来源，用于任务面板、trace、approval、worktree、debug。
- `yuanbao_message` 是聊天 UI 兼容输出，字段严格对齐 haha-cc `ServerMessage` 形状。
- 前端新代码优先消费 `yuanbao`/`yuanbao_message`。

## 5. P0 整改项

### 5.1 Yuanbao 输出协议适配层

新增 `YuanbaoEventAdapter`，把当前 `haha_cc_compat.py` 的职责升级为正式输出协议适配层。

需要支持的消息类型：

- `connected`
- `pong`
- `status`
- `content_start`
- `content_delta`
- `thinking`
- `tool_use_complete`
- `tool_result`
- `permission_request`
- `computer_use_permission_request`
- `message_complete`
- `api_retry`
- `error`
- `system_notification`
- `team_created`
- `team_update`
- `team_deleted`
- `task_update`
- `session_title_updated`

验收标准：

- 每个可映射 RuntimeEvent 都能生成合法 `YuanbaoServerMessage`。
- 旧 `hahaCc` 字段仍存在，值与 `yuanbao` 一致。
- `events.yuanbaoAfter` 返回扁平消息列表。

### 5.2 状态机补齐

前端应该能稳定看到这些状态流转：

```text
thinking -> streaming -> tool_executing -> permission_pending -> idle
```

关键节点：

- 收到用户消息后发 `status.thinking`。
- provider 开始流式输出后发 `status.streaming`。
- 工具开始执行后发 `status.tool_executing`。
- 审批等待时发 `status.permission_pending`。
- 完成、失败、取消后发 `status.idle`。

### 5.3 工具事件标准化

每个工具调用按固定顺序输出：

```text
content_start(blockType=tool_use)
tool_use_complete
content_delta(optional tool output/progress)
tool_result
```

如果 provider 不支持工具参数 delta，不伪造 `toolInput` 流，只在工具参数完整后发 `tool_use_complete`。

### 5.4 工具结果瘦身

模型上下文和 Yuanbao 前端消息不再直接使用完整工具结果 JSON。

目标格式：

```json
{
  "summary": "short summary",
  "preview": "display-safe preview",
  "status": "completed",
  "artifactId": "optional-artifact-id",
  "metadata": {}
}
```

完整结果保留在 RuntimeEvent、trace、artifact 或 command log 中。

优先处理：

- `run_command`
- `search_files`
- `code_search`
- `web_fetch`
- `read_file`
- `git_diff`

### 5.5 只读工具并发

在 ReAct loop 中新增安全 batch executor。

可并发工具：

- `read_file`
- `list_dir`
- `search_files`
- `code_search`
- `git_status`
- `git_diff`
- `web_fetch`
- 安全只读 `run_command`

必须串行工具：

- `apply_patch`
- `write_file`
- 非只读 `run_command`
- `task`
- `computer_use`
- 可能修改状态的 MCP 工具

验收标准：

- 并发 batch 保留 `toolGroupId`、`toolIndex`、`toolTotal`。
- 事件可以按原始工具顺序还原。
- 写操作不会与读操作错误并发导致状态不一致。

### 5.6 权限少打断

扩展 read-only 命令识别，减少无意义审批。

自动允许候选：

- `rg`
- `findstr`
- `ls`
- `dir`
- `Get-ChildItem`
- `cat`
- `type`
- `Get-Content`
- `git status`
- `git diff`
- `git log`
- `git show`
- 常见测试和构建命令

新增或对齐权限模式：

- `default`
- `plan`
- `accept_edits`
- `bypass_permissions` 或内部等价模式

`accept_edits` 行为：

- 文件编辑自动允许。
- 危险 shell、网络、桌面控制仍需审批。

## 6. P1 整改项

### 6.1 AskUserQuestion 工具化

新增模型可调用工具 `ask_user_question`。

能力：

- 一次问 1-3 个问题。
- 支持选项、推荐项、自由输入。
- 用户回答后作为结构化结果回灌，而不是普通 supplement。

复用现有 `_pause_react_for_user_question` 和 task resume 机制。

### 6.2 PlanMode 工具化

新增：

- `enter_plan_mode`
- `exit_plan_mode`

行为：

- plan mode 内只允许只读工具。
- `exit_plan_mode` 提交计划给用户审批。
- 审批通过后恢复执行。

### 6.3 短任务 fast path

对解释类、只读查询、小修改任务使用：

```text
minimal_context + react_fast
```

避免每个小问题都走完整 planner、completion gate 和复杂 task lifecycle。

### 6.4 AgentTool 包装

基于现有 `task` tool、SubagentService、WorkerRunner、collaboration 服务，新增模型侧 `agent` tool。

建议参数：

- `agent_type`
- `prompt`
- `cwd`
- `mode`
- `tool_allowlist`
- `budget`
- `plan_mode_required`

默认策略：

- 子 agent 默认只读。
- 写能力必须显式开启。
- 子 agent 不能 commit/push。

## 7. P2 整改项

### 7.1 Team timeline

将这些事件聚合成稳定 team timeline：

- `collab.worker.*`
- `collab.task.*`
- `collab.message.*`
- `collab.worker.budget.updated`

Yuanbao 消息输出：

- `team_created`
- `team_update`
- `team_deleted`

成员字段固定：

```json
{
  "agentId": "worker-id",
  "role": "reviewer",
  "status": "running",
  "currentTask": "task summary"
}
```

### 7.2 system_notification 完整映射

补齐子类型：

- `init`
- `compact_summary`
- `compact_boundary`
- `memory_saved`
- `task_started`
- `task_progress`
- `session_state_changed`
- `goal_event`

### 7.3 回放和契约测试

为以下流程录制并验证事件：

- 普通文本回答
- provider stream
- 工具调用
- 工具失败
- 审批等待和恢复
- compact
- child agent
- supervisor
- swarm
- worktree merge approval

验收标准：

- RuntimeEvent 完整。
- YuanbaoServerMessage 合法。
- 旧 hahaCc 兼容字段仍可用。

## 8. 推荐实施顺序

第一轮：

1. 命名别名：新增 `yuanbao` 字段、`yuanbao_message`、`events.yuanbaoAfter`。
2. `YuanbaoServerMessage` shared 类型和旧类型别名。
3. 输出协议契约测试。
4. 状态机补齐。

第二轮：

1. 工具结果瘦身。
2. 工具事件标准化。
3. 只读工具并发。
4. 权限少打断。

第三轮：

1. AskUserQuestion 工具。
2. PlanMode 工具。
3. AgentTool 包装。
4. Team timeline。

## 9. 风险和约束

- 不要全局替换 `haha`，否则会误改参考源码、旧测试和第三方语义。
- 命名迁移需要兼容期，避免前端、shared 类型和 runtime 测试同时断裂。
- 只读工具并发要保证 SQLite/store 写入、事件顺序和 tool cache 不出错。
- 工具结果瘦身不能影响模型继续完成任务，必须保留足够关键信息。
- PlanMode 和 AskUserQuestion 需要和现有 pause/resume/approval 状态兼容。

## 10. 完成标准

本轮整改完成时，系统应满足：

- 前端新代码不再依赖 `hahaCc` 主字段，而是使用 `yuanbao`。
- 旧 `hahaCc` 消费路径仍然可用。
- `events.yuanbaoAfter` 可返回合法扁平消息。
- 普通聊天、工具调用、审批、任务完成都能输出稳定 Yuanbao 消息。
- 工具结果不会把大 JSON 直接塞回模型上下文。
- 只读探索任务速度明显提升。
- 权限提示次数减少，高风险操作仍需审批。

## 11. Follow-up Output Parity Tracks

### 11.1 Team adapter snapshot

External adapters should not depend only on incremental collaboration events. The backend now needs a session-scoped flat snapshot endpoint that can return current team lifecycle messages after reconnect:

```json
[
  { "type": "team_created", "teamName": "session-id" },
  { "type": "team_update", "teamName": "session-id", "members": [] }
]
```

This complements realtime `team_update` events and keeps Yuanbao/haha-cc flat output usable for external consumers that join late.

### 11.2 Real provider thinking deltas

Token-level `thinking` should only be emitted when the provider exposes true streaming reasoning/thinking deltas. Non-stream `thought_summary` remains a single summary frame and must not be presented as token-level streaming.

Covered streaming sources:

- OpenAI-compatible chat deltas: `reasoning_content`, `reasoning_delta`, `thinking`, `thinking_delta`, and nested `reasoning.delta`.
- OpenAI Responses deltas: `response.reasoning_summary_text.delta`, `response.reasoning_text.delta`, and compatible reasoning/thinking `.delta` event names.
- Anthropic Messages thinking blocks and `thinking_delta`.

### 11.3 Message lifecycle flat boundary

`message.created` is a Yuanbao envelope lifecycle event only. It should not generate flat `content_start`, because legacy flat consumers treat explicit `content_start`, `content_delta`, and `message_complete` as the assistant-output lifecycle.
