"""Advisor-led recovery selection for failed tool calls."""

from __future__ import annotations

import logging
from typing import Any

from ..tools.failure_analysis import build_tool_failure_record

logger = logging.getLogger(__name__)


def _looks_like_full_file_patch(arguments: dict[str, Any]) -> bool:
    patch_text = str(arguments.get("patchText") or arguments.get("patch_text") or "")
    if not patch_text.strip():
        return False
    lines = [line.strip() for line in patch_text.splitlines() if line.strip()]
    delete_targets = [
        line[len("*** Delete File: "):].strip()
        for line in lines
        if line.startswith("*** Delete File: ")
    ]
    add_targets = [
        line[len("*** Add File: "):].strip()
        for line in lines
        if line.startswith("*** Add File: ")
    ]
    if not delete_targets or not add_targets:
        return False
    return any(target in set(add_targets) for target in delete_targets)


class ToolRecoveryMixin:
    """Mixin for bounded, auditable tool recovery advice.

    The advisor may choose a recovery action, but this mixin does not execute
    that action. Any follow-up tool, command, MCP refresh, or permission change
    still has to pass through the normal runtime policy and approval gates.
    """

    def _annotate_failed_tool_recovery(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        failure = build_tool_failure_record(tool_name=tool_name, payload=result)
        result.setdefault("failureKind", failure["failureKind"])
        result.setdefault("recoveryHint", failure["recoveryHint"])
        decision = self._select_tool_recovery_action(
            session_id=session_id,
            task=task,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
            failure=failure,
        )
        if decision:
            result["recoveryDecision"] = decision
            failure["recoveryDecision"] = decision
        return failure

    def _select_tool_recovery_action(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        failure: dict[str, Any],
    ) -> dict[str, Any]:
        default_payload = self._default_tool_recovery_payload(
            failure,
            tool_name=tool_name,
            arguments=arguments,
        )
        advice = None
        advisor = getattr(self, "_decision_advisor", None)
        if advisor is not None:
            try:
                advice = advisor.advise(
                    "tool_recovery",
                    self._tool_recovery_advisor_context(
                        task=task,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        arguments=arguments,
                        failure=failure,
                    ),
                )
            except Exception:  # noqa: BLE001
                logger.debug("Tool recovery advisor failed", exc_info=True)

        selected_payload = self._bounded_tool_recovery_payload(
            default_payload,
            default_payload=default_payload,
            tool_name=tool_name,
        )
        if advice is not None and getattr(advice, "accepted", False):
            payload = getattr(advice, "payload", None)
            if isinstance(payload, dict):
                selected_payload = self._bounded_tool_recovery_payload(
                    payload,
                    default_payload=default_payload,
                    tool_name=tool_name,
                )

        proposal_ids = self._record_tool_recovery_proposals(
            session_id=session_id,
            task=task,
            tool_call_id=tool_call_id,
            failure=failure,
            advice=advice,
            selected_payload=selected_payload,
        )
        decision = {
            "action": selected_payload["action"],
            "reason": selected_payload.get("reason") or failure.get("recoveryHint"),
            "source": "llm" if advice is not None and getattr(advice, "accepted", False) else "runtime_fallback",
            "advisorAccepted": bool(advice is not None and getattr(advice, "accepted", False)),
            "confidence": getattr(advice, "confidence", None) if advice is not None else None,
            "rationale": getattr(advice, "rationale", None) if advice is not None else selected_payload.get("reason"),
            "execution": "not_auto_executed",
            "requiresApproval": self._tool_recovery_requires_approval(selected_payload["action"]),
            "proposalIds": proposal_ids,
        }
        for key in (
            "userMessage",
            "retryWithNarrowerArgs",
            "usePartialEvidence",
            "fallbackTool",
            "requestPermission",
            "refreshMcpTools",
            "risk",
        ):
            if key in selected_payload:
                decision[key] = selected_payload[key]
        followup = self._create_tool_recovery_followup_approval(
            session_id=session_id,
            task=task,
            tool_call_id=tool_call_id,
            failed_tool_name=tool_name,
            failed_arguments=arguments,
            failure=failure,
            selected_payload=selected_payload,
            decision=decision,
        )
        if followup:
            decision["followup"] = followup
            if followup.get("executionState") == "approval_pending":
                decision["execution"] = "approval_pending"
        self._publish(
            session_id=session_id,
            task=task,
            event_type="agent.decision.tool_recovery",
            payload={
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "failure": failure,
                "decision": decision,
            },
            visibility="panel",
        )
        return decision

    def _create_tool_recovery_followup_approval(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        tool_call_id: str,
        failed_tool_name: str,
        failed_arguments: dict[str, Any],
        failure: dict[str, Any],
        selected_payload: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any] | None:
        action = str(selected_payload.get("action") or "").strip()
        if action == "refresh_mcp_tools":
            request = self._tool_recovery_refresh_mcp_approval_request(
                task=task,
                tool_call_id=tool_call_id,
                failed_tool_name=failed_tool_name,
                failure=failure,
                selected_payload=selected_payload,
                decision=decision,
            )
            fields = {
                "toolRecoveryAction": "refresh_mcp_tools",
                "toolCallId": tool_call_id,
            }
            return self._create_tool_recovery_approval(
                session_id=session_id,
                task=task,
                request=request,
                fields=fields,
            )
        if action == "fallback_tool":
            request = self._tool_recovery_fallback_tool_approval_request(
                task=task,
                tool_call_id=tool_call_id,
                failed_tool_name=failed_tool_name,
                failed_arguments=failed_arguments,
                failure=failure,
                selected_payload=selected_payload,
                decision=decision,
            )
            if request is None:
                return {
                    "executionState": "unavailable",
                    "reason": "Fallback tool is not available or is denied by policy.",
                }
            fields = {
                "toolRecoveryAction": "fallback_tool",
                "toolCallId": tool_call_id,
                "toolName": request.get("toolName"),
            }
            return self._create_tool_recovery_approval(
                session_id=session_id,
                task=task,
                request=request,
                fields=fields,
            )
        return None

    def _tool_recovery_refresh_mcp_approval_request(
        self,
        *,
        task: dict[str, Any],
        tool_call_id: str,
        failed_tool_name: str,
        failure: dict[str, Any],
        selected_payload: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        server_id = self._mcp_server_id_from_tool_name(failed_tool_name) or "all"
        permission = self._tool_recovery_permission_summary(
            task=task,
            capability="mcpTool",
            tool_name="mcp.tools.refresh",
            context={
                "taskId": task.get("id"),
                "sessionId": task.get("sessionId"),
                "toolCallId": tool_call_id,
                "failedToolName": failed_tool_name,
                "serverId": server_id,
                "source": "tool_recovery_advisor",
            },
        )
        advisor_metadata = self._tool_recovery_advisor_metadata(
            tool_call_id=tool_call_id,
            failed_tool_name=failed_tool_name,
            failure=failure,
            selected_payload=selected_payload,
            decision=decision,
        )
        return {
            "taskId": str(task.get("id") or ""),
            "toolName": "mcp.tools.refresh",
            "arguments": {"serverId": server_id},
            "toolRecoveryAction": "refresh_mcp_tools",
            "toolCallId": tool_call_id,
            "failedToolName": failed_tool_name,
            "serverId": server_id,
            "permissionDecision": permission.get("decision"),
            "capability": permission.get("capability"),
            "permissionReason": permission.get("reason"),
            "advisorEvidence": advisor_metadata,
        }

    def _tool_recovery_fallback_tool_approval_request(
        self,
        *,
        task: dict[str, Any],
        tool_call_id: str,
        failed_tool_name: str,
        failed_arguments: dict[str, Any],
        failure: dict[str, Any],
        selected_payload: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any] | None:
        fallback_tool = selected_payload.get("fallbackTool")
        if not isinstance(fallback_tool, dict) or fallback_tool.get("available") is False:
            return None
        fallback_name = str(fallback_tool.get("name") or fallback_tool.get("toolName") or "").strip()
        if not fallback_name or fallback_name == "run_command":
            return None
        fallback_arguments = fallback_tool.get("arguments")
        arguments = dict(fallback_arguments) if isinstance(fallback_arguments, dict) else {}
        policy_context = {}
        try:
            policy_context = self._context_builder.build(
                session_id=str(task.get("sessionId") or ""),
                goal=task.get("goal") or "",
            )
        except Exception:  # noqa: BLE001
            logger.debug("Failed to build fallback tool policy context", exc_info=True)
        permission = self._tool_recovery_fallback_tool_permission_summary(
            task=task,
            context=policy_context,
            tool_name=fallback_name,
            arguments=arguments,
        )
        if permission.get("decision") == "deny":
            return None
        advisor_metadata = self._tool_recovery_advisor_metadata(
            tool_call_id=tool_call_id,
            failed_tool_name=failed_tool_name,
            failure=failure,
            selected_payload=selected_payload,
            decision=decision,
        )
        advisor_metadata["fallbackForArguments"] = self._redacted_tool_arguments(failed_arguments)
        return {
            "taskId": str(task.get("id") or ""),
            "toolName": fallback_name,
            "arguments": arguments,
            "toolRecoveryAction": "fallback_tool",
            "toolCallId": tool_call_id,
            "failedToolName": failed_tool_name,
            "permissionDecision": permission.get("decision"),
            "capability": permission.get("capability"),
            "permissionReason": permission.get("reason"),
            "advisorEvidence": advisor_metadata,
        }

    def _create_tool_recovery_approval(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        request: dict[str, Any],
        fields: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not task.get("id") or not task.get("sessionId"):
            return {
                "executionState": "unavailable",
                "reason": "Tool recovery approval requires a persisted runtime task.",
            }
        permission_decision = str(request.get("permissionDecision") or "").strip()
        if permission_decision == "deny":
            return {
                "executionState": "denied_by_policy",
                "reason": request.get("permissionReason") or "Tool recovery follow-up denied by policy.",
            }
        approval = None
        if hasattr(self._store, "find_approval_by_request_fields"):
            approval = self._store.find_approval_by_request_fields(
                task_id=task["id"],
                kind="advisor_tool",
                fields=fields,
            )
        created = False
        if approval is None:
            approval = self._store.create_approval(task["id"], "advisor_tool", request)
            created = True
        decision = approval.get("decision")
        execution_state = "approval_pending"
        if decision == "approved":
            execution_state = "approval_approved"
        elif decision == "rejected":
            execution_state = "approval_rejected"
        elif decision not in (None, ""):
            execution_state = "approval_resolved"
        followup = {
            "approvalId": approval["id"],
            "approvalKind": "advisor_tool",
            "approvalDecision": decision or "pending",
            "executionMode": "approval_then_tool",
            "executionState": execution_state,
            "toolRecoveryAction": request.get("toolRecoveryAction"),
            "toolName": request.get("toolName"),
            "serverId": request.get("serverId"),
            "created": created,
        }
        if created:
            self._publish_tool_recovery_approval_requested(
                session_id=session_id,
                task=task,
                approval=approval,
                request=request,
            )
        return {key: value for key, value in followup.items() if value not in (None, "")}

    def _publish_tool_recovery_approval_requested(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        approval: dict[str, Any],
        request: dict[str, Any],
    ) -> None:
        self._publish(
            session_id=session_id,
            task=task,
            event_type="approval.requested",
            payload={
                "approvalId": approval["id"],
                "taskId": task["id"],
                "kind": approval["kind"],
                "request": request,
                "source": "tool_recovery_advisor",
                "advisorEvidence": request.get("advisorEvidence"),
            },
        )
        self._fire_hooks(
            "on_approval_required",
            session_id,
            task,
            extra_context={
                "approvalId": approval["id"],
                "kind": approval["kind"],
                "source": "tool_recovery_advisor",
                "advisorEvidence": request.get("advisorEvidence"),
            },
        )

    def _tool_recovery_advisor_metadata(
        self,
        *,
        tool_call_id: str,
        failed_tool_name: str,
        failure: dict[str, Any],
        selected_payload: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        proposal_ids = decision.get("proposalIds") if isinstance(decision.get("proposalIds"), list) else []
        metadata = {
            "source": "tool_recovery_advisor",
            "requestKind": "tool_recovery",
            "summary": selected_payload.get("reason") or failure.get("recoveryHint"),
            "target": failed_tool_name,
            "blocking": True,
            "toolCallId": tool_call_id,
            "failureKind": failure.get("failureKind"),
            "recoveryAction": selected_payload.get("action"),
            "proposalRecordId": proposal_ids[0] if proposal_ids else None,
            "proposalIds": proposal_ids,
            "rationale": decision.get("rationale"),
        }
        return {key: value for key, value in metadata.items() if value not in ("", None, [])}

    def _tool_recovery_permission_summary(
        self,
        *,
        task: dict[str, Any],
        capability: str,
        tool_name: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            from ..policy.permission_engine import PermissionEngine, PermissionRequest

            config_result = self._store.get_config({}) if hasattr(self._store, "get_config") else {}
            config = config_result.get("config") if isinstance(config_result, dict) else {}
            if not isinstance(config, dict):
                config = {}
            decision = PermissionEngine(config).evaluate(PermissionRequest(
                capability=capability,
                tool_name=tool_name,
                context=context,
            ))
            return {
                "decision": decision.decision,
                "capability": decision.capability,
                "reason": decision.reason,
                "approvalKind": decision.approval_kind,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to evaluate tool recovery permission", exc_info=True)
            return {
                "decision": "approval_required",
                "capability": capability,
                "reason": f"Permission evaluation failed; approval required before recovery execution: {exc}",
                "approvalKind": "advisor_tool",
            }

    def _tool_recovery_fallback_tool_permission_summary(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if hasattr(self, "_advisor_evidence_tool_permission_summary"):
            return self._advisor_evidence_tool_permission_summary(
                task=task,
                context=context,
                request={
                    "kind": "tool_recovery",
                    "target": tool_name,
                },
                tool_name=tool_name,
                arguments=arguments,
            )
        capability = "mcpTool" if tool_name.startswith("mcp__") else "customTool"
        return self._tool_recovery_permission_summary(
            task=task,
            capability=capability,
            tool_name=tool_name,
            context={
                "taskId": task.get("id"),
                "sessionId": task.get("sessionId"),
                "toolName": tool_name,
                "arguments": arguments,
                "source": "tool_recovery_advisor",
            },
        )

    def _tool_recovery_advisor_context(
        self,
        *,
        task: dict[str, Any],
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        failure: dict[str, Any],
    ) -> dict[str, Any]:
        context: dict[str, Any] = {
            "goal": task.get("goal") or "",
            "tool_failure": {
                **failure,
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "arguments": self._redacted_tool_arguments(arguments),
                "isMcp": tool_name.startswith("mcp__"),
                "mcpServerId": self._mcp_server_id_from_tool_name(tool_name),
            },
            "task": {
                "id": task.get("id"),
                "status": task.get("status"),
                "role": task.get("role") or (task.get("routing") or {}).get("runtimeRole"),
            },
            "availableTools": self._available_tool_names_for_recovery()[:80],
        }
        try:
            context["config"] = self._store.get_config({})["config"]
        except Exception:  # noqa: BLE001
            pass
        return context

    def _record_tool_recovery_proposals(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        tool_call_id: str,
        failure: dict[str, Any],
        advice: Any | None,
        selected_payload: dict[str, Any],
    ) -> list[str]:
        proposal_ids: list[str] = []

        def create_record(
            *,
            proposal: dict[str, Any],
            source: dict[str, Any],
            status: str,
            reasons: list[str],
            model_id: str | None = None,
        ) -> str | None:
            try:
                record = self._store.create_proposal({
                    "kind": "tool_recovery",
                    "sessionId": session_id,
                    "taskId": task["id"],
                    "proposal": proposal,
                    "source": source,
                    "inputSummary": f"{failure.get('name')}: {failure.get('summary')}"[:500],
                    "modelId": model_id,
                    "turnId": tool_call_id,
                })
                proposal_id = record["proposal"]["id"]
                self._store.validate_proposal({
                    "proposalId": proposal_id,
                    "status": status,
                    "reasons": reasons,
                })
                return proposal_id
            except Exception:  # noqa: BLE001
                logger.debug("Failed to record tool recovery proposal", exc_info=True)
                return None

        if advice is not None:
            advice_payload = getattr(advice, "payload", None)
            if isinstance(advice_payload, dict) and advice_payload:
                status = "accepted" if bool(getattr(advice, "accepted", False)) else "rejected"
                reasons = [] if status == "accepted" else (
                    list(getattr(advice, "validation_reasons", None) or [])
                    or [str(getattr(advice, "fallback_reason", None) or "advisor rejected")]
                )
                proposal_id = create_record(
                    proposal=advice_payload,
                    source={
                        "type": getattr(advice, "source", "llm"),
                        "confidence": getattr(advice, "confidence", None),
                        "rationale": getattr(advice, "rationale", None),
                        "fallbackReason": getattr(advice, "fallback_reason", None),
                        "failureKind": failure.get("failureKind"),
                    },
                    status=status,
                    reasons=reasons,
                    model_id=getattr(advice, "model_id", None),
                )
                if proposal_id:
                    proposal_ids.append(proposal_id)

        runtime_proposal = {
            **selected_payload,
            "failureKind": failure.get("failureKind"),
            "toolName": failure.get("name"),
            "execution": "not_auto_executed",
        }
        proposal_id = create_record(
            proposal=runtime_proposal,
            source={
                "type": "runtime_bounded_recovery",
                "advisorAccepted": bool(advice is not None and getattr(advice, "accepted", False)),
                "recoveryHint": failure.get("recoveryHint"),
            },
            status="accepted",
            reasons=[],
        )
        if proposal_id:
            proposal_ids.append(proposal_id)
        return proposal_ids

    @staticmethod
    def _default_tool_recovery_payload(
        failure: dict[str, Any],
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        failure_kind = failure.get("failureKind")
        if failure_kind == "mcp_server_unavailable":
            action = "refresh_mcp_tools"
        elif failure_kind == "permission_denied":
            action = "request_permission"
        elif failure_kind == "patch_validation_failed" and _looks_like_full_file_patch(arguments):
            action = "fallback_tool"
        elif failure_kind in {"partial_response", "timeout", "mcp_tool_failed"}:
            action = "retry_narrower"
        else:
            action = "ask_user"
        payload = {
            "action": action,
            "reason": failure.get("recoveryHint") or "Recover the failed tool under normal runtime policy.",
        }
        if action == "refresh_mcp_tools":
            payload["refreshMcpTools"] = True
        if action == "request_permission":
            payload["requestPermission"] = True
        if action == "fallback_tool":
            payload["fallbackTool"] = {
                "name": "write_file",
            }
        if action == "retry_narrower":
            payload["retryWithNarrowerArgs"] = True
        return payload

    def _bounded_tool_recovery_payload(
        self,
        payload: dict[str, Any],
        *,
        default_payload: dict[str, Any],
        tool_name: str,
    ) -> dict[str, Any]:
        bounded = dict(payload)
        action = str(bounded.get("action") or "").strip()
        if action == "refresh_mcp_tools" and not tool_name.startswith("mcp__"):
            bounded = dict(default_payload)
            bounded["boundedReason"] = "refresh_mcp_tools only applies to MCP tools"
        fallback_tool = bounded.get("fallbackTool")
        if isinstance(fallback_tool, dict):
            fallback_name = fallback_tool.get("name") or fallback_tool.get("toolName")
            if isinstance(fallback_name, str) and fallback_name.strip():
                bounded["fallbackTool"] = {
                    **fallback_tool,
                    "available": fallback_name.strip() in set(self._available_tool_names_for_recovery()),
                }
        elif action == "fallback_tool" and tool_name == "apply_patch":
            bounded["fallbackTool"] = {
                "name": "write_file",
                "available": "write_file" in set(self._available_tool_names_for_recovery()),
            }
        return bounded

    @staticmethod
    def _tool_recovery_requires_approval(action: str) -> bool:
        return action in {"request_permission", "fallback_tool", "refresh_mcp_tools"}

    @staticmethod
    def _redacted_tool_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        redacted: dict[str, Any] = {}
        for key, value in arguments.items():
            key_text = str(key)
            if any(token in key_text.casefold() for token in ("key", "token", "secret", "password", "authorization")):
                redacted[key_text] = "<redacted>"
            else:
                text = str(value)
                redacted[key_text] = text[:300] if len(text) > 300 else value
        return redacted

    def _available_tool_names_for_recovery(self) -> list[str]:
        names: set[str] = set()
        for schema in getattr(self._tool_registry, "schemas", []) or []:
            if isinstance(schema, dict) and isinstance(schema.get("name"), str):
                names.add(schema["name"])
        try:
            names.update(str(name) for name in getattr(self._tool_registry, "_tools", {}).keys())
        except Exception:  # noqa: BLE001
            pass
        return sorted(names)

    @staticmethod
    def _mcp_server_id_from_tool_name(tool_name: str) -> str | None:
        if not tool_name.startswith("mcp__"):
            return None
        parts = tool_name.split("__", 2)
        return parts[1] if len(parts) >= 2 and parts[1] else None
