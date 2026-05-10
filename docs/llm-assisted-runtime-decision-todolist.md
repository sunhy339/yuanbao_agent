# LLM-Assisted Runtime Decision TODO

Date: 2026-05-09

This checklist tracks system-wide hard-coded decisions that should become
LLM-assisted proposals guarded by deterministic runtime validators.

## P0: Inventory

- [x] Inventory task and mode routing hard-coded decisions.
- [x] Inventory subagent planning hard-coded decisions.
- [x] Inventory model/provider selection hard-coded decisions.
- [x] Inventory skill selection hard-coded decisions.
- [x] Inventory tool selection and permission hard-coded decisions.
- [x] Inventory MCP selection and configuration hard-coded decisions.
- [x] Inventory context assembly and compaction hard-coded decisions.
- [x] Inventory memory extraction and recall hard-coded decisions.
- [x] Inventory artifact/report hard-coded decisions.
- [x] Inventory risk and approval hard-coded decisions.
- [x] Inventory retry/failure recovery hard-coded decisions.
- [x] Inventory test and verification hard-coded decisions.
- [x] Inventory frontend event presentation hard-coded decisions.
- [x] Inventory final synthesis and TODO maintenance hard-coded decisions.

## P1: Proposal Record Foundation

- [x] Define proposal record schema.
- [x] Add proposal id.
- [x] Add proposal kind.
- [x] Add session id.
- [x] Add task id.
- [x] Add source metadata.
- [x] Add model/turn metadata for LLM proposals.
- [x] Add input summary.
- [x] Add proposal payload.
- [x] Add validation status.
- [x] Add validation reasons.
- [x] Add applied-to references.
- [x] Add created/updated timestamps.
- [x] Add store migration for proposal records or proposal artifacts.
- [x] Add `proposal.create`.
- [x] Add `proposal.validate`.
- [x] Add `proposal.apply`.
- [x] Add `proposal.list`.
- [x] Emit `proposal.created`.
- [x] Emit `proposal.accepted`.
- [x] Emit `proposal.rejected`.
- [x] Emit `proposal.applied`.
- [x] Add tests for proposal persistence.
- [x] Add tests for proposal event emission.

## P2: Proposal Kinds

- [x] Add `intent_mode_proposal`.
- [x] Add `decomposition_proposal`.
- [x] Add `agent_profile_proposal`.
- [x] Add `model_policy_proposal`.
- [x] Add `skill_policy_proposal`.
- [x] Add `tool_policy_proposal`.
- [x] Add `mcp_policy_proposal`.
- [x] Add `context_policy_proposal`.
- [x] Add `memory_policy_proposal`.
- [x] Add `artifact_contract_proposal`.
- [x] Add `risk_policy_proposal`.
- [x] Add `approval_policy_proposal`.
- [x] Add `test_strategy_proposal`.
- [x] Add `failure_recovery_proposal`.
- [x] Add `event_presentation_proposal`.
- [x] Add `synthesis_strategy_proposal`.
- [x] Add `todo_maintenance_proposal`.

## P3: Runtime Validators

- [x] Add schema validator.
- [x] Add session/task state validator.
- [x] Add mode validator.
- [x] Add model/provider availability validator.
- [x] Add model budget validator.
- [x] Add installed skill validator.
- [x] Add skill root allowlist validator.
- [x] Add tool allowlist validator.
- [x] Add tool alias normalizer.
- [x] Add unsafe tool validator.
- [x] Add MCP server availability validator.
- [x] Add MCP tool schema validator.
- [x] Add context token budget validator.
- [x] Add required context section validator.
- [x] Add memory source validator.
- [x] Add artifact contract validator.
- [x] Add risk policy validator.
- [x] Add approval gate validator.
- [x] Add retry budget validator.
- [x] Add test command allowlist validator.
- [x] Add frontend visibility validator.
- [x] Add roadmap edit approval validator.

## P4: Intent and Mode Routing

- [x] Let LLM propose whether to answer directly or create a task.
- [x] Let LLM propose normal, queued, supplement, or collaboration mode.
- [x] Let LLM propose whether clarification is needed.
- [x] Validate proposed mode against current session/task state.
- [x] Fall back to deterministic routing on planner failure.
- [x] Add tests for simple direct answer route.
- [x] Add tests for queued task proposal.
- [x] Add tests for supplement proposal.
- [x] Add tests for invalid mode rejection.

## P5: Model and Provider Policy

