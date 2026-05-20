# Agent Gap Follow-up Optimization Plan

> Based on the implementation review of `docs/agent-gap-implementation-plan.md`.
> Current scope: Phase 0-5 completion hardening plus the extra MCP service adaptation and integration work.

> 2026-05-20 status note: this document is now a historical follow-up plan.
> The original P0 blockers described below are no longer the current source of
> truth. MCP provider visibility, shared/frontend MCP management, lifecycle
> cleanup, foreground/background routing alignment, and full-suite stability have
> been closed or superseded by later main-flow work. Current status and remaining
> remediation priorities live in
> `docs/YUANBAO_AGENT_DOCS_CONSOLIDATED.md#2026-05-20-current-test-gate-and-remediation-queue`.

## 0. Current Reconciliation - 2026-05-20

Closed since this plan was written:

- MCP tools are model-visible through the merged ToolRegistry/provider tool path.
- MCP CRUD and refresh are exposed through shared RPC, runtime client, UI, and
  desktop E2E.
- Desktop MCP live covers create/list/update/enable/refresh/disable/delete.
- Partial MCP updates now merge stored config before validation
  (`de722aea Fix MCP partial update validation`).
- Runtime full suite completed after the latest fix: `2271 passed, 12 skipped`.
- Frontend typecheck/unit/build, Tauri `cargo check`, desktop UI smoke, MCP
  live, and session recovery all passed.
- Advisor-led Evidence execution baseline is done: generic evidence requests,
  executor records, approval-resume execution, tool/MCP policy checks,
  executor-state events, and completion evidence/audit integration are wired.

Still relevant as follow-up:

- Repair user-facing mojibake/encoding issues, especially the MCP workspace and
  older docs.
- Run another complex real LLM main-flow regression.
- Run real MCP + Skills combined coverage.
- Add concrete evidence adapters only when real advisor-selected proof asks for
  a missing executable capability.

## 1. Current Assessment

The Phase 0-5 backend implementation is mostly in place:

- Phase 0 MetaRouter exists and is connected to `send_message`.
- Phase 1 context compaction, scratchpad, and JIT-style context injection are implemented.
- Phase 2 memory store, retriever, manager, tools, and ReAct recall/remember paths exist.
- Phase 3 skill presets are wired into context building, system prompts, tool filtering, and provider parameter overrides.
- Phase 4 reflection evaluator, config, task persistence, and events exist.
- Phase 5 task decomposition, DAG execution, and coverage evaluation exist.
- MCP server CRUD, tool schema conversion, runtime registration, refresh, and tool execution bridge exist on the backend.

Validation already performed:

- `python -m compileall runtime\src\local_agent_runtime` passed.
- Focused Phase 0-5 and MCP tests passed: `169 passed in 10.87s`.
- Full runtime test suite did not complete within the current timeout window, so it cannot yet be treated as a release gate pass.

Overall status: backend feature surface is substantial, but the work is not yet fully complete because MCP tool execution is not fully closed-loop, frontend/shared RPC exposure is missing, background execution is inconsistent with foreground routing, and full-suite stability has not been proven.

## 2. Priority Goals

### P0. Close the MCP Tool-Calling Loop

Problem:

MCP tools can be registered into `ToolRegistry`, but the provider tool list can still prefer `context.openai_tools`, which is created by `ContextBuilder` before dynamic MCP tool registration is merged. This means the model may never see newly registered MCP tools during tool calling.

Target behavior:

- Any enabled MCP server discovered at runtime contributes tools to the model-visible tool list.
- Registered MCP tools appear in both `context.tools` and `context.openai_tools`, or are merged safely in `_provider_tools`.
- Tool invocation of `mcp__{server_id}__{tool_name}` reaches `McpClientManager.sync_call_tool`.
- MCP refresh/update/delete updates the visible provider tool list without requiring process restart.

Implementation tasks:

