use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    env,
    fs,
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
struct TraceListPayload {
    task_id: String,
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
    fn call(
        &mut self,
        app_handle: &AppHandle,
        method: &str,
        params: Value,
    ) -> Result<Value, String> {
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

        if let Err(reason) = writeln!(process.stdin, "{payload}").and_then(|_| process.stdin.flush()) {
            let _ = process
                .pending
                .lock()
                .map(|mut pending| pending.remove(&request_id));
            return Err(format!("Failed to write RPC request to runtime: {reason}"));
        }

        match rx.recv_timeout(RPC_TIMEOUT) {
            Ok(Ok(value)) => Ok(value),
            Ok(Err(message)) => Err(message),
            Err(_) => {
                let _ = process
                    .pending
                    .lock()
                    .map(|mut pending| pending.remove(&request_id));
                Err("Timed out while waiting for the Python runtime".to_string())
            }
        }
    }

    fn ensure_started(&mut self, app_handle: &AppHandle) -> Result<(), String> {
        if self.process.is_some() {
            return Ok(());
        }

        let repo_root = repo_root()?;
        let runtime_src = repo_root.join("runtime").join("src");
        let database_path = env::var_os("LOCAL_AGENT_DB_PATH")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                repo_root
                    .join("runtime")
                    .join(".local-agent-runtime.sqlite3")
            });
        let python_executable =
            env::var("LOCAL_AGENT_PYTHON").unwrap_or_else(|_| "python".to_string());

        let mut command = Command::new(python_executable);
        command
            .arg("-m")
            .arg("local_agent_runtime.main")
            .current_dir(&repo_root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .env("PYTHONPATH", append_path_env("PYTHONPATH", &runtime_src)?)
            .env("LOCAL_AGENT_DB_PATH", database_path.as_os_str())
            .env("PYTHONUTF8", "1")
            .env("PYTHONIOENCODING", "utf-8");

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
fn workspace_memory_clear(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorkspaceMemoryClearPayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "workspace.memory.clear",
        json!({ "workspaceId": payload.workspace_id }),
    )
}

#[tauri::command]
fn workspace_focus_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorkspaceFocusUpdatePayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "workspace.focus.update",
        json!({ "workspaceId": payload.workspace_id, "focus": payload.focus }),
    )
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
async fn message_send(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: MessageSendPayload,
) -> Result<Value, String> {
    let manager = state.inner().clone();
    let params = json!({
        "sessionId": payload.session_id,
        "content": payload.content,
        "attachments": payload.attachments,
        "background": true,
    });

    tauri::async_runtime::spawn_blocking(move || manager.call(&app_handle, "message.send", params))
        .await
        .map_err(|reason| format!("Runtime message worker failed: {reason}"))?
}

#[tauri::command]
fn message_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: MessageListPayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "message.list",
        json!({ "sessionId": payload.session_id, "limit": payload.limit }),
    )
}

#[tauri::command]
fn task_get(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TaskGetPayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "task.get",
        json!({ "taskId": payload.task_id }),
    )
}

#[tauri::command]
fn task_cancel(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TaskControlPayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "task.cancel",
        json!({ "taskId": payload.task_id }),
    )
}

#[tauri::command]
fn task_pause(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TaskControlPayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "task.pause",
        json!({ "taskId": payload.task_id }),
    )
}

#[tauri::command]
fn task_resume(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TaskControlPayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "task.resume",
        json!({ "taskId": payload.task_id }),
    )
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
fn schedule_create(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ScheduledTaskCreatePayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "schedule.create",
        json!({
            "name": payload.name,
            "prompt": payload.prompt,
            "schedule": payload.schedule,
            "enabled": payload.enabled,
            "status": payload.status,
        }),
    )
}

#[tauri::command]
fn schedule_list(app_handle: AppHandle, state: State<'_, RuntimeManager>) -> Result<Value, String> {
    state.call(&app_handle, "schedule.list", json!({}))
}

#[tauri::command]
fn schedule_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ScheduledTaskUpdatePayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "schedule.update",
        json!({
            "taskId": payload.task_id,
            "name": payload.name,
            "prompt": payload.prompt,
            "schedule": payload.schedule,
            "enabled": payload.enabled,
            "status": payload.status,
        }),
    )
}

#[tauri::command]
fn schedule_toggle(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ScheduledTaskTogglePayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "schedule.toggle",
        json!({
            "taskId": payload.task_id,
            "enabled": payload.enabled,
        }),
    )
}

#[tauri::command]
fn schedule_run_now(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ScheduledTaskIdPayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "schedule.run_now",
        json!({ "taskId": payload.task_id }),
    )
}

#[tauri::command]
fn schedule_logs(
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
    state.call(&app_handle, "schedule.logs", params)
}

#[tauri::command]
async fn approval_submit(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ApprovalSubmitPayload,
) -> Result<Value, String> {
    let manager = state.inner().clone();
    let params = json!({
        "approvalId": payload.approval_id,
        "decision": payload.decision,
    });

    tauri::async_runtime::spawn_blocking(move || manager.call(&app_handle, "approval.submit", params))
        .await
        .map_err(|reason| format!("Runtime approval worker failed: {reason}"))?
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
fn command_log_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<CommandLogListPayload>,
) -> Result<Value, String> {
    let payload = payload.unwrap_or_default();
    state.call(
        &app_handle,
        "command_log.list",
        json!({
            "taskId": payload.task_id,
            "sessionId": payload.session_id,
            "status": payload.status,
            "limit": payload.limit,
        }),
    )
}

#[tauri::command]
fn command_cancel(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: CommandCancelPayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "command.cancel",
        json!({ "commandId": payload.command_id }),
    )
}

#[tauri::command]
fn diff_get(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: DiffGetPayload,
) -> Result<Value, String> {
    state.call(
        &app_handle,
        "diff.get",
        json!({ "patchId": payload.patch_id }),
    )
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

#[tauri::command]
fn e2e_fixture() -> Result<Value, String> {
    let flow = env::var("YUANBAO_TAURI_E2E").unwrap_or_default();
    if flow == "ui-smoke" || flow == "session-recovery-seed" || flow == "session-recovery-verify" {
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
        "provider": {
            "profileId": env::var("YUANBAO_TAURI_E2E_PROVIDER_ID").unwrap_or_else(|_| "e2e-provider".to_string()),
            "name": env::var("YUANBAO_TAURI_E2E_PROVIDER_NAME").unwrap_or_else(|_| "E2E Provider".to_string()),
            "baseUrl": env::var("YUANBAO_TAURI_E2E_BASE_URL").unwrap_or_else(|_| "https://api.ximeixg.cloud/v1".to_string()),
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
        .manage(RuntimeManager::default())
        .invoke_handler(tauri::generate_handler![
            host_status,
            workspace_open,
            workspace_focus_update,
            workspace_memory_clear,
            session_create,
            session_list,
            message_send,
            message_list,
            task_get,
            task_cancel,
            task_pause,
            task_resume,
            task_list,
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
