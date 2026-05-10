# Subagent Generation TODO

Date: 2026-05-09

This checklist tracks the concrete implementation work for
`docs/subagent-generation-remediation-plan.md`.

This checklist is a subagent-focused slice of
`docs/llm-assisted-runtime-decision-todolist.md`. Shared proposal records,
validators, model/skill/tool decisions, risk policy, test strategy, and final
synthesis should use the system-wide decision framework.

## P0: Baseline and Safety

- [x] Capture current behavior with a regression test for headless multi-subagent dispatch.
- [x] Assert parent task id, child task ids, worker ids, statuses, messages, and trace event types.
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
- [x] Add fallback path for planner failure.
- [x] Add fallback path for simple tasks that should not be decomposed.
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
- [x] Include trace event counts by type and visibility.
- [x] Include message ids and artifact ids.
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

- [x] Let child executor output artifact candidates in a structured field.
- [x] Register artifacts when a child task completes.
- [x] Attach artifact ids to `collab.message.sent` payload.
- [x] Attach artifact ids to the child task result metadata.
- [x] Add report query support for artifact references.
- [x] Add tests proving message-to-artifact lookup.
- [x] Add tests proving artifact-to-task lookup.

## P5: Real Process-RPC Child Worker E2E

- [x] Add an e2e test that uses the process worker instead of a test executor.
- [x] Use a file-backed runtime database.
- [x] Create parent task and child explorer task.
- [x] Allow read-only child tools.
- [x] Verify child process can search/read workspace context.
- [x] Verify child events bridge into the parent event bus.
- [x] Verify child task completes or fails with structured error.
- [x] Verify trace events are persisted for the child task.
- [x] Verify parent report includes the child execution result.

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
- [x] Let LLM propose whether subagents are needed.
- [x] Let LLM propose number of subagents.
- [x] Let LLM propose dynamic agent names.
- [x] Let LLM propose agent missions.
- [x] Let LLM propose dependencies.
- [x] Let LLM propose expected artifacts.
- [x] Let LLM propose done criteria.
- [x] Let LLM propose owned write scopes.
- [x] Let LLM propose allowed tools.
- [x] Let LLM propose risk level.
- [x] Let LLM propose reviewer requirements.
- [x] Let LLM propose verifier requirements.
- [x] Let LLM propose fallback plan.
- [x] Let LLM propose failure recovery policy.
- [x] Let LLM propose final synthesis hints.
- [x] Define planner output schema.
- [x] Include title.
- [x] Include prompt.
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
- [x] Add tests for LLM planner proposal repaired after rejection.

## P8: DAG Scheduling

- [x] Add dependency-ready task selection.
- [x] Run independent read-only tasks concurrently.
- [x] Keep dependent tasks queued until upstream completion.
- [x] Block downstream tasks when upstream failure policy requires it.
- [x] Add parent report fields for dependency order and execution order.
- [x] Add tests for serial dependencies.
- [x] Add tests for independent parallel tasks.
- [x] Add tests for blocked downstream tasks.

## P9: Multi-Agent Write Safety

- [x] Add child task write scope metadata.
- [x] Enforce write scope for `apply_patch`.
- [x] Enforce explicit opt-in for `run_command`.
- [x] Detect overlapping write scopes before dispatch.
- [x] Record patch artifacts before applying them.
- [x] Add reviewer gate status.
- [x] Prevent final merge when reviewer rejects.
- [x] Add tests for out-of-scope patch rejection.
- [x] Add tests for patch conflict detection.
- [x] Add tests for reviewer rejection blocking finalization.

## P10: Frontend Visibility and Recovery

- [x] Route root chat using `visibility = "chat"`.
- [x] Route collaboration status using `visibility = "panel"`.
- [x] Route tool/command details using `visibility = "trace"`.
- [ ] Remove child-task-id heuristics where visibility is sufficient.
- [ ] Add subagent panel data model.
- [ ] Show child task status, worker, duration, result, and artifacts.
- [ ] Add trace drawer filters by task id.
- [ ] Add trace drawer filters by agent type.
- [ ] Add trace drawer filters by visibility.
- [x] Use `events.after` for reconnect or refresh recovery.
- [ ] Add frontend tests for visibility routing.
- [ ] Add frontend tests for missed-event merge.

## P11: Acceptance Scenarios

- [x] Scenario: "Build a UI music player" creates a parent generation report.
- [x] Scenario: explorer child produces a scope artifact.
- [x] Scenario: worker child proposes file or patch artifacts.
- [x] Scenario: reviewer child accepts or rejects artifacts.
- [ ] Scenario: trace drawer shows child event chain.
- [x] Scenario: failed child task remains visible in report.
- [x] Scenario: refresh restores task, artifact, and trace state.
- [x] Scenario: common tool alias such as `rg` does not fail dispatch.
- [x] Scenario: LLM planner creates task-specific agents, not only fixed roles.
- [x] Scenario: LLM planner proposes unsafe tools and runtime rejects them.
- [x] Scenario: LLM planner proposes conflicting scopes and runtime rejects them.
- [x] Scenario: LLM planner proposes test strategy and verifier records results.
- [x] Scenario: LLM trace summarizer creates a concise report linked to raw trace.

## P12: LLM-Assisted Reporting and Maintenance

- [x] Add trace summarizer input contract.
- [x] Add trace summarizer output contract.
- [x] Link trace summaries to event sequence ranges.
- [x] Include failures in trace summaries.
- [x] Include retries in trace summaries.
- [x] Include generated artifacts in trace summaries.
- [x] Include review decisions in trace summaries.
- [x] Add parent synthesis input contract.
- [x] Add parent synthesis output contract.
- [x] Ensure synthesis distinguishes completed, failed, and skipped work.
- [x] Ensure synthesis does not claim unverified artifacts as complete.
- [x] Add TODO maintenance suggestion contract.
- [x] Require approval before roadmap docs are modified from trace state.
- [x] Add tests for trace summary generation with raw trace still available.
- [x] Add tests for final synthesis from mixed child task outcomes.

## Suggested First Batch

- [x] Implement child tool alias normalization.
- [x] Define first proposal schemas for decomposition and dynamic profiles.
- [x] Add validators for tools, dependencies, and write scopes.
- [x] Add the minimum artifact registry.
- [x] Link artifacts from child result messages.
- [x] Add parent generation report helper.
- [x] Add focused backend tests.
- [x] Run `python -m pytest -q -p no:cacheprovider`.
- [x] Run `npx tsc --noEmit`.
- [x] Run `git diff --check`.
