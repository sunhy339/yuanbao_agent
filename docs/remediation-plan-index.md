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
| LLM material decision advisory | `docs/remediation-plan-index.md` | New top-level batch for making every material runtime decision request an LLM proposal before validator/policy裁决 |
| Agent autonomy governance | `docs/remediation-plan-index.md` | New cross-cutting batch for configurable, auditable, replayable, and permission-bounded autonomy |
| Agent soul and prompt profiles | `docs/remediation-plan-index.md` | New cross-cutting batch for configurable agent identity, system prompts, and prompt layering |
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

### LLM Material Decision Advisory

The target architecture is that every material runtime decision asks the LLM for
advice, then lets runtime validators and policy gates decide whether to accept,
modify, reject, defer, or require approval. The LLM should participate in
semantic and strategic choices, but never directly grant authority or execute
side effects.

Material decision rule:

`LLM proposes every material decision -> runtime validates every proposal -> policy gate controls every authority boundary -> trace records every accepted/rejected decision`

In scope decisions:

- conversation mode: direct answer, task execution, queued task, supplement, or
  clarification;
- routing strategy: fast ReAct, standard ReAct, reflection, skill mode,
  planning, supervisor, or swarm;
- decomposition: whether to split into subtasks, dependency graph, and whether
  independent subtasks can run in parallel;
- role/profile selection: generated `RoleProfile` proposals mapped to safe
  runtime `baseRole` values;
- autonomy profile selection and any per-task autonomy overrides;
- agent soul / prompt profile selection when workspace/session defaults are not
  enough;
- provider/model/budget choice;
- tool, MCP, and child-tool allowlist choice;
- memory recall, memory extraction, and memory invalidation choices;
- context policy: which sections to include, when to compact, and what to
  summarize;
- risk level, approval gates, write-scope expectations, and test strategy;
- failure recovery: retry, fallback, skip, ask user, or abort;
- synthesis strategy for multi-agent or mixed-success results.

Out of scope decisions:

- deterministic mechanics such as id generation, JSON parsing, path
  normalization, database writes, event persistence, and schema migrations;
- safety enforcement, permission checks, approval state transitions, and
  write-scope validation. These remain runtime-owned and cannot be overridden by
  an LLM proposal.

Current state:

- validators and proposal records already exist for many proposal kinds;
- `MetaRouter` can ask the provider for low-confidence routing classification;
- tests show LLM proposals can be accepted or rejected by validators;
- default runtime paths still call rules or inline logic at many decision
  points without creating a durable LLM proposal record;
- there is no single `DecisionAdvisor` interface that every material decision
  must pass through.

Rectification order:

1. P0: define a `DecisionAdvisor` interface with `advise(kind, input,
   context)`, returning proposal payload, confidence, rationale, model/provider
   metadata, and fallback reason.
2. P0: define a material decision registry that lists each decision kind,
   required inputs, allowed proposal schema, validator, policy gate, fallback
   behavior, and trace event name.
3. P0: route the first four default runtime decisions through
   `DecisionAdvisor`: conversation mode, routing strategy, context policy, and
   decomposition.
4. P1: automatically create proposal records for every `DecisionAdvisor`
   response, including rejected, malformed, fallback, and rule-only decisions.
5. P1: add `agent.decision` trace events linked to proposal records and policy
   outcomes, with budget before/after and task step where applicable.
6. P1: extend the advisor path to model/provider, tool policy, memory recall,
   risk/approval, retry/failure recovery, and test strategy decisions.
7. P2: add `RoleProfile` proposal support: LLM may propose specialized roles,
   but each proposal must map to a safe runtime `baseRole` and pass scope/tool
   validation.
8. P2: add prompt/soul advisory support so the LLM can suggest which configured
   agent soul or role profile should apply, without generating policy-bypassing
   instructions.
9. P2: add decision reports in generation/autonomy reports showing what the LLM
   suggested, what runtime accepted/rejected, and why.
10. P3: add replay coverage for advisor decisions so audit replay can reconstruct
    proposal input/output and dry-run replay can re-evaluate validators under
    newer policy.

### LLM Decision Wiring Gap

The LLM-assisted decision checklist is complete at the framework level: proposal
records exist, validators cover the 17 proposal kinds, and tests prove accepted
and rejected proposals can be handled. The remaining gap is default runtime
wiring.

Current state:

- default routing is being wired so low-confidence `MetaRouter` decisions can
  ask the configured provider instead of staying purely rule-based;
