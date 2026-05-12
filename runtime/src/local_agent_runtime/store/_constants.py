from __future__ import annotations

from copy import deepcopy

CONFIG_KEY = "app_config"

DEFAULT_AUTONOMY_PROFILE = {
    "id": "balanced",
    "name": "Balanced",
    "level": "L2",
    "maxSteps": 20,
    "maxParallelSubtasks": 4,
    "allowBackground": True,
    "allowSubagents": True,
    "allowFileWrite": "approval_required",
    "allowShell": "approval_required",
    "allowNetwork": False,
    "memoryRecallPolicy": "workspace_first_session_boosted",
    "retryLimit": 2,
    "timeoutMs": 600000,
}

DEFAULT_AGENT_SOUL_PROFILE = {
    "id": "default",
    "name": "Default",
    "description": "Default local coding agent identity.",
    "identity": "A capable local coding agent that works inside the user's desktop runtime.",
    "principles": [
        "Be practical, careful, and transparent about uncertainty.",
        "Prefer existing project patterns over unnecessary new abstractions.",
        "Keep the user in control of risky actions.",
    ],
    "communicationStyle": "Clear, concise, collaborative.",
    "reasoningStyle": "Inspect the current workspace before making changes.",
    "collaborationStyle": "Explain meaningful decisions and keep work scoped to the user's request.",
    "domainPreferences": [],
    "customSystemPrompt": "",
    "enabled": True,
    "scope": "global",
    "createdAt": 0,
    "updatedAt": 0,
}


DEFAULT_CONFIG = {
    "provider": {
        "mode": "mock",
        "baseUrl": "https://api.openai.com/v1",
        "model": "gpt-5-codex",
        "defaultModel": "gpt-5-codex",
        "fallbackModel": "claude-sonnet",
        "apiKeyEnvVarName": "LOCAL_AGENT_PROVIDER_API_KEY",
        "temperature": 0.2,
        "maxTokens": 4000,
        "maxOutputTokens": 4000,
        "maxContextTokens": 256000,
        "timeout": 30,
        "activeProfileId": "default",
        "profiles": [
            {
                "id": "default",
                "name": "Default",
                "mode": "mock",
                "baseUrl": "https://api.openai.com/v1",
                "model": "gpt-5-codex",
                "defaultModel": "gpt-5-codex",
                "fallbackModel": "claude-sonnet",
                "apiKeyEnvVarName": "LOCAL_AGENT_PROVIDER_API_KEY",
                "temperature": 0.2,
                "maxTokens": 4000,
                "maxOutputTokens": 4000,
                "maxContextTokens": 256000,
                "timeout": 30,
            }
        ],
    },
    "workspace": {
        "rootPath": "",
        "ignore": [".git", "node_modules", "dist", ".venv"],
        "writableRoots": [],
    },
    "search": {
        "glob": [],
        "ignore": [".git", "node_modules", "dist", ".venv", "target", "__pycache__"],
    },
    "policy": {
        "approvalMode": "on_write_or_command",
        "commandTimeoutMs": 600000,
        "maxTaskSteps": 20,
        "maxPatchRepairAttempts": 2,
        "maxFilesPerPatch": 20,
        "allowNetwork": False,
        "postTaskValidation": {
            "command": None,
        },
    },
    "autonomy": {
        "activeProfileId": "balanced",
        "profiles": [
            {
                **DEFAULT_AUTONOMY_PROFILE,
                "id": "locked_down",
                "name": "Locked Down",
                "level": "L0",
                "maxSteps": 4,
                "maxParallelSubtasks": 1,
                "allowBackground": False,
                "allowSubagents": False,
                "allowFileWrite": "blocked",
                "allowShell": "blocked",
                "allowNetwork": False,
                "retryLimit": 0,
            },
            {
                **DEFAULT_AUTONOMY_PROFILE,
                "id": "conservative",
                "name": "Conservative",
                "level": "L1",
                "maxSteps": 10,
                "maxParallelSubtasks": 2,
                "allowBackground": False,
                "allowSubagents": True,
            },
            deepcopy(DEFAULT_AUTONOMY_PROFILE),
            {
                **DEFAULT_AUTONOMY_PROFILE,
                "id": "autonomous",
                "name": "Autonomous",
                "level": "L3",
                "maxSteps": 40,
                "maxParallelSubtasks": 6,
                "allowBackground": True,
                "allowSubagents": True,
            },
        ],
    },
    "agentSoul": {
        "activeProfileId": "default",
        "workspaceInstructions": "",
        "sessionOverrideEnabled": False,
        "profiles": [deepcopy(DEFAULT_AGENT_SOUL_PROFILE)],
    },
    "tools": {
        "runCommand": {
            "allowedShell": "powershell",
            "allowedCommands": [],
            "allowlist": [],
            "deniedCommands": [],
            "denylist": [],
            "blockedPatterns": ["rm -rf", "shutdown", "format"],
            "allowedCwdRoots": [],
        }
    },
    "reflection": {
        "enabled": False,
        "maxRetries": 2,
        "confidenceThreshold": 0.7,
        "evaluationPrompt": "",
    },
    "ui": {
        "language": "zh-CN",
        "showRawEvents": False,
        "theme": "light",
        "density": "comfortable",
        "radius": "md",
        "motion": "subtle",
        "accentColor": "cyan",
        "transparency": 0.78,
        "fontScale": 1,
        "reasoningEffort": "max",
        "webFetchPreflight": True,
    },
    "features": {
        "multiAgent": True,
        "streamingDeltaPersist": True,
    },
}
