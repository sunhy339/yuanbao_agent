use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    env,
    fs,
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStderr, ChildStdin, ChildStdout, Command, Stdio},
    sync::{
        atomic::{AtomicU64, Ordering},
        mpsc::{self, Sender},
        Arc, Mutex,
    },
    thread,
    time::{Duration, UNIX_EPOCH},
};
use tauri::{AppHandle, Emitter, Manager, State};

const EVENT_CHANNEL: &str = "agent://event";
const RPC_TIMEOUT: Duration = Duration::from_secs(240);

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct HostStatus {
    runtime_transport: &'static str,
    event_channel: &'static str,
    runtime_running: bool,
    repo_root: String,
    python_module: &'static str,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SessionCreatePayload {
    workspace_id: String,
    title: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceMemoryClearPayload {
    workspace_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceMemoryInitPayload {
    workspace_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceFocusUpdatePayload {
    workspace_id: String,
    focus: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct MessageSendPayload {
    session_id: String,
    content: String,
    attachments: Vec<String>,
    task_id: Option<String>,
    mode: Option<String>,
    new_task: Option<bool>,
    background: Option<bool>,
    client_message_id: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct MessageListPayload {
    session_id: String,
    limit: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TaskGetPayload {
    task_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TaskControlPayload {
    task_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TaskListPayload {
    session_id: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorktreeIdPayload {
    worktree_id: String,
    full: Option<bool>,
    include_full_diff: Option<bool>,
    max_diff_bytes: Option<u64>,
    diff_preview_bytes: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorktreeMergePayload {
    worktree_id: String,
    approved: Option<bool>,
    approval_id: Option<String>,
    target_branch: Option<String>,
    verification_commands: Option<Vec<String>>,
    verification_timeout_ms: Option<u64>,
    review_status: Option<String>,
    reviewer_summary: Option<String>,
    reviewer: Option<String>,
    multi_agent_worktree_strategy: Option<Value>,
    diff_preview_bytes: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorktreeByTaskPayload {
    task_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorktreeCleanupPayload {
    worktree_id: String,
    force: Option<bool>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ScheduledTaskCreatePayload {
    name: String,
    prompt: String,
    schedule: String,
    enabled: Option<bool>,
    status: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ScheduledTaskUpdatePayload {
    task_id: String,
    name: Option<String>,
    prompt: Option<String>,
    schedule: Option<String>,
    enabled: Option<bool>,
    status: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ScheduledTaskTogglePayload {
    task_id: String,
    enabled: bool,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ScheduledTaskIdPayload {
    task_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ScheduledTaskLogsPayload {
    task_id: Option<String>,
    limit: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ApprovalSubmitPayload {
    approval_id: String,
    decision: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CommandLogGetPayload {
    command_id: String,
}

#[derive(Debug, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct CommandLogListPayload {
    task_id: Option<String>,
    session_id: Option<String>,
    status: Option<String>,
    limit: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CommandCancelPayload {
    command_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct DiffGetPayload {
    patch_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceFileListPayload {
    workspace_root: String,
    path: Option<String>,
    max_entries: Option<usize>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceFileReadPayload {
    workspace_root: String,
    path: String,
    max_bytes: Option<usize>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceFileEntryView {
    name: String,
    path: String,
    kind: String,
    size: Option<u64>,
    modified_at: Option<u128>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TraceListPayload {
    task_id: String,
    limit: Option<u64>,
}

#[derive(Debug, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct LogExportPayload {
    session_id: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct ErrorsListPayload {
    session_id: Option<String>,
    task_id: Option<String>,
    source: Option<String>,
    limit: Option<u64>,
}

#[derive(Debug, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct MetricsListPayload {
    session_id: Option<String>,
    limit: Option<u64>,
}

#[derive(Clone, Default)]
struct RuntimeManager {
    bridge: Arc<Mutex<RuntimeBridge>>,
}

#[derive(Default)]
struct RuntimeBridge {
    process: Option<RuntimeProcess>,
    next_request_id: AtomicU64,
}

struct RuntimeProcess {
    _child: Child,
    stdin: ChildStdin,
    pending: Arc<Mutex<HashMap<String, Sender<Result<Value, String>>>>>,
}

impl RuntimeManager {
    fn call(&self, app_handle: &AppHandle, method: &str, params: Value) -> Result<Value, String> {
        // Phase 1: ensure the runtime is started (holds lock briefly)
        {
            let mut bridge = self
                .bridge
                .lock()
                .map_err(|_| "Failed to acquire runtime bridge lock".to_string())?;
            bridge.ensure_started(app_handle)?;
        }

        // Phase 2: send request and register pending callback (lock per-operation)
        let (rx, request_id, pending) = {
            let mut bridge = self
                .bridge
                .lock()
                .map_err(|_| "Failed to acquire runtime bridge lock".to_string())?;

            let request_id = format!(
                "req_{}",
                bridge.next_request_id.fetch_add(1, Ordering::Relaxed)
            );
            let (tx, rx) = mpsc::channel();

            let process = bridge
                .process
                .as_mut()
                .ok_or_else(|| "Runtime process is not available".to_string())?;

            process
                .pending
                .lock()
                .map_err(|_| "Failed to lock pending response map".to_string())?
                .insert(request_id.clone(), tx);

            let payload = serde_json::to_string(&json!({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }))
            .map_err(|reason| format!("Failed to serialize RPC request: {reason}"))?;

            if let Err(reason) = writeln!(process.stdin, "{payload}").and_then(|_| process.stdin.flush()) {
                let _ = process
                    .pending
                    .lock()
                    .map(|mut pending| pending.remove(&request_id));
                return Err(format!("Failed to write RPC request to runtime: {reason}"));
            }

            (rx, request_id, Arc::clone(&process.pending))
        };

        match rx.recv_timeout(RPC_TIMEOUT) {
            Ok(Ok(value)) => Ok(value),
            Ok(Err(message)) => Err(message),
            Err(_) => {
                let _ = pending
                    .lock()
                    .map(|mut pending_guard| pending_guard.remove(&request_id));
                Err("Timed out while waiting for the Python runtime".to_string())
            }
        }
    }

    async fn call_async(&self, app_handle: AppHandle, method: String, params: serde_json::Value) -> Result<serde_json::Value, String> {
        let manager = self.clone();
        tauri::async_runtime::spawn_blocking(move || manager.call(&app_handle, &method, params))
            .await
            .map_err(|reason| format!("Runtime async worker failed: {reason}"))?
    }

    fn runtime_running(&self) -> bool {
        self.bridge
            .lock()
            .map(|bridge| bridge.process.is_some())
            .unwrap_or(false)
    }
}

impl RuntimeBridge {
    fn ensure_started(&mut self, app_handle: &AppHandle) -> Result<(), String> {
        if self.process.is_some() {
            return Ok(());
        }

        let data_dir = resolve_data_dir(app_handle)?;
        fs::create_dir_all(&data_dir)
            .map_err(|reason| format!("Failed to create data directory: {reason}"))?;

        let runtime_src = resolve_runtime_src()?;
        let database_path = env::var_os("LOCAL_AGENT_DB_PATH")
            .map(PathBuf::from)
            .unwrap_or_else(|| data_dir.join("data.db"));
        let working_dir = resolve_working_dir()?;
        let python_executable =
            env::var("LOCAL_AGENT_PYTHON").unwrap_or_else(|_| "python".to_string());

        let mut command = Command::new(python_executable);
        command
            .arg("-u")
            .arg("-m")
            .arg("local_agent_runtime.main")
            .current_dir(&working_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .env("PYTHONPATH", append_path_env("PYTHONPATH", &runtime_src)?)
            .env("LOCAL_AGENT_DB_PATH", database_path.as_os_str())
            .env("PYTHONUTF8", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONUNBUFFERED", "1");

        let mut child = command
            .spawn()
            .map_err(|reason| format!("Failed to start Python runtime: {reason}"))?;

        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "Python runtime stdin is not available".to_string())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "Python runtime stdout is not available".to_string())?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| "Python runtime stderr is not available".to_string())?;

        let pending = Arc::new(Mutex::new(HashMap::new()));
        spawn_stdout_pump(app_handle.clone(), stdout, Arc::clone(&pending));
        spawn_stderr_pump(stderr);

        self.process = Some(RuntimeProcess {
            _child: child,
            stdin,
            pending,
        });

        Ok(())
    }
}

fn spawn_stdout_pump(
    app_handle: AppHandle,
    stdout: ChildStdout,
    pending: Arc<Mutex<HashMap<String, Sender<Result<Value, String>>>>>,
) {
    thread::spawn(move || {
        let reader = BufReader::new(stdout);

        for line_result in reader.lines() {
            let line = match line_result {
                Ok(line) => line,
                Err(reason) => {
                    eprintln!("Failed to read runtime stdout: {reason}");
                    break;
                }
            };

            if line.trim().is_empty() {
                continue;
            }

            let payload: Value = match serde_json::from_str(&line) {
                Ok(payload) => payload,
                Err(reason) => {
                    eprintln!("Failed to parse runtime stdout payload: {reason}");
                    continue;
                }
            };

            if payload.get("kind").and_then(Value::as_str) == Some("event") {
                if let Some(event_payload) = payload.get("payload").cloned() {
                    let _ = app_handle.emit(EVENT_CHANNEL, event_payload);
                }
                continue;
            }

            let Some(request_id) = payload.get("id").and_then(Value::as_str) else {
                continue;
            };

            let result = if let Some(error) = payload.get("error") {
                Err(error
                    .get("message")
                    .and_then(Value::as_str)
                    .unwrap_or("Runtime returned an unknown error")
                    .to_string())
            } else {
                Ok(payload.get("result").cloned().unwrap_or(Value::Null))
            };

            if let Ok(mut pending_requests) = pending.lock() {
                if let Some(sender) = pending_requests.remove(request_id) {
                    let _ = sender.send(result);
                }
            }
        }
    });
}

fn spawn_stderr_pump(stderr: ChildStderr) {
    thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line_result in reader.lines() {
            match line_result {
                Ok(line) if !line.trim().is_empty() => eprintln!("runtime stderr: {line}"),
                Ok(_) => {}
                Err(reason) => {
                    eprintln!("Failed to read runtime stderr: {reason}");
                    break;
                }
            }
        }
    });
}

fn repo_root() -> Result<PathBuf, String> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir
        .parent()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .ok_or_else(|| {
            "Failed to resolve repository root from Cargo manifest directory".to_string()
        })
}

/// Resolve the data directory for database storage.
///
/// In packaged mode (no valid repo root), uses the OS-standard app data dir
/// provided by Tauri (e.g. `%APPDATA%\<app>` on Windows).
/// In dev mode, falls back to `repo_root()/runtime/.local-agent-runtime.sqlite3`
/// only if the repo root is available.
fn resolve_data_dir(app_handle: &AppHandle) -> Result<PathBuf, String> {
    match app_handle.path().app_data_dir() {
        Ok(dir) => Ok(dir),
        Err(reason) => {
            // Fallback: try repo_root for dev mode
            repo_root().map(|root| root.join("runtime")).map_err(|_| {
                format!("Failed to resolve data directory: {reason}")
            })
        }
    }
}

fn resolve_skills_dir(app_handle: &AppHandle) -> Result<PathBuf, String> {
    if let Ok(codex_home) = env::var("CODEX_HOME") {
        let trimmed = codex_home.trim();
        if !trimmed.is_empty() {
            return Ok(PathBuf::from(trimmed).join("skills"));
        }
    }

    match app_handle.path().home_dir() {
        Ok(dir) => Ok(dir.join(".codex").join("skills")),
        Err(reason) => Err(format!("Failed to resolve home directory: {reason}")),
    }
}

/// Resolve the runtime source directory for PYTHONPATH.
///
/// In dev mode uses `repo_root()/runtime/src`.
/// In packaged mode the Python package is installed, so PYTHONPATH can be empty.
fn resolve_runtime_src() -> Result<PathBuf, String> {
    match repo_root() {
        Ok(root) => Ok(root.join("runtime").join("src")),
        Err(_) => Ok(PathBuf::new()),
    }
}

/// Resolve the working directory for the Python process.
///
/// In dev mode uses `repo_root()`.
/// In packaged mode uses the app data directory.
fn resolve_working_dir() -> Result<PathBuf, String> {
    repo_root().or_else(|_| {
        // Packaged mode: use a temp-like dir as working dir
        env::current_dir().map_err(|e| format!("Failed to resolve working dir: {e}"))
    })
}

fn open_path_in_file_manager(path: &PathBuf) -> Result<(), String> {
    let mut command = if cfg!(target_os = "windows") {
        let mut command = Command::new("explorer.exe");
        command.arg(path);
        command
    } else if cfg!(target_os = "macos") {
        let mut command = Command::new("open");
        command.arg(path);
        command
    } else {
        let mut command = Command::new("xdg-open");
        command.arg(path);
        command
    };

    command
        .spawn()
        .map_err(|reason| format!("Failed to open {}: {reason}", path.display()))?;
    Ok(())
}

fn relative_path_string(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .ok()
        .map(|relative| {
            relative
                .components()
                .map(|component| component.as_os_str().to_string_lossy().to_string())
                .collect::<Vec<_>>()
                .join("/")
        })
        .unwrap_or_default()
}

fn resolve_workspace_path(workspace_root: &str, relative_path: Option<&str>) -> Result<(PathBuf, PathBuf), String> {
    let root = PathBuf::from(workspace_root)
        .canonicalize()
        .map_err(|reason| format!("Failed to resolve workspace root: {reason}"))?;
    let raw_relative = relative_path.unwrap_or("").trim();
    let target = if raw_relative.is_empty() || raw_relative == "." {
        root.clone()
    } else {
        let relative = PathBuf::from(raw_relative);
        if relative.is_absolute() {
            return Err("Workspace file paths must be relative to the workspace root".to_string());
        }
        root.join(relative)
            .canonicalize()
            .map_err(|reason| format!("Failed to resolve workspace path: {reason}"))?
    };
    if target != root && !target.starts_with(&root) {
        return Err("Workspace path is outside the selected workspace".to_string());
    }
    Ok((root, target))
}

fn file_modified_at_ms(metadata: &fs::Metadata) -> Option<u128> {
    metadata
        .modified()
        .ok()
        .and_then(|modified| modified.duration_since(UNIX_EPOCH).ok())
        .map(|duration| duration.as_millis())
}

fn append_path_env(name: &str, first_path: &Path) -> Result<std::ffi::OsString, String> {
    let mut paths = vec![first_path.to_path_buf()];
    if let Some(existing) = env::var_os(name) {
        paths.extend(env::split_paths(&existing));
    }
    env::join_paths(paths).map_err(|reason| format!("Failed to prepare {name}: {reason}"))
}

#[tauri::command]
fn host_status(state: State<'_, RuntimeManager>) -> Result<HostStatus, String> {
    let root = repo_root()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| "(packaged)".to_string());
    Ok(HostStatus {
        runtime_transport: "json-rpc-stdio",
        event_channel: EVENT_CHANNEL,
        runtime_running: state.runtime_running(),
        repo_root: root,
        python_module: "local_agent_runtime.main",
    })
}

#[tauri::command]
async fn workspace_open(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    path: String,
) -> Result<Value, String> {
    state.call_async(app_handle, "workspace.open".to_string(), json!({ "path": path })).await
}

#[tauri::command]
async fn workspace_memory_clear(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorkspaceMemoryClearPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "workspace.memory.clear".to_string(),
        json!({ "workspaceId": payload.workspace_id }),
    ).await
}

#[tauri::command]
async fn workspace_memory_init(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorkspaceMemoryInitPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "workspace.memory.init".to_string(),
        json!({ "workspaceId": payload.workspace_id }),
    ).await
}

#[tauri::command]
async fn workspace_focus_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorkspaceFocusUpdatePayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "workspace.focus.update".to_string(),
        json!({ "workspaceId": payload.workspace_id, "focus": payload.focus }),
    ).await
}

#[tauri::command]
async fn session_create(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: SessionCreatePayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "session.create".to_string(),
        json!({
            "workspaceId": payload.workspace_id,
            "title": payload.title,
        }),
    ).await
}

#[tauri::command]
async fn session_list(app_handle: AppHandle, state: State<'_, RuntimeManager>) -> Result<Value, String> {
    state.call_async(app_handle, "session.list".to_string(), json!({})).await
}

#[tauri::command]
async fn message_send(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: MessageSendPayload,
) -> Result<Value, String> {
    let is_supplement = payload.mode.as_deref() == Some("supplement");
    let background = payload.background.unwrap_or(!is_supplement);
    state.call_async(
        app_handle,
        "message.send".to_string(),
        json!({
            "sessionId": payload.session_id,
            "content": payload.content,
            "attachments": payload.attachments,
            "taskId": payload.task_id,
            "mode": payload.mode,
            "newTask": payload.new_task,
            "background": background,
            "clientMessageId": payload.client_message_id,
        }),
    ).await
}

#[tauri::command]
async fn message_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: MessageListPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "message.list".to_string(),
        json!({ "sessionId": payload.session_id, "limit": payload.limit }),
    ).await
}

#[tauri::command]
fn workspace_file_list(payload: WorkspaceFileListPayload) -> Result<Value, String> {
    let (root, target) = resolve_workspace_path(&payload.workspace_root, payload.path.as_deref())?;
    if !target.is_dir() {
        return Err("Workspace path is not a directory".to_string());
    }

    let limit = payload.max_entries.unwrap_or(200).clamp(1, 500);
    let mut entries = Vec::new();
    let mut truncated = false;
    for entry_result in fs::read_dir(&target).map_err(|reason| format!("Failed to read directory: {reason}"))? {
        let entry = entry_result.map_err(|reason| format!("Failed to read directory entry: {reason}"))?;
        if entries.len() >= limit {
            truncated = true;
            break;
        }
        let metadata = entry
            .metadata()
            .map_err(|reason| format!("Failed to read file metadata: {reason}"))?;
        let kind = if metadata.is_dir() { "directory" } else { "file" };
        entries.push(WorkspaceFileEntryView {
            name: entry.file_name().to_string_lossy().to_string(),
            path: relative_path_string(&root, &entry.path()),
            kind: kind.to_string(),
            size: if metadata.is_file() { Some(metadata.len()) } else { None },
            modified_at: file_modified_at_ms(&metadata),
        });
    }
    entries.sort_by(|left, right| {
        let left_rank = if left.kind == "directory" { 0 } else { 1 };
        let right_rank = if right.kind == "directory" { 0 } else { 1 };
        left_rank
            .cmp(&right_rank)
            .then_with(|| left.name.to_lowercase().cmp(&right.name.to_lowercase()))
    });

    Ok(json!({
        "rootPath": root.display().to_string(),
        "path": relative_path_string(&root, &target),
        "entries": entries,
        "truncated": truncated,
    }))
}

#[tauri::command]
fn workspace_file_read(payload: WorkspaceFileReadPayload) -> Result<Value, String> {
    let (root, target) = resolve_workspace_path(&payload.workspace_root, Some(&payload.path))?;
    if !target.is_file() {
        return Err("Workspace path is not a file".to_string());
    }

    let limit = payload.max_bytes.unwrap_or(64 * 1024).clamp(1, 256 * 1024);
    let mut file = fs::File::open(&target).map_err(|reason| format!("Failed to open file: {reason}"))?;
    let mut buffer = Vec::new();
    std::io::Read::by_ref(&mut file)
        .take((limit + 1) as u64)
        .read_to_end(&mut buffer)
        .map_err(|reason| format!("Failed to read file: {reason}"))?;
    let truncated = buffer.len() > limit;
    if truncated {
        buffer.truncate(limit);
    }
    let bytes = buffer.len();
    if buffer.iter().any(|byte| *byte == 0) {
        return Ok(json!({
            "rootPath": root.display().to_string(),
            "path": relative_path_string(&root, &target),
            "bytes": bytes,
            "truncated": truncated,
            "binary": true,
        }));
    }

    match String::from_utf8(buffer) {
        Ok(content) => Ok(json!({
            "rootPath": root.display().to_string(),
            "path": relative_path_string(&root, &target),
            "content": content,
            "bytes": bytes,
            "truncated": truncated,
            "binary": false,
            "encoding": "utf-8",
        })),
        Err(_) => Ok(json!({
            "rootPath": root.display().to_string(),
            "path": relative_path_string(&root, &target),
            "bytes": bytes,
            "truncated": truncated,
            "binary": true,
        })),
    }
}

#[tauri::command]
async fn task_get(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TaskGetPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "task.get".to_string(),
        json!({ "taskId": payload.task_id }),
    ).await
}

#[tauri::command]
async fn task_cancel(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TaskControlPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "task.cancel".to_string(),
        json!({ "taskId": payload.task_id }),
    ).await
}

#[tauri::command]
async fn task_pause(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TaskControlPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "task.pause".to_string(),
        json!({ "taskId": payload.task_id }),
    ).await
}

#[tauri::command]
async fn task_resume(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TaskControlPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "task.resume".to_string(),
        json!({ "taskId": payload.task_id }),
    ).await
}

#[tauri::command]
async fn task_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<TaskListPayload>,
) -> Result<Value, String> {
    let params = match payload.and_then(|payload| payload.session_id) {
        Some(session_id) => json!({ "sessionId": session_id }),
        None => json!({}),
    };
    state.call_async(app_handle, "task.list".to_string(), params).await
}

#[tauri::command]
async fn worktree_get(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorktreeIdPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "worktree.get".to_string(),
        json!({ "worktreeId": payload.worktree_id }),
    ).await
}

#[tauri::command]
async fn worktree_get_by_task(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorktreeByTaskPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "worktree.getByTask".to_string(),
        json!({ "taskId": payload.task_id }),
    ).await
}

#[tauri::command]
async fn worktree_status(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorktreeIdPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "worktree.status".to_string(),
        json!({ "worktreeId": payload.worktree_id }),
    ).await
}

#[tauri::command]
async fn worktree_diff(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorktreeIdPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "worktree.diff".to_string(),
        json!({
            "worktreeId": payload.worktree_id,
            "full": payload.full,
            "includeFullDiff": payload.include_full_diff,
            "maxDiffBytes": payload.max_diff_bytes,
            "diffPreviewBytes": payload.diff_preview_bytes,
        }),
    ).await
}

#[tauri::command]
async fn worktree_merge(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorktreeMergePayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "worktree.merge".to_string(),
        json!({
            "worktreeId": payload.worktree_id,
            "approved": payload.approved.unwrap_or(false),
            "approvalId": payload.approval_id,
            "targetBranch": payload.target_branch,
            "verificationCommands": payload.verification_commands,
            "verificationTimeoutMs": payload.verification_timeout_ms,
            "reviewStatus": payload.review_status,
            "reviewerSummary": payload.reviewer_summary,
            "reviewer": payload.reviewer,
            "multiAgentWorktreeStrategy": payload.multi_agent_worktree_strategy,
            "diffPreviewBytes": payload.diff_preview_bytes,
        }),
    ).await
}

#[tauri::command]
async fn worktree_request_merge_approval(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorktreeMergePayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "worktree.requestMergeApproval".to_string(),
        json!({
            "worktreeId": payload.worktree_id,
            "targetBranch": payload.target_branch,
            "verificationCommands": payload.verification_commands,
            "verificationTimeoutMs": payload.verification_timeout_ms,
            "reviewStatus": payload.review_status,
            "reviewerSummary": payload.reviewer_summary,
            "reviewer": payload.reviewer,
            "multiAgentWorktreeStrategy": payload.multi_agent_worktree_strategy,
            "diffPreviewBytes": payload.diff_preview_bytes,
        }),
    ).await
}

