use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    env,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStderr, ChildStdin, ChildStdout, Command, Stdio},
    sync::{
        atomic::{AtomicU64, Ordering},
        mpsc::{self, Sender},
        Arc, Mutex,
    },
    thread,
    time::Duration,
};
use tauri::{AppHandle, Emitter, State};

const EVENT_CHANNEL: &str = "agent://event";
const RPC_TIMEOUT: Duration = Duration::from_secs(10);

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
struct MessageSendPayload {
    session_id: String,
    content: String,
    attachments: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TaskGetPayload {
    task_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TaskListPayload {
    session_id: Option<String>,
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

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct DiffGetPayload {
    patch_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TraceListPayload {
    task_id: String,
    limit: Option<u64>,
}

#[derive(Default)]
struct RuntimeManager {
    bridge: Mutex<RuntimeBridge>,
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
        let mut bridge = self
            .bridge
            .lock()
            .map_err(|_| "Failed to acquire runtime bridge lock".to_string())?;
        bridge.call(app_handle, method, params)
    }

    fn runtime_running(&self) -> bool {
        self.bridge
            .lock()
            .map(|bridge| bridge.process.is_some())
            .unwrap_or(false)
    }
}

impl RuntimeBridge {
    fn call(&mut self, app_handle: &AppHandle, method: &str, params: Value) -> Result<Value, String> {
        self.ensure_started(app_handle)?;

        let request_id = format!(
            "req_{}",
            self.next_request_id.fetch_add(1, Ordering::Relaxed)
        );
        let (tx, rx) = mpsc::channel();

        let process = self
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

        writeln!(process.stdin, "{payload}")
            .and_then(|_| process.stdin.flush())
            .map_err(|reason| format!("Failed to write RPC request to runtime: {reason}"))?;

        match rx.recv_timeout(RPC_TIMEOUT) {
            Ok(Ok(value)) => Ok(value),
            Ok(Err(message)) => Err(message),
            Err(_) => Err("Timed out while waiting for the Python runtime".to_string()),
        }
    }

    fn ensure_started(&mut self, app_handle: &AppHandle) -> Result<(), String> {
        if self.process.is_some() {
            return Ok(());
        }

        let repo_root = repo_root()?;
        let runtime_src = repo_root.join("runtime").join("src");
        let database_path = repo_root.join("runtime").join(".local-agent-runtime.sqlite3");
        let python_executable = env::var("LOCAL_AGENT_PYTHON").unwrap_or_else(|_| "python".to_string());

        let mut command = Command::new(python_executable);
        command
            .arg("-m")
            .arg("local_agent_runtime.main")
            .current_dir(&repo_root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .env("PYTHONPATH", append_path_env("PYTHONPATH", &runtime_src)?)
            .env("LOCAL_AGENT_DB_PATH", database_path.as_os_str());

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
        .ok_or_else(|| "Failed to resolve repository root from Cargo manifest directory".to_string())
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
    Ok(HostStatus {
        runtime_transport: "json-rpc-stdio",
        event_channel: EVENT_CHANNEL,
        runtime_running: state.runtime_running(),
        repo_root: repo_root()?.display().to_string(),
        python_module: "local_agent_runtime.main",
    })
}

#[tauri::command]
fn workspace_open(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    path: String,
) -> Result<Value, String> {
    state.call(&app_handle, "workspace.open", json!({ "path": path }))
}

#[tauri::command]
fn session_create(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: SessionCreatePayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "session.create",
        json!({
            "workspaceId": payload.workspace_id,
            "title": payload.title,
        }),
    )
}

#[tauri::command]
fn session_list(app_handle: AppHandle, state: State<'_, RuntimeManager>) -> Result<Value, String> {
    state.call(&app_handle, "session.list", json!({}))
}

#[tauri::command]
fn message_send(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: MessageSendPayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "message.send",
        json!({
            "sessionId": payload.session_id,
            "content": payload.content,
            "attachments": payload.attachments,
        }),
    )
}

#[tauri::command]
fn task_get(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TaskGetPayload,
) -> Result<Value, String> {
    state.call(&app_handle, "task.get", json!({ "taskId": payload.task_id }))
}

#[tauri::command]
fn task_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<TaskListPayload>,
) -> Result<Value, String> {
    let params = match payload.and_then(|payload| payload.session_id) {
        Some(session_id) => json!({ "sessionId": session_id }),
        None => json!({}),
    };
    state.call(&app_handle, "task.list", params)
}

#[tauri::command]
fn approval_submit(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ApprovalSubmitPayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "approval.submit",
        json!({
            "approvalId": payload.approval_id,
            "decision": payload.decision,
        }),
    )
}

#[tauri::command]
fn config_get(app_handle: AppHandle, state: State<'_, RuntimeManager>) -> Result<Value, String> {
    state.call(&app_handle, "config.get", json!({}))
}

#[tauri::command]
fn config_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call(&app_handle, "config.update", payload)
}

#[tauri::command]
fn provider_test(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state.call(&app_handle, "provider.test", payload)
}

#[tauri::command]
fn command_log_get(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: CommandLogGetPayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "command_log.get",
        json!({ "commandId": payload.command_id }),
    )
}

#[tauri::command]
fn diff_get(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: DiffGetPayload,
) -> Result<Value, String> {
    state.call(&app_handle, "diff.get", json!({ "patchId": payload.patch_id }))
}

#[tauri::command]
fn trace_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TraceListPayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "trace.list",
        json!({
            "taskId": payload.task_id,
            "limit": payload.limit,
        }),
    )
}

pub fn build_app() -> tauri::Builder<tauri::Wry> {
    tauri::Builder::default()
        .manage(RuntimeManager::default())
        .invoke_handler(tauri::generate_handler![
            host_status,
            workspace_open,
            session_create,
            session_list,
            message_send,
            task_get,
            task_list,
            approval_submit,
            config_get,
            config_update,
            provider_test,
            command_log_get,
            diff_get,
            trace_list
        ])
}
