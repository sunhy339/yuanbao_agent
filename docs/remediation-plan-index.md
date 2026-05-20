# Remediation Plan Index

Date: 2026-05-10

This index consolidates the remediation and TODO documents currently present in
the repository. Most planning documents live under `docs/`, but `docs/` is
ignored by `.gitignore`, so newly created documents under this directory must be
staged with `git add -f` when they need to be committed.

## Current Source Of Truth - 2026-05-20

Use `docs/YUANBAO_AGENT_DOCS_CONSOLIDATED.md#2026-05-20-current-test-gate-and-remediation-queue`
as the current project snapshot. Older remediation documents remain useful as
design history, but many of their P0/P1 gaps have since been closed.

Current status:

- Main workflow backend baseline is closed for the current architecture:
  routing/advisor, tool policy, provider turns, tool/MCP recovery, worktree
  isolation, completion gates, generic advisor evidence executor, user
  takeover, budget convergence, and child partial handoff/continuation are
  runtime-backed and auditable.
- Advisor-led Evidence execution is **baseline done**, not an open P0. Runtime
  stores generic evidence requests, creates executor records, gates suggested
  commands/tools/MCP tools through PermissionEngine and ToolPolicyResolver,
  resumes approved evidence execution, records executor state transitions, and
  feeds the result into completion evidence/audit.
- Remaining evidence work is concrete adapter expansion and UX visibility:
  browser inspection, document/render proof, migration dry-run, benchmark,
  external service proof, or hardware-in-loop adapters should be added only
  when real advisor-selected flows need them.
- Latest broad gate passed: runtime full suite `2271 passed, 12 skipped`,
  frontend typecheck/unit/build, Tauri `cargo check`, desktop UI smoke, desktop
  MCP live, and desktop session recovery.
- The latest MCP live issue was fixed by `de722aea Fix MCP partial update
  validation`: partial MCP updates now merge stored config before validation.
- Strict MCP + Skills live smoke is now passing with the real `gpt-5.5`
  provider override. The release/manual smoke reports `ok=true` only after it
  proves connected MCP tools, explicit skill usage, active worktree binding,
  root workspace isolation, worktree output, task command-log pytest success,
  and direct pytest success.
- Completion verification tuning was tightened without making runtime the
  semantic judge: changed test artifacts now surface missing test-family
  evidence when only lint/typecheck/syntax checks ran, while high-confidence
  completion advisor `verification_sufficient` / `verification_assessment`
  can still accept a domain-specific gap with an auditable advisor resolution.

Current priority order:

1. Complex real LLM regression over the main workflow.
2. Real MCP + Skills combined flow with a local MCP server and skill preset.
   The release/manual smoke should be strict: it must prove connected MCP tools,
   explicit skill usage, worktree isolation, generated worktree output, command
   execution evidence, and passing verification instead of merely writing a
   best-effort report.
3. Add concrete advisor evidence adapters only when the real flow requests
   missing executable proof.
4. Improve cockpit visibility for advisor evidence executor records.
5. Clean up user-facing mojibake and stale/historical docs.

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
| Agent runtime maturity roadmap | `docs/agent-runtime-maturity-roadmap.md` | New post-plan roadmap for effective config, decision trace, budget panel, reports, parallel safety, and replay |
| Real local app long-run remediation | `docs/YUANBAO_AGENT_DOCS_CONSOLIDATED.md#2026-05-16-real-local-app-long-run-remediation-plan` | Main workflow hardening track after the successful real full-stack local app smoke; focus is MCP/Skills coverage, intent confidence, user takeover, automation levels, workspace awareness, structured handoffs, product acceptance, and frontend runtime observability |
| LLM decision closure | `docs/llm-decision-closure-plan.md` | New code-verified closure plan for default advisor wiring, proposal records, completion decision, and context policy |
| Runtime hooks design | `docs/runtime-hooks-design-plan.md` | New detailed design for policy-gated, auditable lifecycle hooks |
| Permission Policy V2 Lite | `docs/permission-policy-v2-lite-plan.md` | New focused plan for capability-based permission presets, unified runtime evaluation, approval gating, and audit records |
| Provider API format support | `docs/provider-api-format-support-plan.md` | New plan to align provider settings options with runtime adapter support for OpenAI Chat, Responses, Anthropic, and future native formats |
| Worktree isolation | `docs/worktree-isolation-design-plan.md` | New near-term isolation plan for task branches, worktrees, review, merge, cleanup, and code-verified closure gaps |
| GitHub PR and CI workflow | `docs/github-pr-ci-workflow-plan.md` | New plan for issue/branch/PR/CI workflow, publish approvals, and review feedback loops |
| Frontend observability and settings | `docs/frontend-observability-settings-plan.md` | New plan for decision/proposal UI, context budget, memory, hooks, worktrees, and settings depth |
| Background and long-running tasks | `docs/background-long-running-task-plan.md` | New plan for durable checkpoints, pause/resume/cancel, heartbeats, and recovery |
| Code refactoring design | `docs/code-refactoring-design-plan.md` | New refactoring plan for facades, repositories, state machines, pipelines, adapters, and frontend module splits |
| LLM material decision advisory | `docs/remediation-plan-index.md` | New top-level batch for making every material runtime decision request an LLM proposal before validator/policy裁决 |
| Agent autonomy governance | `docs/remediation-plan-index.md` | New cross-cutting batch for configurable, auditable, replayable, and permission-bounded autonomy |
| Agent soul and prompt profiles | `docs/remediation-plan-index.md` | New cross-cutting batch for configurable agent identity, system prompts, and prompt layering |
| Agent gap implementation | `docs/agent-gap-implementation-plan.md` | Older implementation reference, no checklist |
| Agent gap follow-up optimization | `docs/agent-gap-followup-optimization-plan.md` | Historical optimization reference; original MCP/shared/full-suite blockers are closed by later work |
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
shell command strings. Treat stronger command path isolation as a separate
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
- `DecisionAdvisor` and the material decision registry exist for routing,
  context policy, decomposition, ReAct turn, and completion decisions;
