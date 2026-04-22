from __future__ import annotations

from typing import Any


class ProviderAdapter:
    """Deterministic provider used to drive the Sprint 1 tool loop."""

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "message": self.summarize_findings(
                goal=prompt,
                context=context,
                tool_results=[],
            ),
            "prompt": prompt,
            "context": context,
        }

    def choose_tool_sequence(self, goal: str, context: dict[str, Any]) -> list[dict[str, Any]]:
        search_config = context.get("search_config", {})
        explicit_command = self._extract_explicit_command(goal)
        sequence: list[dict[str, Any]] = [
            {
                "name": "list_dir",
                "arguments": {
                    "workspaceRoot": context["workspace_root"],
                    "path": ".",
                    "recursive": False,
                    "max_depth": 2,
                    "ignore": search_config.get("ignore", []),
                },
                "plan_step_id": "inspect-workspace",
                "start_token": f"Inspecting the top-level structure of {context['workspace_name']}...",
            }
        ]

        if explicit_command:
            sequence.append(
                {
                    "name": "run_command",
                    "arguments": {
                        "workspaceRoot": context["workspace_root"],
                        "cwd": ".",
                        "command": explicit_command,
                    },
                    "plan_step_id": "run-command",
                    "start_token": f"Preparing to run command: {explicit_command}",
                }
            )
            return sequence

        search_query = context.get("search_query", "")
        if search_query:
            sequence.append(
                {
                    "name": "search_files",
                    "arguments": {
                        "workspaceRoot": context["workspace_root"],
                        "query": search_query,
                        "mode": context.get("search_mode", "content"),
                        "glob": search_config.get("glob", []),
                        "ignore": search_config.get("ignore", []),
                        "max_results": 8,
                    },
                    "plan_step_id": "search-relevant-files",
                    "start_token": f"Searching for files related to: {search_query}",
                }
            )
        return sequence

    def summarize_findings(
        self,
        goal: str,
        context: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> str:
        list_dir_result = self._find_tool_result(tool_results, "list_dir")
        search_result = self._find_tool_result(tool_results, "search_files")
        read_result = self._find_tool_result(tool_results, "read_file")
        command_result = self._find_tool_result(tool_results, "run_command")

        directory_hint = self._describe_directory(list_dir_result)
        search_hint = self._describe_search(search_result)
        read_hint = self._describe_file(read_result)
        command_hint = self._describe_command(command_result)

        parts = [
            f"Completed an initial pass over workspace {context['workspace_name']}.",
            directory_hint,
        ]
        if search_hint:
            parts.append(search_hint)
        if read_hint:
            parts.append(read_hint)
        if command_hint:
            parts.append(command_hint)
            parts.append("Next step: review command output and decide whether a follow-up code change is needed.")
        else:
            parts.append(f"Next step: inspect the most relevant implementation file for goal '{goal}'.")
        return " ".join(part for part in parts if part)

    def pick_follow_up_tool(
        self,
        context: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        search_result = self._find_tool_result(tool_results, "search_files")
        if not search_result:
            return None

        matches = search_result.get("result", {}).get("matches", [])
        if not matches:
            return None

        first_match = matches[0]
        path = first_match.get("path")
        if not path:
            return None

        return {
            "name": "read_file",
            "arguments": {
                "workspaceRoot": context["workspace_root"],
                "path": path,
                "max_bytes": 4000,
            },
            "plan_step_id": "search-relevant-files",
            "start_token": f"Reading the first relevant file: {path}",
        }

    def _find_tool_result(self, tool_results: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
        for item in tool_results:
            if item["name"] == name:
                return item
        return None

    def _extract_explicit_command(self, goal: str) -> str | None:
        lowered = goal.lower()
        for prefix in ("run command:", "execute command:", "cmd:"):
            if lowered.startswith(prefix):
                command = goal[len(prefix) :].strip()
                return command or None
        return None

    def _describe_directory(self, result: dict[str, Any] | None) -> str:
        if not result:
            return "Directory structure was not collected."
        items = result.get("result", {}).get("items", [])
        if not items:
            return "The top-level directory appears empty or has no visible entries."

        labels = [item.get("path") or item.get("name") for item in items[:5]]
        visible = [label for label in labels if label]
        if not visible:
            return "Collected directory metadata, but there are no visible entries to display."
        return f"Top-level entries include: {', '.join(visible)}."

    def _describe_search(self, result: dict[str, Any] | None) -> str:
        if not result:
            return ""
        matches = result.get("result", {}).get("matches", [])
        query = result.get("arguments", {}).get("query", "")
        if not matches:
            return f"No direct matches were found for query '{query}'."
        paths = [match.get("path") for match in matches[:3] if match.get("path")]
        return f"Search for '{query}' returned {len(matches)} match(es), prioritizing: {', '.join(paths)}."

    def _describe_file(self, result: dict[str, Any] | None) -> str:
        if not result:
            return ""
        file_result = result.get("result", {})
        content = (file_result.get("content") or "").strip()
        preview = content.splitlines()[0].strip() if content else ""
        path = file_result.get("path", "unknown file")
        if not preview:
            return f"Read {path}, but the file is empty or returned no preview."
        return f"Read {path}; first line preview: {preview[:120]}."

    def _describe_command(self, result: dict[str, Any] | None) -> str:
        if not result:
            return ""
        command_result = result.get("result", {})
        status = command_result.get("status", "unknown")
        if status == "approval_required":
            approval = command_result.get("approval", {})
            return f"Command is waiting for approval: {approval.get('id', 'unknown approval')}."
        stdout = (command_result.get("stdout") or "").strip()
        stderr = (command_result.get("stderr") or "").strip()
        exit_code = command_result.get("exitCode")
        output_preview = stdout.splitlines()[0] if stdout else stderr.splitlines()[0] if stderr else "no output"
        return f"Command finished with status {status} and exit code {exit_code}; first output: {output_preview[:120]}."