#[tauri::command]
async fn worktree_cleanup(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorktreeCleanupPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "worktree.cleanup".to_string(),
        json!({ "worktreeId": payload.worktree_id, "force": payload.force.unwrap_or(false) }),
    ).await
}

#[tauri::command]
async fn schedule_create(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ScheduledTaskCreatePayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "schedule.create".to_string(),
        json!({
            "name": payload.name,
            "prompt": payload.prompt,
            "schedule": payload.schedule,
            "enabled": payload.enabled,
            "status": payload.status,
        }),
    ).await
}

#[tauri::command]
async fn schedule_list(app_handle: AppHandle, state: State<'_, RuntimeManager>) -> Result<Value, String> {
    state.call_async(app_handle, "schedule.list".to_string(), json!({})).await
}

#[tauri::command]
async fn schedule_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ScheduledTaskUpdatePayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "schedule.update".to_string(),
        json!({
            "taskId": payload.task_id,
            "name": payload.name,
            "prompt": payload.prompt,
            "schedule": payload.schedule,
            "enabled": payload.enabled,
            "status": payload.status,
        }),
    ).await
}

#[tauri::command]
async fn schedule_toggle(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ScheduledTaskTogglePayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "schedule.toggle".to_string(),
        json!({
            "taskId": payload.task_id,
            "enabled": payload.enabled,
        }),
    ).await
}

