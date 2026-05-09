# Subagent Generation TODO

Date: 2026-05-09

This checklist tracks the concrete implementation work for
`docs/subagent-generation-remediation-plan.md`.

This checklist is a subagent-focused slice of
`docs/llm-assisted-runtime-decision-todolist.md`. Shared proposal records,
validators, model/skill/tool decisions, risk policy, test strategy, and final
synthesis should use the system-wide decision framework.

## P0: Baseline and Safety

- [ ] Capture current behavior with a regression test for headless multi-subagent dispatch.
- [ ] Assert parent task id, child task ids, worker ids, statuses, messages, and trace event types.
- [x] Assert child collaboration lifecycle events use `visibility = "panel"`.
- [x] Assert root assistant streaming events still use `visibility = "chat"`.
- [x] Document the current supported child tool names.

## P0.5: LLM Decision Proposal Framework

- [x] Define `decomposition_proposal` schema.
- [x] Define `agent_profile_proposal` schema.
- [x] Define `tool_policy_proposal` schema.
- [x] Define `scope_policy_proposal` schema.
- [x] Define `dag_proposal` schema.
- [x] Define `artifact_contract_proposal` schema.
- [x] Define `test_strategy_proposal` schema.
- [x] Define `review_strategy_proposal` schema.
- [x] Define `failure_recovery_proposal` schema.
- [x] Define `synthesis_strategy_proposal` schema.
- [x] Define `trace_summary_proposal` schema.
- [x] Add proposer metadata to proposals.
- [x] Add parent task id to proposals.
- [x] Add source goal to proposals.
- [x] Add validation status to proposals.
- [x] Add rejection reasons to invalid proposals.
- [x] Link accepted proposals to child tasks.
- [x] Link accepted proposals to generated reports.
- [x] Persist proposal records or proposal artifacts.
- [x] Add tests for valid proposal acceptance.
- [x] Add tests for invalid proposal rejection.
- [x] Add tests for repairable proposal rejection reasons.

## P0.6: Runtime Proposal Validators

- [x] Add tool allowlist validator.
- [x] Add unsafe tool validator.
- [x] Add dependency graph validator.
- [x] Add owned scope validator.
- [x] Add artifact contract validator.
- [x] Add test command validator.
- [x] Add risk policy validator.
- [x] Add approval gate validator.
- [x] Prevent dispatch when proposal validation fails.
- [x] Support sending rejection reasons back to planner.
- [x] Persist validator decisions in trace or report state.
- [ ] Add fallback path for planner failure.
- [ ] Add fallback path for simple tasks that should not be decomposed.
- [x] Add report field describing planning mode: `llm`, `rule_fallback`, or `manual`.

## P1: Child Tool Alias Normalization

- [x] Add alias normalization before child tool allowlist validation.
- [x] Map `rg` to `search_files`.
- [x] Map `grep` to `search_files`.
- [x] Map `search` to `search_files`.
- [x] Map `cat` to `read_file`.
- [x] Map `read` to `read_file`.
- [x] Map `git status` to `git_status`.
- [x] Map `status` to `git_status`.
- [x] Map `git diff` to `git_diff`.
- [x] Map `diff` to `git_diff`.
- [x] Map `shell` to `run_command`.
- [x] Map `command` to `run_command`.
- [x] Map `patch` to `apply_patch`.
- [x] Keep nested `task` blocked.
- [x] Add tests for string allowlist input.
- [x] Add tests for array allowlist input.
- [x] Add tests for duplicate aliases collapsing to one canonical tool.
- [x] Add tests proving unsafe aliases are rejected.

## P2: Dispatch Summary and Report

- [x] Define a generation report shape for a parent task.
- [x] Include parent task id and session id.
- [x] Include child task id, title, agent type, status, and priority.
- [x] Include worker id, role, status, and capabilities.
- [x] Include execution mode and attempt count.
- [x] Include started/completed timestamps and duration.
- [x] Include result summary.
- [x] Include error code, message, retryable flag, and attempts for failures.
- [ ] Include trace event counts by type and visibility.
- [ ] Include message ids and artifact ids.
- [x] Add store/service helper to build the report from durable state.
- [x] Add tests for completed child tasks.
- [x] Add tests for failed child tasks.
- [x] Add tests for mixed completed/failed child tasks under one parent.

## P3: Artifact Registry Minimum Version

