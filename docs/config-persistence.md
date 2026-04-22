# Config Persistence

The runtime now persists `config.get` / `config.update` in the same SQLite database used by the Python sidecar.

## Behavior

- A single `config` table row stores `app_config` as JSON.
- On startup, the runtime loads the stored config and deep-merges it over `DEFAULT_CONFIG`.
- If the database is empty, bootstrap seeds the config row with defaults.
- If stored config is missing newly added fields, the runtime rewrites a normalized snapshot back to SQLite.

## Update Semantics

- Nested objects are merged recursively.
- Lists are replaced, which keeps updates like `search.glob` and `search.ignore` simple.
- Partial updates to `policy` and `tools.runCommand` are supported.

## Scope

- `LOCAL_AGENT_DB_PATH` should point at a SQLite file for persistence across runtime restarts.
- The Tauri bridge already injects that path when launching the Python runtime.