- `MetaRouter` can ask `DecisionAdvisor` or the provider for low-confidence
  routing classification;
- tests show LLM proposals can be accepted or rejected by validators;
- code review on 2026-05-13 found the default `build_server()` path still
  constructs `MetaRouter(provider=provider)` without a `DecisionAdvisor`, so the
  production-style default entry can bypass proposal-record routing;
- default runtime paths still call rules or inline logic at several decision
  points without creating a durable LLM proposal record.

Rectification order:

1. P0: wire `DecisionAdvisor(provider=provider)` into the default
   `build_server()` path and stop passing a router instance that lacks the
   advisor unless a test explicitly needs rule-only routing.
2. P0: make default routing create proposal records for accepted, rejected,
   malformed-provider, provider-unavailable, and rule-fallback decisions.
3. P0: route the first default runtime decisions through `DecisionAdvisor`:
   conversation mode, routing strategy, context policy, decomposition, ReAct
   turn, and completion.
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

- `DecisionAdvisor` tests cover accepted/rejected routing proposals, but the
  default runtime entry still needs to pass the advisor into `MetaRouter`;
- context builder uses a default context budget of `256000`, and the ReAct loop
  now uses progressive compaction (tier 1: <50K no-op, tier 2: 50K-220K truncate
  tool outputs, tier 3: >=220K full compact);
- `ContextCompactor.should_compact()` can ask the provider near budget, but that
  advisory path does not yet create `context_policy` proposal records;
- `completion_decision` is registered and completion trace events are emitted,
  but `_complete_task()` does not yet ask the advisor before marking a task
  complete;
- default runtime paths do not yet consistently create durable proposal records
  for key decisions such as `intent_mode`, `context_policy`, `decomposition`,
  `completion_decision`, `model_policy`, `tool_policy`, `risk_policy`, and
  `test_strategy`;
- runtime validators and approval gates must remain the final authority. LLM
  output should propose decisions, not grant permissions, apply patches, run
  commands, or bypass write-scope policy.

Rectification order:

1. P0: finish default `MetaRouter` advisor wiring in `build_server()`, with
   tests proving the normal `message.send` path records a `routing_strategy`
   proposal when low-confidence routing asks the advisor.
2. P0: finalize context-compaction decision policy: default context budget
   `256000`, configurable ReAct compaction threshold from the active autonomy
   profile, LLM advisory decisions near the threshold, and hard runtime
   compaction over budget.
3. P1: add a proposal-record helper used by runtime decision points so each
   LLM-assisted decision can be recorded as created, accepted or rejected, and
   applied when applicable.
4. P1: wire automatic proposal records for the first four default decisions:
   `intent_mode`, `context_policy`, `decomposition`, and
   `completion_decision`.
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
   `approval_required`, `blocked`, `deferred`, and `worktree_isolated`, and ensure LLM
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

### Permission Policy V2 Lite

The current permission model is useful but coarse. Settings can update
`policy.approvalMode`, and tools such as `run_command`, `apply_patch`, and
`write_file` consult the runtime policy before requesting approval. The missing
piece is a single capability-based permission contract that also covers network,
subagents, hooks, future Computer Use, and audit reporting.

Target execution chain:

`tool request -> PermissionEngine.evaluate -> allow | deny | approval_required -> audit record -> execute/block/wait`

Current state:

- `approvalMode` is persisted and affects high-risk tool approvals;
- command/path validation and approval records already exist;
- authority decisions are still distributed across individual tools;
- there is no top-level `permissions` config layer;
- there is no unified policy decision record explaining why an action was
  allowed, blocked, or sent to approval;
- Computer Use/browser automation are UI placeholders and should remain blocked
  until implemented.

Rectification order:

1. P0: add a `permissions` config layer with presets `safe`, `balanced`, and
   `autonomous`, while keeping `policy.approvalMode` as a compatibility field.
2. P0: implement `PermissionEngine.evaluate()` with capability, actor, task,
   workspace/worktree scope, matched rule, reason, and decision.
3. P0: route `run_command`, `apply_patch`, `write_file`, `web_fetch`, and child
   task dispatch through the permission engine.
4. P0: persist or trace `policy_decision` records for `allow`,
   `approval_required`, and `blocked` outcomes.
5. P1: expose permission presets and a capability summary in settings, without
   building a full policy editor yet.
6. P1: add task permission snapshots and include policy decisions in
   `autonomy.report`.
7. P1: make hook side effects and worktree-bound writes use the same engine.
8. P2: add temporary grants, URL/domain policy, replay re-evaluation, and
   Computer Use/browser automation details after those capabilities exist.

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

## Code-Verified Open Loops

Date checked: 2026-05-13.

These items were found by reading the current runtime/UI code, not by relying
on commit history or checklist state.

| Area | Current code state | Open loop | Completion criteria |
| --- | --- | --- | --- |
| ~~Default LLM routing advisory~~ | **Closed** (`18e3be1`). `build_server()` wires `DecisionAdvisor(provider=provider)` into `MetaRouter`; proposal records and `agent.decision.routing_strategy` events created on low-confidence routes. | — | — |
| ~~Completion decision~~ | **Closed** (`18e3be1`). `_complete_task()` consults `DecisionAdvisor("completion_decision")` before final status commit; accepted/rejected/fallback outcomes persisted. | — | — |
| ~~ReAct context compaction~~ | **Closed** (`b315596`). ReAct compaction reads `compactionThreshold` from autonomy profile snapshot via `_autonomy_profile_int()`, falls back to `256000`. Uses progressive compaction: tier 1 (<50K) no-op, tier 2 (50K-220K) truncate tool outputs, tier 3 (>=220K) full primer/summary/recent compact. | — | — |
| ~~Context policy proposal records~~ | **Closed** (`18e3be1`). `_consult_context_policy_advisor()` creates proposal records with token budget, threshold, and trace linkage via `agent.decision.context_policy` events. | — | — |
| ~~Worktree RPC and service~~ | **Closed** (`18e3be1`). `worktree.create/status/diff/cleanup` route through `WorktreeService`; `worktree.merge` RPC added. | — | — |
| Task-worktree binding | `task_worktrees` records exist. | Write-capable tasks are not automatically bound to a task branch/worktree record. | Planning/edit tasks can allocate or resolve a worktree, write tools execute in the correct worktree root, and task reports show worktree id/path/branch. See `docs/worktree-isolation-design-plan.md`. |
| ~~Worktree hooks~~ | **Closed** (`18e3be1`). `WorktreeService.create_for_task()` fires `before/after_worktree_create`; `merge()` fires `before/after_worktree_merge`. `HookService` shared between Orchestrator and WorktreeService. | — | — |
| ~~Memory Chinese recall quality~~ | **Closed** (`18e3be1`). Mojibake tokenizer constants replaced with valid Unicode/CJK ranges; Chinese recall regression tests added. | — | — |
| Settings/Soul management depth | Settings page exposes basic Autonomy and Soul fields. | Full profile lifecycle is not complete: create/duplicate/disable/reset/preview audit UX is still shallow. | UI supports profile CRUD, active profile switching, prompt preview, reset to default, and tests for serialization into runtime config. See `docs/frontend-observability-settings-plan.md`. |
| Provider API formats | Settings form exposes `openai-chat`, `openai-responses`, and `anthropic-messages`; runtime adapter only supports `openai-chat`/`chat-completions` today. | UI can imply support for formats that runtime will reject; provider helper extraction also needs a shared `DEFAULT_PROVIDER_API_FORMAT` import/type source. | Add a shared provider API format type, normalize aliases, mark unsupported formats in UI, and implement/test `openai-responses` and `anthropic-messages` adapters in priority order. See `docs/provider-api-format-support-plan.md`. |
| ~~Permission Policy V2 Lite~~ | **Closed** (`5cca5e8`). `PermissionEngine` with `evaluate()`, 3 presets, config normalizer, 5 tool integrations, `tool.blocked` pipeline status, and 56 dedicated tests. | — | — |
| Large-file follow-up | Backend `service.py`/`sqlite_store.py` have been split; `App.tsx` has been split from 5493 to 652 lines. | `session.css` (3151), `SessionWorkspace.tsx` (2824), `runtimeClient.ts` (1886), `SettingsWorkspace.tsx` (1698) remain above the desired long-term size. | Add a large-file budget gate and split remaining frontend modules without changing behavior. |
| GitHub PR and CI workflow | Git/diff tools exist, but there is no issue/PR/CI workflow service. | Completed local work cannot yet become an approval-gated branch/PR with CI feedback inside the runtime. | Add a GitHub workflow service, publish proposal, approval-gated draft PR creation, CI status ingestion, and report linkage. See `docs/github-pr-ci-workflow-plan.md`. |
| Background and long-running tasks | Background execution, queues, pause/cancel statuses, and partial pending-state persistence exist. | Lifecycle semantics are not yet one explicit contract across ReAct, DAG, supervisor/swarm, approvals, restart recovery, and UI controls. | Define one lifecycle/checkpoint contract with heartbeat, stale detection, recovery, pause/resume/cancel/retry semantics, and tests. See `docs/background-long-running-task-plan.md`. |