- [x] Add artifact table migration.
- [x] Add artifact model/schema type.
- [x] Support artifact kind `plan`.
- [x] Support artifact kind `file`.
- [x] Support artifact kind `patch`.
- [x] Support artifact kind `review`.
- [x] Support artifact kind `test_report`.
- [x] Support artifact kind `asset`.
- [x] Support artifact status `proposed`.
- [x] Support artifact status `applied`.
- [x] Support artifact status `verified`.
- [x] Support artifact status `rejected`.
- [x] Add `create_artifact`.
- [x] Add `update_artifact`.
- [x] Add `list_artifacts`.
- [x] Add filters by session id.
- [x] Add filters by parent task id.
- [x] Add filters by producer child task id.
- [x] Add filters by kind.
- [x] Add filters by status.
- [x] Add tests for migration on new database.
- [x] Add tests for migration on existing database.
- [x] Add tests for artifact creation and listing.

## P4: Result Message and Artifact Linking

- [ ] Let child executor output artifact candidates in a structured field.
- [x] Register artifacts when a child task completes.
- [ ] Attach artifact ids to `collab.message.sent` payload.
- [x] Attach artifact ids to the child task result metadata.
- [x] Add report query support for artifact references.
- [x] Add tests proving message-to-artifact lookup.
- [x] Add tests proving artifact-to-task lookup.

## P5: Real Process-RPC Child Worker E2E

- [ ] Add an e2e test that uses the process worker instead of a test executor.
- [ ] Use a file-backed runtime database.
- [ ] Create parent task and child explorer task.
- [ ] Allow read-only child tools.
- [ ] Verify child process can search/read workspace context.
- [ ] Verify child events bridge into the parent event bus.
- [ ] Verify child task completes or fails with structured error.
- [ ] Verify trace events are persisted for the child task.
- [ ] Verify parent report includes the child execution result.

## P6: Failure, Timeout, Cancel, and Retry Observability

- [x] Standardize child failure payload fields.
- [x] Persist timeout failures with `CHILD_TASK_TIMEOUT`.
- [x] Persist cancellation failures with `CHILD_TASK_CANCELLED`.
- [x] Persist process startup failures with a stable error code.
- [x] Persist retry attempt counts.
- [x] Emit trace events for retry attempts.
- [x] Add tests for timeout.
- [x] Add tests for cancellation.
- [x] Add tests for retry success.
- [x] Add tests for retry exhaustion.

## P7: Planner Contract

- [x] Define dynamic agent profile schema.
- [x] Include profile `name`.
- [x] Include profile `baseType`.
- [x] Include profile `mission`.
- [x] Include profile `ownedScope`.
- [x] Include profile `allowedTools`.
- [x] Include profile `expectedArtifacts`.
- [x] Include profile `doneCriteria`.
- [x] Include profile `dependencies`.
- [x] Include profile `riskLevel`.
- [x] Include profile `handoffNotes`.
- [x] Persist dynamic profile metadata on child collaboration tasks.
- [x] Show dynamic profile name in generation reports.
- [x] Keep base types small and stable.
- [x] Allow task-specific names such as `Audio Engine Agent`.
- [x] Allow task-specific names such as `Player UI Agent`.
- [x] Allow task-specific names such as `Playlist State Agent`.
- [x] Validate dynamic profiles before dispatch.
- [ ] Let LLM propose whether subagents are needed.
- [ ] Let LLM propose number of subagents.
- [ ] Let LLM propose dynamic agent names.
- [ ] Let LLM propose agent missions.
- [ ] Let LLM propose dependencies.
- [ ] Let LLM propose expected artifacts.
- [ ] Let LLM propose done criteria.
- [ ] Let LLM propose owned write scopes.
- [ ] Let LLM propose allowed tools.
- [ ] Let LLM propose risk level.
- [ ] Let LLM propose reviewer requirements.
- [ ] Let LLM propose verifier requirements.
- [ ] Let LLM propose fallback plan.
- [ ] Let LLM propose failure recovery policy.
- [ ] Let LLM propose final synthesis hints.
- [x] Define planner output schema.
- [x] Include title.
- [ ] Include prompt.
- [x] Include agent type.
- [x] Include priority.
- [x] Include dependencies.
- [x] Include expected artifacts.
- [x] Include allowed tools.
- [x] Include write scope.
- [x] Include verification requirements.
- [x] Add a deterministic planner test fixture for "build a UI music player".
- [x] Validate planner output before dispatch.
- [x] Reject planner output with unsafe tools.
- [x] Reject planner output with invalid dependency references.
- [x] Reject planner output with conflicting write scopes.
- [x] Reject planner output that requires approval but lacks a gate.
- [x] Add tests for LLM planner proposal accepted by validator.
- [x] Add tests for LLM planner proposal rejected by validator.
- [ ] Add tests for LLM planner proposal repaired after rejection.