- context compaction is being moved toward "runtime hard budget plus LLM
  advisory decision" instead of a fixed small threshold;
- default runtime paths do not yet consistently create durable proposal records
  for key decisions such as `intent_mode`, `context_policy`, `decomposition`,
  `model_policy`, `tool_policy`, `risk_policy`, and `test_strategy`;
- runtime validators and approval gates must remain the final authority. LLM
  output should propose decisions, not grant permissions, apply patches, run
  commands, or bypass write-scope policy.

Rectification order:

1. P0: finish provider wiring for `MetaRouter`, with tests covering low-rule
   confidence LLM routing and malformed-provider fallback.
2. P0: finalize context-compaction decision policy: default context budget
   `256000`, ReAct compaction threshold `60000`, LLM advisory decisions near the
   threshold, and hard runtime compaction over budget.
3. P1: add a proposal-record helper used by runtime decision points so each
   LLM-assisted decision can be recorded as created, accepted or rejected, and
   applied when applicable.
4. P1: wire automatic proposal records for the first four default decisions:
   `intent_mode`, `context_policy`, `decomposition`, and `test_strategy`.
5. P2: extend proposal recording to model/tool/MCP/risk/failure-recovery
   decisions after the first four are stable.
6. P2: expose proposal trace summaries in the session UI so users can inspect
   what the LLM proposed and why the runtime accepted or rejected it.

### Agent Autonomy Governance

The runtime already has bounded autonomy: routing, planning, ReAct execution,
background tasks, subagent dispatch, retries, cancellation, memory recall, and
approval gates. The missing product-quality layer is making that autonomy
configurable, auditable, replayable, and permission-bounded across every
default path.

Target execution chain:

`user goal -> autonomy profile -> router/proposal -> plan -> execution gate -> tool/runtime -> event trace -> replay/report`

Current state:

- autonomy exists mostly as distributed runtime behavior rather than one
  explicit policy contract;
- provider/model/context/tool decisions are not all tied to a durable autonomy
  profile snapshot;
- proposal records prove LLM decisions can be validated, but default autonomous
  decisions still need consistent proposal and decision-event capture;
- generation reports aggregate child tasks and artifacts, but do not yet provide
  a full "why did the agent do this" autonomy report;
- runtime approval gates and validators exist, but the user-facing autonomy
  levels and policy outcomes are not yet exposed as a coherent model.

Rectification order:

1. P0: define `AutonomyProfile` and autonomy levels `L0` through `L4`.
   Suggested profiles are `locked_down`, `conservative`, `balanced`, and
   `autonomous`, each covering max steps, context budget, compaction threshold,
   background execution, subagents, shell, file writes, network, approval mode,
   memory recall policy, retry limits, and timeout limits.
2. P0: snapshot the active autonomy profile into each task at creation time and
   make `MetaRouter`, context compaction, memory recall, ReAct max steps,
   background execution, and subagent dispatch read from that snapshot.
3. P1: add structured `agent.decision` events for autonomous choices, including
   decision kind, reason, proposal id when present, policy outcome, approval
   requirement, budget before/after, tool target, memory ids, and task step.
4. P1: make default autonomous decisions create or link proposal records where
   appropriate, starting with routing, context policy, decomposition, tool
   policy, risk policy, retry policy, and test strategy.
5. P1: add a runtime policy gate result model with `allowed`,
   `approval_required`, `blocked`, `deferred`, and `sandboxed`, and ensure LLM
   output can only propose actions while validators and approval gates remain
   the final authority.
6. P2: build an autonomy run report that summarizes selected profile, routing
   decisions, tools called, files written, approvals requested, blocked actions,
   memory used, compactions performed, child tasks spawned, retries, budget use,
   and final outcome.
7. P2: add audit replay for saved runs without re-calling the LLM, using stored
   task input, profile snapshot, proposal records, decision events, tool
   inputs/outputs, approval decisions, memory recall results, compaction
   summaries, and final artifacts.
8. P3: add dry-run replay that re-evaluates policy gates and validators against
   the stored trace so changes in policy can be tested before enabling broader
   autonomy.
9. P3: expose autonomy profile selection and autonomy run reports in the UI,
   with clear labels for policy outcomes and blocked/approval-required steps.
10. P3: add an autonomy release gate covering profile snapshot persistence,
    default decision trace creation, approval-blocked paths, replay integrity,
    and report rendering.

### Agent Soul and Prompt Profiles

