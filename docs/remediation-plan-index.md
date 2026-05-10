# Remediation Plan Index

Date: 2026-05-10

This index consolidates the remediation and TODO documents currently present in
the repository. Most planning documents live under `docs/`, but `docs/` is
ignored by `.gitignore`, so newly created documents under this directory must be
staged with `git add -f` when they need to be committed.

## Current Plan Inventory

| Area | Document | Status |
| --- | --- | --- |
| System-wide LLM decision layer | `docs/llm-assisted-runtime-decision-plan.md` | Checklist implementation complete; current follow-up is hardening polish |
| System-wide LLM decision TODO | `docs/llm-assisted-runtime-decision-todolist.md` | 166 done / 0 open |
| TDD remediation test suite | `docs/tdd-remediation-test-suite.md` | New test design, use as acceptance gate |
| Subagent generation | `docs/subagent-generation-remediation-plan.md` | Checklist implementation complete through backend, frontend visibility, and recovery |
| Subagent generation TODO | `docs/subagent-generation-todolist.md` | 242 done / 0 open |
| Multi-agent collaboration backbone | `docs/multi-agent-collaboration-todo.md` | Checklist complete, 22 done / 0 open |
| Chat runtime agent run | `docs/chat-runtime-agent-run-todolist.md` | Legacy ledger, 404 done / 74 open; needs reconciliation against newer subagent/LLM checklists |
| Chat runtime architecture plan | `docs/chat-runtime-agent-run-rectification-plan.md` | Design reference, no checklist |
| Chat runtime detailed remediation | `docs/chat-runtime-agent-run-detailed-remediation.md` | Design reference, no checklist |
| Memory and context compaction | `docs/memory-and-context-compaction-plan.md` | Older plan, checklist not updated after implementation |
| Frontend V2 follow-up | `docs/frontend-v2-followup-plan.md` | Checklist complete, 46 done / 0 open |
| Agent gap implementation | `docs/agent-gap-implementation-plan.md` | Older implementation reference, no checklist |
| Agent gap follow-up optimization | `docs/agent-gap-followup-optimization-plan.md` | Older optimization reference, no checklist |
| Phase 0-5 rectification | `docs/phase0-5-rectification-plan.md` | Older remediation reference, no checklist |
| Runtime performance | `runtime/PERFORMANCE_PLAN.md` | Runtime-side performance reference outside `docs/` |

## What Has Been Completed

### System-Wide LLM Decision Layer

`docs/llm-assisted-runtime-decision-todolist.md` is now fully checked off.
Completed areas include:

- proposal record persistence, validation, application, and events;
- all 17 proposal kinds;
- runtime validators for mode, model, skill, MCP, context, memory, risk,
  approval, retry, test commands, visibility, and roadmap edits;
- LLM-assisted intent/mode, model/provider, skill/tool/MCP, context/memory,
  risk/recovery, event presentation, synthesis, and TODO maintenance flows;
- acceptance scenarios covering rejected unsafe planner output, unverified
  artifact synthesis, and approval-required roadmap updates.

### Multi-Agent Collaboration Backbone

`docs/multi-agent-collaboration-todo.md` is fully checked off. Completed areas
include:

- durable collaboration task graph;
- agent worker registry;
- mailbox/message store;
- collaboration RPC methods;
- collaboration trace/runtime events;
- runtime-native child task dispatch tool;
- worker runner abstraction;
- retry and timeout policy;
- child worker process boundary;
- child environment and tool allowlists;
- approval handoff and resume path;
- production controls such as background jobs, skills, compaction, plan
  approval, shutdown handshakes, and observability dashboards.

This checklist describes the collaboration substrate. It does not cover the new
system-wide LLM decision layer or dynamic agent-profile planning.

### Frontend V2 Follow-Up

`docs/frontend-v2-followup-plan.md` is fully checked off. Completed areas
include:

- interaction audit;
- placeholder cleanup;
- desktop utility gaps;
- skill and agent management;
- visual regression follow-up;
- final frontend V2 gate closure.