#[tauri::command]
async fn schedule_run_now(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ScheduledTaskIdPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "schedule.run_now".to_string(),
        json!({ "taskId": payload.task_id }),
    ).await
}

#[tauri::command]
async fn schedule_logs(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<ScheduledTaskLogsPayload>,
) -> Result<Value, String> {
    let params = match payload {
        Some(payload) => json!({
            "taskId": payload.task_id,
            "limit": payload.limit,
        }),
        None => json!({}),
    };
    state.call_async(app_handle, "schedule.logs".to_string(), params).await
}

#[tauri::command]
async fn approval_submit(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ApprovalSubmitPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "approval.submit".to_string(),
        json!({
            "approvalId": payload.approval_id,
            "decision": payload.decision,
        }),
    ).await
}

#[tauri::command]
async fn config_get(app_handle: AppHandle, state: State<'_, RuntimeManager>) -> Result<Value, String> {
    state.call_async(app_handle, "config.get".to_string(), json!({})).await
}

#[tauri::command]
async fn config_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "config.update".to_string(), payload).await
}

#[tauri::command]
async fn provider_test(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "provider.test".to_string(), payload).await
}

#[tauri::command]
async fn command_log_get(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: CommandLogGetPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "command_log.get".to_string(),
        json!({ "commandId": payload.command_id }),
    ).await
}

