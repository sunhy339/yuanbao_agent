"""Schema bootstrap and migration helpers for SQLiteStore.

Extracted from sqlite_store.py to reduce file size.
Contains the _bootstrap method (table creation) and all _ensure_*_columns
migration helpers.
"""
from __future__ import annotations

from typing import Any


class SchemaBootstrapMixin:
    """Mixin providing database schema creation and column migration."""

    def _bootstrap(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                root_path TEXT NOT NULL UNIQUE,
                focus TEXT,
                summary TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                title TEXT,
                status TEXT NOT NULL,
                summary TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                goal TEXT NOT NULL,
                acceptance_criteria_json TEXT,
                out_of_scope_json TEXT,
                current_step TEXT,
                plan_json TEXT,
                changed_files_json TEXT,
                commands_json TEXT,
                verification_json TEXT,
                reflection_json TEXT,
                routing_json TEXT,
                summary TEXT,
                result_json TEXT,
                error_code TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                root_task_id TEXT DEFAULT NULL,
                role TEXT DEFAULT 'root',
                active_assistant_message_id TEXT DEFAULT NULL,
                created_seq INTEGER DEFAULT NULL,
                tests_run_json TEXT DEFAULT NULL,
                risks_json TEXT DEFAULT NULL,
                structured_result_json TEXT DEFAULT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                task_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                client_message_id TEXT DEFAULT NULL,
                kind TEXT DEFAULT 'normal',
                status TEXT DEFAULT 'completed',
                created_seq INTEGER DEFAULT NULL,
                updated_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                prompt TEXT NOT NULL,
                schedule TEXT NOT NULL,
                status TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_run_at INTEGER,
                next_run_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS scheduled_task_runs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at INTEGER NOT NULL,
                finished_at INTEGER,
                summary TEXT,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS patches (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                summary TEXT,
                diff_text TEXT NOT NULL,
                status TEXT NOT NULL,
                files_changed INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS command_logs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                command TEXT NOT NULL,
                cwd TEXT NOT NULL,
                exit_code INTEGER,
                status TEXT NOT NULL,
                stdout_path TEXT,
                stderr_path TEXT,
                started_at INTEGER NOT NULL,
                finished_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                request_json TEXT NOT NULL,
                decision TEXT,
                decided_by TEXT,
                created_at INTEGER NOT NULL,
                decided_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS pending_react_tasks (
                task_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                goal TEXT NOT NULL,
                context_json TEXT NOT NULL,
                messages_json TEXT NOT NULL,
                tool_results_json TEXT NOT NULL,
                pending_tool_call_json TEXT NOT NULL,
                pending_tool_spec_json TEXT NOT NULL,
                remaining_tool_calls_json TEXT NOT NULL,
                steps INTEGER NOT NULL,
                react_started INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pending_dag_tasks (
                task_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                goal TEXT NOT NULL,
                context_json TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                completed_ids_json TEXT NOT NULL,
                failed_ids_json TEXT NOT NULL,
                results_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trace_events (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                type TEXT NOT NULL,
                source TEXT NOT NULL,
                related_id TEXT,
                payload_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                sequence INTEGER NOT NULL,
                visibility TEXT NOT NULL DEFAULT 'chat'
            );

            CREATE TABLE IF NOT EXISTS collaboration_tasks (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                parent_task_id TEXT,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL,
                priority INTEGER NOT NULL,
                assigned_worker_id TEXT,
                dependencies_json TEXT NOT NULL,
                result_json TEXT,
                error_json TEXT,
                metadata_json TEXT NOT NULL,
                claimed_at INTEGER,
                completed_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_workers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                status TEXT NOT NULL,
                current_task_id TEXT,
                capabilities_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_heartbeat_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_messages (
                id TEXT PRIMARY KEY,
                sender_worker_id TEXT NOT NULL,
                recipient_worker_id TEXT,
                task_id TEXT,
                kind TEXT NOT NULL,
                body TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                read_at INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_trace_events_task_order
                ON trace_events (task_id, created_at, sequence);

            CREATE INDEX IF NOT EXISTS idx_messages_session_created
                ON messages (session_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_scheduled_task_runs_task_started
                ON scheduled_task_runs (task_id, started_at DESC);

            CREATE INDEX IF NOT EXISTS idx_collaboration_tasks_queue
                ON collaboration_tasks (status, priority, updated_at DESC);

            CREATE INDEX IF NOT EXISTS idx_collaboration_tasks_parent
                ON collaboration_tasks (parent_task_id, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_agent_workers_status
                ON agent_workers (status, updated_at DESC);

            CREATE INDEX IF NOT EXISTS idx_agent_messages_task_created
                ON agent_messages (task_id, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_agent_messages_recipient_created
                ON agent_messages (recipient_worker_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS task_metrics (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                duration_ms INTEGER,
                tool_call_count INTEGER DEFAULT 0,
                command_count INTEGER DEFAULT 0,
                patch_count INTEGER DEFAULT 0,
                provider_call_count INTEGER DEFAULT 0,
                patch_success_count INTEGER DEFAULT 0,
                patch_failure_count INTEGER DEFAULT 0,
                command_success_count INTEGER DEFAULT 0,
                command_failure_count INTEGER DEFAULT 0,
                task_status TEXT,
                patch_repair_attempts INTEGER DEFAULT 0,
                approval_approved_count INTEGER DEFAULT 0,
                approval_rejected_count INTEGER DEFAULT 0,
                was_cancelled INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_task_metrics_session
                ON task_metrics (session_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS scratchpad_entries (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(session_id, key)
            );

            CREATE INDEX IF NOT EXISTS idx_scratchpad_session
                ON scratchpad_entries (session_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS compaction_records (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                strategy TEXT NOT NULL,
                tokens_before INTEGER NOT NULL,
                tokens_after INTEGER NOT NULL,
                summary TEXT,
                primer_hash TEXT,
                handoff_summary_json TEXT DEFAULT '{}',
                created_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_compaction_session
                ON compaction_records (session_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS skill_presets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                system_prompt TEXT NOT NULL,
                tool_whitelist TEXT NOT NULL,
                parameter_constraints TEXT NOT NULL DEFAULT '{}',
                category TEXT NOT NULL DEFAULT 'custom',
                tool_policy TEXT NOT NULL DEFAULT 'strict_whitelist',
                is_builtin INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_skill_presets_category
                ON skill_presets (category);

            CREATE TABLE IF NOT EXISTS mcp_servers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                transport TEXT NOT NULL DEFAULT 'stdio',
                command TEXT,
                args TEXT DEFAULT '[]',
                url TEXT,
                headers TEXT DEFAULT '{}',
                env TEXT DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory_entries (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                workspace_id TEXT,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                keywords TEXT,
                metadata TEXT DEFAULT '{}',
                created_at INTEGER NOT NULL,
                accessed_at INTEGER NOT NULL,
                access_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS task_inbox (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                message_id TEXT,
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                consumed_by_turn_id TEXT,
                created_seq INTEGER DEFAULT NULL,
                created_at INTEGER NOT NULL,
                consumed_at INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_task_inbox_task_status
                ON task_inbox (task_id, status);

            CREATE INDEX IF NOT EXISTS idx_memory_kind
                ON memory_entries (kind);
            CREATE INDEX IF NOT EXISTS idx_memory_workspace
                ON memory_entries (workspace_id);
            CREATE INDEX IF NOT EXISTS idx_memory_session
                ON memory_entries (session_id);
            CREATE INDEX IF NOT EXISTS idx_memory_accessed
                ON memory_entries (accessed_at);

            CREATE INDEX IF NOT EXISTS idx_memory_kind_workspace_accessed
                ON memory_entries (kind, workspace_id, accessed_at);
            CREATE INDEX IF NOT EXISTS idx_memory_kind_session_accessed
                ON memory_entries (kind, session_id, accessed_at);

            CREATE TABLE IF NOT EXISTS trace_spans (
                trace_id TEXT NOT NULL,
                span_id TEXT PRIMARY KEY,
                parent_span_id TEXT,
                operation TEXT NOT NULL,
                started_at INTEGER NOT NULL,
                finished_at INTEGER,
                status TEXT NOT NULL DEFAULT 'in_progress',
                attributes TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_spans_trace
                ON trace_spans (trace_id);
            CREATE INDEX IF NOT EXISTS idx_spans_parent
                ON trace_spans (parent_span_id);

            CREATE TABLE IF NOT EXISTS llm_cache (
                prompt_hash TEXT PRIMARY KEY,
                response TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skill_usage (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                skill_id TEXT NOT NULL,
                triggered_at INTEGER NOT NULL,
                triggered_by TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_skill_usage_skill_id
                ON skill_usage (skill_id);
            CREATE INDEX IF NOT EXISTS idx_skill_usage_task_id
                ON skill_usage (task_id);

            CREATE TABLE IF NOT EXISTS memory_recall_records (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                task_id TEXT,
                query TEXT NOT NULL,
                memory_ids TEXT NOT NULL DEFAULT '[]',
                scores TEXT NOT NULL DEFAULT '{}',
                injected INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_recall_session
                ON memory_recall_records (session_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS session_rolling_summaries (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                covered_message_ids TEXT NOT NULL DEFAULT '[]',
                token_estimate INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_rolling_summary_session
                ON session_rolling_summaries (session_id);

            CREATE TABLE IF NOT EXISTS agent_profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                role TEXT NOT NULL DEFAULT 'custom',
                cwd TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                permission_mode TEXT,
                provider_profile_id TEXT,
                model TEXT,
                skill_ids_json TEXT NOT NULL DEFAULT '[]',
                mcp_server_ids_json TEXT DEFAULT '[]',
                tool_policy_json TEXT,
                system_prompt TEXT,
                is_builtin INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_agent_profiles_role
                ON agent_profiles (role);
            """
        )
        self._conn.commit()

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS provider_turns (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                model TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error_summary TEXT,
                request_message_count INTEGER,
                request_tool_count INTEGER,
                request_token_estimate INTEGER,
                response_finish_reason TEXT,
                response_usage_json TEXT,
                response_tool_call_count INTEGER,
                context_snapshot_id TEXT,
                turn_decision TEXT,
                thought_summary TEXT,
                tool_policy_decision_json TEXT,
                role_snapshot_json TEXT,
                failure_recovery_json TEXT,
                created_at INTEGER NOT NULL,
                completed_at INTEGER
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_provider_turns_task ON provider_turns(task_id)"
        )
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS context_snapshots (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                provider_turn_id TEXT,
                included_sections_json TEXT,
                trimmed_sections_json TEXT,
                dropped_sections_json TEXT,
                recent_message_ids_json TEXT,
                summarized_message_ids_json TEXT,
                memory_ids_json TEXT,
                supplement_inbox_ids_json TEXT,
                tool_count INTEGER,
                skill_id TEXT,
                token_estimate INTEGER,
                max_context_tokens INTEGER,
                prompt_layers_json TEXT,
                tool_policy_decision_json TEXT,
                role_snapshot_json TEXT,
                active_worktree_json TEXT,
                created_at INTEGER NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_context_snapshots_task ON context_snapshots(task_id)"
        )
        self._conn.commit()

        # proposal_records table — P1 of llm-assisted-runtime-decision-todolist
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS proposal_records (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                session_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                source_json TEXT NOT NULL DEFAULT '{}',
                input_summary TEXT,
                proposal_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                validation_reasons_json TEXT NOT NULL DEFAULT '[]',
                applied_to_json TEXT NOT NULL DEFAULT '{}',
                model_id TEXT,
                turn_id TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proposal_records_session ON proposal_records(session_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proposal_records_task ON proposal_records(task_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proposal_records_status ON proposal_records(status)"
        )
        self._conn.commit()

        # artifacts table — P3 of subagent-generation-todolist
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                parent_task_id TEXT NOT NULL,
                producer_task_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'proposed',
                title TEXT,
                description TEXT,
                content_json TEXT NOT NULL DEFAULT '{}',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_parent_task ON artifacts(parent_task_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_producer_task ON artifacts(producer_task_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_kind ON artifacts(kind)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_status ON artifacts(status)"
        )
        self._conn.commit()

        # Ensure seq_counter table exists
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS seq_counter (
                id INTEGER PRIMARY KEY,
                val INTEGER NOT NULL DEFAULT 0
            )
        """)

        # -- runtime_hooks --
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS runtime_hooks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                scope TEXT NOT NULL DEFAULT 'workspace',
                workspace_id TEXT NOT NULL,
                event TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 100,
                conditions_json TEXT NOT NULL DEFAULT '{}',
                action_json TEXT NOT NULL DEFAULT '{}',
                authority_json TEXT NOT NULL DEFAULT '{}',
                timeout_ms INTEGER NOT NULL DEFAULT 60000,
                retry_json TEXT NOT NULL DEFAULT '{"maxAttempts":0}',
                on_failure TEXT NOT NULL DEFAULT 'warn',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_hooks_workspace ON runtime_hooks(workspace_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_hooks_event ON runtime_hooks(event)"
        )

        # -- hook_executions --
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS hook_executions (
                id TEXT PRIMARY KEY,
                hook_id TEXT NOT NULL,
                event TEXT NOT NULL,
                session_id TEXT,
                task_id TEXT,
                trigger_event_id TEXT,
                condition_result TEXT NOT NULL DEFAULT 'matched',
                policy_outcome TEXT NOT NULL DEFAULT 'allowed',
                approval_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                started_at INTEGER NOT NULL,
                finished_at INTEGER,
                duration_ms INTEGER,
                input_summary TEXT,
                output_summary TEXT,
                error_summary TEXT,
                created_at INTEGER NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hook_executions_task ON hook_executions(task_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hook_executions_hook ON hook_executions(hook_id)"
        )

        # -- scope_conflict_checks --
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS scope_conflict_checks (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                session_id TEXT,
                check_type TEXT NOT NULL,
                subtask_ids_json TEXT NOT NULL DEFAULT '[]',
                scope_map_json TEXT NOT NULL DEFAULT '{}',
                overlaps_json TEXT NOT NULL DEFAULT '[]',
                resolution TEXT NOT NULL DEFAULT 'none',
                serialized_order_json TEXT,
                safe INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scope_conflict_checks_task ON scope_conflict_checks(task_id)"
        )

        # replay_sessions table — P2 Replay And Dry-Run Replay
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS replay_sessions (
                id TEXT PRIMARY KEY,
                source_task_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                config_overrides_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                timeline_json TEXT,
                warnings_json TEXT NOT NULL DEFAULT '[]',
                gate_evaluations_json TEXT,
                summary TEXT,
                error_summary TEXT,
                started_at INTEGER,
                completed_at INTEGER,
                created_at INTEGER NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_replay_sessions_task ON replay_sessions(source_task_id)"
        )

        # task_worktrees table — P0 Worktree Isolation
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS task_worktrees (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                session_id TEXT,
                workspace_id TEXT NOT NULL,
                base_ref TEXT NOT NULL,
                branch_name TEXT NOT NULL,
                worktree_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'creating',
                cleanup_policy TEXT NOT NULL DEFAULT 'ask_user',
                merge_policy TEXT NOT NULL DEFAULT 'approval_required',
                last_status_json TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                cleaned_at INTEGER
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_worktrees_task ON task_worktrees(task_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_worktrees_workspace ON task_worktrees(workspace_id)"
        )

        self._conn.execute(
            "INSERT OR IGNORE INTO seq_counter (id, val) VALUES (1, 0)"
        )
        self._conn.commit()

        self._ensure_workspace_columns()
        self._ensure_task_columns()
        self._ensure_message_columns()
        self._ensure_patch_columns()
        self._ensure_collaboration_task_columns()
        self._ensure_schedule_columns()
        self._ensure_compaction_columns()
        self._ensure_provider_turn_columns()
        self._ensure_context_snapshot_columns()
        self._ensure_inbox_columns()
        self._ensure_mcp_server_columns()

    def _ensure_workspace_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(workspaces)").fetchall()
        }
        if "focus" not in columns:
            self._conn.execute("ALTER TABLE workspaces ADD COLUMN focus TEXT")
        if "summary" not in columns:
            self._conn.execute("ALTER TABLE workspaces ADD COLUMN summary TEXT")
        self._conn.commit()

    def _ensure_task_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        expected = {
            "acceptance_criteria_json": "TEXT DEFAULT '[]'",
            "out_of_scope_json": "TEXT DEFAULT '[]'",
            "current_step": "TEXT",
            "changed_files_json": "TEXT DEFAULT '[]'",
            "commands_json": "TEXT DEFAULT '[]'",
            "verification_json": "TEXT DEFAULT '[]'",
            "reflection_json": "TEXT",
            "routing_json": "TEXT",
            "summary": "TEXT",
            "root_task_id": "TEXT DEFAULT NULL",
            "role": "TEXT DEFAULT 'root'",
            "active_assistant_message_id": "TEXT DEFAULT NULL",
            "created_seq": "INTEGER DEFAULT NULL",
            "tests_run_json": "TEXT DEFAULT NULL",
            "risks_json": "TEXT DEFAULT NULL",
            "structured_result_json": "TEXT DEFAULT NULL",
        }
        for column, definition in expected.items():
            if column not in columns:
                self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {definition}")
        self._conn.execute("UPDATE tasks SET root_task_id = id WHERE root_task_id IS NULL OR root_task_id = ''")
        self._conn.commit()

    def _ensure_message_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        expected = {
            "client_message_id": "TEXT DEFAULT NULL",
            "kind": "TEXT DEFAULT 'normal'",
            "status": "TEXT DEFAULT 'completed'",
            "created_seq": "INTEGER DEFAULT NULL",
            "updated_at": "INTEGER",
        }
        for column, definition in expected.items():
            if column not in columns:
                self._conn.execute(f"ALTER TABLE messages ADD COLUMN {column} {definition}")
        self._conn.commit()

    def _ensure_patch_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(patches)").fetchall()
        }
        if "summary" not in columns:
            self._conn.execute("ALTER TABLE patches ADD COLUMN summary TEXT")
        if "diff_text" not in columns:
            self._conn.execute("ALTER TABLE patches ADD COLUMN diff_text TEXT DEFAULT ''")
        if "status" not in columns:
            self._conn.execute("ALTER TABLE patches ADD COLUMN status TEXT DEFAULT 'proposed'")
        if "files_changed" not in columns:
            self._conn.execute("ALTER TABLE patches ADD COLUMN files_changed INTEGER DEFAULT 0")
        if "created_at" not in columns:
            self._conn.execute("ALTER TABLE patches ADD COLUMN created_at INTEGER DEFAULT 0")
        if "updated_at" not in columns:
            self._conn.execute("ALTER TABLE patches ADD COLUMN updated_at INTEGER DEFAULT 0")
        self._conn.commit()

    def _ensure_schedule_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(scheduled_tasks)").fetchall()
        }
        expected = {
            "prompt": "TEXT DEFAULT ''",
            "schedule": "TEXT DEFAULT 'every 30 minutes'",
            "status": "TEXT DEFAULT 'active'",
            "enabled": "INTEGER DEFAULT 1",
            "created_at": "INTEGER DEFAULT 0",
            "updated_at": "INTEGER DEFAULT 0",
            "last_run_at": "INTEGER",
            "next_run_at": "INTEGER",
        }
        for column, definition in expected.items():
            if column not in columns:
                self._conn.execute(f"ALTER TABLE scheduled_tasks ADD COLUMN {column} {definition}")

        run_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(scheduled_task_runs)").fetchall()
        }
        run_expected = {
            "status": "TEXT DEFAULT 'completed'",
            "started_at": "INTEGER DEFAULT 0",
            "finished_at": "INTEGER",
            "summary": "TEXT",
            "error": "TEXT",
        }
        for column, definition in run_expected.items():
            if column not in run_columns:
                self._conn.execute(f"ALTER TABLE scheduled_task_runs ADD COLUMN {column} {definition}")
        self._conn.commit()

    def _ensure_compaction_columns(self) -> None:
        """Add traceability columns to compaction_records if missing."""
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(compaction_records)").fetchall()
        }
        expected = {
            "covered_message_ids": "TEXT DEFAULT '[]'",
            "trimmed_sections": "TEXT DEFAULT '[]'",
            "task_id": "TEXT",
            "handoff_summary_json": "TEXT DEFAULT '{}'",
        }
        for column, definition in expected.items():
            if column not in columns:
                self._conn.execute(f"ALTER TABLE compaction_records ADD COLUMN {column} {definition}")
        self._conn.commit()

    def _ensure_context_snapshot_columns(self) -> None:
        """Add budget columns to context_snapshots if missing."""
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(context_snapshots)").fetchall()
        }
        expected = {
            "max_context_tokens": "INTEGER",
            "prompt_layers_json": "TEXT",
            "tool_policy_decision_json": "TEXT",
            "role_snapshot_json": "TEXT",
            "active_worktree_json": "TEXT",
        }
        for column, definition in expected.items():
            if column not in columns:
                self._conn.execute(f"ALTER TABLE context_snapshots ADD COLUMN {column} {definition}")
        self._conn.commit()

    def _ensure_provider_turn_columns(self) -> None:
        """Add replay/audit columns to provider_turns if missing."""
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(provider_turns)").fetchall()
        }
        expected = {
            "tool_policy_decision_json": "TEXT",
            "role_snapshot_json": "TEXT",
            "failure_recovery_json": "TEXT",
            "response_transport": "TEXT",
        }
        for column, definition in expected.items():
            if column not in columns:
                self._conn.execute(f"ALTER TABLE provider_turns ADD COLUMN {column} {definition}")
        self._conn.commit()

    def _ensure_inbox_columns(self) -> None:
        """Add created_seq column to task_inbox if missing."""
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(task_inbox)").fetchall()
        }
        if "created_seq" not in columns:
            self._conn.execute("ALTER TABLE task_inbox ADD COLUMN created_seq INTEGER DEFAULT NULL")
        self._conn.commit()

    def _ensure_mcp_server_columns(self) -> None:
        """Ensure mcp_servers table has all required columns for migration."""
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(mcp_servers)").fetchall()
        }
        expected = {
            "transport": "TEXT NOT NULL DEFAULT 'stdio'",
            "command": "TEXT",
            "args": "TEXT DEFAULT '[]'",
            "url": "TEXT",
            "headers": "TEXT DEFAULT '{}'",
            "env": "TEXT DEFAULT '{}'",
            "enabled": "INTEGER NOT NULL DEFAULT 1",
        }
        for column, definition in expected.items():
            if column not in columns:
                self._conn.execute(f"ALTER TABLE mcp_servers ADD COLUMN {column} {definition}")
        self._conn.commit()