## Recommended Next Batch

1. ~~P0: close the default LLM routing advisory loop~~ — **Done**.
2. ~~P0: close the completion decision loop~~ — **Done**.
3. ~~P0: close the context policy loop~~ — **Done**.
4. ~~P0: finish Worktree Isolation P0~~ — **Done**.
5. ~~P0: establish Permission Policy V2 Lite~~ — **Done** (`5cca5e8`).
   PermissionEngine with 3 presets, config normalizer, 5 tool integrations,
   tool.blocked pipeline status, and 56 dedicated tests all merged.
   Provider API format alignment remains a P0 item:
   keep `apiFormat` explicit during provider setup, fix the shared
   default/type source after provider helper extraction, mark unsupported formats
   in UI, and implement `openai-responses` then `anthropic-messages` in the
   runtime adapter. See `docs/provider-api-format-support-plan.md`.
6. ~~P0: fix memory Chinese recall quality~~ — **Done**.
7. ~~P0: finish Runtime Hooks lifecycle hardening~~ — **Done** (worktree hook points wired, 25 hook tests passing).
8. ~~P0: establish the refactoring protection layer~~ — **Done** (R1-R9 refactoring completed with 1841 tests as protection).
9. P0: continue Agent Autonomy Governance with `AutonomyProfile` definitions,
   per-task profile snapshots, and runtime reads for router, compaction, memory,
   ReAct max steps, background execution, and subagent dispatch.
10. P0: deepen Agent Soul and Prompt Profiles beyond the current base config:
   full profile lifecycle UI, prompt preview/audit, safety validation, and
   subagent inheritance behavior.
11. ~~P1: add structured `agent.decision` events and policy gate outcomes~~ — **Done** (routing, completion, context_policy, decomposition, react_turn events all emitted).
12. ~~P1: snapshot active autonomy and soul profiles into each task~~ — **Done** (`_runtime_profile_snapshot()` captures autonomyProfile, agentSoulProfile, promptLayers in routing context).
13. ~~P1: start Code Refactoring R1/R2~~ — **Done** (R1-R9 all completed: TaskStateMachine, HookRepository, ToolPipeline, mixins, message_flow split, react_runner split, patch/schema/context extraction).
14. P1: continue large-file reduction:
    `App.tsx` (5493→652) and backend splits are done; remaining targets are
    `session.css` (3151), `SessionWorkspace.tsx` (2824), `runtimeClient.ts` (1886),
    and `SettingsWorkspace.tsx` (1698).
15. P1: add a large-file regression budget to the release gate:
    flag runtime/frontend files above 1500 lines and require a split plan for
    files above 2500 lines.
16. P0: reconcile the legacy `chat-runtime-agent-run-todolist.md` open items
   against the newer completed subagent/LLM checklists, then close or rewrite
   stale entries.
17. P0: run the broader frontend regression suite and visual smoke around the
   session workspace now that subagent panel work is checked off.
18. P1: harden `run_command` beyond cwd-based write-scope checks if shell command
   path isolation becomes a release requirement.
19. P1: add a focused release gate that combines backend subagent tests,
   frontend visibility tests, typecheck, and diff hygiene.
20. P2: review memory/context UI gaps from the legacy chat runtime checklist and
   decide whether they still belong in the current product scope.