#[tauri::command]
async fn command_log_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<CommandLogListPayload>,
) -> Result<Value, String> {
    let payload = payload.unwrap_or_default();
    state.call_async(
        app_handle,
        "command_log.list".to_string(),
        json!({
            "taskId": payload.task_id,
            "sessionId": payload.session_id,
            "status": payload.status,
            "limit": payload.limit,
        }),
    ).await
}

#[tauri::command]
async fn command_cancel(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: CommandCancelPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "command.cancel".to_string(),
        json!({ "commandId": payload.command_id }),
    ).await
}

#[tauri::command]
async fn diff_get(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: DiffGetPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "diff.get".to_string(),
        json!({ "patchId": payload.patch_id }),
    ).await
}

#[tauri::command]
async fn trace_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TraceListPayload,
) -> Result<Value, String> {
    state.call_async(
        app_handle,
        "trace.list".to_string(),
        json!({
            "taskId": payload.task_id,
            "limit": payload.limit,
        }),
    ).await
}

#[tauri::command]
async fn log_export(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<LogExportPayload>,
) -> Result<Value, String> {
    let payload = payload.unwrap_or_default();
    state.call_async(
        app_handle,
        "log.export".to_string(),
        json!({ "sessionId": payload.session_id }),
    ).await
}

#[tauri::command]
async fn errors_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<ErrorsListPayload>,
) -> Result<Value, String> {
    let payload = payload.unwrap_or_default();
    state.call_async(
        app_handle,
        "errors.list".to_string(),
        json!({
            "sessionId": payload.session_id,
            "taskId": payload.task_id,
            "source": payload.source,
            "limit": payload.limit,
        }),
    ).await
}

