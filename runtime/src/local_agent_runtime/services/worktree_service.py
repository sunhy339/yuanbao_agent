"""Worktree Service — orchestrates worktree lifecycle via store + git adapter.

Higher-level operations that coordinate the SQLite worktree records with actual
git worktree operations. Supports hook firing for worktree lifecycle events.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from ..git.worktree_adapter import GitWorktreeAdapter
from ..policy.guard import PolicyGuard
from ..policy.permission_engine import PermissionRequest
from ..services.command_execution import run_shell_command
from ..services.write_scope_enforcement import WriteScopeEnforcer
from ..store.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

_DEFAULT_MERGE_VERIFICATION_TIMEOUT_MS = 120_000
_VERIFICATION_OUTPUT_LIMIT = 4000
_DEFAULT_APPROVAL_DIFF_LIMIT = 12_000


class WorktreeService:
    """Orchestrates worktree create / merge / status / diff / cleanup."""

    def __init__(
        self,
        store: SQLiteStore,
        git_adapter: GitWorktreeAdapter,
        hook_service: Any | None = None,
        policy_guard: PolicyGuard | None = None,
        permission_engine: Any | None = None,
    ) -> None:
        self._store = store
        self._git = git_adapter
        self._hook_service = hook_service
        self._policy_guard = policy_guard
        self._permission_engine = permission_engine

    def _fire_hooks(self, event: str, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Fire hooks for a worktree lifecycle event. No-op if no hook service."""
        if self._hook_service is None:
            return []
        try:
            return self._hook_service.invoke_hooks(event, context)
        except Exception:
            logger.warning("Worktree hook execution failed for %s", event, exc_info=True)
            return []

    # -- High-level operations --------------------------------------------------

    def request_merge_approval(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a formal approval record for a worktree merge request."""
        record = self._store.get_worktree(params)
        wt = record["worktree"]
        target_branch = params.get("targetBranch", "main")
        if wt.get("mergePolicy") == "manual_only":
            raise ValueError("Worktree merge policy is manual_only")

        status = self._git.status(wt["worktreePath"])
        if status.get("dirtyFiles", 0):
            raise ValueError("Worktree has uncommitted changes; review and commit or clean before merge")
        diff = self._git.diff(wt["worktreePath"], wt.get("baseRef", "HEAD"))
        full_diff = self._git.diff_full(wt["worktreePath"], wt.get("baseRef", "HEAD"))
        diff_summary = self._diff_summary(diff=diff, full_diff=full_diff, params=params)
        review = self._review_summary(params)
        self._ensure_reviewer_gate(review)
        multi_agent_strategy = self._multi_agent_worktree_strategy(wt=wt, params=params)
        verification = self._run_merge_verification(wt, params)
        last_status_patch: dict[str, Any] = {
            "dirtyFiles": status.get("dirtyFiles", 0),
            "diffStat": diff.get("diffStat") or "",
            "mergeDiff": diff_summary,
            "review": review,
            "multiAgentWorktreeStrategy": multi_agent_strategy,
        }
        if verification:
            last_status_patch["mergeVerification"] = verification
        self._store.update_worktree({
            "worktreeId": wt["id"],
            "lastStatus": self._merge_last_status(wt, last_status_patch),
        })
        failed_verification = next((item for item in verification if item.get("status") == "failed"), None)
        if failed_verification is not None:
            raise ValueError(
                "Worktree merge verification failed: "
                f"{failed_verification.get('command')}: {failed_verification.get('summary')}"
            )
        wt = self._store.get_worktree({"worktreeId": wt["id"]})["worktree"]
        request = {
            "worktreeId": wt["id"],
            "taskId": wt.get("taskId", ""),
            "branchName": wt.get("branchName", ""),
            "targetBranch": target_branch,
            "baseRef": wt.get("baseRef", "HEAD"),
            "worktreePath": wt.get("worktreePath", ""),
            "diffStat": diff.get("diffStat") or "",
            "diffSummary": diff_summary,
            "diffPreview": diff_summary.get("preview", ""),
            "diffTruncated": diff_summary.get("truncated", False),
            "diffBytes": diff_summary.get("bytes", 0),
            "dirtyFiles": status.get("dirtyFiles", 0),
            "files": status.get("files") or diff.get("files") or [],
            "review": review,
            "reviewStatus": review.get("status"),
            "reviewerSummary": review.get("summary"),
            "multiAgentWorktreeStrategy": multi_agent_strategy,
            "risk": "write merge worktree changes into target branch",
        }
        if verification:
            request["verification"] = verification
            request["verificationCommands"] = [item.get("command") for item in verification if item.get("command")]
            request["verificationStatus"] = "passed"

        stable_fields = {
            "worktreeId": wt["id"],
            "targetBranch": target_branch,
        }
        existing = self._store.find_approval_by_request_fields(
            task_id=wt.get("taskId", ""),
            kind="worktree_merge",
            fields=stable_fields,
        )
        if existing is not None and existing.get("decision") is None:
            approval = self._store.update_approval_request(existing["id"], request)
        else:
            approval = self._store.create_approval(wt["taskId"], "worktree_merge", request)

        return {
            "approval": approval,
            "worktree": wt,
            "gitStatus": status,
            "diff": diff,
            "verification": verification,
        }

    def create_for_task(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a worktree record AND a git worktree for a task.

        Steps:
        1. Validate no existing worktree for this task.
        2. Fire before_worktree_create hooks.
        3. Create git worktree via adapter.
        4. Create store record.
        5. Fire after_worktree_create hooks.
        """
        task_id = params.get("taskId", "")
        workspace_id = params.get("workspaceId", "")
        session_id = params.get("sessionId", "")

        # Check for existing allocation
        existing = self._store.get_worktree_by_task({"taskId": task_id})
        if existing.get("worktree") is not None:
            raise ValueError(f"Task {task_id} already has a worktree")

        hook_context = {
            "workspaceId": workspace_id,
            "sessionId": session_id,
            "taskId": task_id,
            "branchName": params.get("branchName", ""),
            "worktreePath": params.get("worktreePath", ""),
            "baseRef": params.get("baseRef", "HEAD"),
        }

        # Fire before hooks
        self._fire_hooks("before_worktree_create", hook_context)

        # Create git worktree
        Path(params["worktreePath"]).parent.mkdir(parents=True, exist_ok=True)
        git_result = self._git.create(
            branch_name=params["branchName"],
            target_path=params["worktreePath"],
            base_ref=params.get("baseRef", "HEAD"),
        )
        requested_base_ref = params.get("baseRef", "HEAD")
        stable_base_ref = git_result.get("baseRef") or requested_base_ref

        # Create store record
        record = self._store.create_worktree({
            **params,
            "baseRef": stable_base_ref,
        })
        worktree_id = record.get("worktree", {}).get("id")
        if worktree_id:
            record = self._store.update_worktree({
                "worktreeId": worktree_id,
                "status": "active",
                "lastStatus": {
                    **(record.get("worktree", {}).get("lastStatus") or {}),
                    "requestedBaseRef": requested_base_ref,
                    "resolvedBaseRef": stable_base_ref,
                },
            })

        # Fire after hooks
        hook_context["worktreeId"] = record.get("worktree", {}).get("id", "")
        hook_context["gitBranch"] = git_result.get("branch", "")
        self._fire_hooks("after_worktree_create", hook_context)

        return {**record, "git": git_result}

    def merge(self, params: dict[str, Any]) -> dict[str, Any]:
        """Merge a worktree branch back into the target branch.

        Steps:
        1. Get worktree record.
        2. Fire before_worktree_merge hooks.
        3. Merge via git adapter.
        4. Update store record status to ``merged``.
        5. Fire after_worktree_merge hooks.
        """
        record = self._store.get_worktree(params)
        wt = record["worktree"]
        target_branch = params.get("targetBranch", "main")
        branch_name = wt.get("branchName", "")
        approval: dict[str, Any] | None = None
        approved_request: dict[str, Any] = {}
        if wt.get("mergePolicy") == "manual_only":
            raise ValueError("Worktree merge policy is manual_only")
        if wt.get("mergePolicy") == "approval_required":
            approval = self._require_approved_merge_record(wt, params, target_branch)
            approved_request = self._approval_request(approval)
        verification = approved_request.get("verification") if isinstance(approved_request.get("verification"), list) else []
        review = approved_request.get("review") if isinstance(approved_request.get("review"), dict) else self._review_summary(approved_request)
        self._ensure_reviewer_gate(review)
        diff_summary = approved_request.get("diffSummary") if isinstance(approved_request.get("diffSummary"), dict) else {}
        approval_summary = self._approval_summary(approval=approval, request=approved_request)
        multi_agent_strategy = approved_request.get("multiAgentWorktreeStrategy") if isinstance(approved_request.get("multiAgentWorktreeStrategy"), dict) else {}
        status = self._git.status(wt["worktreePath"])
        if status.get("dirtyFiles", 0):
            raise ValueError("Worktree has uncommitted changes; review and commit or clean before merge")
        diff = self._git.diff(wt["worktreePath"], wt.get("baseRef", "HEAD"))

        hook_context = {
            "workspaceId": wt.get("workspaceId", ""),
            "taskId": wt.get("taskId", ""),
            "worktreeId": wt["id"],
            "branchName": branch_name,
            "targetBranch": target_branch,
            "diff": diff,
            "verification": verification,
            "review": review,
            "approvalSummary": approval_summary,
            "multiAgentWorktreeStrategy": multi_agent_strategy,
        }

        # Fire before hooks
        self._fire_hooks("before_worktree_merge", hook_context)

        # Merge via git adapter
        merge_result = self._git.merge(
            branch_name=branch_name,
            target_branch=target_branch,
        )
        if merge_result.get("result") != "ok":
            self._store.update_worktree({
                "worktreeId": wt["id"],
                "status": "failed",
                "lastStatus": self._merge_last_status(wt, {
                    "mergeResult": merge_result,
                    "mergeVerification": verification,
                    "mergeApproval": approval_summary,
                    "mergeDiff": diff_summary,
                    "review": review,
                    "multiAgentWorktreeStrategy": multi_agent_strategy,
                    "dirtyFiles": status.get("dirtyFiles", 0),
                }),
            })
            hook_context["mergeResult"] = merge_result.get("result", "")
            self._fire_hooks("after_worktree_merge", hook_context)
            return {
                "worktreeId": wt["id"],
                "merged": False,
                "branchName": branch_name,
                "targetBranch": target_branch,
                "result": merge_result,
                "verification": verification,
                "approvalSummary": approval_summary,
                "review": review,
                "diffSummary": diff_summary,
                "multiAgentWorktreeStrategy": multi_agent_strategy,
            }

        # Update store record
        self._store.update_worktree({
            "worktreeId": wt["id"],
            "status": "merged",
            "lastStatus": self._merge_last_status(wt, {
                "mergeResult": merge_result,
                "mergeVerification": verification,
                "mergeApproval": approval_summary,
                "mergeDiff": diff_summary,
                "review": review,
                "multiAgentWorktreeStrategy": multi_agent_strategy,
                "dirtyFiles": status.get("dirtyFiles", 0),
            }),
        })

        # Fire after hooks
        hook_context["mergeResult"] = merge_result.get("result", "")
        self._fire_hooks("after_worktree_merge", hook_context)

        return {
            "worktreeId": wt["id"],
            "merged": True,
            "branchName": branch_name,
            "targetBranch": target_branch,
            "result": merge_result,
            "verification": verification,
            "approvalSummary": approval_summary,
            "review": review,
            "diffSummary": diff_summary,
            "multiAgentWorktreeStrategy": multi_agent_strategy,
        }

    def _require_approved_merge_record(
        self,
        wt: dict[str, Any],
        params: dict[str, Any],
        target_branch: str,
    ) -> dict[str, Any]:
        approval_id = params.get("approvalId")
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ValueError("Worktree merge requires approved approval record")
        approval = self._store.get_approval({"approvalId": approval_id})["approval"]
        if approval.get("kind") != "worktree_merge":
            raise ValueError("Approval record is not for worktree merge")
        if approval.get("decision") != "approved":
            raise ValueError("Worktree merge approval has not been approved")
        if approval.get("taskId") != wt.get("taskId"):
            raise ValueError("Worktree merge approval does not belong to this task")
        try:
            request = json.loads(approval.get("requestJson") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Worktree merge approval request is invalid") from exc
        if request.get("worktreeId") != wt.get("id"):
            raise ValueError("Worktree merge approval does not match this worktree")
        approved_target = request.get("targetBranch") or "main"
        if approved_target != target_branch:
            raise ValueError("Worktree merge approval target branch does not match")
        return approval

    def _approval_request(self, approval: dict[str, Any]) -> dict[str, Any]:
        try:
            request = json.loads(approval.get("requestJson") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Worktree merge approval request is invalid") from exc
        return request if isinstance(request, dict) else {}

    def _merge_last_status(self, wt: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        current = wt.get("lastStatus") if isinstance(wt.get("lastStatus"), dict) else {}
        return {**current, **patch}

    def _approval_diff_limit(self, params: dict[str, Any]) -> int:
        raw = params.get("diffPreviewBytes") or params.get("maxDiffBytes")
        if raw is None:
            raw = self._worktree_config().get("mergeApprovalDiffPreviewBytes")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = _DEFAULT_APPROVAL_DIFF_LIMIT
        return max(1000, min(value, 100_000))

    def _diff_summary(
        self,
        *,
        diff: dict[str, Any],
        full_diff: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        text = str(full_diff.get("diff") or "")
        limit = self._approval_diff_limit(params)
        encoded = text.encode("utf-8", errors="replace")
        truncated = len(encoded) > limit
        preview_bytes = encoded[:limit]
        preview = preview_bytes.decode("utf-8", errors="replace")
        return {
            "mode": "preview",
            "diffStat": diff.get("diffStat") or "",
            "preview": preview,
            "bytes": len(encoded),
            "previewBytes": len(preview_bytes),
            "truncated": truncated,
            "fullDiffAvailable": True,
            "returnCode": full_diff.get("returnCode"),
        }

    def _review_summary(self, params: dict[str, Any]) -> dict[str, Any]:
        status = str(params.get("reviewStatus") or "pending").strip() or "pending"
        return {
            "status": status,
            "summary": str(params.get("reviewerSummary") or params.get("reviewSummary") or "").strip(),
            "reviewer": str(params.get("reviewer") or params.get("reviewerId") or "").strip(),
        }

    def _ensure_reviewer_gate(self, review: dict[str, Any]) -> None:
        reasons = WriteScopeEnforcer(self._store).check_reviewer_gate({
            "mergeRequested": True,
            "reviewStatus": review.get("status"),
        })
        if reasons:
            raise ValueError("; ".join(reasons))

    def _multi_agent_worktree_strategy(self, *, wt: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        raw = params.get("multiAgentWorktreeStrategy")
        if isinstance(raw, dict):
            return raw
        strategy = str(raw or self._worktree_config().get("multiAgentWorktreeStrategy") or "").strip()
        try:
            task = self._store.get_task({"taskId": wt.get("taskId", "")}).get("task", {})
        except Exception:  # noqa: BLE001
            task = {}
        routing = task.get("routing") if isinstance(task.get("routing"), dict) else {}
        is_child = bool(routing.get("rootTaskId") or routing.get("parentTaskId") or routing.get("childCollaborationTaskId"))
        if not strategy:
            strategy = "isolated_child_worktrees" if is_child else "root_worktree"
        return {
            "strategy": strategy,
            "taskRole": task.get("role") or routing.get("role") or ("child" if is_child else "root"),
            "rootTaskId": routing.get("rootTaskId") or task.get("rootTaskId"),
            "reason": (
                "Child write scopes merge independently before root integration."
                if strategy == "isolated_child_worktrees"
                else "Root task owns the active worktree for this merge."
            ),
        }

    def _approval_summary(self, *, approval: dict[str, Any] | None, request: dict[str, Any]) -> dict[str, Any]:
        if approval is None:
            return {}
        verification = request.get("verification") if isinstance(request.get("verification"), list) else []
        return {
            "approvalId": approval.get("id"),
            "decision": approval.get("decision"),
            "decidedBy": approval.get("decidedBy") or "user",
            "decidedAt": approval.get("decidedAt"),
            "targetBranch": request.get("targetBranch") or "main",
            "verificationStatus": request.get("verificationStatus") or ("passed" if verification else "not_run"),
            "verificationCount": len(verification),
            "reviewStatus": request.get("reviewStatus"),
            "reviewerSummary": request.get("reviewerSummary"),
        }

    def _worktree_config(self) -> dict[str, Any]:
        try:
            config = self._store.get_config({}).get("config", {})
        except Exception:  # noqa: BLE001
            return {}
        worktree = config.get("worktree") if isinstance(config, dict) else {}
        return worktree if isinstance(worktree, dict) else {}

    def _merge_verification_commands(self, params: dict[str, Any]) -> list[str]:
        raw = params.get("verificationCommands")
        if raw is None:
            raw = params.get("mergeVerificationCommands")
        if raw is None:
            config = self._worktree_config()
            raw = config.get("mergeVerificationCommands")
            if raw is None:
                raw = config.get("verificationCommands")
        return self._normalize_verification_commands(raw)

    def _merge_verification_timeout_ms(self, params: dict[str, Any]) -> int:
        config = self._worktree_config()
        raw = (
            params.get("verificationTimeoutMs")
            or params.get("mergeVerificationTimeoutMs")
            or config.get("mergeVerificationTimeoutMs")
            or config.get("verificationTimeoutMs")
            or _DEFAULT_MERGE_VERIFICATION_TIMEOUT_MS
        )
        try:
            timeout_ms = int(raw)
        except (TypeError, ValueError):
            timeout_ms = _DEFAULT_MERGE_VERIFICATION_TIMEOUT_MS
        return max(1000, timeout_ms)

    def _normalize_verification_commands(self, raw: Any) -> list[str]:
        if isinstance(raw, str):
            candidates: list[Any] = [raw]
        elif isinstance(raw, list):
            candidates = raw
        else:
            candidates = []
        commands: list[str] = []
        for item in candidates:
            command: str | None = None
            if isinstance(item, str):
                command = item
            elif isinstance(item, dict) and isinstance(item.get("command"), str):
                command = item["command"]
            if command is not None and command.strip():
                commands.append(command.strip())
        return commands

    def _policy_guard_for_verification(self) -> PolicyGuard:
        if self._policy_guard is not None:
            return self._policy_guard
        try:
            config = self._store.get_config({}).get("config", {})
            approval_mode = config.get("policy", {}).get("approvalMode", "on_write_or_command")
        except Exception:  # noqa: BLE001
            approval_mode = "on_write_or_command"
        return PolicyGuard(approval_mode=approval_mode)

    def _run_command_config(self) -> dict[str, Any]:
        try:
            config = self._store.get_config({}).get("config", {})
        except Exception:  # noqa: BLE001
            return {}
        tools = config.get("tools") if isinstance(config, dict) else {}
        run_command = tools.get("runCommand") if isinstance(tools, dict) else {}
        return run_command if isinstance(run_command, dict) else {}

    def _verification_shell(self) -> str:
        shell_name = str(self._run_command_config().get("allowedShell") or "powershell").strip().lower()
        if shell_name not in {"powershell", "bash", "zsh"}:
            return "powershell"
        return shell_name

    def _ensure_verification_command_allowed(self, command: str) -> None:
        self._policy_guard_for_verification().validate_command(command, self._run_command_config())
        if self._permission_engine is not None:
            decision = self._permission_engine.evaluate(
                PermissionRequest(capability="runCommand", tool_name="run_command")
            )
            if decision.decision == "deny":
                raise ValueError(decision.reason)

    def _run_merge_verification(self, wt: dict[str, Any], params: dict[str, Any]) -> list[dict[str, Any]]:
        commands = self._merge_verification_commands(params)
        if not commands:
            return []
        timeout_ms = self._merge_verification_timeout_ms(params)
        shell_name = self._verification_shell()
        worktree_path = Path(wt["worktreePath"]).resolve()
        if not worktree_path.is_dir():
            raise ValueError(f"Worktree path does not exist: {wt['worktreePath']}")
        results: list[dict[str, Any]] = []
        for command in commands:
            self._ensure_verification_command_allowed(command)
            command_log = self._store.create_command_log(
                task_id=wt.get("taskId", ""),
                command=command,
                cwd=str(worktree_path),
                shell=shell_name,
            )
            started_at = command_log.get("startedAt") or int(time.time() * 1000)
            stdout = ""
            stderr = ""
            exit_code: int | None = None
            command_status = "failed"
            duration_ms = 0
            try:
                stdout, stderr, exit_code, command_status, duration_ms = run_shell_command(
                    shell_name,
                    command,
                    worktree_path,
                    timeout_ms,
                )
            except Exception as exc:  # noqa: BLE001
                stderr = str(exc)
                command_status = "failed"
            finished_at = self._store.now()
            stdout_path = self._store.write_command_artifact(command_log["id"], "stdout", stdout)
            stderr_path = self._store.write_command_artifact(command_log["id"], "stderr", stderr)
            command_log = self._store.update_command_log(
                command_log["id"],
                status=command_status,
                exit_code=exit_code,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                finished_at=finished_at,
            )
            status = "passed" if command_status == "completed" and exit_code == 0 else "failed"
            stdout_text = self._truncate_output(stdout)
            stderr_text = self._truncate_output(stderr)
            result = {
                "id": command_log["id"],
                "command": command,
                "cwd": str(worktree_path),
                "shell": shell_name,
                "status": status,
                "exitCode": exit_code,
                "durationMs": duration_ms or max(0, finished_at - started_at),
                "summary": self._verification_summary(
                    status=status,
                    exit_code=exit_code,
                    stdout=stdout_text,
                    stderr=stderr_text,
                ),
                "startedAt": started_at,
                "finishedAt": finished_at,
                "stdoutPath": command_log.get("stdoutPath"),
                "stderrPath": command_log.get("stderrPath"),
            }
            if stdout_text:
                result["stdout"] = stdout_text
            if stderr_text:
                result["stderr"] = stderr_text
            results.append(result)
            if status == "failed":
                break
        return results

    def _verification_summary(self, *, status: str, exit_code: int | None, stdout: str, stderr: str) -> str:
        output = (stderr if status == "failed" else stdout).strip()
        if not output:
            output = (stdout or stderr).strip()
        if output:
            return output.splitlines()[0][:240]
        return "Verification passed." if status == "passed" else f"Verification failed with exit code {exit_code}."

    def _truncate_output(self, value: Any) -> str:
        text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
        text = text.strip()
        if len(text) <= _VERIFICATION_OUTPUT_LIMIT:
            return text
        return f"{text[:_VERIFICATION_OUTPUT_LIMIT]}... [truncated]"

    def get_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get worktree record + live git status."""
        record = self._store.get_worktree(params)
        wt = record["worktree"]
        try:
            git_status = self._git.status(wt["worktreePath"])
        except Exception:
            git_status = {"error": "worktree path not accessible"}
        return {"worktree": wt, "gitStatus": git_status}

    def get_diff(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get diff between worktree and its base ref."""
        record = self._store.get_worktree(params)
        wt = record["worktree"]
        if params.get("full") or params.get("includeFullDiff"):
            summary = self._git.diff(wt["worktreePath"], wt["baseRef"])
            full = self._git.diff_full(wt["worktreePath"], wt["baseRef"])
            git_diff = {
                **summary,
                **self._diff_summary(diff=summary, full_diff=full, params=params),
                "diff": full.get("diff") or "",
                "mode": "full",
                "truncated": False,
            }
        else:
            git_diff = self._git.diff(wt["worktreePath"], wt["baseRef"])
        return {"worktree": wt, "diff": git_diff}

    def cleanup(self, params: dict[str, Any]) -> dict[str, Any]:
        """Remove git worktree and mark store record as cleaned.

        Steps:
        1. Check if worktree is clean (no uncommitted changes) or force=True.
        2. Remove git worktree.
        3. Update store record status to ``cleaned``.
        """
        record = self._store.get_worktree(params)
        wt = record["worktree"]
        force = params.get("force", False)

        if not force and not self._git.has_clean_branch(wt["worktreePath"]):
            raise ValueError("Worktree has uncommitted changes; use force=True to override")

        self._git.remove(wt["worktreePath"], force=force)

        self._store.update_worktree({
            "worktreeId": wt["id"],
            "status": "cleaned",
        })
        return {"cleaned": True, "worktreeId": wt["id"]}