## P8: DAG Scheduling

- [x] Add dependency-ready task selection.
- [x] Run independent read-only tasks concurrently.
- [x] Keep dependent tasks queued until upstream completion.
- [x] Block downstream tasks when upstream failure policy requires it.
- [ ] Add parent report fields for dependency order and execution order.
- [x] Add tests for serial dependencies.
- [x] Add tests for independent parallel tasks.
- [x] Add tests for blocked downstream tasks.

## P9: Multi-Agent Write Safety

- [ ] Add child task write scope metadata.
- [ ] Enforce write scope for `apply_patch`.
- [ ] Enforce explicit opt-in for `run_command`.
- [ ] Detect overlapping write scopes before dispatch.
- [ ] Record patch artifacts before applying them.
- [ ] Add reviewer gate status.
- [ ] Prevent final merge when reviewer rejects.
- [ ] Add tests for out-of-scope patch rejection.
- [ ] Add tests for patch conflict detection.
- [ ] Add tests for reviewer rejection blocking finalization.

## P10: Frontend Visibility and Recovery

- [ ] Route root chat using `visibility = "chat"`.
- [ ] Route collaboration status using `visibility = "panel"`.
- [ ] Route tool/command details using `visibility = "trace"`.
- [ ] Remove child-task-id heuristics where visibility is sufficient.
- [ ] Add subagent panel data model.
- [ ] Show child task status, worker, duration, result, and artifacts.
- [ ] Add trace drawer filters by task id.
- [ ] Add trace drawer filters by agent type.
- [ ] Add trace drawer filters by visibility.
- [ ] Use `events.after` for reconnect or refresh recovery.
- [ ] Add frontend tests for visibility routing.
- [ ] Add frontend tests for missed-event merge.

## P11: Acceptance Scenarios

- [ ] Scenario: "Build a UI music player" creates a parent generation report.
- [ ] Scenario: explorer child produces a scope artifact.
- [ ] Scenario: worker child proposes file or patch artifacts.
- [ ] Scenario: reviewer child accepts or rejects artifacts.
- [ ] Scenario: trace drawer shows child event chain.
- [ ] Scenario: failed child task remains visible in report.
- [ ] Scenario: refresh restores task, artifact, and trace state.
- [ ] Scenario: common tool alias such as `rg` does not fail dispatch.
- [ ] Scenario: LLM planner creates task-specific agents, not only fixed roles.
- [ ] Scenario: LLM planner proposes unsafe tools and runtime rejects them.
- [ ] Scenario: LLM planner proposes conflicting scopes and runtime rejects them.
- [ ] Scenario: LLM planner proposes test strategy and verifier records results.
- [ ] Scenario: LLM trace summarizer creates a concise report linked to raw trace.

## P12: LLM-Assisted Reporting and Maintenance

- [ ] Add trace summarizer input contract.
- [ ] Add trace summarizer output contract.
- [ ] Link trace summaries to event sequence ranges.
- [ ] Include failures in trace summaries.
- [ ] Include retries in trace summaries.
- [ ] Include generated artifacts in trace summaries.
- [ ] Include review decisions in trace summaries.
- [ ] Add parent synthesis input contract.
- [ ] Add parent synthesis output contract.
- [ ] Ensure synthesis distinguishes completed, failed, and skipped work.
- [ ] Ensure synthesis does not claim unverified artifacts as complete.
- [ ] Add TODO maintenance suggestion contract.
- [ ] Require approval before roadmap docs are modified from trace state.
- [ ] Add tests for trace summary generation with raw trace still available.
- [ ] Add tests for final synthesis from mixed child task outcomes.

## Suggested First Batch

- [x] Implement child tool alias normalization.
- [x] Define first proposal schemas for decomposition and dynamic profiles.
- [x] Add validators for tools, dependencies, and write scopes.
- [x] Add the minimum artifact registry.
- [x] Link artifacts from child result messages.
- [x] Add parent generation report helper.
- [x] Add focused backend tests.
- [x] Run `python -m pytest -q -p no:cacheprovider`.
- [ ] Run `npx tsc --noEmit`.
- [ ] Run `git diff --check`.