#[tauri::command]
async fn metrics_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<MetricsListPayload>,
) -> Result<Value, String> {
    let payload = payload.unwrap_or_default();
    state.call_async(
        app_handle,
        "metrics.list".to_string(),
        json!({
            "sessionId": payload.session_id,
            "limit": payload.limit,
        }),
    ).await
}

#[tauri::command]
fn open_app_path(app_handle: AppHandle, kind: String) -> Result<Value, String> {
    let data_dir = resolve_data_dir(&app_handle)?;
    fs::create_dir_all(&data_dir)
        .map_err(|reason| format!("Failed to create data directory: {reason}"))?;

    let target = match kind.as_str() {
        "data" => data_dir,
        "logs" => {
            let logs_dir = data_dir.join("logs");
            fs::create_dir_all(&logs_dir)
                .map_err(|reason| format!("Failed to create logs directory: {reason}"))?;
            logs_dir
        }
        "skills" => {
            let skills_dir = resolve_skills_dir(&app_handle)?;
            fs::create_dir_all(&skills_dir)
                .map_err(|reason| format!("Failed to create skills directory: {reason}"))?;
            skills_dir
        }
        _ => return Err(format!("Unsupported app path kind: {kind}")),
    };

    open_path_in_file_manager(&target)?;
    Ok(json!({ "path": target.display().to_string() }))
}