The runtime currently has role-based system prompts and skill-level
`system_prompt` presets, but it does not yet expose a first-class configurable
"agent soul" for the root agent or workspace. Provider profiles configure the
model/API, while skills configure narrow scenario behavior. They do not define a
durable identity, working style, communication style, or user-editable global
system prompt for the agent.

This should be treated as a separate profile layer from `AutonomyProfile`:

- `AutonomyProfile` answers what the agent is allowed to do.
- `AgentSoulProfile` answers who the agent is, how it should reason, and how it
  should communicate.
- role and skill prompts answer what specialized job the current run is doing.
- safety and policy prompts remain mandatory runtime-owned guardrails.

Target prompt layering:

`runtime safety/developer guardrails -> agent soul -> workspace instructions -> role prompt -> skill prompt -> task context -> user request`

Current state:

- `ContextBuilder` creates role-based system prompts for root, worker, reviewer,
  planner, and summarizer roles;
- skill presets can override the role prompt through `system_prompt`;
- provider profiles store endpoint/model/temperature/context settings only;
- there is no config schema, persistence, UI, or per-task snapshot for agent
  identity/personality/system prompt settings;
- there is no explicit prompt-layer audit showing which soul/profile/system
  instructions affected a task.

Rectification order:

1. P0: define `AgentSoulProfile` with `id`, `name`, `description`,
   `identity`, `principles`, `communicationStyle`, `reasoningStyle`,
   `collaborationStyle`, `domainPreferences`, `customSystemPrompt`,
   `enabled`, `scope`, `createdAt`, and `updatedAt`.
2. P0: add config persistence for global default, workspace override, and
   optional session override, with a clear precedence order and stable fallback
   to the current built-in root prompt.
3. P0: update `ContextBuilder` so it composes prompt layers instead of choosing
   only role prompt versus skill prompt. Runtime safety boundaries must remain
   non-editable and appended even when custom prompts are configured.
4. P1: snapshot the active `AgentSoulProfile` and effective prompt layers into
   task metadata so completed runs remain auditable even after the user edits
   the profile.
5. P1: add validation and injection protection: custom prompts can describe
   behavior and style but cannot disable approvals, tools policy, workspace
   boundaries, memory policy, or runtime safety instructions.
6. P1: add UI for managing agent soul profiles: create, duplicate, edit,
   enable/disable, set default, bind to workspace/session, preview effective
   system prompt, and reset to built-in defaults.
7. P2: add prompt-layer trace events or report sections showing the selected
   soul profile, workspace instructions, role prompt, skill prompt, and runtime
   safety layer without exposing secrets.
8. P2: allow subagents to receive either inherited soul settings or specialized
   role-specific soul profiles, while preserving their `profile.ownedScope` and
   write-scope enforcement.
9. P3: add tests for prompt precedence, snapshot persistence, safety
   immutability, skill + soul composition, subagent inheritance, and UI form
   serialization.
10. P3: include prompt profile checks in the autonomy release gate so broader
    autonomy cannot ship with untraceable or policy-overriding system prompts.

## Recommended Next Batch

1. P0: implement the LLM Material Decision Advisory foundation:
   `DecisionAdvisor`, material decision registry, and advisor routing for
   conversation mode, routing strategy, context policy, and decomposition.
2. P0: complete the LLM decision wiring gap above, especially `MetaRouter`
   provider routing, compaction decision policy, and proposal-record creation
   for default runtime paths.
3. P0: start Agent Autonomy Governance with `AutonomyProfile` definitions,
   per-task profile snapshots, and runtime reads for router, compaction, memory,
   ReAct max steps, background execution, and subagent dispatch.
4. P0: start Agent Soul and Prompt Profiles with schema, config persistence,
   prompt-layer composition, and non-editable runtime safety boundaries.
5. P1: add structured `agent.decision` events and policy gate outcomes so each
   autonomous choice can be audited before the UI/replay work starts.
6. P1: snapshot active autonomy and soul profiles into each task so later audits
   and replay can explain both capability limits and prompt identity.
7. P0: reconcile the legacy `chat-runtime-agent-run-todolist.md` open items
   against the newer completed subagent/LLM checklists, then close or rewrite
   stale entries.
8. P0: run the broader frontend regression suite and visual smoke around the
   session workspace now that subagent panel work is checked off.
9. P1: harden `run_command` beyond cwd-based write-scope checks if shell command
   path isolation becomes a release requirement.
10. P1: add a focused release gate that combines backend subagent tests,
   frontend visibility tests, typecheck, and diff hygiene.
11. P2: review memory/context UI gaps from the legacy chat runtime checklist and
   decide whether they still belong in the current product scope.
