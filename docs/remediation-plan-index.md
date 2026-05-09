# Remediation Plan Index

Date: 2026-05-09

This index consolidates the remediation and TODO documents currently present in
the repository. Most planning documents live under `docs/`, but `docs/` is
ignored by `.gitignore`, so newly created documents under this directory must be
staged with `git add -f` when they need to be committed.

## Current Plan Inventory

| Area | Document | Status |
| --- | --- | --- |
| System-wide LLM decision layer | `docs/llm-assisted-runtime-decision-plan.md` | Partially implemented: P1, P2, P3 (partial), P8 complete |
| System-wide LLM decision TODO | `docs/llm-assisted-runtime-decision-todolist.md` | 39 done / 129 open |
| TDD remediation test suite | `docs/tdd-remediation-test-suite.md` | New test design, use as acceptance gate |
| Subagent generation | `docs/subagent-generation-remediation-plan.md` | Partially implemented: P1-P4, P7, P8 backend done |
| Subagent generation TODO | `docs/subagent-generation-todolist.md` | 128 done / 114 open |
| Multi-agent collaboration backbone | `docs/multi-agent-collaboration-todo.md` | Checklist complete, 22 done / 0 open |
| Chat runtime agent run | `docs/chat-runtime-agent-run-todolist.md` | Largely complete, 323 done / 153 open |
| Chat runtime architecture plan | `docs/chat-runtime-agent-run-rectification-plan.md` | Design reference, no checklist |
| Chat runtime detailed remediation | `docs/chat-runtime-agent-run-detailed-remediation.md` | Design reference, no checklist |
| Memory and context compaction | `docs/memory-and-context-compaction-plan.md` | Older plan, checklist not updated after implementation |
| Frontend V2 follow-up | `docs/frontend-v2-followup-plan.md` | Checklist complete, 46 done / 0 open |
| Agent gap implementation | `docs/agent-gap-implementation-plan.md` | Older implementation reference, no checklist |
| Agent gap follow-up optimization | `docs/agent-gap-followup-optimization-plan.md` | Older optimization reference, no checklist |
| Phase 0-5 rectification | `docs/phase0-5-rectification-plan.md` | Older remediation reference, no checklist |
| Runtime performance | `runtime/PERFORMANCE_PLAN.md` | Runtime-side performance reference outside `docs/` |

## What Has Been Completed

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
323 checked items and 153 open items.

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

## What Is Still Open

### System-Wide LLM Decision Remediation

The new system-wide plan is open. It covers all current hard-coded decision
surfaces that should become LLM-assisted proposals guarded by runtime
validators:

- intent and mode routing;
- model/provider selection;
- skill selection;
- tool and permission selection;
- MCP selection;
- context and memory policy;
- artifact contracts;
- risk and approval gates;
- test and verification strategy;
- failure recovery;
- frontend event presentation;
- final synthesis and TODO maintenance.

### Subagent Generation

The new subagent plan is also open. It depends on the system decision framework
and adds subagent-specific work:

- dynamic agent profiles;
- LLM planner proposal schemas;
- runtime validators for tools, dependencies, and scopes;
- artifact registry;
- result-message to artifact linking;
- real process-RPC child worker e2e;
- DAG scheduling;
- multi-agent write safety;
- subagent panel and trace drawer recovery.

### Chat Runtime Remaining Items

The older chat runtime TODO still has open work. The most important remaining
themes are:

- frontend missed-event recovery through `events.after`;
- frontend task list recovery;
- frontend tests around streaming merge, supplement UI, queued task UI, and
  task panel behavior;
- replacing remaining child-task heuristics with first-class event visibility;
- stronger user-facing task panel state for queued/recovery/context details.

## Recommended Next Batch

1. Implement remaining P3 validators (session/task state, mode, model/provider,
   skill, MCP, context, memory, retry budget, visibility, roadmap).
2. Implement P5 real process-RPC child worker e2e with file-backed database.
3. Implement P6 failure/timeout/cancel/retry observability.
4. Implement P9 multi-agent write safety (out-of-scope patch rejection,
   patch conflict detection, reviewer rejection blocking).
5. Connect LLM planner output to actual dispatch (P7 "Let LLM propose..." items).
6. Frontend visibility and recovery (P10).