#[tauri::command]
async fn skill_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<Value>,
) -> Result<Value, String> {
    state.call_async(app_handle, "skill.list".to_string(), payload.unwrap_or_else(|| json!({}))).await
}

#[tauri::command]
async fn skill_create(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "skill.create".to_string(), payload).await
}

#[tauri::command]
async fn skill_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "skill.update".to_string(), payload).await
}

#[tauri::command]
async fn skill_delete(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "skill.delete".to_string(), payload).await
}

#[tauri::command]
async fn mcp_server_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<Value>,
) -> Result<Value, String> {
    state.call_async(app_handle, "mcp.server.list".to_string(), payload.unwrap_or_else(|| json!({}))).await
}

#[tauri::command]
async fn mcp_server_create(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "mcp.server.create".to_string(), payload).await
}

#[tauri::command]
async fn mcp_server_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "mcp.server.update".to_string(), payload).await
}

#[tauri::command]
async fn mcp_server_delete(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "mcp.server.delete".to_string(), payload).await
}

#[tauri::command]
async fn mcp_tools_refresh(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<Value>,
) -> Result<Value, String> {
    state.call_async(app_handle, "mcp.tools.refresh".to_string(), payload.unwrap_or_else(|| json!({}))).await
}

#[tauri::command]
async fn agent_profile_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<Value>,
) -> Result<Value, String> {
    state.call_async(app_handle, "agent.profile.list".to_string(), payload.unwrap_or_else(|| json!({}))).await
}

