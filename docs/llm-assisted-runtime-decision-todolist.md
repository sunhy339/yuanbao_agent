# LLM-Assisted Runtime Decision TODO

Date: 2026-05-09

This checklist tracks system-wide hard-coded decisions that should become
LLM-assisted proposals guarded by deterministic runtime validators.

## P0: Inventory

- [ ] Inventory task and mode routing hard-coded decisions.
- [ ] Inventory subagent planning hard-coded decisions.
- [ ] Inventory model/provider selection hard-coded decisions.
- [ ] Inventory skill selection hard-coded decisions.
- [ ] Inventory tool selection and permission hard-coded decisions.
- [ ] Inventory MCP selection and configuration hard-coded decisions.
- [ ] Inventory context assembly and compaction hard-coded decisions.
- [ ] Inventory memory extraction and recall hard-coded decisions.
- [ ] Inventory artifact/report hard-coded decisions.
- [ ] Inventory risk and approval hard-coded decisions.
- [ ] Inventory retry/failure recovery hard-coded decisions.
- [ ] Inventory test and verification hard-coded decisions.
- [ ] Inventory frontend event presentation hard-coded decisions.
- [ ] Inventory final synthesis and TODO maintenance hard-coded decisions.

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
- [ ] Add session/task state validator.
- [ ] Add mode validator.
- [ ] Add model/provider availability validator.
- [ ] Add model budget validator.
- [ ] Add installed skill validator.
- [ ] Add skill root allowlist validator.
- [x] Add tool allowlist validator.
- [x] Add tool alias normalizer.
- [x] Add unsafe tool validator.
- [ ] Add MCP server availability validator.
- [ ] Add MCP tool schema validator.
- [ ] Add context token budget validator.
- [ ] Add required context section validator.
- [ ] Add memory source validator.
- [x] Add artifact contract validator.
- [x] Add risk policy validator.
- [x] Add approval gate validator.
- [ ] Add retry budget validator.
- [x] Add test command allowlist validator.
- [ ] Add frontend visibility validator.
- [ ] Add roadmap edit approval validator.

## P4: Intent and Mode Routing

- [ ] Let LLM propose whether to answer directly or create a task.
- [ ] Let LLM propose normal, queued, supplement, or collaboration mode.
- [ ] Let LLM propose whether clarification is needed.
- [ ] Validate proposed mode against current session/task state.
- [ ] Fall back to deterministic routing on planner failure.
- [ ] Add tests for simple direct answer route.
- [ ] Add tests for queued task proposal.
- [ ] Add tests for supplement proposal.
- [ ] Add tests for invalid mode rejection.

## P5: Model and Provider Policy

- [ ] Let LLM propose model class by complexity.
- [ ] Let LLM propose stronger model for planning or review.
- [ ] Let LLM propose cheaper model for summary/classification.
- [ ] Validate configured provider availability.
- [ ] Validate workspace model policy.
- [ ] Validate budget constraints.
- [ ] Add tests for accepted model proposal.
- [ ] Add tests for unavailable model rejection.
- [ ] Add tests for budget rejection.

## P6: Skill, Tool, and MCP Policy

- [ ] Let LLM propose relevant installed skills.
- [ ] Let LLM propose no skill when none is needed.
- [ ] Let LLM propose minimal required tools.
- [ ] Let LLM propose read-only tool scope.
- [ ] Let LLM propose write-capable tool scope.
- [ ] Let LLM propose relevant MCP server/tool.
- [ ] Validate skill availability.
- [ ] Validate tool aliases and allowlist.
- [ ] Validate unsafe tool rejection.
- [ ] Validate MCP availability and schema.
- [ ] Add tests for skill proposal acceptance.
- [ ] Add tests for missing skill rejection.
- [ ] Add tests for unsafe tool rejection.
- [ ] Add tests for unavailable MCP rejection.

## P7: Context and Memory Policy

- [ ] Let LLM propose task-relevant context sections.
- [ ] Let LLM propose context sections to summarize.
- [ ] Let LLM propose context sections to drop.
- [ ] Let LLM propose memory recall focus.
- [ ] Let LLM propose memory extraction from completed work.
- [ ] Let LLM propose stale memory invalidation.
- [ ] Validate token budget.
- [ ] Validate required system/safety sections.
- [ ] Validate memory source ids.
- [ ] Add tests for context proposal accepted.
- [ ] Add tests for over-budget context proposal repaired.
- [ ] Add tests for invalid memory source rejection.

## P8: Subagent and Task Graph Policy

- [x] Use `docs/subagent-generation-todolist.md` for detailed subagent tasks.
- [x] Ensure system proposal records link to subagent generation reports.
- [x] Ensure dynamic agent profiles use shared proposal validation.
- [x] Ensure DAG scheduling uses shared dependency validator.
- [x] Ensure write scopes use shared scope validator.

## P9: Risk, Approval, Test, and Recovery Policy

- [ ] Let LLM propose risk level.
- [ ] Let LLM propose approval gates.
- [ ] Let LLM propose reviewer requirements.
- [ ] Let LLM propose verifier requirements.
- [ ] Let LLM propose test strategy by changed files and risk.
- [ ] Let LLM propose retry or fallback strategy.
- [ ] Let LLM propose when to ask the user for clarification.
- [ ] Validate approval requirements.
- [x] Validate test commands.
- [ ] Validate retry budget.
- [x] Add tests for high-risk proposal requiring reviewer.
- [x] Add tests for unsafe approval bypass rejection.
- [x] Add tests for invalid test command rejection.
- [ ] Add tests for retry budget exhaustion.

## P10: Event Presentation and Synthesis

- [ ] Let LLM propose trace summary grouping.
- [ ] Let LLM propose panel summary labels.
- [ ] Let LLM propose final synthesis structure.
- [ ] Let LLM propose completed/failed/skipped summary.
- [ ] Validate raw trace remains available.
- [ ] Validate source event links.
- [ ] Validate unverified artifacts are not claimed complete.
- [ ] Add tests for trace summary linked to event ranges.
- [ ] Add tests for final synthesis with mixed child outcomes.

## P11: TODO and Roadmap Maintenance

- [ ] Let LLM propose completed TODO updates from verified artifacts.
- [ ] Let LLM propose new TODOs from reviewer findings.
- [ ] Let LLM propose next-batch priority.
- [ ] Require approval before editing roadmap docs.
- [ ] Link TODO suggestions to trace/artifact evidence.
- [ ] Add tests for roadmap proposal creation.
- [ ] Add tests for approval-required roadmap application.

## P12: Acceptance Scenarios

- [ ] Scenario: simple question uses no LLM decomposition proposal.
- [ ] Scenario: medium task receives accepted intent/mode proposal.
- [ ] Scenario: UI task receives subagent decomposition proposal.
- [ ] Scenario: planner proposes unsafe tool and runtime rejects it.
- [ ] Scenario: planner proposes unavailable skill and runtime rejects it.
- [ ] Scenario: planner proposes relevant test strategy and verifier runs it.
- [ ] Scenario: high-risk patch proposal requires approval.
- [ ] Scenario: trace summary links to raw event sequence ranges.
- [ ] Scenario: final synthesis refuses to claim unverified artifacts.
- [ ] Scenario: TODO maintenance suggestion requires approval before docs change.