### Chat Runtime Agent Run

`docs/chat-runtime-agent-run-todolist.md` has substantial completed coverage:
404 checked items and 74 open items.

Completed areas include:

- message persistence and message lifecycle;
- assistant streaming message lifecycle;
- task state machine;
- queued task backend;
- provider turn records;
- context snapshots;
- task inbox/supplement plumbing;
- memory and context compaction implementation;
- MCP lifecycle and runtime events;
- skill tool policy and filtering;
- event visibility persistence and root chat visibility fixes;
- proposal record CRUD and 17 proposal kinds;
- tool alias normalization and tool/scope/dependency validators;
- artifact registry (6 kinds, 4 statuses) with CRUD and filters;
- generation report builder with child task and artifact aggregation;
- planner contract with dynamic agent profile validation;
- DAG scheduling with topological sort and cycle detection;
- risk policy, approval gate, and test strategy validators;
- downstream blocking computation for DAG failure propagation.

Remaining work is concentrated around frontend recovery/polish and deeper
multi-agent generation behavior, not the basic runtime message/task substrate.

### Subagent Generation

`docs/subagent-generation-todolist.md` is now fully checked off. Completed areas
include:

- baseline multi-subagent dispatch regression coverage;
- proposal framework, runtime validators, tool alias normalization, and planner
  contract;
- generation report, artifact registry, artifact linking, and synthesis;
- real process-RPC child worker execution with failure, timeout, cancel, retry,
  and report observability;
- DAG scheduling and multi-agent write safety;
- frontend visibility routing, subagent panel data model, child task status and
  artifact display, trace filters by task id, visibility, and agent type;
- frontend tests for visibility routing, missed-event merge, and trace drawer
  child event-chain coverage.

### Subagent Backend and Write Safety Audit

The subagent backend checklist is complete through P9 and P12. A 2026-05-10
review also fixed issues found in completed backend items:

- write-scope path traversal and nested-scope overlap validation;
- runtime child task to collaboration task write-scope resolution;
- `write_file` approval enforcement and `overwrite=false` behavior;
- rejected artifact status transitions back to `verified`;
- background command line streaming for real-time output events.

The current backend regression gate is:

- `471 passed` across write safety, proposal/planner validation, artifact,
  command policy/background, schema, and selected runtime-flow tests;
- `python -m compileall` passed on modified runtime modules;
- `git diff --check` passed.

The current frontend gate for the subagent panel/visibility work is:

- `npx.cmd tsc --noEmit` passed from `app/`;
- `npm.cmd test -- SessionWorkspace.test.tsx visibilityRouting.test.ts` passed
  with 50 tests.

## What Is Still Open

### Chat Runtime Remaining Items

The older chat runtime TODO still has open work. The most important remaining
themes are:

- frontend task list recovery and richer task-panel recovery state;
- frontend tests around streaming merge, supplement UI, queued task UI, and
  task panel behavior;
- replacing remaining child-task heuristics with first-class event visibility;
- memory/context UI gaps from the legacy checklist.

### Residual Backend Risk

`run_command` write-scope enforcement currently validates the command working
directory. It does not fully parse arbitrary file path arguments embedded inside
shell command strings. Treat stronger command path sandboxing as a separate
hardening item after the frontend recovery batch.

## Recommended Next Batch

1. P0: reconcile the legacy `chat-runtime-agent-run-todolist.md` open items
   against the newer completed subagent/LLM checklists, then close or rewrite
   stale entries.
2. P0: run the broader frontend regression suite and visual smoke around the
   session workspace now that subagent panel work is checked off.
3. P1: harden `run_command` beyond cwd-based write-scope checks if shell command
   path isolation becomes a release requirement.
4. P1: add a focused release gate that combines backend subagent tests,
   frontend visibility tests, typecheck, and diff hygiene.
5. P2: review memory/context UI gaps from the legacy chat runtime checklist and
   decide whether they still belong in the current product scope.