#[tauri::command]
async fn agent_profile_create(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "agent.profile.create".to_string(), payload).await
}

#[tauri::command]
async fn agent_profile_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "agent.profile.update".to_string(), payload).await
}

#[tauri::command]
async fn agent_profile_delete(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "agent.profile.delete".to_string(), payload).await
}

#[tauri::command]
async fn agent_profile_validate(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "agent.profile.validate".to_string(), payload).await
}

#[tauri::command]
async fn agent_profile_preview_tools(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "agent.profile.previewTools".to_string(), payload).await
}

#[tauri::command]
async fn hook_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "hook.list".to_string(), payload).await
}

#[tauri::command]
async fn hook_create(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "hook.create".to_string(), payload).await
}

#[tauri::command]
async fn hook_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "hook.update".to_string(), payload).await
}

#[tauri::command]
async fn hook_delete(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "hook.delete".to_string(), payload).await
}

#[tauri::command]
async fn hook_get(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "hook.get".to_string(), payload).await
}

#[tauri::command]
async fn hook_list_executions(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call_async(app_handle, "hook.listExecutions".to_string(), payload).await
}

#[tauri::command]
fn e2e_fixture() -> Result<Value, String> {
    let flow = env::var("YUANBAO_TAURI_E2E").unwrap_or_default();
    if flow == "ui-smoke"
        || flow == "mcp-live"
        || flow == "session-recovery-seed"
        || flow == "session-recovery-verify" {
        let repo_root = repo_root()?;
        let workspace_path = env::var("YUANBAO_TAURI_E2E_WORKSPACE")
            .unwrap_or_else(|_| repo_root.display().to_string());
        let prompt = env::var("YUANBAO_TAURI_E2E_PROMPT").unwrap_or_else(|_| {
            "Session recovery seed: persist this message across a desktop restart.".to_string()
        });
        let session_title = env::var("YUANBAO_TAURI_E2E_SESSION_TITLE")
            .unwrap_or_else(|_| "E2E Recovery Session".to_string());

        return Ok(json!({
            "enabled": true,
            "flow": flow,
            "workspacePath": workspace_path,
            "prompt": prompt,
            "sessionTitle": session_title,
        }));
    }

    if flow != "provider-flow" {
        return Ok(json!({ "enabled": false }));
    }

    let repo_root = repo_root()?;
    let api_key_env_var_name =
        env::var("YUANBAO_TAURI_E2E_API_KEY_ENV").unwrap_or_else(|_| "LOCAL_AGENT_PROVIDER_API_KEY".to_string());
    if env::var(&api_key_env_var_name).unwrap_or_default().is_empty() {
        return Err(format!(
            "E2E provider API key env var is not set: {api_key_env_var_name}"
        ));
    }

    let workspace_path = env::var("YUANBAO_TAURI_E2E_WORKSPACE")
        .unwrap_or_else(|_| repo_root.display().to_string());
    let prompt = env::var("YUANBAO_TAURI_E2E_PROMPT").unwrap_or_else(|_| {
        "Read-only check: confirm whether app/src/lib/runtimeClient.ts exists. Answer in one short sentence and do not modify files.".to_string()
    });

    Ok(json!({
        "enabled": true,
        "flow": flow,
        "workspacePath": workspace_path,
        "prompt": prompt,
        "autoApprove": env::var("YUANBAO_TAURI_E2E_AUTO_APPROVE")
            .map(|value| value == "1" || value.eq_ignore_ascii_case("true"))
            .unwrap_or(false),
        "provider": {
            "profileId": env::var("YUANBAO_TAURI_E2E_PROVIDER_ID").unwrap_or_else(|_| "e2e-provider".to_string()),
            "name": env::var("YUANBAO_TAURI_E2E_PROVIDER_NAME").unwrap_or_else(|_| "E2E Provider".to_string()),
            "baseUrl": env::var("YUANBAO_TAURI_E2E_BASE_URL").unwrap_or_else(|_| "https://api.ximeixg.cloud/v1".to_string()),
            "apiFormat": env::var("YUANBAO_TAURI_E2E_API_FORMAT").unwrap_or_else(|_| "openai-chat".to_string()),
            "model": env::var("YUANBAO_TAURI_E2E_MODEL").unwrap_or_else(|_| "MiniMax-M2.7-highspeed".to_string()),
            "apiKeyEnvVarName": api_key_env_var_name,
            "timeout": env::var("YUANBAO_TAURI_E2E_PROVIDER_TIMEOUT")
                .ok()
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(120),
        }
    }))
}

