# 工具对比分析：Yuanbao vs haha-cc

## haha-cc 工具列表

| 类别 | 工具名 |
|:---|:---|
| 文件操作 | BashTool, FileEditTool, FileReadTool, FileWriteTool, GlobTool, GrepTool |
| 任务管理 | TaskCreateTool, TaskGetTool, TaskListTool, TaskOutputTool, TaskStopTool, TaskUpdateTool |
| 团队协作 | TeamCreateTool, TeamDeleteTool, ListPeersTool |
| 计划模式 | EnterPlanModeTool, ExitPlanModeTool |
| 工作树 | EnterWorktreeTool, ExitWorktreeTool |
| MCP | MCPTool, ListMcpResourcesTool, ReadMcpResourceTool, McpAuthTool |
| 技能 | SkillTool, DiscoverSkillsTool |
| 代理 | AgentTool |
| 网络 | WebBrowserTool, WebFetchTool, WebSearchTool |
| 工具搜索 | ToolSearchTool |
| 监控 | MonitorTool |
| 代码编辑 | LSPTool |
| 其他 | AskUserQuestionTool, ConfigTool, NotebookEditTool, PowerShellTool, REPLTool, SleepTool, TodoWriteTool, WorkflowTool |

## Yuanbao 当前工具

| 类别 | 工具名 | 对应 haha-cc | 状态 |
|:---|:---|:---|:---|
| 文件操作 | run_command, apply_patch, write_file, read_file | ✅ BashTool, FileEditTool, FileWriteTool, FileReadTool | ✅ |
| 目录/搜索 | list_dir, search_files, code_search | ✅ GlobTool, GrepTool | ✅ 已对齐 |
| 任务 | task (subagent) | ⚠️ AgentTool | ✅ 功能合并 |
| Git | git_status, git_diff | ✅ | ✅ |
| 网络 | web_fetch, browser | ⚠️ WebBrowserTool, WebFetchTool | ✅ |
| 记忆 | memory, scratchpad_tool | ✅ | ✅ |
| 计划模式 | plan_mode | ⚠️ EnterPlanModeTool, ExitPlanModeTool | ✅ 已实现 |
| 其他 | ask_user_question, notebook, computer_use | ✅ AskUserQuestionTool, NotebookEditTool | ✅ |

## 对比结论

### ✅ 已对齐工具

| 工具 | 说明 |
|:---|:---|
| search_files | 对应 GlobTool，模式支持 content/filename |
| code_search | 对应 GrepTool，支持 definition/reference/symbol/chunk |
| plan_mode | 对应 EnterPlanModeTool + ExitPlanModeTool |

### 🔄 待实现/低优先级

| 优先级 | 工具 | 说明 |
|:---:|:---|:---|
| 🟢 低 | SkillTool | 技能发现 - 需评估使用场景 |
| 🟢 低 | DiscoverSkillsTool | 技能发现 |
| 🟢 低 | TeamCreateTool | 团队创建 |
| 🟢 低 | TeamDeleteTool | 团队删除 |
| 🟢 低 | ListPeersTool | 列出团队成员 |
| 🟢 低 | ListMcpResourcesTool | MCP 资源列表 |
| 🟢 低 | WebSearchTool | 网络搜索 - 可用 web_fetch 替代 |
| 🟢 低 | TodoWriteTool | 待办事项 |
| 🟢 低 | MonitorTool | 系统监控 |
| 🟢 低 | WorkflowTool | 工作流 |
| 🟢 低 | ToolSearchTool | 工具搜索 |

### 说明

1. **search_files → GlobTool**: Yuanbao 的 search_files 工具支持 content/filename 两种搜索模式，与 haha-cc 的 GlobTool + GrepTool 功能对齐
2. **code_search → GrepTool**: code_search 支持 definition/reference/symbol/chunk 模式，等同于 GrepTool
3. **plan_mode**: 已在 `runtime/src/local_agent_runtime/tools/plan_mode.py` 中实现

---

*更新时间: 2026-06-10*
*对比结果: 大部分核心工具已对齐，低优先级工具待后续实现*