- [x] Let LLM propose model class by complexity.
- [x] Let LLM propose stronger model for planning or review.
- [x] Let LLM propose cheaper model for summary/classification.
- [x] Validate configured provider availability.
- [x] Validate workspace model policy.
- [x] Validate budget constraints.
- [x] Add tests for accepted model proposal.
- [x] Add tests for unavailable model rejection.
- [x] Add tests for budget rejection.

## P6: Skill, Tool, and MCP Policy

- [x] Let LLM propose relevant installed skills.
- [x] Let LLM propose no skill when none is needed.
- [x] Let LLM propose minimal required tools.
- [x] Let LLM propose read-only tool scope.
- [x] Let LLM propose write-capable tool scope.
- [x] Let LLM propose relevant MCP server/tool.
- [x] Validate skill availability.
- [x] Validate tool aliases and allowlist.
- [x] Validate unsafe tool rejection.
- [x] Validate MCP availability and schema.
- [x] Add tests for skill proposal acceptance.
- [x] Add tests for missing skill rejection.
- [x] Add tests for unsafe tool rejection.
- [x] Add tests for unavailable MCP rejection.

## P7: Context and Memory Policy

- [x] Let LLM propose task-relevant context sections.
- [x] Let LLM propose context sections to summarize.
- [x] Let LLM propose context sections to drop.
- [x] Let LLM propose memory recall focus.
- [x] Let LLM propose memory extraction from completed work.
- [x] Let LLM propose stale memory invalidation.
- [x] Validate token budget.
- [x] Validate required system/safety sections.
- [x] Validate memory source ids.
- [x] Add tests for context proposal accepted.
- [x] Add tests for over-budget context proposal repaired.
- [x] Add tests for invalid memory source rejection.

## P8: Subagent and Task Graph Policy

- [x] Use `docs/subagent-generation-todolist.md` for detailed subagent tasks.
- [x] Ensure system proposal records link to subagent generation reports.
- [x] Ensure dynamic agent profiles use shared proposal validation.
- [x] Ensure DAG scheduling uses shared dependency validator.
- [x] Ensure write scopes use shared scope validator.

## P9: Risk, Approval, Test, and Recovery Policy

- [x] Let LLM propose risk level.
- [x] Let LLM propose approval gates.
- [x] Let LLM propose reviewer requirements.
- [x] Let LLM propose verifier requirements.
- [x] Let LLM propose test strategy by changed files and risk.
- [x] Let LLM propose retry or fallback strategy.
- [x] Let LLM propose when to ask the user for clarification.
- [x] Validate approval requirements.
- [x] Validate test commands.
- [x] Validate retry budget.
- [x] Add tests for high-risk proposal requiring reviewer.
- [x] Add tests for unsafe approval bypass rejection.
- [x] Add tests for invalid test command rejection.
- [x] Add tests for retry budget exhaustion.

## P10: Event Presentation and Synthesis

- [x] Let LLM propose trace summary grouping.
- [x] Let LLM propose panel summary labels.
- [x] Let LLM propose final synthesis structure.
- [x] Let LLM propose completed/failed/skipped summary.
- [x] Validate raw trace remains available.
- [x] Validate source event links.
- [x] Validate unverified artifacts are not claimed complete.
- [x] Add tests for trace summary linked to event ranges.
- [x] Add tests for final synthesis with mixed child outcomes.

## P11: TODO and Roadmap Maintenance

- [x] Let LLM propose completed TODO updates from verified artifacts.
- [x] Let LLM propose new TODOs from reviewer findings.
- [x] Let LLM propose next-batch priority.
- [x] Require approval before editing roadmap docs.
- [x] Link TODO suggestions to trace/artifact evidence.
- [x] Add tests for roadmap proposal creation.
- [x] Add tests for approval-required roadmap application.

## P12: Acceptance Scenarios

- [x] Scenario: simple question uses no LLM decomposition proposal.
- [x] Scenario: medium task receives accepted intent/mode proposal.
- [x] Scenario: UI task receives subagent decomposition proposal.
- [x] Scenario: planner proposes unsafe tool and runtime rejects it.
- [x] Scenario: planner proposes unavailable skill and runtime rejects it.
- [x] Scenario: planner proposes relevant test strategy and verifier runs it.
- [x] Scenario: high-risk patch proposal requires approval.
- [x] Scenario: trace summary links to raw event sequence ranges.
- [x] Scenario: final synthesis refuses to claim unverified artifacts.
- [x] Scenario: TODO maintenance suggestion requires approval before docs change.