#[tauri::command]
fn e2e_finish(app_handle: AppHandle, payload: Value) -> Result<(), String> {
    let flow = env::var("YUANBAO_TAURI_E2E").unwrap_or_default();
    if flow != "provider-flow"
        && flow != "mcp-live"
        && flow != "ui-smoke"
        && flow != "session-recovery-seed"
        && flow != "session-recovery-verify" {
        return Err("E2E result writing is disabled.".to_string());
    }

    let result_path = env::var("YUANBAO_TAURI_E2E_RESULT_PATH")
        .map_err(|_| "YUANBAO_TAURI_E2E_RESULT_PATH is required.".to_string())?;
    let result_path = PathBuf::from(result_path);
    if let Some(parent) = result_path.parent() {
        fs::create_dir_all(parent)
            .map_err(|reason| format!("Failed to create E2E result directory: {reason}"))?;
    }

    let body = serde_json::to_string_pretty(&payload)
        .map_err(|reason| format!("Failed to serialize E2E result: {reason}"))?;
    fs::write(&result_path, body)
        .map_err(|reason| format!("Failed to write E2E result: {reason}"))?;

    if env::var("YUANBAO_TAURI_E2E_EXIT").unwrap_or_else(|_| "1".to_string()) != "0" {
        let exit_code = if payload
            .get("ok")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            0
        } else {
            1
        };
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(200));
            app_handle.exit(exit_code);
        });
    }

    Ok(())
}

pub fn build_app() -> tauri::Builder<tauri::Wry> {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(RuntimeManager::default())
        .invoke_handler(tauri::generate_handler![
            host_status,
            workspace_open,
            workspace_focus_update,
            workspace_memory_clear,
            workspace_memory_init,
            session_create,
            session_list,
            message_send,
            message_list,
            workspace_file_list,
            workspace_file_read,
            task_get,
            task_cancel,
            task_pause,
            task_resume,
            task_list,
            worktree_get,
            worktree_get_by_task,
            worktree_status,
            worktree_diff,
            worktree_merge,
            worktree_request_merge_approval,
            worktree_cleanup,
            schedule_create,
            schedule_list,
            schedule_update,
            schedule_toggle,
            schedule_run_now,
            schedule_logs,
            approval_submit,
            config_get,
            config_update,
            provider_test,
            command_log_get,
            command_log_list,
            command_cancel,
            diff_get,
            trace_list,
            log_export,
            errors_list,
            metrics_list,
            open_app_path,
            skill_list,
            skill_create,
            skill_update,
            skill_delete,
            mcp_server_list,
            mcp_server_create,
            mcp_server_update,
            mcp_server_delete,
            mcp_tools_refresh,
            agent_profile_list,
            agent_profile_create,
            agent_profile_update,
            agent_profile_delete,
            agent_profile_validate,
            agent_profile_preview_tools,
            hook_list,
            hook_create,
            hook_update,
            hook_delete,
            hook_get,
            hook_list_executions,
            e2e_fixture,
            e2e_finish
        ])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn runtime_manager_clones_share_the_same_bridge() {
        let manager = RuntimeManager::default();
        let cloned = manager.clone();

        let _guard = manager.bridge.lock().expect("manager bridge lock");

        assert!(cloned.bridge.try_lock().is_err());
    }
}