1. Change context/tool assembly so dynamic `ToolRegistry.schemas` are merged into provider-visible tools before model calls.
2. Avoid returning early from `_provider_tools` when `context.openai_tools` exists; merge registry schemas first, then normalize once.
3. Add a regression test where an MCP tool is registered after `ContextBuilder` construction and is still visible to the provider.
4. Add an end-to-end mocked test: model requests `mcp__pg__query`, runtime executes it, and tool output is returned to the ReAct loop.

Acceptance criteria:

- A registered MCP tool appears in the provider request payload.
- MCP tool execution produces a normal `tool.started` and `tool.completed` event.
- Deleting or disabling an MCP server removes its tools from provider-visible discovery.

### P0. Expose MCP Through Shared RPC and UI Client

Problem:

Backend RPC handlers exist for:

- `mcp.server.list`
- `mcp.server.create`
- `mcp.server.update`
- `mcp.server.delete`
- `mcp.tools.refresh`

But `shared/src/rpc.ts` does not include these methods, and the frontend client/UI does not expose the MCP management flow.

Target behavior:

- Shared TypeScript RPC contracts include MCP server records, params, and results.
- `app/src/lib/runtimeClient.ts` has typed client methods for MCP CRUD and refresh.
- Settings UI can list, create, edit, enable/disable, delete, and refresh MCP servers.
- UI communicates connection errors without losing saved server config.

Implementation tasks:

1. Add MCP domain types to `shared/src/domain.ts`.
2. Add MCP RPC methods, params, and results to `shared/src/rpc.ts`.
3. Add runtime client wrappers and browser mock behavior for MCP methods.
4. Add a Settings workspace MCP section with server list and basic CRUD controls.
5. Add frontend tests for list/create/update/delete/refresh flows.

Acceptance criteria:

- User can configure an MCP server from UI.
- Refresh shows discovered tool names.
- Disabled MCP servers are stored but not connected on runtime startup.

### P0. Stabilize MCP Lifecycle and Async Cleanup

Problem:

Focused tests pass but emit:

`RuntimeWarning: coroutine 'McpClientManager.disconnect_server' was never awaited`

This indicates at least one shutdown or cleanup path can create a coroutine that is not awaited. It may not break happy-path tests, but it is risky for long-running desktop sessions.

Target behavior:

- MCP disconnect and shutdown never emit unawaited coroutine warnings.
- Event loop lifecycle is deterministic.
- Failed connects clean up partially opened transports.

Implementation tasks:

1. Audit `McpClientManager.shutdown`, `sync_disconnect_server`, and test mocks that patch async methods.
2. Make shutdown safe when no loop exists, when the loop is stopped, and when disconnect fails.
3. Ensure partial connection failures close `AsyncExitStack`.
4. Add tests that assert no RuntimeWarning is emitted for shutdown with connected mocked servers.

Acceptance criteria:

- Focused MCP tests pass with no unawaited coroutine warnings.
- Repeated connect/update/delete/refresh cycles do not leak tools or connections.

### P1. Align Background Execution With Routing

Problem:

Foreground `send_message` uses MetaRouter and builds context with routing/skill data. The background path returns early and starts background work without the same routing context.

Target behavior:

- Background and foreground tasks use the same routing decision model.
- Background tasks preserve `routing`, `skill_id`, `max_steps`, `enable_reflection`, and `enable_planning`.
- Background task resume/pause behavior works for ReAct and planning paths.

Implementation tasks:

1. Move routing/context construction before the background branch or explicitly duplicate the same routing setup.
2. Persist routing metadata in task context or task metadata so background workers can recover it.
3. Add tests for background simple query, skill-based task, and planning task routing.

Acceptance criteria:

- Background CODE_REVIEW uses `code_reviewer` skill.
- Background MULTI_STEP_TASK can enter planning mode.
- Background max step count follows routing decision.

### P1. Full Runtime Test Suite Stabilization

Problem:

Focused tests pass, but the full runtime test suite timed out. This blocks a confident "fully complete" claim.

Target behavior:

- Full runtime test suite completes within a predictable timeout.
- Slow/hanging tests are categorized and either fixed or marked with clear integration labels.

Implementation tasks:

1. Run tests by group to locate the timeout source:
   - orchestrator/react loop
   - worker process runtime
   - worker transport
   - collaboration
   - runtime flows
2. Add shorter default timeouts to tests that spawn child processes.
3. Ensure background worker and MCP event loop cleanup happens in teardown.
4. Add a CI-friendly command for the release gate.

Acceptance criteria:

- `python -m pytest -q -p no:cacheprovider` completes.
- If integration tests are intentionally long-running, a separate `pytest -m integration` path is documented.

### P1. Repair Encoding/Mojibake in Comments and Docs

Problem:

Several source files and test comments display mojibake in terminal output. Python compiles, but readability and future maintenance suffer.

Target behavior:

- User-facing strings, comments, and docs are valid UTF-8.
- Routing keywords are visibly correct Chinese and English terms.

Implementation tasks:

1. Fix `runtime/src/local_agent_runtime/router/defaults.py` Chinese keyword strings and comments.
2. Fix mojibake in MCP comments and safety text.
3. Add a small encoding smoke test for key source files if needed.
4. Normalize docs encoding and confirm they render correctly.

Acceptance criteria:

- Routing tests still pass.
- Chinese routing terms are readable in source and docs.

### P2. Improve Observability for Routing, Planning, and MCP

Problem:

There is event publication for tasks and tools, but routing and MCP state transitions need better diagnostics for debugging real desktop sessions.

Target behavior:

- Each task exposes the selected routing decision.
- MCP connect/disconnect/refresh failures are visible in logs/events.
- Planning execution emits enough structured state to debug failed subtasks.

Implementation tasks:

1. Persist routing decision on task creation or trace events.
2. Emit MCP lifecycle events or structured logs with server id, transport, and discovered tool count.
3. Add task metrics for ReAct step count, tool call count, and planning subtask count.
4. Add UI surface for recent MCP errors if the Settings UI is implemented.

Acceptance criteria:

- A user can inspect why a request chose a given strategy.
- MCP connection errors are discoverable without reading raw terminal logs.

## 3. Suggested Implementation Order

1. Fix MCP model-visible tool discovery.
2. Fix MCP lifecycle cleanup warning.
3. Add MCP shared RPC/client/UI contracts.
4. Align background routing with foreground routing.
5. Stabilize full runtime tests and split slow integration tests if needed.
6. Repair mojibake/encoding issues.
7. Add observability improvements.

## 4. Test Plan

Focused backend gate:

```powershell
cd D:\py\yuanbao_agent\runtime
python -m pytest tests\test_meta_router.py tests\test_context_phase1.py tests\test_memory_manager.py tests\test_skill_registry.py tests\test_reflection_evaluator.py tests\test_task_decomposer.py tests\test_dag_executor.py tests\test_coverage_evaluator.py tests\test_mcp_client.py -q -p no:cacheprovider
```

MCP execution regression gate:

```powershell
cd D:\py\yuanbao_agent\runtime
python -m pytest tests\test_mcp_client.py tests\test_orchestrator_react_loop.py -q -p no:cacheprovider
```

Full runtime release gate:

```powershell
cd D:\py\yuanbao_agent\runtime
python -m pytest -q -p no:cacheprovider
```

Frontend/shared gate after MCP UI work:

```powershell
cd D:\py\yuanbao_agent\app
npm test -- --run
npm run build
```

## 5. Definition of Done

The implementation can be called complete when all of the following are true:

- Foreground and background tasks both use MetaRouter decisions consistently.
- Skill, memory, reflection, planning, and DAG paths have passing regression tests.
- MCP servers can be managed from UI or typed client APIs.
- MCP tools are visible to the model and executable in the ReAct loop.
- MCP lifecycle has no unawaited coroutine warnings.
- Full runtime test suite completes or is intentionally split into documented fast/integration gates.
- Source comments, docs, and routing keywords are readable UTF-8.
